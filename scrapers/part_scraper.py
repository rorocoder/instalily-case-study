"""
Main scraper for PartSelect parts data.
Follows the schema defined in ARCHITECTURE.md.
"""

import json
import time
import random
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from concurrent.futures import ThreadPoolExecutor, as_completed
from .config import APPLIANCE_CONFIGS, SCRAPER_SETTINGS, PARTS_SCHEMA, OUTPUT_FILES
from .utils import (
    setup_driver,
    safe_navigate,
    wait_and_find_element,
    wait_and_find_elements,
    safe_get_text,
    safe_get_attribute,
    scroll_infinite_container,
)
from .utils.driver_utils import is_valid_url
from .utils.file_utils import (
    append_parts_data,
    append_model_compatibility_data,
    append_qna_data,
    append_repair_stories_data,
    append_reviews_data,
)
from .extractors import (
    extract_qna,
    extract_repair_stories,
    extract_reviews,
    format_qna_for_embedding,
    format_story_for_embedding,
    format_review_for_embedding,
)


def gentle_delay(delay_setting):
    """
    Apply a random delay from a tuple range or fixed value.

    Args:
        delay_setting: Either a tuple (min, max) or a single number
    """
    if isinstance(delay_setting, tuple):
        delay = random.uniform(delay_setting[0], delay_setting[1])
    else:
        delay = delay_setting
    print(f"  [Waiting {delay:.1f}s to avoid rate limiting...]")
    time.sleep(delay)
    return delay


def create_empty_part_record(appliance_type):
    """Create an empty part record with all schema fields."""
    return {field: "" for field in PARTS_SCHEMA} | {"appliance_type": appliance_type}


def scrape_part_page(driver, part_name, product_url, appliance_type, extract_embeddings=True):
    """
    Scrape all information from a single part page.

    Args:
        driver: Selenium WebDriver instance
        part_name: Name of the part
        product_url: URL of the product page
        appliance_type: Type of appliance (refrigerator, dishwasher)
        extract_embeddings: If True, also extract Q&A, repair stories, and reviews for embeddings

    Returns:
        tuple: (part_data dict, model_compatibility list, qna_data list, stories_data list, reviews_data list)
    """
    part_data = create_empty_part_record(appliance_type)
    part_data["part_name"] = part_name
    part_data["part_url"] = product_url
    model_compatibility = []
    qna_data = []
    stories_data = []
    reviews_data = []

    if not safe_navigate(driver, product_url):
        print(f"Failed to navigate to {part_name}. Skipping.")
        return part_data, model_compatibility, qna_data, stories_data, reviews_data

    # Part Name - from h1 title (e.g., "Whirlpool EveryDrop6 Refrigerator Water Filter EDR6D1")
    title_element = wait_and_find_element(driver, By.CSS_SELECTOR, "h1[itemprop='name']")
    if title_element:
        part_data["part_name"] = safe_get_text(title_element)

    # PS Number (Product ID) - e.g., "PS11722135"
    ps_elements = wait_and_find_elements(driver, By.CSS_SELECTOR, "span[itemprop='productID']")
    if ps_elements:
        part_data["ps_number"] = safe_get_text(ps_elements[0])

    # Manufacturer Part Number - e.g., "EDR6D1"
    mpn_elements = wait_and_find_elements(driver, By.CSS_SELECTOR, "span[itemprop='mpn']")
    if mpn_elements:
        part_data["manufacturer_part_number"] = safe_get_text(mpn_elements[0])

    # Part Manufacturer and Brand (e.g., "Whirlpool")
    brand_element = wait_and_find_element(driver, By.CSS_SELECTOR, "span[itemprop='brand'] span[itemprop='name']")
    if brand_element:
        brand_name = safe_get_text(brand_element)
        part_data["part_manufacturer"] = brand_name
        part_data["brand"] = brand_name  # Brand is same as manufacturer

    # Manufactured For - list of appliance brands this part is made for
    # e.g., "Whirlpool, KitchenAid, Maytag, Jenn-Air"
    try:
        brand_span = driver.find_element(By.CSS_SELECTOR, "span[itemprop='brand']")
        if brand_span:
            parent_div = brand_span.find_element(By.XPATH, "./..")
            spans = parent_div.find_elements(By.TAG_NAME, "span")
            for span in spans:
                span_text = safe_get_text(span)
                if span_text.lower().startswith("for "):
                    part_data["manufactured_for"] = span_text[4:].strip()
                    break
    except Exception:
        pass

    # Price - from content attribute or displayed text
    try:
        wait = WebDriverWait(driver, 10)
        price_container = wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "span.price.pd__price"))
        )
        if price_container:
            time.sleep(0.5)  # Wait for dynamic price updates

            # Try content attribute first (e.g., content="69.5900")
            price_content = safe_get_attribute(price_container, "content")
            if price_content:
                part_data["part_price"] = price_content
            else:
                # Try the displayed price element
                price_element = price_container.find_elements(By.CSS_SELECTOR, "span.js-partPrice")
                if price_element:
                    part_data["part_price"] = safe_get_text(price_element[0])
    except Exception:
        pass

    # Availability - e.g., "In Stock"
    availability_element = wait_and_find_element(driver, By.CSS_SELECTOR, "span[itemprop='availability']")
    if availability_element:
        part_data["availability"] = safe_get_text(availability_element)

    # Installation Video URL - look for actual install/repair video in #PartVideos section
    # NOT the generic OEM promotional video in pd__video section
    try:
        # First, try to find the PartVideos section which contains actual install videos
        part_videos_section = driver.find_elements(By.CSS_SELECTOR, "#PartVideos ~ div div.yt-video[data-yt-init]")
        if part_videos_section:
            video_id = safe_get_attribute(part_videos_section[0], "data-yt-init")
            if video_id:
                part_data["install_video_url"] = f"https://www.youtube.com/watch?v={video_id}"
        else:
            # Alternative: look for RepairVideo media type
            repair_video = driver.find_elements(By.CSS_SELECTOR, "[data-part-media-type='RepairVideo'][data-source-id]")
            if repair_video:
                video_id = safe_get_attribute(repair_video[0], "data-source-id")
                if video_id:
                    part_data["install_video_url"] = f"https://www.youtube.com/watch?v={video_id}"
    except Exception:
        pass

    # Part Description
    desc_element = wait_and_find_element(driver, By.CSS_SELECTOR, "div[itemprop='description']")
    if desc_element:
        part_data["part_description"] = safe_get_text(desc_element)

    # Rating - from meta tag content attribute
    rating_element = wait_and_find_element(driver, By.CSS_SELECTOR, "meta[itemprop='ratingValue']")
    if rating_element:
        part_data["average_rating"] = safe_get_attribute(rating_element, "content")

    # Review count - from meta tag content attribute
    review_count_element = wait_and_find_element(driver, By.CSS_SELECTOR, "meta[itemprop='reviewCount']")
    if review_count_element:
        part_data["num_reviews"] = safe_get_attribute(review_count_element, "content")

    # Part Type - extract from breadcrumb JSON data
    breadcrumb_data = wait_and_find_element(driver, By.CSS_SELECTOR, "div.js-breadcrumb-data")
    if breadcrumb_data:
        try:
            breadcrumb_json = breadcrumb_data.get_attribute("textContent")
            if breadcrumb_json:
                breadcrumbs = json.loads(breadcrumb_json)
                # Part type is typically the second-to-last breadcrumb (before the part itself)
                if len(breadcrumbs) >= 3:
                    part_data["part_type"] = breadcrumbs[-2].get("name", "")
        except Exception:
            pass

    # Extract troubleshooting info from the Troubleshooting section
    troubleshooting_section = wait_and_find_element(driver, By.CSS_SELECTOR, "div#Troubleshooting + div.pd__wrap, div[data-collapsible]")

    # Find all info divs in troubleshooting section
    info_divs = driver.find_elements(By.CSS_SELECTOR, "div.pd__wrap.row div.col-md-6.mt-3")
    symptoms_list = []
    products_list = []

    for div in info_divs:
        try:
            header = div.find_elements(By.CSS_SELECTOR, "div.bold.mb-1")
            if not header:
                continue
            header_text = safe_get_text(header[0])

            # Symptoms this part fixes
            if "This part fixes the following symptoms:" in header_text:
                symptom_items = div.find_elements(By.CSS_SELECTOR, "ul.list-disc li")
                symptoms_list = [safe_get_text(li) for li in symptom_items if safe_get_text(li)]

            # Products this part works with (appliance types) - informational only
            elif "This part works with the following products:" in header_text:
                # This is appliance types (Refrigerator, Dishwasher, etc.), not brand
                pass

            # Replaces parts
            elif "replaces these:" in header_text:
                replace_div = div.find_elements(By.CSS_SELECTOR, "div[data-collapse-container]")
                if replace_div:
                    part_data["replaces_parts"] = safe_get_text(replace_div[0]).strip()
        except Exception:
            continue

    # Install difficulty and time (from repair rating container)
    # Structure: div.d-flex with SVG icon (difficulty or duration) + p.bold text
    repair_rating = wait_and_find_element(driver, By.CSS_SELECTOR, "div.pd__repair-rating__container")
    if repair_rating:
        # Find difficulty - look for SVG with #difficulty href
        try:
            difficulty_svg = repair_rating.find_element(
                By.CSS_SELECTOR, "svg use[href*='difficulty']"
            )
            if difficulty_svg:
                # Get the parent div and find the p element
                parent_div = difficulty_svg.find_element(By.XPATH, "./ancestor::div[contains(@class, 'd-flex')][1]")
                p_element = parent_div.find_element(By.TAG_NAME, "p")
                if p_element:
                    part_data["install_difficulty"] = safe_get_text(p_element).strip()
        except Exception:
            pass

        # Find time - look for SVG with #duration href
        try:
            duration_svg = repair_rating.find_element(
                By.CSS_SELECTOR, "svg use[href*='duration']"
            )
            if duration_svg:
                # Get the parent div and find the p element
                parent_div = duration_svg.find_element(By.XPATH, "./ancestor::div[contains(@class, 'd-flex')][1]")
                p_element = parent_div.find_element(By.TAG_NAME, "p")
                if p_element:
                    part_data["install_time"] = safe_get_text(p_element).strip()
        except Exception:
            pass

    # Model Compatibility - scrape the cross-reference table
    model_compatibility = scrape_model_compatibility(driver, part_data.get("ps_number", ""))

    # Extract Q&A, Repair Stories, and Reviews for vector embeddings
    if extract_embeddings:
        ps_number = part_data.get("ps_number", "")
        part_name_clean = part_data.get("part_name", "")

        # Extract Q&A
        raw_qna = extract_qna(driver)
        for qna in raw_qna:
            qna["ps_number"] = ps_number
            qna["embedding_text"] = format_qna_for_embedding(qna, ps_number, part_name_clean)
            qna_data.append(qna)

        # Extract Repair Stories
        raw_stories = extract_repair_stories(driver)
        for story in raw_stories:
            story["ps_number"] = ps_number
            story["embedding_text"] = format_story_for_embedding(story, ps_number, part_name_clean)
            stories_data.append(story)

        # Extract Reviews
        raw_reviews = extract_reviews(driver)
        for review in raw_reviews:
            review["ps_number"] = ps_number
            review["embedding_text"] = format_review_for_embedding(review, ps_number, part_name_clean)
            reviews_data.append(review)

    return part_data, model_compatibility, qna_data, stories_data, reviews_data


def scrape_model_compatibility(driver, part_id):
    """
    Scrape model compatibility data from the cross-reference table.
    Expands the infinite scroll to capture all compatible models.

    Args:
        driver: Selenium WebDriver instance
        part_id: The PS number of the part

    Returns:
        list: List of model compatibility dictionaries
    """
    compatibility_data = []

    try:
        # Expand infinite scroll to load all models, then get all rows
        rows = scroll_infinite_container(
            driver,
            container_selector="div.pd__crossref__list.js-dataContainer",
            row_selector="div.row",
            max_scrolls=50,
            scroll_pause=0.5
        )

        for row in rows:
            try:
                cols = row.find_elements(By.CSS_SELECTOR, "div.col-6, div.col, a.col-6, a.col")
                if len(cols) >= 3:
                    # Brand is in first col
                    brand = safe_get_text(cols[0])
                    # Model number is in second col (could be a link)
                    model_el = cols[1]
                    model_number = safe_get_text(model_el)
                    # Description is in third col
                    description = safe_get_text(cols[2])

                    if model_number:
                        compatibility_data.append({
                            "part_id": part_id,
                            "brand": brand,
                            "model_number": model_number,
                            "description": description.strip(),
                        })
            except Exception:
                continue
    except Exception:
        pass  # Cross-reference may not be available on all pages

    return compatibility_data


def process_category_page(driver, category_url, appliance_type, scraped_ids=None):
    """
    Process a category page and scrape all parts within it.

    Args:
        driver: Selenium WebDriver instance
        category_url: URL of the category page
        appliance_type: Type of appliance
        scraped_ids: Set of ps_number values to skip (for resume capability)

    Returns:
        tuple: (parts_data, model_compatibility, qna_data, stories_data, reviews_data)
    """
    if scraped_ids is None:
        scraped_ids = set()

    parts_data = []
    all_compatibility = []
    all_qna = []
    all_stories = []
    all_reviews = []

    print(f"\nVisiting category: {category_url}")

    if not safe_navigate(driver, category_url):
        print(f"Failed to navigate to {category_url}. Skipping.")
        return parts_data, all_compatibility, all_qna, all_stories, all_reviews

    # Find all part divs
    part_divs = wait_and_find_elements(driver, By.CSS_SELECTOR, "div.nf__part.mb-3")
    if not part_divs:
        print(f"No parts found in {category_url}")
        return parts_data, all_compatibility, all_qna, all_stories, all_reviews

    print(f"Found {len(part_divs)} parts")

    # Collect part info to avoid stale element issues
    part_info = []
    for part_div in part_divs:
        try:
            a_tag = part_div.find_element(By.CLASS_NAME, "nf__part__detail__title")
            span = a_tag.find_element(By.TAG_NAME, "span")
            part_name = safe_get_text(span)
            href = safe_get_attribute(a_tag, "href")

            if href and is_valid_url(href):
                # Extract ps_number from URL to check if already scraped
                # URL format: /PS12345678-...
                ps_from_url = None
                if '/PS' in href:
                    try:
                        ps_start = href.index('/PS') + 1
                        ps_end = href.index('-', ps_start)
                        ps_from_url = href[ps_start:ps_end]
                    except ValueError:
                        pass

                # Skip if already scraped
                if ps_from_url and ps_from_url in scraped_ids:
                    print(f"  [SKIP] Already scraped: {ps_from_url}")
                    continue

                part_info.append((part_name, href))
        except Exception:
            continue

    if not part_info:
        print(f"All parts in this category already scraped or no valid parts found")
        return parts_data, all_compatibility, all_qna, all_stories, all_reviews

    print(f"Processing {len(part_info)} parts (after filtering already-scraped)")

    # Process each part with gentle delays
    for i, (part_name, product_url) in enumerate(part_info, 1):
        print(f"  [{i}/{len(part_info)}] Processing: {part_name}")
        part_data, compatibility, qna, stories, reviews = scrape_part_page(driver, part_name, product_url, appliance_type)
        parts_data.append(part_data)
        all_compatibility.extend(compatibility)
        all_qna.extend(qna)
        all_stories.extend(stories)
        all_reviews.extend(reviews)

        # Gentle delay before navigating back
        gentle_delay(SCRAPER_SETTINGS["delay_between_pages"])

        # Navigate back to category page
        if not safe_navigate(driver, category_url):
            print(f"Failed to return to category. Stopping.")
            break

    return parts_data, all_compatibility, all_qna, all_stories, all_reviews


def get_brand_links(driver, base_url):
    """Get all brand links from the main appliance page."""
    brand_links = []

    if not safe_navigate(driver, base_url):
        print("Failed to navigate to main page")
        return brand_links

    try:
        wait = WebDriverWait(driver, 10)
        wait.until(EC.presence_of_element_located((By.CLASS_NAME, "nf__links")))

        ul_tags = driver.find_elements(By.CLASS_NAME, "nf__links")
        if ul_tags:
            li_tags = ul_tags[0].find_elements(By.TAG_NAME, "li")
            print(f"Found {len(li_tags)} brand links")

            for li_tag in li_tags:
                try:
                    a_tag = li_tag.find_element(By.TAG_NAME, "a")
                    link_url = safe_get_attribute(a_tag, "href")
                    if link_url and is_valid_url(link_url):
                        brand_links.append(link_url)
                except Exception:
                    continue
    except Exception as e:
        print(f"Error finding brand links: {e}")

    return brand_links


def get_related_links(driver, related_pattern):
    """Get related part category links from current page."""
    related_links = []

    try:
        section_titles = driver.find_elements(By.CLASS_NAME, "section-title")
        for title in section_titles:
            title_text = safe_get_text(title)
            if "Related" in title_text and related_pattern in title_text:
                print(f"Found related section: {title_text}")
                related_ul = title.find_element(By.XPATH, "./following::ul[@class='nf__links'][1]")
                if related_ul:
                    li_tags = related_ul.find_elements(By.TAG_NAME, "li")
                    for li_tag in li_tags:
                        try:
                            a_tag = li_tag.find_element(By.TAG_NAME, "a")
                            link_url = safe_get_attribute(a_tag, "href")
                            if link_url and is_valid_url(link_url):
                                related_links.append(link_url)
                        except Exception:
                            continue
    except Exception as e:
        print(f"Error finding related links: {e}")

    return related_links


def process_brand_with_retry(brand_url, appliance_type, related_pattern, max_retries=None,
                             max_categories=None, output_files=None, scraped_ids=None):
    """
    Process a brand page and its related pages with retry mechanism.
    Writes data immediately after each category page for incremental progress saving.

    Args:
        brand_url: URL of the brand page
        appliance_type: Type of appliance
        related_pattern: Pattern to match related sections
        max_retries: Number of retry attempts
        max_categories: Optional limit on category pages (for testing)
        output_files: Dict of output file names for immediate writes
        scraped_ids: Set of ps_number values to skip (for resume)

    Returns:
        dict: Counts of items scraped {parts, compatibility, qna, stories, reviews}
    """
    if max_retries is None:
        max_retries = SCRAPER_SETTINGS["max_retries"]
    if scraped_ids is None:
        scraped_ids = set()

    totals = {"parts": 0, "compatibility": 0, "qna": 0, "stories": 0, "reviews": 0}
    driver = None

    for attempt in range(max_retries):
        try:
            driver = setup_driver()

            if not safe_navigate(driver, brand_url):
                print(f"Failed to navigate to brand {brand_url}. Retrying...")
                driver.quit()
                continue

            # Process brand page
            print(f"Processing brand page: {brand_url}")
            parts, compatibility, qna, stories, reviews = process_category_page(
                driver, brand_url, appliance_type, scraped_ids
            )

            # Write immediately after category page
            if output_files and parts:
                append_parts_data(parts, output_files["parts"])
                append_model_compatibility_data(compatibility, output_files["compat"])
                append_qna_data(qna, output_files["qna"])
                append_repair_stories_data(stories, output_files["stories"])
                append_reviews_data(reviews, output_files["reviews"])
                # Add newly scraped IDs to the set so we don't re-scrape
                for p in parts:
                    if p.get("ps_number"):
                        scraped_ids.add(p["ps_number"])
                print(f"  >> Saved {len(parts)} parts to CSV")

            totals["parts"] += len(parts)
            totals["compatibility"] += len(compatibility)
            totals["qna"] += len(qna)
            totals["stories"] += len(stories)
            totals["reviews"] += len(reviews)

            # Get and process related pages
            related_links = get_related_links(driver, related_pattern)

            # Limit categories if in test mode
            if max_categories:
                related_links = related_links[:max_categories]

            print(f"Found {len(related_links)} related pages")

            for idx, related_url in enumerate(related_links, 1):
                print(f"\nProcessing related page {idx}/{len(related_links)}")
                if not safe_navigate(driver, related_url):
                    continue

                parts, compatibility, qna, stories, reviews = process_category_page(
                    driver, related_url, appliance_type, scraped_ids
                )

                # Write immediately after each category page
                if output_files and parts:
                    append_parts_data(parts, output_files["parts"])
                    append_model_compatibility_data(compatibility, output_files["compat"])
                    append_qna_data(qna, output_files["qna"])
                    append_repair_stories_data(stories, output_files["stories"])
                    append_reviews_data(reviews, output_files["reviews"])
                    # Add newly scraped IDs to the set
                    for p in parts:
                        if p.get("ps_number"):
                            scraped_ids.add(p["ps_number"])
                    print(f"  >> Saved {len(parts)} parts to CSV")

                totals["parts"] += len(parts)
                totals["compatibility"] += len(compatibility)
                totals["qna"] += len(qna)
                totals["stories"] += len(stories)
                totals["reviews"] += len(reviews)

                # Gentle delay between category pages
                gentle_delay(SCRAPER_SETTINGS["delay_between_pages"])

            driver.quit()
            return totals

        except Exception as e:
            print(f"Attempt {attempt + 1} failed for {brand_url}: {e}")
            if driver:
                driver.quit()
            if attempt < max_retries - 1:
                time.sleep(5)
            else:
                return totals

    return totals


def scrape_appliance_parts(appliance_type, max_brands=None, max_categories=None, resume=False):
    """
    Scrape all parts for a specific appliance type.
    Writes data incrementally after each category page.
    Supports parallel processing and resume capability.

    Args:
        appliance_type: Type of appliance (e.g., 'refrigerator', 'dishwasher')
        max_brands: Optional limit on number of brands to scrape (for testing)
        max_categories: Optional limit on category pages per brand (for testing)
        resume: If True, skip parts already in output files

    Returns:
        dict: Counts of scraped items {parts, compatibility, qna, stories, reviews}
    """
    from .utils.file_utils import get_scraped_part_ids

    if appliance_type not in APPLIANCE_CONFIGS:
        raise ValueError(f"Unknown appliance type: {appliance_type}")

    config = APPLIANCE_CONFIGS[appliance_type]
    base_url = config["base_url"]
    related_pattern = config["related_section_pattern"]

    # Use unified output files (all appliance types go into same files)
    output_files = {
        "parts": OUTPUT_FILES["parts"],
        "compat": OUTPUT_FILES["model_compatibility"],
        "qna": OUTPUT_FILES["qna"],
        "stories": OUTPUT_FILES["repair_stories"],
        "reviews": OUTPUT_FILES["reviews"],
    }

    totals = {"parts": 0, "compatibility": 0, "qna": 0, "stories": 0, "reviews": 0}

    # Load already-scraped part IDs if resuming
    scraped_ids = set()
    if resume:
        scraped_ids = get_scraped_part_ids(output_files["parts"])

    print(f"\n{'='*60}")
    print(f"Starting {appliance_type} parts scraping...")
    if resume:
        print(f"RESUME MODE: Skipping {len(scraped_ids)} already-scraped parts")
    if max_brands or max_categories:
        print(f"TEST MODE: max_brands={max_brands}, max_categories={max_categories}")
    print(f"Writing incrementally to output files:")
    print(f"  Parts: {output_files['parts']}")
    print(f"  Compatibility: {output_files['compat']}")
    print(f"  Q&A (embeddings): {output_files['qna']}")
    print(f"  Repair Stories (embeddings): {output_files['stories']}")
    print(f"  Reviews (embeddings): {output_files['reviews']}")
    print(f"{'='*60}")

    # Get all brand links
    driver = setup_driver()
    brand_links = get_brand_links(driver, base_url)
    driver.quit()

    if not brand_links:
        print("No brand links found. Exiting.")
        return totals

    # Limit brands if in test mode
    if max_brands:
        brand_links = brand_links[:max_brands]

    max_workers = SCRAPER_SETTINGS.get("max_workers", 1)

    if max_workers <= 1:
        # Sequential processing
        print(f"\nProcessing {len(brand_links)} brands SEQUENTIALLY")
        totals = _process_brands_sequential(
            brand_links, appliance_type, related_pattern, max_categories,
            output_files, scraped_ids
        )
    else:
        # Parallel processing
        print(f"\nProcessing {len(brand_links)} brands with {max_workers} PARALLEL workers")
        totals = _process_brands_parallel(
            brand_links, appliance_type, related_pattern, max_categories,
            output_files, scraped_ids, max_workers
        )

    print(f"\n{'='*60}")
    print(f"Completed {appliance_type} scraping:")
    print(f"  Parts: {totals['parts']}")
    print(f"  Compatibility records: {totals['compatibility']}")
    print(f"  Q&A entries: {totals['qna']}")
    print(f"  Repair stories: {totals['stories']}")
    print(f"  Reviews: {totals['reviews']}")
    print(f"{'='*60}")

    return totals


def _process_brands_sequential(brand_links, appliance_type, related_pattern,
                                max_categories, output_files, scraped_ids):
    """Process brands sequentially (single worker)."""
    totals = {"parts": 0, "compatibility": 0, "qna": 0, "stories": 0, "reviews": 0}

    for idx, brand_url in enumerate(brand_links, 1):
        print(f"\n{'='*40}")
        print(f"Brand {idx}/{len(brand_links)}: {brand_url}")
        print(f"{'='*40}")

        try:
            brand_totals = process_brand_with_retry(
                brand_url, appliance_type, related_pattern,
                max_categories=max_categories,
                output_files=output_files,
                scraped_ids=scraped_ids
            )

            # Accumulate totals
            for key in totals:
                totals[key] += brand_totals.get(key, 0)

            print(f"\nCompleted {idx}/{len(brand_links)} brands")
            print(f"  This brand: {brand_totals.get('parts', 0)} parts")

            # Gentle delay between brands (unless it's the last one)
            if idx < len(brand_links):
                print(f"\nWaiting before next brand...")
                gentle_delay(SCRAPER_SETTINGS["delay_between_brands"])

        except Exception as e:
            print(f"Error processing {brand_url}: {e}")

    return totals


def _process_brands_parallel(brand_links, appliance_type, related_pattern,
                              max_categories, output_files, scraped_ids, max_workers):
    """Process brands in parallel using ThreadPoolExecutor."""
    totals = {"parts": 0, "compatibility": 0, "qna": 0, "stories": 0, "reviews": 0}
    stagger_delay = SCRAPER_SETTINGS.get("stagger_start_delay", (5, 10))

    def process_brand_worker(args):
        idx, brand_url = args
        # Stagger start times to avoid hitting the site simultaneously
        if idx > 0:
            delay = random.uniform(stagger_delay[0], stagger_delay[1])
            print(f"[Worker {idx}] Staggering start by {delay:.1f}s...")
            time.sleep(delay)

        print(f"\n[Worker {idx}] Starting brand: {brand_url}")
        try:
            brand_totals = process_brand_with_retry(
                brand_url, appliance_type, related_pattern,
                max_categories=max_categories,
                output_files=output_files,
                scraped_ids=scraped_ids  # Note: shared set, but reads are fine
            )
            return (idx, brand_url, brand_totals, None)
        except Exception as e:
            return (idx, brand_url, {"parts": 0, "compatibility": 0, "qna": 0, "stories": 0, "reviews": 0}, str(e))

    completed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all brands with their indices
        futures = {
            executor.submit(process_brand_worker, (idx, url)): url
            for idx, url in enumerate(brand_links)
        }

        for future in as_completed(futures):
            completed += 1
            idx, url, brand_totals, error = future.result()

            if error:
                print(f"\n[{completed}/{len(brand_links)}] ERROR on {url}: {error}")
            else:
                print(f"\n[{completed}/{len(brand_links)}] Completed: {url}")
                print(f"  Parts: {brand_totals.get('parts', 0)}")

            # Accumulate totals (this is thread-safe since we're in the main thread)
            for key in totals:
                totals[key] += brand_totals.get(key, 0)

    return totals
