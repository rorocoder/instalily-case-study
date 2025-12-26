"""
Extractors for different data types from PartSelect pages.

Each extractor module handles a specific type of content:
- qna: Customer questions and expert answers
- repair_stories: Customer repair instructions and experiences
- reviews: Customer product reviews

All extractors operate on an already-loaded Selenium driver and return
structured data that can be formatted for vector embeddings.
"""

from .qna import extract_qna, format_for_embedding as format_qna_for_embedding
from .repair_stories import extract_repair_stories, format_for_embedding as format_story_for_embedding
from .reviews import extract_reviews, format_for_embedding as format_review_for_embedding
