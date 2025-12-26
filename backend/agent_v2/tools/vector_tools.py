"""
Vector-based tools for semantic search.

These tools use embeddings to find semantically similar content:
- Q&A from customer questions
- Repair stories from user experiences

Copied from backend/tools/vector_tools.py with registry decorators.
"""
from sentence_transformers import SentenceTransformer
from backend.config import get_settings
from backend.db import get_supabase_client
from backend.agent_v2.tools.registry import registry

# Module-level model cache (more robust than lru_cache for ML models)
_embedding_model = None


def get_embedding_model() -> SentenceTransformer:
    """Get cached embedding model instance with error recovery."""
    global _embedding_model
    if _embedding_model is None:
        settings = get_settings()
        _embedding_model = SentenceTransformer(settings.EMBEDDING_MODEL)
    return _embedding_model


def generate_embedding(text: str) -> list[float]:
    """Generate embedding vector for text."""
    global _embedding_model
    try:
        model = get_embedding_model()
        embedding = model.encode(text, convert_to_numpy=True)
        return embedding.tolist()
    except RuntimeError as e:
        # Handle meta tensor or corrupted model state - recreate model
        if "meta tensor" in str(e) or "no data" in str(e):
            print(f"  [WARN] Embedding model corrupted, recreating: {e}")
            _embedding_model = None
            model = get_embedding_model()
            embedding = model.encode(text, convert_to_numpy=True)
            return embedding.tolist()
        raise


@registry.register(category="vector")
def search_qna(
    query: str,
    ps_number: str,
    limit: int = 5
) -> list[dict]:
    """
    Search Q&A content for a specific part by semantic similarity.

    IMPORTANT: ps_number is REQUIRED. Only searches Q&A for the specified part.

    Finds relevant customer questions and answers that match the query intent.
    Use for questions about:
    - Finding out how easy or difficult installation is
    - Installation tips and gotchas
    - Part quality and durability
    - Specific use cases and compatibility details

    Args:
        query: Natural language query (e.g., "is this part easy to install")
        ps_number: REQUIRED - the part's PS number to search Q&A for
        limit: Maximum number of results (default 5)

    Returns:
        List of relevant Q&A with question, answer, and similarity score
    """
    if not ps_number:
        return []  # ps_number required - return empty for consistency

    db = get_supabase_client()

    # If query is empty or generic, fetch all Q&A for this part without semantic search
    if not query or query.strip() == "":
        results = db.get_qna_by_ps_number(ps_number, limit=limit)
    else:
        query_embedding = generate_embedding(query)
        results = db.search_qna(
            query_embedding=query_embedding,
            ps_number=ps_number,
            match_threshold=0.2,
            limit=limit
        )

    return results


@registry.register(category="vector")
def search_repair_stories(
    query: str,
    ps_number: str,
    limit: int = 5
) -> list[dict]:
    """
    Search repair stories for a specific part by semantic similarity.

    IMPORTANT: ps_number is REQUIRED. Only searches stories for the specified part.

    Finds relevant customer repair experiences that match the query.
    Use for:
    - Real-world troubleshooting experiences
    - Installation difficulty insights
    - Tips from people who fixed similar problems
    - Installation tips 
    - Repair tips

    Args:
        query: Natural language query (e.g., "my ice maker makes clicking noises")
        ps_number: REQUIRED - the part's PS number to search stories for
        limit: Maximum number of results (default 5)

    Returns:
        List of relevant stories with title, instruction, difficulty, and similarity
    """
    if not ps_number:
        return []  # ps_number required - return empty for consistency

    db = get_supabase_client()

    # If query is empty or generic, fetch all repair stories for this part without semantic search
    if not query or query.strip() == "":
        results = db.get_repair_stories_by_ps_number(ps_number, limit=limit)
    else:
        query_embedding = generate_embedding(query)
        results = db.search_repair_stories(
            query_embedding=query_embedding,
            ps_number=ps_number,
            match_threshold=0.2,  # Lowered to catch more related content
            limit=limit
        )

    return results


@registry.register(category="search")
def search_parts_semantic(
    query: str,
    appliance_type: str | None = None,
    limit: int = 10
) -> list[dict]:
    """
    Search for parts using natural language (semantic search).

    Use this when searching for parts by description or concept where the exact
    terminology might not match the database categories. For example:
    - "refrigerator bins" → finds parts with part_type "Drawer or Glides"
    - "ice maker components" → finds Ice Maker, Water Inlet Valve, etc.
    - "door seal" → finds parts with part_type "Seal or Gasket"

    This is more forgiving than search_parts() which requires exact text matches.
    Use search_parts() when you know the exact part_type or have specific filters.

    Args:
        query: Natural language description (e.g., "refrigerator bins", "door seal")
        appliance_type: Optional filter ("refrigerator" or "dishwasher")
        limit: Maximum number of results (default 10)

    Returns:
        List of matching parts with ps_number, part_name, part_type, price, etc.
        Returns empty list if no matches found or embeddings not yet generated.
    """
    if not query or query.strip() == "":
        return []  # Return empty list for consistency, not a dict

    db = get_supabase_client()

    try:
        query_embedding = generate_embedding(query)
    except Exception as e:
        print(f"  [WARN] Failed to generate embedding for query: {e}")
        return []

    results = db.search_parts_semantic(
        query_embedding=query_embedding,
        appliance_type=appliance_type,
        match_threshold=0.4,  # Lower threshold for broader matches
        limit=limit
    )

    # Results will be empty if:
    # - No parts match semantically
    # - Parts don't have embeddings yet (embedding column is NULL)
    # - The RPC function doesn't exist yet
    # All cases return [] gracefully
    return results


@registry.register(category="vector")
def search_reviews(
    query: str,
    ps_number: str,
    limit: int = 5
) -> list[dict]:
    """
    Search customer reviews for a specific part by semantic similarity.

    IMPORTANT: ps_number is REQUIRED. Only searches reviews for the specified part.

    WHEN TO USE:
    - User asks "is this part any good?" or "should I buy this?"
    - User asks about quality, durability, or value for money
    - User wants to know about common issues or complaints
    - User asks "is this easy to install?" (reviews often mention installation)

    WHEN NOT TO USE:
    - You don't have a ps_number yet → use search_parts first
    - User wants step-by-step instructions → use get_repair_instructions
    - User wants Q&A from PartSelect → use search_qna

    Args:
        query: Natural language query (e.g., "is this easy to install", "any quality issues")
        ps_number: REQUIRED - the part's PS number to search reviews for
        limit: Maximum number of results (default 5)

    Returns:
        List of relevant reviews with rating, title, content, and similarity score
    """
    if not ps_number:
        return []  # ps_number required - return empty for consistency

    db = get_supabase_client()

    # If query is empty, fetch all reviews for this part without semantic search
    if not query or query.strip() == "":
        results = db.get_reviews_by_ps_number(ps_number, limit=limit)
    else:
        try:
            query_embedding = generate_embedding(query)
        except Exception as e:
            print(f"  [WARN] Failed to generate embedding for query: {e}")
            return []

        results = db.search_reviews(
            query_embedding=query_embedding,
            ps_number=ps_number,
            match_threshold=0.2,
            limit=limit
        )

    return results
