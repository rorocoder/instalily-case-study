#!/usr/bin/env python3
"""
Diagnose missing parts in the database.

Compares parts in the CSV files with what's actually in the database
to identify which parts failed to load.
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


def main():
    print("=" * 60)
    print("Diagnosing missing parts")
    print("=" * 60)

    # Read parts from CSV
    print("\nReading parts.csv...")
    csv_parts = set()
    with open(DATA_DIR / "parts.csv", "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ps_number = row.get("ps_number", "").strip()
            if ps_number:
                csv_parts.add(ps_number)
    print(f"  Found {len(csv_parts)} parts in CSV")

    # Read parts from database
    print("\nFetching parts from database...")
    supabase = get_supabase_client()
    try:
        # Fetch in batches to avoid timeout
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

        db_parts = {row["ps_number"] for row in all_parts}
        print(f"  Found {len(db_parts)} parts in database")
    except Exception as e:
        print(f"  Error fetching from database: {e}")
        return

    # Find missing parts
    missing = csv_parts - db_parts
    extra = db_parts - csv_parts

    print("\n" + "=" * 60)
    print("Analysis Results")
    print("=" * 60)
    print(f"Parts in CSV:      {len(csv_parts)}")
    print(f"Parts in database: {len(db_parts)}")
    print(f"Missing from DB:   {len(missing)}")
    print(f"Extra in DB:       {len(extra)}")

    if missing:
        print(f"\nFirst 20 missing parts:")
        for i, part in enumerate(sorted(missing)[:20], 1):
            print(f"  {i}. {part}")

        # Check which ones are referenced in compatibility
        print("\nChecking compatibility references...")
        compat_refs = set()
        with open(DATA_DIR / "model_compatibility.csv", "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                part_id = row.get("part_id", "").strip()
                if part_id in missing:
                    compat_refs.add(part_id)

        print(f"  {len(compat_refs)} missing parts are referenced in compatibility table")
        print(f"  This will cause {len(compat_refs)} parts to have compatibility issues")

    if extra:
        print(f"\nFirst 10 extra parts in database (not in CSV):")
        for i, part in enumerate(sorted(extra)[:10], 1):
            print(f"  {i}. {part}")

    # Save missing parts to file for reference
    if missing:
        output_file = Path(__file__).parent / "missing_parts.txt"
        with open(output_file, "w") as f:
            for part in sorted(missing):
                f.write(f"{part}\n")
        print(f"\nSaved missing parts list to: {output_file}")


if __name__ == "__main__":
    main()
