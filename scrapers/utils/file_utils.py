"""
File I/O utilities for saving scraped data.
"""

import csv
import os
import threading
from pathlib import Path

from ..config import OUTPUT_DIR, PARTS_SCHEMA, MODEL_COMPATIBILITY_SCHEMA, QNA_SCHEMA, REPAIR_STORIES_SCHEMA, REVIEWS_SCHEMA

# Thread lock for safe concurrent file writes
_file_locks = {}
_lock_lock = threading.Lock()


def _get_file_lock(filepath):
    """Get or create a lock for a specific file."""
    with _lock_lock:
        if filepath not in _file_locks:
            _file_locks[filepath] = threading.Lock()
        return _file_locks[filepath]


def ensure_output_dir():
    """Create output directory if it doesn't exist."""
    output_path = Path(OUTPUT_DIR)
    output_path.mkdir(parents=True, exist_ok=True)
    return output_path


def save_to_csv(data, filename, schema=None):
    """
    Save data to a CSV file (overwrites existing).

    Args:
        data: List of dictionaries containing the data
        filename: Name of the CSV file (will be saved in OUTPUT_DIR)
        schema: List of field names to use (optional, defaults to data keys)
    """
    if not data:
        print("No data to save.")
        return

    output_path = ensure_output_dir()
    filepath = output_path / filename

    try:
        # Use schema if provided, otherwise use keys from first dict
        fieldnames = schema if schema else list(data[0].keys())

        with open(filepath, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(data)

        print(f"Successfully saved {len(data)} records to {filepath}")
    except Exception as e:
        print(f"Error saving to CSV: {e}")


def append_to_csv(data, filename, schema):
    """
    Append data to a CSV file (thread-safe). Creates file with header if it doesn't exist.

    Args:
        data: List of dictionaries containing the data
        filename: Name of the CSV file (will be saved in OUTPUT_DIR)
        schema: List of field names (required for consistent column order)
    """
    if not data:
        return 0

    output_path = ensure_output_dir()
    filepath = output_path / filename
    file_lock = _get_file_lock(str(filepath))

    try:
        with file_lock:
            file_exists = filepath.exists() and filepath.stat().st_size > 0

            with open(filepath, 'a', newline='', encoding='utf-8') as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=schema, extrasaction='ignore')

                # Write header only if file is new/empty
                if not file_exists:
                    writer.writeheader()

                writer.writerows(data)

        return len(data)
    except Exception as e:
        print(f"Error appending to CSV: {e}")
        return 0


def append_parts_data(parts_data, filename):
    """Append parts data to CSV (thread-safe)."""
    return append_to_csv(parts_data, filename, PARTS_SCHEMA)


def append_model_compatibility_data(compatibility_data, filename):
    """Append model compatibility data to CSV (thread-safe)."""
    return append_to_csv(compatibility_data, filename, MODEL_COMPATIBILITY_SCHEMA)


def clear_output_file(filename):
    """Remove an output file if it exists (for fresh start)."""
    output_path = ensure_output_dir()
    filepath = output_path / filename
    if filepath.exists():
        filepath.unlink()
        print(f"Cleared {filepath}")


def get_scraped_part_ids(filename):
    """
    Read existing parts CSV and return set of already-scraped ps_number values.
    Used for resume capability - skip parts we've already scraped.

    Args:
        filename: Name of the parts CSV file

    Returns:
        set: Set of ps_number strings already in the file
    """
    output_path = ensure_output_dir()
    filepath = output_path / filename
    scraped_ids = set()

    if not filepath.exists():
        return scraped_ids

    try:
        with open(filepath, 'r', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                ps_number = row.get('ps_number', '').strip()
                if ps_number:
                    scraped_ids.add(ps_number)
        print(f"Resume mode: found {len(scraped_ids)} already-scraped parts")
    except Exception as e:
        print(f"Warning: Could not read {filepath} for resume: {e}")

    return scraped_ids


def save_parts_data(parts_data, filename):
    """Save parts data using the parts schema."""
    save_to_csv(parts_data, filename, PARTS_SCHEMA)


def save_model_compatibility_data(compatibility_data, filename):
    """Save model compatibility data using the compatibility schema."""
    save_to_csv(compatibility_data, filename, MODEL_COMPATIBILITY_SCHEMA)


def append_qna_data(qna_data, filename):
    """Append Q&A data to CSV (thread-safe)."""
    return append_to_csv(qna_data, filename, QNA_SCHEMA)


def append_repair_stories_data(stories_data, filename):
    """Append repair stories data to CSV (thread-safe)."""
    return append_to_csv(stories_data, filename, REPAIR_STORIES_SCHEMA)


def append_reviews_data(reviews_data, filename):
    """Append reviews data to CSV (thread-safe)."""
    return append_to_csv(reviews_data, filename, REVIEWS_SCHEMA)
