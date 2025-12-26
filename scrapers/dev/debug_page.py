#!/usr/bin/env python3
"""Debug script to inspect page content."""

import sys
from ..utils import setup_driver, safe_navigate
from selenium.webdriver.common.by import By

TEST_URL = "https://www.partselect.com/PS11752778-Whirlpool-WPW10321304-Refrigerator-Door-Shelf-Bin.htm"


def debug_page():
    headless = "--visible" not in sys.argv

    print(f"Setting up driver (headless={headless})...")
    driver = setup_driver(headless=headless)

    try:
        print(f"\nNavigating to: {TEST_URL}\n")
        if not safe_navigate(driver, TEST_URL, add_delay=False):
            print("Navigation failed!")
            return

        # Check page title
        print(f"Page title: {driver.title}")
        print(f"Current URL: {driver.current_url}")

        # Check for cross-reference table
        crossref_container = driver.find_elements(By.CSS_SELECTOR, "div.pd__crossref__list.js-dataContainer")
        print(f"\nCross-ref containers found: {len(crossref_container)}")
        if crossref_container:
            rows = crossref_container[0].find_elements(By.CSS_SELECTOR, "div.row")
            print(f"  Rows in container: {len(rows)}")

        # Check for Q&A section
        qna_containers = driver.find_elements(By.CSS_SELECTOR, "div.qna__question.js-qnaResponse")
        print(f"\nQ&A containers found: {len(qna_containers)}")

        # Check for Reviews section
        review_containers = driver.find_elements(By.CSS_SELECTOR, "div.pd__cust-review__submitted-review")
        print(f"Review containers found: {len(review_containers)}")

        # Check for repair stories
        story_containers = driver.find_elements(By.CSS_SELECTOR, "div.row.repair-story")
        print(f"Repair story containers found: {len(story_containers)}")

        # Check if there's an access denied message
        page_source = driver.page_source.lower()
        if "access denied" in page_source:
            print("\n*** ACCESS DENIED DETECTED IN PAGE SOURCE ***")

        # Look for common tab elements
        tabs = driver.find_elements(By.CSS_SELECTOR, "[data-tab], .tab, [role='tab']")
        print(f"\nTab elements found: {len(tabs)}")

        # Check for lazy loading elements
        lazy_elements = driver.find_elements(By.CSS_SELECTOR, "[data-load-more], .js-loadMore")
        print(f"Lazy load elements found: {len(lazy_elements)}")

        # Print a snippet of the HTML near the cross-ref section
        try:
            crossref_section = driver.find_element(By.CSS_SELECTOR, "#Crossref")
            print(f"\n#Crossref section found: {crossref_section is not None}")
            print(f"  Outer HTML (first 500 chars): {crossref_section.get_attribute('outerHTML')[:500]}...")
        except Exception as e:
            print(f"\n#Crossref section NOT found: {e}")

        # Check what IDs exist on the page
        all_ids = driver.find_elements(By.CSS_SELECTOR, "[id]")
        id_names = [el.get_attribute("id") for el in all_ids[:30]]
        print(f"\nFirst 30 element IDs: {id_names}")

    finally:
        driver.quit()
        print("\nDriver closed.")


if __name__ == "__main__":
    debug_page()
