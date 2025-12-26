#!/usr/bin/env python3
"""
Repair Help Scraper for PartSelect.com

Scrapes repair troubleshooting data:
- Symptom listings per appliance type (Noisy, Leaking, Will not start, etc.)
- Part inspection instructions for each symptom
- YouTube repair video links
- Difficulty ratings

This is SEPARATE from the parts scraper - focuses on troubleshooting guides.

Usage:
    python -m scrapers.repair_scraper                    # Scrape all appliances
    python -m scrapers.repair_scraper refrigerator       # Scrape refrigerator only
    python -m scrapers.repair_scraper --test             # Test mode (1 symptom per appliance)
"""

import re
import time
from html import unescape
from urllib.parse import urljoin

from selenium.webdriver.common.by import By

from .config import (
    REPAIR_APPLIANCE_CONFIGS,
    REPAIR_SYMPTOMS_SCHEMA,
    REPAIR_PART_INSTRUCTIONS_SCHEMA,
    SCRAPER_SETTINGS,
)
from .utils import setup_driver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException

from .utils.driver_utils import (
    wait_and_find_element,
    wait_and_find_elements,
    safe_get_text,
    safe_get_attribute,
    is_blocked_page,
)
from .utils.file_utils import append_to_csv, clear_output_file, ensure_output_dir


BASE_URL = "https://www.partselect.com"

# Consolidated output files (all appliances in one file)
SYMPTOMS_FILE = "repair_symptoms.csv"
INSTRUCTIONS_FILE = "repair_instructions.csv"


def navigate_to_repair_page(driver, url, max_retries=2):
    """
    Navigate to a repair page (different element checks than parts pages).

    Args:
        driver: Selenium WebDriver instance
        url: URL to navigate to
        max_retries: Number of retry attempts

    Returns:
        bool: True if navigation successful, False otherwise
    """
    for attempt in range(max_retries):
        try:
            driver.get(url)

            # Wait for document ready state
            wait = WebDriverWait(driver, 30)
            wait.until(lambda d: d.execute_script('return document.readyState') == 'complete')

            # Check for blocked/access denied page
            if is_blocked_page(driver):
                print(f"ACCESS DENIED on {url} (attempt {attempt + 1}/{max_retries})")
                if attempt < max_retries - 1:
                    time.sleep(5)
                    continue
                return False

            # Wait for repair page elements
            try:
                # Repair pages have either symptom-list or repair class on main div
                wait.until(EC.presence_of_element_located((
                    By.CSS_SELECTOR,
                    "div.symptom-list, div.repair, div#main"
                )))
                return True
            except TimeoutException:
                # Check if page loaded anyway
                if driver.find_elements(By.CSS_SELECTOR, "div.symptom-list") or \
                   driver.find_elements(By.CSS_SELECTOR, "div.repair") or \
                   driver.find_elements(By.TAG_NAME, "h1"):
                    return True

                if attempt < max_retries - 1:
                    time.sleep(3)
                continue

        except WebDriverException as e:
            print(f"Navigation error (attempt {attempt+1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(5)
            else:
                return False

    return False


def extract_symptoms_from_page(driver, appliance_type):
    """
    Extract all symptoms from an appliance's repair page.

    Args:
        driver: Selenium WebDriver
        appliance_type: Type of appliance (refrigerator, dishwasher, etc.)

    Returns:
        list: List of symptom dicts with title, description, percentage, url
    """
    symptoms = []

    # Find the symptom list container
    symptom_links = wait_and_find_elements(
        driver,
        By.CSS_SELECTOR,
        "div.symptom-list > a.row"
    )

    if not symptom_links:
        print(f"No symptoms found for {appliance_type}")
        return symptoms

    for link in symptom_links:
        try:
            symptom = {"appliance_type": appliance_type}

            # Get URL
            href = safe_get_attribute(link, "href")
            symptom["symptom_url"] = href if href.startswith("http") else urljoin(BASE_URL, href)

            # Get title from h3
            title_elem = link.find_element(By.CSS_SELECTOR, "h3.title-md")
            symptom["symptom"] = safe_get_text(title_elem).strip() if title_elem else ""

            # Get description from p
            desc_elem = link.find_element(By.CSS_SELECTOR, "p")
            symptom["symptom_description"] = safe_get_text(desc_elem).strip() if desc_elem else ""

            # Get percentage from the reported-by span
            try:
                percent_elem = link.find_element(
                    By.CSS_SELECTOR,
                    "div.symptom-list__reported-by span:last-child"
                )
                percent_text = safe_get_text(percent_elem).strip()
                # Extract just the percentage, e.g., "29% of customers" -> "29%"
                match = re.search(r'(\d+%)', percent_text)
                symptom["percentage"] = match.group(1) if match else percent_text
            except Exception:
                symptom["percentage"] = ""

            symptoms.append(symptom)

        except Exception as e:
            print(f"Error extracting symptom: {e}")
            continue

    return symptoms


def extract_symptom_details(driver, symptom_url, appliance_type, symptom_title):
    """
    Extract detailed information from a specific symptom page.

    Args:
        driver: Selenium WebDriver
        symptom_url: URL of the symptom page
        appliance_type: Type of appliance
        symptom_title: Title of the symptom

    Returns:
        tuple: (symptom_details dict, list of part_instructions dicts)
    """
    symptom_details = {
        "video_url": "",
        "difficulty": "",
        "parts": "",
    }
    part_instructions = []

    if not navigate_to_repair_page(driver, symptom_url):
        return symptom_details, part_instructions

    # Extract YouTube video URL
    try:
        video_elem = wait_and_find_element(driver, By.CSS_SELECTOR, "div[data-yt-init]")
        if video_elem:
            video_id = safe_get_attribute(video_elem, "data-yt-init")
            if video_id:
                symptom_details["video_url"] = f"https://www.youtube.com/watch?v={video_id}"
    except Exception:
        pass

    # Extract difficulty rating
    try:
        list_items = wait_and_find_elements(driver, By.CSS_SELECTOR, "ul.list-disc li")
        for li in list_items:
            text = safe_get_text(li).strip()
            if "EASY" in text.upper():
                symptom_details["difficulty"] = "EASY"
                break
            elif "MODERATE" in text.upper():
                symptom_details["difficulty"] = "MODERATE"
                break
            elif "DIFFICULT" in text.upper() or "HARD" in text.upper():
                symptom_details["difficulty"] = "DIFFICULT"
                break
    except Exception:
        pass

    # Extract parts list from the anchor links
    parts_list = []
    try:
        part_anchors = wait_and_find_elements(
            driver,
            By.CSS_SELECTOR,
            "a.js-scrollTrigger.scroll-to"
        )
        for anchor in part_anchors:
            part_name = safe_get_text(anchor).strip()
            if part_name:
                # Decode HTML entities like &amp;
                parts_list.append(unescape(part_name))
    except Exception:
        pass

    symptom_details["parts"] = ", ".join(parts_list)

    # Extract detailed instructions for each part
    try:
        # Find all part sections (h2 with id followed by div.symptom-list__desc)
        part_sections = wait_and_find_elements(
            driver,
            By.CSS_SELECTOR,
            "div.symptom-list h2.section-title[id]"
        )

        for section_header in part_sections:
            try:
                part_info = {
                    "appliance_type": appliance_type,
                    "symptom": symptom_title,
                    "part_type": "",
                    "instructions": "",
                    "part_category_url": "",
                }

                # Get part type name from h2
                part_type = safe_get_text(section_header).strip()
                part_info["part_type"] = unescape(part_type)

                # Get anchor ID from h2 to build the section URL
                anchor_id = safe_get_attribute(section_header, "id")
                if anchor_id:
                    # Build URL like: https://www.partselect.com/Repair/Refrigerator/Not-Making-Ice/#WaterFill
                    part_info["part_category_url"] = f"{symptom_url}#{anchor_id}"

                # Find the following div.symptom-list__desc
                # Using XPath to get the following sibling
                try:
                    desc_div = section_header.find_element(
                        By.XPATH,
                        "following-sibling::div[contains(@class, 'symptom-list__desc')]"
                    )

                    # Get instructions from the first col-lg-6 div
                    instructions_div = desc_div.find_element(
                        By.CSS_SELECTOR,
                        "div.col-lg-6:first-child"
                    )

                    # Extract all text including numbered steps
                    instructions_html = instructions_div.get_attribute("innerHTML")
                    # Convert HTML to clean text
                    instructions_text = _html_to_text(instructions_html)
                    part_info["instructions"] = instructions_text

                except Exception:
                    pass

                if part_info["part_type"]:
                    part_instructions.append(part_info)

            except Exception as e:
                print(f"Error extracting part section: {e}")
                continue

    except Exception as e:
        print(f"Error extracting part instructions: {e}")

    return symptom_details, part_instructions


def _html_to_text(html):
    """Convert HTML to clean text, preserving numbered list structure."""
    import re

    # Remove script and style elements
    html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)

    # Convert list items to numbered format
    def replace_li(match):
        return "\n- " + match.group(1).strip()
    html = re.sub(r'<li[^>]*>(.*?)</li>', replace_li, html, flags=re.DOTALL | re.IGNORECASE)

    # Convert paragraphs and breaks to newlines
    html = re.sub(r'<br\s*/?>', '\n', html, flags=re.IGNORECASE)
    html = re.sub(r'</p>', '\n', html, flags=re.IGNORECASE)
    html = re.sub(r'</div>', '\n', html, flags=re.IGNORECASE)

    # Remove all remaining HTML tags
    html = re.sub(r'<[^>]+>', '', html)

    # Decode HTML entities
    html = unescape(html)

    # Clean up whitespace
    html = re.sub(r'\n\s*\n', '\n\n', html)
    html = re.sub(r'[ \t]+', ' ', html)

    return html.strip()


def scrape_appliance_repairs(appliance_type, max_symptoms=None, clear_files=True):
    """
    Scrape all repair symptom data for a specific appliance type.

    Args:
        appliance_type: Type of appliance (refrigerator, dishwasher, etc.)
        max_symptoms: Optional limit on symptoms to scrape (for testing)
        clear_files: Whether to clear output files before scraping (default: True)

    Returns:
        dict: Counts of scraped items {symptoms, part_instructions}
    """
    if appliance_type not in REPAIR_APPLIANCE_CONFIGS:
        raise ValueError(f"Unknown appliance type: {appliance_type}")

    config = REPAIR_APPLIANCE_CONFIGS[appliance_type]
    repair_url = config["repair_url"]

    # Clear output files only if requested (first appliance in batch)
    if clear_files:
        clear_output_file(SYMPTOMS_FILE)
        clear_output_file(INSTRUCTIONS_FILE)

    totals = {"symptoms": 0, "part_instructions": 0}

    print(f"\n{'='*60}")
    print(f"Starting {appliance_type} repair scraping...")
    print(f"{'='*60}")

    driver = setup_driver()

    try:
        # Navigate to appliance repair page
        if not navigate_to_repair_page(driver, repair_url):
            print(f"Failed to navigate to {repair_url}")
            return totals

        # Extract symptoms list
        symptoms = extract_symptoms_from_page(driver, appliance_type)
        print(f"Found {len(symptoms)} symptoms")

        if max_symptoms:
            symptoms = symptoms[:max_symptoms]
            print(f"Test mode: limiting to {max_symptoms} symptoms")

        # Process each symptom
        for i, symptom in enumerate(symptoms, 1):
            print(f"\n[{i}/{len(symptoms)}] Processing: {symptom['symptom']}")

            # Get detailed info from symptom page
            details, instructions = extract_symptom_details(
                driver,
                symptom["symptom_url"],
                appliance_type,
                symptom["symptom"]
            )

            # Merge details into symptom
            symptom.update(details)

            # Save symptom to CSV
            append_to_csv([symptom], SYMPTOMS_FILE, REPAIR_SYMPTOMS_SCHEMA)
            totals["symptoms"] += 1

            # Save part instructions
            if instructions:
                append_to_csv(instructions, INSTRUCTIONS_FILE, REPAIR_PART_INSTRUCTIONS_SCHEMA)
                totals["part_instructions"] += len(instructions)
                print(f"  Found {len(instructions)} parts to check")

            time.sleep(SCRAPER_SETTINGS.get("delay_between_pages", 0))

    finally:
        driver.quit()

    print(f"\n{'='*60}")
    print(f"Completed {appliance_type} repair scraping:")
    print(f"  Symptoms: {totals['symptoms']}")
    print(f"  Part Instructions: {totals['part_instructions']}")
    print(f"{'='*60}")

    return totals


def scrape_all_repairs(appliances=None, max_symptoms=None):
    """
    Scrape repair data for multiple appliance types into consolidated files.

    Args:
        appliances: List of appliance types to scrape (default: all)
        max_symptoms: Optional limit on symptoms per appliance (for testing)

    Returns:
        dict: Total counts of scraped items
    """
    if appliances is None:
        appliances = list(REPAIR_APPLIANCE_CONFIGS.keys())

    # Clear files once at start
    clear_output_file(SYMPTOMS_FILE)
    clear_output_file(INSTRUCTIONS_FILE)

    print(f"\nOutput files (consolidated):")
    print(f"  Symptoms: data/{SYMPTOMS_FILE}")
    print(f"  Instructions: data/{INSTRUCTIONS_FILE}")

    totals = {"symptoms": 0, "part_instructions": 0}

    for appliance_type in appliances:
        if appliance_type not in REPAIR_APPLIANCE_CONFIGS:
            print(f"Unknown appliance type: {appliance_type}. Skipping.")
            continue

        # Don't clear files - we already did that above
        result = scrape_appliance_repairs(appliance_type, max_symptoms=max_symptoms, clear_files=False)
        totals["symptoms"] += result["symptoms"]
        totals["part_instructions"] += result["part_instructions"]

    return totals


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Scrape repair troubleshooting data from PartSelect"
    )

    parser.add_argument(
        "appliances",
        nargs="*",
        default=None,
        help="Appliance types to scrape (default: all)"
    )

    parser.add_argument(
        "--test",
        action="store_true",
        help="Test mode: scrape only 1 symptom per appliance"
    )

    parser.add_argument(
        "--max-symptoms",
        type=int,
        default=None,
        help="Limit number of symptoms to scrape per appliance"
    )

    args = parser.parse_args()

    appliances = args.appliances if args.appliances else None
    max_symptoms = args.max_symptoms

    if args.test:
        max_symptoms = max_symptoms or 1

    ensure_output_dir()

    print(f"\n{'='*60}")
    print("PartSelect Repair Scraper")
    print(f"{'='*60}")
    if appliances:
        print(f"Appliances to scrape: {', '.join(appliances)}")
    else:
        print(f"Appliances to scrape: all ({', '.join(REPAIR_APPLIANCE_CONFIGS.keys())})")
    if max_symptoms:
        print(f"Max symptoms per appliance: {max_symptoms}")

    totals = scrape_all_repairs(appliances, max_symptoms)

    print(f"\n{'='*60}")
    print("Scraping complete!")
    print(f"Total symptoms: {totals['symptoms']}")
    print(f"Total part instructions: {totals['part_instructions']}")
    print(f"{'='*60}")
