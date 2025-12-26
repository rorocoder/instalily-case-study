"""
Configuration for PartSelect web scraper.
Easily extensible to add new appliance types.
"""

# Appliance configurations - add new appliances here
APPLIANCE_CONFIGS = {
    "refrigerator": {
        "base_url": "https://www.partselect.com/Refrigerator-Parts.htm",
        "related_section_pattern": "Refrigerator Parts"
    },
    "dishwasher": {
        "base_url": "https://www.partselect.com/Dishwasher-Parts.htm",
        "related_section_pattern": "Dishwasher Parts"
    }
}

# Unified output filenames (all appliance types go into same files)
OUTPUT_FILES = {
    "parts": "parts.csv",
    "model_compatibility": "model_compatibility.csv",
    "qna": "qna.csv",
    "repair_stories": "repair_stories.csv",
    "reviews": "reviews.csv",
}

# Scraper settings - OPTIMIZED FOR SPEED
SCRAPER_SETTINGS = {
    "max_workers": 10,          # Parallel brand processing (increased from 3 to 10)
    "max_retries": 2,           # Retry attempts per page (reduced from 3 to 2)
    "page_load_timeout": 20,    # Page load timeout (reduced from 60 to 20)
    "script_timeout": 15,       # Script execution time (reduced from 30 to 15)
    "element_timeout": 5,       # Element wait time (reduced from 15 to 5)
    "delay_between_pages": (0.5, 1),    # Random delay between pages (reduced from 3-6 to 0.5-1)
    "delay_between_brands": (1, 2),     # Random delay between brands (reduced from 8-12 to 1-2)
    "delay_before_navigate": (0.3, 0.7),  # Random delay before navigation (reduced from 1-3 to 0.3-0.7)
    "stagger_start_delay": (1, 2),      # Stagger parallel worker starts (reduced from 5-10 to 1-2)
}

# Output directory for scraped data
OUTPUT_DIR = "data"

# Parts table schema (from ARCHITECTURE.md)
PARTS_SCHEMA = [
    "ps_number",                # Primary key - PartSelect number
    "part_name",
    "part_type",                # e.g., "Ice Maker Assembly", "Water Inlet Valve"
    "manufacturer_part_number",
    "part_manufacturer",
    "part_price",
    "part_description",
    "install_difficulty",
    "install_time",
    "install_video_url",
    "part_url",
    "average_rating",
    "num_reviews",
    "appliance_type",           # refrigerator, dishwasher
    "brand",                    # e.g., "Whirlpool"
    "manufactured_for",         # e.g., "Whirlpool, KitchenAid, Maytag, Jenn-Air"
    "availability",
    "replaces_parts",           # List of other part numbers this replaces
]

# Model compatibility schema (separate table - one part fits many models)
MODEL_COMPATIBILITY_SCHEMA = [
    "part_id",                  # FK to parts.ps_number
    "model_number",
    "brand",
    "description",
]

# Q&A schema (for vector embeddings)
QNA_SCHEMA = [
    "ps_number",                # FK to parts.ps_number
    "question_id",
    "asker",
    "date",
    "question",
    "answer",
    "model_number",             # If customer specified their model
    "helpful_count",
]

# Repair Stories schema (for vector embeddings)
REPAIR_STORIES_SCHEMA = [
    "ps_number",                # FK to parts.ps_number
    "story_id",
    "title",                    # Problem description
    "instruction",              # How they fixed it
    "author",
    "difficulty",               # e.g., "Really Easy", "A bit difficult"
    "repair_time",              # e.g., "Less than 15 mins"
    "helpful_count",
    "vote_count",
]

# Reviews schema (for vector embeddings)
REVIEWS_SCHEMA = [
    "ps_number",                # FK to parts.ps_number
    "review_id",                # Hash of author+date+title
    "rating",                   # 1-5 stars
    "title",                    # Review headline
    "content",                  # Full review text
    "author",                   # Reviewer name
    "date",                     # Review date
    "verified_purchase",        # Boolean
]

# =============================================================================
# REPAIR HELP SCRAPER SCHEMAS (separate from parts scraper)
# =============================================================================

# Appliance types for repair help pages
REPAIR_APPLIANCE_CONFIGS = {
    "refrigerator": {
        "repair_url": "https://www.partselect.com/Repair/Refrigerator/",
        "output_prefix": "refrigerator",
    },
    "dishwasher": {
        "repair_url": "https://www.partselect.com/Repair/Dishwasher/",
        "output_prefix": "dishwasher",
    },
    "microwave": {
        "repair_url": "https://www.partselect.com/Repair/Microwave/",
        "output_prefix": "microwave",
    },
    "washer": {
        "repair_url": "https://www.partselect.com/Repair/Washer/",
        "output_prefix": "washer",
    },
    "dryer": {
        "repair_url": "https://www.partselect.com/Repair/Dryer/",
        "output_prefix": "dryer",
    },
    "range": {
        "repair_url": "https://www.partselect.com/Repair/Range-Stove-Oven/",
        "output_prefix": "range",
    },
}

# Repair Symptoms schema (SQL table - one row per symptom)
REPAIR_SYMPTOMS_SCHEMA = [
    "appliance_type",           # refrigerator, dishwasher, etc.
    "symptom",                  # "Noisy", "Leaking", "Will not start", etc.
    "symptom_description",      # Detailed description of the problem
    "percentage",               # "29%" - percentage of customers reporting
    "video_url",                # YouTube video URL for repair
    "parts",                    # Comma-separated list of part types to check
    "symptom_url",              # Full URL to the symptom page
    "difficulty",               # EASY, MODERATE, DIFFICULT
]

# Repair Part Instructions schema (one row per part per symptom)
REPAIR_PART_INSTRUCTIONS_SCHEMA = [
    "appliance_type",           # refrigerator, dishwasher, etc.
    "symptom",                  # "Noisy", "Leaking", etc.
    "part_type",                # "Water Fill Tubes", "Water Inlet Valve", etc.
    "instructions",             # Full step-by-step repair instructions
    "part_category_url",        # URL to the specific section
]
