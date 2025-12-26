"""
Vector-based tools for semantic search.

These tools use embeddings to find semantically similar content:
- Q&A from customer questions
- Repair stories from user experiences
"""
from langchain_core.tools import tool
from sentence_transformers import SentenceTransformer
from backend.config import get_settings
from backend.db import get_supabase_client

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


@tool
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
        return {"error": "ps_number is required - must specify which part to search Q&A for"}

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


@tool
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

    Args:
        query: Natural language query (e.g., "my ice maker makes clicking noises")
        ps_number: REQUIRED - the part's PS number to search stories for
        limit: Maximum number of results (default 5)

    Returns:
        List of relevant stories with title, instruction, difficulty, and similarity
    """
    if not ps_number:
        return {"error": "ps_number is required - must specify which part to search stories for"}

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


