"""
PartSelect Web Scraper Package

Scrapes parts data for refrigerators and dishwashers from PartSelect.com.
Designed to be extensible for additional appliance types.

Outputs:
- Parts data (SQL)
- Model compatibility (SQL)
- Q&A entries (for vector embeddings)
- Repair stories (for vector embeddings)

Usage:
    python -m scrapers.run_scraper                    # Scrape all
    python -m scrapers.run_scraper refrigerator       # Scrape refrigerator only
    python -m scrapers.run_scraper --test             # Test mode (2 brands, 5 categories)
"""

from .config import (
    APPLIANCE_CONFIGS,
    PARTS_SCHEMA,
    MODEL_COMPATIBILITY_SCHEMA,
    QNA_SCHEMA,
    REPAIR_STORIES_SCHEMA,
)
from .part_scraper import scrape_appliance_parts

__all__ = [
    "APPLIANCE_CONFIGS",
    "PARTS_SCHEMA",
    "MODEL_COMPATIBILITY_SCHEMA",
    "QNA_SCHEMA",
    "REPAIR_STORIES_SCHEMA",
    "scrape_appliance_parts",
]
