#!/usr/bin/env python3
"""
Test script for the repair help scraper.
Tests extraction of symptoms and part instructions from a single appliance.
"""

from ..repair_scraper import (
    extract_symptoms_from_page,
    extract_symptom_details,
    scrape_appliance_repairs,
    navigate_to_repair_page,
)
from ..utils import setup_driver
from ..config import REPAIR_APPLIANCE_CONFIGS


def test_symptom_extraction():
    """Test extracting symptoms from an appliance repair page."""
    print("=" * 60)
    print("TEST: Symptom Extraction")
    print("=" * 60)

    driver = setup_driver()

    try:
        # Test with refrigerator
        appliance = "refrigerator"
        config = REPAIR_APPLIANCE_CONFIGS[appliance]

        print(f"\nNavigating to: {config['repair_url']}")
        if not navigate_to_repair_page(driver, config['repair_url']):
            print("Failed to navigate")
            return

        symptoms = extract_symptoms_from_page(driver, appliance)
        print(f"\nFound {len(symptoms)} symptoms:\n")

        for i, symptom in enumerate(symptoms, 1):
            print(f"{i}. {symptom['symptom']}")
            print(f"   Percentage: {symptom['percentage']}")
            print(f"   Description: {symptom['symptom_description'][:80]}...")
            print(f"   URL: {symptom['symptom_url']}")
            print()

    finally:
        driver.quit()


def test_symptom_details():
    """Test extracting detailed info from a specific symptom page."""
    print("=" * 60)
    print("TEST: Symptom Details Extraction")
    print("=" * 60)

    driver = setup_driver()

    try:
        # Test with a specific symptom page
        symptom_url = "https://www.partselect.com/Repair/Refrigerator/Not-Making-Ice/"
        appliance = "refrigerator"
        symptom_title = "Ice maker not making ice"

        print(f"\nFetching details from: {symptom_url}\n")

        details, instructions = extract_symptom_details(
            driver, symptom_url, appliance, symptom_title
        )

        print("SYMPTOM DETAILS:")
        print(f"  Video URL: {details['video_url']}")
        print(f"  Difficulty: {details['difficulty']}")
        print(f"  Parts to check: {details['parts']}")

        print(f"\nPART INSTRUCTIONS: {len(instructions)} parts")
        for i, part in enumerate(instructions, 1):
            print(f"\n  --- Part {i}: {part['part_type']} ---")
            print(f"  Category URL: {part['part_category_url']}")
            print(f"  Instructions (first 200 chars):")
            print(f"    {part['instructions'][:200]}...")
            print(f"\n  Embedding Text (first 300 chars):")
            print(f"    {part['embedding_text'][:300]}...")

    finally:
        driver.quit()


def test_full_scrape():
    """Test a full scrape with one symptom."""
    print("=" * 60)
    print("TEST: Full Scrape (1 symptom)")
    print("=" * 60)

    result = scrape_appliance_repairs("refrigerator", max_symptoms=1)

    print(f"\nResults:")
    print(f"  Symptoms scraped: {result['symptoms']}")
    print(f"  Part instructions: {result['part_instructions']}")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        test_name = sys.argv[1]
        if test_name == "symptoms":
            test_symptom_extraction()
        elif test_name == "details":
            test_symptom_details()
        elif test_name == "full":
            test_full_scrape()
        else:
            print(f"Unknown test: {test_name}")
            print("Available tests: symptoms, details, full")
    else:
        # Run the quick details test by default
        test_symptom_details()
