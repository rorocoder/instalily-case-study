"""
Live scraping tools for parts not in database.

This module provides real-time scraping capability as a fallback when parts
are not found in the database. It uses the existing scraper infrastructure
to fetch data directly from PartSelect.
"""
import json
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException

from langchain_anthropic import ChatAnthropic
from backend.config import get_settings
from backend.agent_v2.tools.registry import registry
from scrapers.utils import setup_driver
from scrapers.part_scraper import scrape_part_page


def classify_appliance_type_with_llm(part_data: dict) -> str:
    """
    Use LLM to classify what type of appliance a part is for.

    Analyzes part name, description, reviews, Q&A, and compatible models
    to intelligently determine the appliance type.

    Args:
        part_data: Dictionary with scraped part data

    Returns:
        Appliance type string (e.g., "refrigerator", "dishwasher", "chainsaw", "microwave")
    """
    settings = get_settings()

    # Gather all available text data
    part_name = part_data.get("part_name", "")
    description = part_data.get("part_description", "")
    manufacturer = part_data.get("part_manufacturer", "")

    # Get some sample reviews/Q&A if available
    reviews = part_data.get("_reviews_data", [])[:3]  # First 3 reviews
    qna = part_data.get("_qna_data", [])[:3]  # First 3 Q&A

    # Get compatible model descriptions
    models = part_data.get("_compatible_models", [])[:5]  # First 5 models
    model_descriptions = [m.get("description", "") for m in models if m.get("description")]

    # Build context for LLM
    context_parts = [f"Part Name: {part_name}"]

    if manufacturer:
        context_parts.append(f"Manufacturer: {manufacturer}")

    if description:
        context_parts.append(f"Description: {description[:500]}")

    if model_descriptions:
        context_parts.append(f"Compatible Models: {', '.join(model_descriptions[:3])}")

    if reviews:
        review_texts = [r.get("review_text", "")[:200] for r in reviews if r.get("review_text")]
        if review_texts:
            context_parts.append(f"Sample Reviews: {' | '.join(review_texts)}")

    if qna:
        qa_texts = [f"Q: {q.get('question', '')} A: {q.get('answer', '')}"[:150] for q in qna]
        if qa_texts:
            context_parts.append(f"Sample Q&A: {' | '.join(qa_texts)}")

    context = "\n".join(context_parts)

    # Ask LLM to classify
    prompt = f"""Based on the following part information, what type of appliance is this part for?

{context}

Respond with ONLY the appliance type name in lowercase (e.g., "refrigerator", "dishwasher", "microwave", "oven", "washing machine", "dryer", "chainsaw", "lawn mower", "vacuum cleaner", etc.).

If you cannot determine the appliance type, respond with "unknown".

Appliance type:"""

    try:
        llm = ChatAnthropic(
            model=settings.HAIKU_MODEL,
            api_key=settings.ANTHROPIC_API_KEY,
            max_tokens=20,
        )

        response = llm.invoke(prompt)
        appliance_type = response.content.strip().lower()

        print(f"  [SCRAPE_LIVE] LLM classified appliance type: {appliance_type}")
        return appliance_type

    except Exception as e:
        print(f"  [SCRAPE_LIVE] Error classifying appliance type: {e}")
        return "unknown"


@registry.register(category="scrape")
def scrape_part_live(ps_number: str) -> dict:
    """
    Live scrape a part from PartSelect when not in database.

    WARNING: This is a SLOW operation (5-30 seconds). Only use when
    the part is not available in the database.

    Args:
        ps_number: The PS number to scrape (e.g., "PS11752778")

    Returns:
        Dictionary with part data in same format as get_part(), or error dict.
        On success, includes additional metadata:
        - _scraped_live: True (indicates data was scraped, not from DB)
        - _qna_count: Number of Q&A entries found
        - _stories_count: Number of repair stories found
        - _reviews_count: Number of reviews found
        - _model_compatibility_count: Number of compatible models

    Example:
        >>> scrape_part_live("PS11752778")
        {
            "ps_number": "PS11752778",
            "part_name": "Ice Maker Assembly",
            "part_price": "129.99",
            "_scraped_live": True,
            ...
        }
    """
    # 1. Validate PS number format
    if not ps_number or not isinstance(ps_number, str):
        return {
            "error": f"Invalid input: {ps_number}",
            "ps_number": ps_number
        }

    ps_number_clean = ps_number.strip()
    if not ps_number_clean.startswith("PS"):
        return {
            "error": f"Invalid PS number format: {ps_number}. Must start with 'PS'",
            "ps_number": ps_number
        }

    driver = None
    try:
        print(f"  [SCRAPE_LIVE] Starting live scrape for {ps_number_clean}...")

        # 2. Setup headless Chrome with optimizations
        driver = setup_driver(
            headless=True,
            use_proxy=False,
            rotate_user_agent=True,
            disable_images=True  # Faster page loads
        )

        print(f"  [SCRAPE_LIVE] Navigating to PartSelect homepage...")

        # 3. Navigate to homepage
        driver.get("https://www.partselect.com/")

        # 4. Find search input (class: js-headerNavSearch)
        print(f"  [SCRAPE_LIVE] Finding search input...")
        search_input = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input.js-headerNavSearch"))
        )

        # 5. Enter PS number and submit
        print(f"  [SCRAPE_LIVE] Searching for {ps_number_clean}...")
        search_input.clear()
        search_input.send_keys(ps_number_clean)
        search_input.send_keys(Keys.RETURN)

        # 6. Wait for page load and get final URL
        # The search should redirect to the part page
        print(f"  [SCRAPE_LIVE] Waiting for redirect to part page...")
        try:
            WebDriverWait(driver, 15).until(
                EC.url_contains("partselect.com/PS")
            )
        except TimeoutException:
            # Part might not exist or search failed
            print(f"  [SCRAPE_LIVE] No redirect to part page - part may not exist")
            return {
                "error": f"Part {ps_number_clean} not found on PartSelect. The search did not return a valid part page.",
                "ps_number": ps_number_clean
            }

        final_url = driver.current_url
        print(f"  [SCRAPE_LIVE] Redirected to: {final_url}")

        # 7. Call existing scraper with final URL
        print(f"  [SCRAPE_LIVE] Extracting part data...")
        part_data, model_compat, qna, stories, reviews = scrape_part_page(
            driver=driver,
            part_name="",  # Will be extracted from page
            product_url=final_url,
            appliance_type="",  # Will be detected from page
            extract_embeddings=True  # Get Q&A, stories, reviews
        )

        # 8. Validate scrape success
        if not part_data.get("ps_number"):
            print(f"  [SCRAPE_LIVE] Failed to extract part data from page")
            return {
                "error": f"Failed to scrape {ps_number_clean}. Could not extract part data from page.",
                "ps_number": ps_number_clean
            }

        # 9. Verify we got the correct part
        scraped_ps = part_data.get("ps_number", "")
        if scraped_ps != ps_number_clean:
            print(f"  [SCRAPE_LIVE] WARNING: Scraped wrong part. Expected {ps_number_clean}, got {scraped_ps}")
            # Still return the data, but include a warning
            part_data["_scrape_warning"] = f"Requested {ps_number_clean} but got {scraped_ps}"

        # 10. Add metadata and include all scraped data
        part_data["_scraped_live"] = True
        part_data["_model_compatibility_count"] = len(model_compat)
        part_data["_qna_count"] = len(qna)
        part_data["_stories_count"] = len(stories)
        part_data["_reviews_count"] = len(reviews)

        # Include the actual data (not just counts) so agent can use it
        part_data["_compatible_models"] = model_compat
        part_data["_qna_data"] = qna
        part_data["_repair_stories"] = stories
        part_data["_reviews_data"] = reviews

        print(f"  [SCRAPE_LIVE] ✓ Success! Scraped: {part_data.get('part_name', 'Unknown')}")
        print(f"  [SCRAPE_LIVE]   - {len(model_compat)} compatible models")
        print(f"  [SCRAPE_LIVE]   - {len(qna)} Q&A entries")
        print(f"  [SCRAPE_LIVE]   - {len(stories)} repair stories")
        print(f"  [SCRAPE_LIVE]   - {len(reviews)} reviews")

        # If appliance_type is empty/unknown, use LLM to classify it
        current_appliance_type = part_data.get("appliance_type", "")
        if not current_appliance_type or current_appliance_type.strip() == "":
            print(f"  [SCRAPE_LIVE] Appliance type unknown, using LLM to classify...")
            classified_type = classify_appliance_type_with_llm(part_data)
            part_data["appliance_type"] = classified_type
            part_data["_appliance_type_source"] = "llm_classified"
        else:
            part_data["_appliance_type_source"] = "scraped"

        return part_data

    except TimeoutException as e:
        print(f"  [SCRAPE_LIVE] ✗ Timeout: {str(e)}")
        return {
            "error": f"Timeout while scraping {ps_number_clean}. PartSelect may be slow or the part doesn't exist.",
            "ps_number": ps_number_clean
        }

    except WebDriverException as e:
        print(f"  [SCRAPE_LIVE] ✗ WebDriver error: {str(e)}")
        return {
            "error": f"Browser error while scraping {ps_number_clean}: {str(e)[:100]}",
            "ps_number": ps_number_clean
        }

    except Exception as e:
        print(f"  [SCRAPE_LIVE] ✗ Unexpected error: {str(e)}")
        return {
            "error": f"Failed to scrape {ps_number_clean}: {str(e)[:100]}",
            "ps_number": ps_number_clean
        }

    finally:
        # CRITICAL: Always cleanup WebDriver to prevent memory leaks
        if driver:
            try:
                driver.quit()
                print(f"  [SCRAPE_LIVE] WebDriver cleaned up")
            except Exception as e:
                print(f"  [SCRAPE_LIVE] Warning: Error during cleanup: {e}")
