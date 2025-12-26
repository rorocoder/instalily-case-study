"""
Reviews Extractor - Extracts customer reviews from part pages.

Pagination Strategy:
Only extracts first page (~10 reviews), sorted by default ordering.
This captures representative reviews without pagination overhead.
"""

import re
import hashlib
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException


def extract_reviews(driver):
    """
    Extract customer reviews from the current product page.

    Args:
        driver: Selenium WebDriver instance (already on the part page)

    Returns:
        list[dict]: List of review dictionaries with fields:
            - review_id: str (hash of author+date+title for uniqueness)
            - rating: int (1-5 stars)
            - title: str
            - content: str
            - author: str
            - date: str
            - verified_purchase: bool
    """
    reviews_list = []

    try:
        review_containers = driver.find_elements(
            By.CSS_SELECTOR, "div.pd__cust-review__submitted-review"
        )

        for container in review_containers:
            try:
                review = {}

                # Rating - extract from star width percentage
                # width: 100% = 5 stars, 80% = 4 stars, etc.
                try:
                    star_el = container.find_element(
                        By.CSS_SELECTOR, "div.rating__stars__upper"
                    )
                    style = star_el.get_attribute("style") or ""
                    match = re.search(r'width:\s*(\d+)%', style)
                    if match:
                        percentage = int(match.group(1))
                        review["rating"] = round(percentage / 20)  # 100% -> 5, 80% -> 4
                    else:
                        review["rating"] = 0
                except NoSuchElementException:
                    review["rating"] = 0

                # Author and Date - from header
                try:
                    header_el = container.find_element(
                        By.CSS_SELECTOR, "div.pd__cust-review__submitted-review__header"
                    )
                    header_text = header_el.text.strip()

                    # Parse "Author Name - Date" format
                    author_el = header_el.find_element(By.CSS_SELECTOR, "span.bold")
                    review["author"] = author_el.text.strip()

                    # Date is after the dash
                    if " - " in header_text:
                        review["date"] = header_text.split(" - ", 1)[1].strip()
                    else:
                        review["date"] = ""
                except NoSuchElementException:
                    review["author"] = ""
                    review["date"] = ""

                # Verified Purchase badge
                try:
                    container.find_element(By.XPATH, ".//*[contains(text(), 'Verified Purchase')]")
                    review["verified_purchase"] = True
                except NoSuchElementException:
                    review["verified_purchase"] = False

                # Title - first bold div after header (not inside header)
                try:
                    # Get all bold divs, skip the header one
                    bold_divs = container.find_elements(By.CSS_SELECTOR, ":scope > div.bold")
                    if bold_divs:
                        review["title"] = bold_divs[0].text.strip()
                    else:
                        review["title"] = ""
                except NoSuchElementException:
                    review["title"] = ""

                # Content - from js-searchKeys div
                try:
                    content_el = container.find_element(By.CSS_SELECTOR, "div.js-searchKeys")
                    review["content"] = content_el.text.strip()
                except NoSuchElementException:
                    review["content"] = ""

                # Generate review_id as hash of author+date+title
                id_string = f"{review.get('author', '')}{review.get('date', '')}{review.get('title', '')}"
                review["review_id"] = hashlib.md5(id_string.encode()).hexdigest()[:16]

                # Only add if we have meaningful content
                if review.get("title") or review.get("content"):
                    reviews_list.append(review)

            except Exception as e:
                print(f"Error extracting review: {e}")
                continue

    except Exception as e:
        print(f"Error finding review containers: {e}")

    return reviews_list


def format_for_embedding(review, ps_number, part_name=None):
    """
    Format a review into text suitable for vector embedding.

    Args:
        review: Review dict from extract_reviews
        ps_number: PartSelect part number (e.g., "PS11722135")
        part_name: Optional part name

    Returns:
        str: Formatted text for embedding
    """
    parts = [f"Part ID: {ps_number}"]

    if part_name:
        parts.append(f"Part: {part_name}")

    if review.get("rating"):
        parts.append(f"Rating: {review['rating']}/5 stars")

    if review.get("title"):
        parts.append(f"Review Title: {review['title']}")

    if review.get("content"):
        parts.append(f"Review: {review['content']}")

    if review.get("verified_purchase"):
        parts.append("Verified Purchase: Yes")

    return "\n".join(parts)
