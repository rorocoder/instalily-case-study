#!/usr/bin/env python3
"""
Load CSV data into Supabase.

Usage:
    python -m database.load_data              # Load all data
    python -m database.load_data --no-embeddings  # Skip embedding generation
    python -m database.load_data --sql-only   # Only load SQL tables

Requires:
    pip install supabase sentence-transformers python-dotenv

Environment variables (in .env):
    SUPABASE_URL=https://your-project.supabase.co
    SUPABASE_KEY=your-anon-key
"""

import os
import csv
import argparse
import time
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Data directory
DATA_DIR = Path(__file__).parent.parent / "data"

# Embedding model - runs locally, no API key needed
EMBEDDING_MODEL = "all-MiniLM-L6-v2"  # 384 dimensions, fast and free
EMBEDDING_DIM = 384


def get_supabase_client():
    """Initialize Supabase client."""
    from supabase import create_client

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")

    if not url or not key:
        raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set in .env")

    return create_client(url, key)


def get_embedding_model():
    """Initialize local embedding model (no API key needed)."""
    from sentence_transformers import SentenceTransformer

    print(f"Loading embedding model: {EMBEDDING_MODEL}")
    return SentenceTransformer(EMBEDDING_MODEL)


def generate_embedding(model, text: str) -> list:
    """Generate embedding for text using local model."""
    embedding = model.encode(text, convert_to_numpy=True)
    return embedding.tolist()


def upsert_with_retry(supabase, table: str, data: list | dict, on_conflict: str, max_retries: int = 5, skip_on_failure: bool = False):
    """Upsert data with retry logic and exponential backoff."""
    for attempt in range(max_retries):
        try:
            supabase.table(table).upsert(data, on_conflict=on_conflict).execute()
            return True
        except Exception as e:
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt  # Exponential backoff: 1, 2, 4, 8, 16 seconds
                print(f"    Retry {attempt + 1}/{max_retries} after error: {str(e)[:80]}... waiting {wait_time}s")
                time.sleep(wait_time)
            else:
                error_msg = str(e)[:200]
                print(f"    Failed after {max_retries} retries: {error_msg}")
                if skip_on_failure:
                    print(f"    Skipping batch and continuing...")
                    return False
                else:
                    raise


def validate_foreign_keys(supabase, rows: list, foreign_key_field: str, data_type: str) -> list:
    """
    Validate and filter rows to only include those with valid foreign key references.

    Args:
        supabase: Supabase client
        rows: List of data rows to validate
        foreign_key_field: Name of the field containing the foreign key (e.g., 'ps_number', 'part_id')
        data_type: Description of data type for logging (e.g., 'reviews', 'Q&A', 'compatibility')

    Returns:
        Filtered list containing only rows with valid foreign key references
    """
    print("  Fetching existing parts from database...")
    try:
        # Fetch all parts with pagination
        existing_parts = set()
        offset = 0
        batch_size = 1000

        while True:
            response = supabase.table("parts").select("ps_number").range(offset, offset + batch_size - 1).execute()
            if not response.data:
                break

            for row in response.data:
                existing_parts.add(row["ps_number"])

            # If we got fewer than batch_size results, we've reached the end
            if len(response.data) < batch_size:
                break

            offset += batch_size

        print(f"  Found {len(existing_parts)} parts in database")
    except Exception as e:
        print(f"  Warning: Could not fetch parts from database: {e}")
        print(f"  Proceeding without validation - foreign key errors may occur")
        return rows

    missing_parts = set()
    valid_rows = []
    for row in rows:
        foreign_key = row.get(foreign_key_field)
        if foreign_key in existing_parts:
            valid_rows.append(row)
        else:
            missing_parts.add(foreign_key)

    if missing_parts:
        print(f"  Warning: {len(missing_parts)} parts referenced in {data_type} are missing from database")
        print(f"  Sample missing parts: {list(missing_parts)[:10]}")
        print(f"  Skipping {len(rows) - len(valid_rows)} {data_type} records")

    return valid_rows


def read_csv(filename: str) -> list[dict]:
    """Read CSV file and return list of dicts."""
    filepath = DATA_DIR / filename
    if not filepath.exists():
        print(f"  Warning: {filename} not found, skipping")
        return []

    with open(filepath, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


def clean_decimal(value: str) -> float | None:
    """Convert string to decimal, handling percentages."""
    if not value:
        return None
    # Remove % sign if present
    value = value.replace("%", "").strip()
    try:
        return float(value)
    except ValueError:
        return None


def load_parts(supabase, embedding_model=None, batch_size: int = 50):
    """Load parts.csv into parts table with optional embeddings."""
    print("\nLoading parts...")
    rows = read_csv("parts.csv")
    if not rows:
        return 0

    # Deduplicate by ps_number - keep last occurrence
    seen = {}
    for row in rows:
        ps_number = row.get("ps_number")
        if ps_number:
            seen[ps_number] = row

    unique_rows = list(seen.values())
    print(f"  Deduplicated: {len(rows)} -> {len(unique_rows)} unique parts")

    batch = []
    count = 0

    for row in unique_rows:
        # Build embedding text from name + type + description
        part_name = row.get("part_name", "")
        part_type = row.get("part_type", "")
        part_description = row.get("part_description", "")
        embedding_text = f"{part_name} {part_type} {part_description}".strip()

        data = {
            "ps_number": row.get("ps_number"),
            "part_name": part_name,
            "part_type": part_type,
            "manufacturer_part_number": row.get("manufacturer_part_number"),
            "part_manufacturer": row.get("part_manufacturer"),
            "part_price": clean_decimal(row.get("part_price")),
            "part_description": part_description,
            "install_difficulty": row.get("install_difficulty"),
            "install_time": row.get("install_time"),
            "install_video_url": row.get("install_video_url"),
            "part_url": row.get("part_url"),
            "average_rating": clean_decimal(row.get("average_rating")),
            "num_reviews": int(row.get("num_reviews", 0) or 0),
            "appliance_type": row.get("appliance_type"),
            "brand": row.get("brand"),
            "manufactured_for": row.get("manufactured_for"),
            "availability": row.get("availability"),
            "replaces_parts": row.get("replaces_parts"),
        }

        # Generate embedding if model is available
        if embedding_model and embedding_text:
            data["embedding"] = generate_embedding(embedding_model, embedding_text)

        batch.append(data)
        count += 1

        # Batch upsert for efficiency
        if len(batch) >= batch_size:
            upsert_with_retry(supabase, "parts", batch, "ps_number")
            batch = []
            if embedding_model:
                print(f"  Processed {count}/{len(unique_rows)} parts with embeddings...")
            time.sleep(0.1)  # Small delay between batches

    # Final batch
    if batch:
        upsert_with_retry(supabase, "parts", batch, "ps_number")

    if embedding_model:
        print(f"  Loaded {len(unique_rows)} parts with embeddings")
    else:
        print(f"  Loaded {len(unique_rows)} parts (no embeddings)")
    return len(unique_rows)


def load_model_compatibility(supabase, batch_size: int = 50):
    """Load model_compatibility.csv into model_compatibility table."""
    print("\nLoading model compatibility...")
    rows = read_csv("model_compatibility.csv")
    if not rows:
        return 0

    # Deduplicate by (part_id, model_number) - keep last occurrence
    seen = {}
    for row in rows:
        key = (row.get("part_id"), row.get("model_number"))
        seen[key] = {
            "part_id": row.get("part_id"),
            "model_number": row.get("model_number"),
            "brand": row.get("brand"),
            "description": row.get("description"),
        }

    unique_rows = list(seen.values())
    print(f"  Deduplicated: {len(rows)} -> {len(unique_rows)} unique records")

    # Validate foreign keys
    unique_rows = validate_foreign_keys(supabase, unique_rows, "part_id", "compatibility")

    # Batch insert with retry logic
    batch = []
    count = 0
    for data in unique_rows:
        batch.append(data)
        count += 1

        if len(batch) >= batch_size:
            upsert_with_retry(supabase, "model_compatibility", batch, "part_id,model_number")
            batch = []
            if count % 5000 == 0:
                print(f"  Processed {count}/{len(unique_rows)} compatibility records...")
            time.sleep(0.05)  # Small delay between batches

    if batch:
        upsert_with_retry(supabase, "model_compatibility", batch, "part_id,model_number")

    print(f"  Loaded {len(unique_rows)} compatibility records")
    return len(unique_rows)


def load_repair_symptoms(supabase):
    """Load repair_symptoms.csv into repair_symptoms table."""
    print("\nLoading repair symptoms...")
    rows = read_csv("repair_symptoms.csv")
    if not rows:
        return 0

    for row in rows:
        data = {
            "appliance_type": row.get("appliance_type"),
            "symptom": row.get("symptom"),
            "symptom_description": row.get("symptom_description"),
            "percentage": clean_decimal(row.get("percentage")),
            "video_url": row.get("video_url"),
            "parts": row.get("parts"),
            "symptom_url": row.get("symptom_url"),
            "difficulty": row.get("difficulty"),
        }

        supabase.table("repair_symptoms").upsert(
            data, on_conflict="appliance_type,symptom"
        ).execute()

    print(f"  Loaded {len(rows)} symptoms")
    return len(rows)


def load_repair_instructions(supabase):
    """Load repair_instructions.csv into repair_instructions table."""
    print("\nLoading repair instructions...")
    rows = read_csv("repair_instructions.csv")
    if not rows:
        return 0

    batch = []
    for row in rows:
        data = {
            "appliance_type": row.get("appliance_type"),
            "symptom": row.get("symptom"),
            "part_type": row.get("part_type"),
            "instructions": row.get("instructions"),
            "part_category_url": row.get("part_category_url"),
        }
        batch.append(data)

        if len(batch) >= 50:
            supabase.table("repair_instructions").upsert(
                batch, on_conflict="appliance_type,symptom,part_type"
            ).execute()
            batch = []

    if batch:
        supabase.table("repair_instructions").upsert(
            batch, on_conflict="appliance_type,symptom,part_type"
        ).execute()

    print(f"  Loaded {len(rows)} instructions")
    return len(rows)


def load_qna_with_embeddings(supabase, embedding_model, batch_size: int = 50):
    """Load qna.csv with generated embeddings using batching."""
    print("\nLoading Q&A with embeddings...")
    rows = read_csv("qna.csv")
    if not rows:
        return 0

    # Deduplicate by (ps_number, question_id) - keep last occurrence
    seen = {}
    for row in rows:
        ps_number = row.get("ps_number")
        question_id = row.get("question_id")
        if not ps_number or not question_id:
            continue
        key = (ps_number, question_id)
        seen[key] = row

    unique_rows = list(seen.values())
    print(f"  Deduplicated: {len(rows)} -> {len(unique_rows)} unique Q&A entries")

    # Validate foreign keys
    unique_rows = validate_foreign_keys(supabase, unique_rows, "ps_number", "Q&A")

    batch = []
    count = 0

    for row in unique_rows:
        # Build embedding text from question + answer
        question = row.get("question", "")
        answer = row.get("answer", "")
        embedding_text = f"{question} {answer}".strip()

        if not embedding_text:
            continue

        # Generate embedding
        embedding = generate_embedding(embedding_model, embedding_text)

        data = {
            "ps_number": row.get("ps_number"),
            "question_id": row.get("question_id"),
            "question": row.get("question"),
            "answer": row.get("answer"),
            "asker": row.get("asker"),
            "date": row.get("date"),
            "model_number": row.get("model_number"),
            "helpful_count": int(row.get("helpful_count", 0) or 0),
            "embedding_text": embedding_text,
            "embedding": embedding,
        }

        batch.append(data)
        count += 1

        # Batch upsert with retry logic
        if len(batch) >= batch_size:
            upsert_with_retry(supabase, "qna_embeddings", batch, "ps_number,question_id")
            batch = []
            print(f"  Processed {count}/{len(unique_rows)} Q&A entries...")
            # Small delay between batches to avoid rate limiting
            time.sleep(0.1)

    # Final batch
    if batch:
        upsert_with_retry(supabase, "qna_embeddings", batch, "ps_number,question_id")

    print(f"  Loaded {count} Q&A entries with embeddings")
    return count


def load_repair_stories_with_embeddings(supabase, embedding_model, batch_size: int = 50):
    """Load repair_stories.csv with generated embeddings using batching."""
    print("\nLoading repair stories with embeddings...")
    rows = read_csv("repair_stories.csv")
    if not rows:
        return 0

    # Deduplicate by (ps_number, story_id) - keep last occurrence
    seen = {}
    for row in rows:
        ps_number = row.get("ps_number")
        story_id = row.get("story_id")
        if not ps_number or not story_id:
            continue
        key = (ps_number, story_id)
        seen[key] = row

    unique_rows = list(seen.values())
    print(f"  Deduplicated: {len(rows)} -> {len(unique_rows)} unique stories")

    # Validate foreign keys
    unique_rows = validate_foreign_keys(supabase, unique_rows, "ps_number", "repair stories")

    batch = []
    count = 0

    for row in unique_rows:
        # Build embedding text from title + instruction
        title = row.get("title", "")
        instruction = row.get("instruction", "")
        embedding_text = f"{title} {instruction}".strip()

        if not embedding_text:
            continue

        # Generate embedding
        embedding = generate_embedding(embedding_model, embedding_text)

        data = {
            "ps_number": row.get("ps_number"),
            "story_id": row.get("story_id"),
            "title": row.get("title"),
            "instruction": row.get("instruction"),
            "author": row.get("author"),
            "difficulty": row.get("difficulty"),
            "repair_time": row.get("repair_time"),
            "helpful_count": int(row.get("helpful_count", 0) or 0),
            "vote_count": int(row.get("vote_count", 0) or 0),
            "embedding_text": embedding_text,
            "embedding": embedding,
        }

        batch.append(data)
        count += 1

        # Batch upsert with retry logic
        if len(batch) >= batch_size:
            upsert_with_retry(supabase, "repair_stories_embeddings", batch, "ps_number,story_id")
            batch = []
            print(f"  Processed {count}/{len(unique_rows)} stories...")
            # Small delay between batches to avoid rate limiting
            time.sleep(0.1)

    # Final batch
    if batch:
        upsert_with_retry(supabase, "repair_stories_embeddings", batch, "ps_number,story_id")

    print(f"  Loaded {count} repair stories with embeddings")
    return count


def load_reviews_with_embeddings(supabase, embedding_model, batch_size: int = 50):
    """Load reviews.csv with generated embeddings using batching."""
    print("\nLoading reviews with embeddings...")
    rows = read_csv("reviews.csv")
    if not rows:
        return 0

    # Deduplicate by (ps_number, review_id) - keep last occurrence
    seen = {}
    for row in rows:
        ps_number = row.get("ps_number")
        review_id = row.get("review_id")
        if not ps_number or not review_id:
            continue
        key = (ps_number, review_id)
        seen[key] = row

    unique_rows = list(seen.values())
    print(f"  Deduplicated: {len(rows)} -> {len(unique_rows)} unique reviews")

    # Validate foreign keys
    unique_rows = validate_foreign_keys(supabase, unique_rows, "ps_number", "reviews")

    batch = []
    count = 0

    for row in unique_rows:
        # Build embedding text from title + content
        title = row.get("title", "")
        content = row.get("content", "")
        embedding_text = f"{title} {content}".strip()

        if not embedding_text:
            continue

        # Generate embedding
        embedding = generate_embedding(embedding_model, embedding_text)

        # Parse verified_purchase boolean
        verified = row.get("verified_purchase", "").lower() in ("true", "1", "yes")

        data = {
            "ps_number": row.get("ps_number"),
            "review_id": row.get("review_id"),
            "rating": int(row.get("rating", 0) or 0),
            "title": title,
            "content": content,
            "author": row.get("author"),
            "date": row.get("date"),
            "verified_purchase": verified,
            "embedding_text": embedding_text,
            "embedding": embedding,
        }

        batch.append(data)
        count += 1

        # Batch upsert with retry logic
        if len(batch) >= batch_size:
            upsert_with_retry(supabase, "reviews_embeddings", batch, "ps_number,review_id")
            batch = []
            print(f"  Processed {count}/{len(unique_rows)} reviews...")
            time.sleep(0.1)

    # Final batch
    if batch:
        upsert_with_retry(supabase, "reviews_embeddings", batch, "ps_number,review_id")

    print(f"  Loaded {count} reviews with embeddings")
    return count


def main():
    parser = argparse.ArgumentParser(description="Load CSV data into Supabase")
    parser.add_argument("--no-embeddings", action="store_true",
                        help="Skip embedding generation (faster, for testing)")
    parser.add_argument("--sql-only", action="store_true",
                        help="Only load SQL tables, skip vector tables")
    parser.add_argument("--skip-parts", action="store_true",
                        help="Skip loading parts table")
    parser.add_argument("--skip-compatibility", action="store_true",
                        help="Skip loading model compatibility table")
    parser.add_argument("--embeddings-only", action="store_true",
                        help="Only load embedding tables (Q&A, stories, reviews)")
    parser.add_argument("--only-qna", action="store_true",
                        help="Only load Q&A table")
    parser.add_argument("--only-stories", action="store_true",
                        help="Only load repair stories table")
    parser.add_argument("--only-reviews", action="store_true",
                        help="Only load reviews table")
    args = parser.parse_args()

    print("=" * 60)
    print("Loading data into Supabase")
    print("=" * 60)

    # Initialize clients
    supabase = get_supabase_client()
    print("Connected to Supabase")

    # Determine if we're in "only" mode (only loading specific embedding tables)
    only_mode = args.embeddings_only or args.only_qna or args.only_stories or args.only_reviews

    embedding_model = None
    if not args.no_embeddings and (not args.sql_only or only_mode):
        embedding_model = get_embedding_model()
        print(f"Loaded embedding model ({EMBEDDING_DIM} dimensions)")

    totals = {}

    # Load SQL tables (parts now includes embeddings if model available)
    if not args.skip_parts and not only_mode:
        totals["parts"] = load_parts(supabase, embedding_model)

    if not args.skip_compatibility and not only_mode:
        totals["compatibility"] = load_model_compatibility(supabase)

    if not only_mode:
        totals["symptoms"] = load_repair_symptoms(supabase)
        totals["instructions"] = load_repair_instructions(supabase)

    # Load vector tables (with embeddings)
    load_embeddings = not args.sql_only or args.embeddings_only or args.only_qna or args.only_stories or args.only_reviews
    if load_embeddings:
        if embedding_model:
            # Load specific tables based on flags
            if args.only_qna:
                totals["qna"] = load_qna_with_embeddings(supabase, embedding_model)
            elif args.only_stories:
                totals["stories"] = load_repair_stories_with_embeddings(supabase, embedding_model)
            elif args.only_reviews:
                totals["reviews"] = load_reviews_with_embeddings(supabase, embedding_model)
            else:
                # Load all embedding tables
                totals["qna"] = load_qna_with_embeddings(supabase, embedding_model)
                totals["stories"] = load_repair_stories_with_embeddings(supabase, embedding_model)
                totals["reviews"] = load_reviews_with_embeddings(supabase, embedding_model)
        else:
            print("\nSkipping embeddings (--no-embeddings flag)")
            totals["qna"] = 0
            totals["stories"] = 0
            totals["reviews"] = 0

    print("\n" + "=" * 60)
    print("Data loading complete!")
    print("=" * 60)
    for key, count in totals.items():
        print(f"  {key}: {count}")


if __name__ == "__main__":
    main()
