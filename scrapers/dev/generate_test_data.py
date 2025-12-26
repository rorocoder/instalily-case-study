#!/usr/bin/env python3
"""
Generate test data by scraping a few specific product pages.
Bypasses the main catalog page which may be blocked.
"""

from ..utils import setup_driver
from ..part_scraper import scrape_part_page
from ..utils.file_utils import (
    append_to_csv,
    append_qna_data,
    append_repair_stories_data,
    clear_output_file,
    ensure_output_dir,
)
from ..config import PARTS_SCHEMA, MODEL_COMPATIBILITY_SCHEMA

# Sample product URLs (known working pages)
TEST_URLS = [
    # Refrigerator parts
    ("https://www.partselect.com/PS11752778-Whirlpool-WPW10321304-Refrigerator-Door-Shelf-Bin.htm", "refrigerator"),
    ("https://www.partselect.com/PS11757023-Whirlpool-WPW10715708-Ice-Maker-Assembly.htm", "refrigerator"),
    ("https://www.partselect.com/PS11770016-Whirlpool-W11537061-Refrigerator-Water-Filter.htm", "refrigerator"),
    # Dishwasher parts
    ("https://www.partselect.com/PS11722152-Whirlpool-WPW10503548-Dishwasher-Door-Latch.htm", "dishwasher"),
    ("https://www.partselect.com/PS11752074-Whirlpool-WPW10195417-Dishwasher-Pump-and-Motor-Assembly.htm", "dishwasher"),
]


def generate_test_data():
    """Generate test CSV data from a few sample product pages."""
    ensure_output_dir()

    # Output files
    parts_file = "parts.csv"
    compat_file = "model_compatibility.csv"
    qna_file = "qna.csv"
    stories_file = "repair_stories.csv"

    # Clear files
    clear_output_file(parts_file)
    clear_output_file(compat_file)
    clear_output_file(qna_file)
    clear_output_file(stories_file)

    print("=" * 60)
    print("Generating Test Data")
    print("=" * 60)
    print(f"Scraping {len(TEST_URLS)} product pages...")
    print(f"Output files:")
    print(f"  Parts: data/{parts_file}")
    print(f"  Compatibility: data/{compat_file}")
    print(f"  Q&A: data/{qna_file}")
    print(f"  Repair Stories: data/{stories_file}")
    print("=" * 60)

    driver = setup_driver()

    totals = {"parts": 0, "compatibility": 0, "qna": 0, "stories": 0}

    try:
        for i, (url, appliance_type) in enumerate(TEST_URLS, 1):
            print(f"\n[{i}/{len(TEST_URLS)}] Scraping: {url[:60]}...")

            try:
                part_data, compatibility, qna, stories = scrape_part_page(
                    driver,
                    part_name="",  # Will be extracted
                    product_url=url,
                    appliance_type=appliance_type
                )

                # Save part data
                if part_data.get("ps_number"):
                    append_to_csv([part_data], parts_file, PARTS_SCHEMA)
                    totals["parts"] += 1
                    print(f"  Part: {part_data.get('ps_number')} - {part_data.get('part_name', '')[:40]}")

                # Save compatibility
                if compatibility:
                    append_to_csv(compatibility, compat_file, MODEL_COMPATIBILITY_SCHEMA)
                    totals["compatibility"] += len(compatibility)
                    print(f"  Compatibility: {len(compatibility)} models")

                # Save Q&A
                if qna:
                    append_qna_data(qna, qna_file)
                    totals["qna"] += len(qna)
                    print(f"  Q&A: {len(qna)} entries")

                # Save stories
                if stories:
                    append_repair_stories_data(stories, stories_file)
                    totals["stories"] += len(stories)
                    print(f"  Stories: {len(stories)} entries")

            except Exception as e:
                print(f"  Error: {e}")
                continue

    finally:
        driver.quit()

    print(f"\n{'='*60}")
    print("Test data generation complete!")
    print(f"  Parts: {totals['parts']}")
    print(f"  Compatibility: {totals['compatibility']}")
    print(f"  Q&A: {totals['qna']}")
    print(f"  Repair Stories: {totals['stories']}")
    print(f"{'='*60}")

    return totals


if __name__ == "__main__":
    generate_test_data()
