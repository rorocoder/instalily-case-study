#!/usr/bin/env python3
"""
Test script to scrape a single part page and verify all extractors.
Tests: part data, model compatibility, Q&A, repair stories, and reviews.
"""

import sys
from ..utils import setup_driver
from ..part_scraper import scrape_part_page

TEST_URL = "https://www.partselect.com/PS11752778-Whirlpool-WPW10321304-Refrigerator-Door-Shelf-Bin.htm"


def test_single_page():
    """Test scraping a single part page with all extractors."""
    headless = "--visible" not in sys.argv

    print("Setting up driver...")
    driver = setup_driver(headless=headless)

    try:
        print(f"\nScraping: {TEST_URL}\n")
        part_data, model_compatibility, qna_data, stories_data, reviews_data = scrape_part_page(
            driver,
            part_name="Test Part",
            product_url=TEST_URL,
            appliance_type="refrigerator"
        )

        print("=" * 60)
        print("PART DATA:")
        print("=" * 60)
        for key, value in part_data.items():
            if key in ("part_url", "install_video_url"):
                print(f"  {key}: {value}")
            else:
                display_val = str(value)[:80] + "..." if len(str(value)) > 80 else value
                print(f"  {key}: {display_val}")

        print("\n" + "=" * 60)
        print(f"MODEL COMPATIBILITY: {len(model_compatibility)} records")
        print("=" * 60)
        for i, compat in enumerate(model_compatibility[:3]):
            print(f"  {i+1}. {compat['brand']} - {compat['model_number']}: {compat['description'][:50]}...")
        if len(model_compatibility) > 3:
            print(f"  ... and {len(model_compatibility) - 3} more")

        print("\n" + "=" * 60)
        print(f"Q&A (for embeddings): {len(qna_data)} entries")
        print("=" * 60)
        for i, qna in enumerate(qna_data[:2]):
            print(f"  --- Q&A {i+1} ---")
            print(f"  ps_number: {qna.get('ps_number')}")
            q = qna.get('question', '')[:60]
            print(f"  Q: {q}..." if len(qna.get('question', '')) > 60 else f"  Q: {q}")
            a = qna.get('answer', '')[:60]
            print(f"  A: {a}..." if len(qna.get('answer', '')) > 60 else f"  A: {a}")
        if len(qna_data) > 2:
            print(f"  ... and {len(qna_data) - 2} more")

        print("\n" + "=" * 60)
        print(f"REPAIR STORIES (for embeddings): {len(stories_data)} stories")
        print("=" * 60)
        for i, story in enumerate(stories_data[:2]):
            print(f"  --- Story {i+1} ---")
            print(f"  ps_number: {story.get('ps_number')}")
            print(f"  title: {story.get('title', '')[:60]}...")
            print(f"  difficulty: {story.get('difficulty')}")
            print(f"  repair_time: {story.get('repair_time')}")
        if len(stories_data) > 2:
            print(f"  ... and {len(stories_data) - 2} more")

        print("\n" + "=" * 60)
        print(f"REVIEWS (for embeddings): {len(reviews_data)} reviews")
        print("=" * 60)
        for i, review in enumerate(reviews_data[:2]):
            print(f"  --- Review {i+1} ---")
            print(f"  ps_number: {review.get('ps_number')}")
            print(f"  rating: {review.get('rating')}/5 stars")
            print(f"  title: {review.get('title', '')[:60]}")
            print(f"  author: {review.get('author')} - {review.get('date')}")
            print(f"  verified: {review.get('verified_purchase')}")
        if len(reviews_data) > 2:
            print(f"  ... and {len(reviews_data) - 2} more")

        # Show one embedding example
        if qna_data:
            print("\n" + "=" * 60)
            print("SAMPLE EMBEDDING TEXT (Q&A):")
            print("=" * 60)
            print(qna_data[0].get('embedding_text', '')[:300])

        if reviews_data:
            print("\n" + "=" * 60)
            print("SAMPLE EMBEDDING TEXT (Review):")
            print("=" * 60)
            print(reviews_data[0].get('embedding_text', '')[:300])

    finally:
        driver.quit()
        print("\nDriver closed.")


if __name__ == "__main__":
    test_single_page()
