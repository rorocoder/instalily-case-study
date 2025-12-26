#!/usr/bin/env python3
"""
Entry point for running the PartSelect scraper.

Usage:
    python -m scrapers.run_scraper                    # Scrape all appliances
    python -m scrapers.run_scraper refrigerator       # Scrape refrigerator only
    python -m scrapers.run_scraper --resume           # Resume from existing data
    python -m scrapers.run_scraper --resume refrigerator  # Resume refrigerator only
"""

import sys
import argparse
from datetime import datetime

from .config import APPLIANCE_CONFIGS, OUTPUT_DIR, OUTPUT_FILES
from .part_scraper import scrape_appliance_parts
from .utils.file_utils import ensure_output_dir, clear_output_file


def main():
    parser = argparse.ArgumentParser(
        description="Scrape parts data from PartSelect",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python -m scrapers.run_scraper                     # Scrape all appliances
    python -m scrapers.run_scraper refrigerator        # Scrape refrigerator only
    python -m scrapers.run_scraper dishwasher          # Scrape dishwasher only
    python -m scrapers.run_scraper refrigerator dishwasher  # Scrape both
        """
    )

    parser.add_argument(
        "appliances",
        nargs="*",
        choices=list(APPLIANCE_CONFIGS.keys()) + [[]],
        default=list(APPLIANCE_CONFIGS.keys()),
        help="Appliance types to scrape (default: all)"
    )

    parser.add_argument(
        "--output-dir",
        default=OUTPUT_DIR,
        help=f"Output directory for CSV files (default: {OUTPUT_DIR})"
    )

    parser.add_argument(
        "--test",
        action="store_true",
        help="Run in test mode: 2 brands, 5 categories each"
    )

    parser.add_argument(
        "--max-brands",
        type=int,
        default=None,
        help="Limit number of brands to scrape"
    )

    parser.add_argument(
        "--max-categories",
        type=int,
        default=None,
        help="Limit number of category pages per brand"
    )

    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from existing data, skipping already-scraped parts"
    )

    args = parser.parse_args()

    # Handle empty list case
    appliances = args.appliances if args.appliances else list(APPLIANCE_CONFIGS.keys())

    # Set test mode limits
    max_brands = args.max_brands
    max_categories = args.max_categories
    if args.test:
        max_brands = max_brands or 2
        max_categories = max_categories or 5

    # Ensure output directory exists
    ensure_output_dir()

    # Clear unified output files (skip if resuming)
    if args.resume:
        print("\nRESUME MODE: Keeping existing output files")
    else:
        print("\nClearing output files for fresh start...")
        for filename in OUTPUT_FILES.values():
            clear_output_file(filename)

    print(f"\n{'='*60}")
    print(f"PartSelect Scraper - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")
    print(f"Appliances to scrape: {', '.join(appliances)}")
    print(f"Output directory: {args.output_dir}")
    print(f"Output files: {', '.join(OUTPUT_FILES.values())}")
    if args.resume:
        print("Mode: RESUME (skipping already-scraped parts)")
    if max_brands or max_categories:
        print(f"TEST MODE: max_brands={max_brands}, max_categories={max_categories}")

    totals = {"parts": 0, "compatibility": 0, "qna": 0, "stories": 0}

    for appliance_type in appliances:
        if appliance_type not in APPLIANCE_CONFIGS:
            print(f"Unknown appliance type: {appliance_type}. Skipping.")
            continue

        # Scrape parts for this appliance type (data saved incrementally)
        result = scrape_appliance_parts(
            appliance_type,
            max_brands=max_brands,
            max_categories=max_categories,
            resume=args.resume
        )

        for key in totals:
            totals[key] += result.get(key, 0)

    print(f"\n{'='*60}")
    print(f"Scraping complete!")
    print(f"Total parts: {totals['parts']}")
    print(f"Total compatibility records: {totals['compatibility']}")
    print(f"Total Q&A entries: {totals['qna']}")
    print(f"Total repair stories: {totals['stories']}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
