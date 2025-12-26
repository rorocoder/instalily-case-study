#!/usr/bin/env python3
"""
Diagnostic script to check what selectors work on the page.
Helps identify if the page structure changed.
"""

import sys
import time
from selenium.webdriver.common.by import By
from ..utils import setup_driver, safe_navigate

TEST_URL = "https://www.partselect.com/PS11752778-Whirlpool-WPW10321304-Refrigerator-Door-Shelf-Bin.htm"


def diagnose():
    headless = "--visible" not in sys.argv
    print(f"Setting up driver (headless={headless})...")
    driver = setup_driver(headless=headless)

    try:
        print(f"\nNavigating to: {TEST_URL}\n")
        if not safe_navigate(driver, TEST_URL, add_delay=False):
            print("Navigation failed!")
            return

        # Wait a bit for dynamic content
        time.sleep(3)

        print("=" * 60)
        print("SELECTOR DIAGNOSIS")
        print("=" * 60)

        # Test each selector
        selectors = {
            "Part Name (h1)": "h1[itemprop='name']",
            "PS Number": "span[itemprop='productID']",
            "MPN": "span[itemprop='mpn']",
            "Brand (nested)": "span[itemprop='brand'] span[itemprop='name']",
            "Brand (direct)": "span[itemprop='brand']",
            "Price": "span.price.pd__price",
            "Availability": "span[itemprop='availability']",
            "Description": "div[itemprop='description']",
            "Cross-ref container": "div.pd__crossref__list.js-dataContainer",
            "Cross-ref rows": "div.pd__crossref__list.js-dataContainer div.row",
            "Q&A containers": "div.qna__question.js-qnaResponse",
            "Review containers": "div.pd__cust-review__submitted-review",
            "Repair stories": "div.row.repair-story",
            "Rating meta": "meta[itemprop='ratingValue']",
            "Review count meta": "meta[itemprop='reviewCount']",
        }

        for name, selector in selectors.items():
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
            count = len(elements)
            if count > 0:
                first_text = elements[0].text[:50] if elements[0].text else "(no text)"
                first_attr = elements[0].get_attribute("content") or elements[0].get_attribute("innerHTML")[:50] if count > 0 else ""
                print(f"  {name}: {count} found")
                print(f"    Text: {first_text}")
                if first_attr and first_attr != first_text:
                    print(f"    Attr: {first_attr[:50]}")
            else:
                print(f"  {name}: NOT FOUND")
            print()

        # Try some alternative selectors
        print("=" * 60)
        print("TRYING ALTERNATIVE SELECTORS")
        print("=" * 60)

        alt_selectors = {
            "Any itemprop=productID": "[itemprop='productID']",
            "Any itemprop=mpn": "[itemprop='mpn']",
            "Any itemprop=brand": "[itemprop='brand']",
            "Any data-ps-partid": "[data-ps-partid]",
            "Part number from URL class": ".pd__partId",
            "MPN alternative": ".pd__mpn",
            "Brand class": ".pd__brand",
            "Schema Product": "[itemtype*='Product']",
            "Q&A Section by ID": "#Questions",
            "Reviews Section by ID": "#Reviews",
            "Cross-ref by ID": "#Crossref",
        }

        for name, selector in alt_selectors.items():
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
            count = len(elements)
            if count > 0:
                first_text = elements[0].text[:80] if elements[0].text else "(no text)"
                print(f"  {name}: {count} found - {first_text}")
            else:
                print(f"  {name}: NOT FOUND")

        # Print page source snippet around product info
        print("\n" + "=" * 60)
        print("LOOKING FOR PRODUCT ID IN PAGE SOURCE")
        print("=" * 60)

        source = driver.page_source
        # Look for PS number
        import re
        ps_matches = re.findall(r'PS\d{8,}', source)
        if ps_matches:
            print(f"  Found PS numbers in source: {set(ps_matches)}")

        # Look for WPW part number
        wpw_matches = re.findall(r'WPW\d+', source)
        if wpw_matches:
            print(f"  Found WPW numbers in source: {set(wpw_matches)}")

        # Save page source for inspection
        with open("data/debug_page_source.html", "w") as f:
            f.write(source)
        print("\n  Page source saved to data/debug_page_source.html")

    finally:
        driver.quit()
        print("\nDriver closed.")


if __name__ == "__main__":
    diagnose()
