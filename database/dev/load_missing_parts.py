#!/usr/bin/env python3
"""
Load only the missing parts into the database.

This script identifies parts in the CSV that are missing from the database
and loads only those parts, avoiding duplicates.
"""

import os
import csv
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

DATA_DIR = Path(__file__).parent.parent / "data"


def get_supabase_client():
    """Initialize Supabase client."""
    from supabase import create_client

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")

    if not url or not key:
        raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set in .env")

    return create_client(url, key)


def clean_decimal(value: str):
    """Convert string to decimal, handling percentages."""
    if not value:
        return None
    value = value.replace("%", "").strip()
    try:
        return float(value)
    except ValueError:
        return None


def main():
    print("=" * 60)
    print("Loading missing parts")
    print("=" * 60)

    supabase = get_supabase_client()

    # Get existing parts from database
    print("\nFetching existing parts from database...")
    all_parts = []
    offset = 0
    batch_size = 1000

    while True:
        response = supabase.table("parts").select("ps_number").range(offset, offset + batch_size - 1).execute()
        if not response.data:
            break
        all_parts.extend(response.data)
        offset += batch_size
        if len(response.data) < batch_size:
            break

    existing_parts = {row["ps_number"] for row in all_parts}
    print(f"  Found {len(existing_parts)} existing parts")

    # Read all parts from CSV
    print("\nReading parts.csv...")
    csv_parts = {}
    with open(DATA_DIR / "parts.csv", "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ps_number = row.get("ps_number", "").strip()
            if ps_number:
                csv_parts[ps_number] = row

    print(f"  Found {len(csv_parts)} parts in CSV")

    # Identify missing parts
    missing_parts = set(csv_parts.keys()) - existing_parts
    print(f"  {len(missing_parts)} parts need to be loaded")

    if not missing_parts:
        print("\nNo missing parts to load!")
        return

    # Load missing parts
    print("\nLoading missing parts...")
    batch = []
    batch_size = 50
    count = 0

    for ps_number in sorted(missing_parts):
        row = csv_parts[ps_number]

        data = {
            "ps_number": ps_number,
            "part_name": row.get("part_name", ""),
            "part_type": row.get("part_type", ""),
            "manufacturer_part_number": row.get("manufacturer_part_number", ""),
            "part_manufacturer": row.get("part_manufacturer", ""),
            "part_price": clean_decimal(row.get("part_price")),
            "part_description": row.get("part_description", ""),
            "install_difficulty": row.get("install_difficulty", ""),
            "install_time": row.get("install_time", ""),
            "install_video_url": row.get("install_video_url", ""),
            "part_url": row.get("part_url", ""),
            "average_rating": clean_decimal(row.get("average_rating")),
            "num_reviews": int(row.get("num_reviews", 0) or 0),
            "appliance_type": row.get("appliance_type", ""),
            "brand": row.get("brand", ""),
            "manufactured_for": row.get("manufactured_for", ""),
            "availability": row.get("availability", ""),
            "replaces_parts": row.get("replaces_parts", ""),
        }

        batch.append(data)
        count += 1

        if len(batch) >= batch_size:
            try:
                supabase.table("parts").upsert(batch, on_conflict="ps_number").execute()
                print(f"  Loaded {count}/{len(missing_parts)} parts...")
                batch = []
            except Exception as e:
                print(f"  Error loading batch: {e}")
                # Try one at a time
                for item in batch:
                    try:
                        supabase.table("parts").upsert(item, on_conflict="ps_number").execute()
                    except Exception as e2:
                        print(f"  Failed to load {item['ps_number']}: {e2}")
                batch = []

    # Load remaining batch
    if batch:
        try:
            supabase.table("parts").upsert(batch, on_conflict="ps_number").execute()
        except Exception as e:
            print(f"  Error loading final batch: {e}")
            for item in batch:
                try:
                    supabase.table("parts").upsert(item, on_conflict="ps_number").execute()
                except Exception as e2:
                    print(f"  Failed to load {item['ps_number']}: {e2}")

    print(f"\nCompleted! Loaded {count} missing parts")


if __name__ == "__main__":
    main()
