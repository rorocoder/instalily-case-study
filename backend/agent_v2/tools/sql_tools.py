"""
SQL-based tools for exact database lookups.

These tools query the PostgreSQL database for structured data:
- Parts catalog
- Model compatibility
- Repair symptoms and instructions

Copied from backend/tools/sql_tools.py with registry decorators.
"""
import re
from backend.db import get_supabase_client
from backend.agent_v2.tools.registry import registry


# =============================================================================
# Resolution Tools - Parse messy input → clean identifiers
# =============================================================================

@registry.register(category="resolution")
def resolve_part(
    input: str,
    session_context: dict | None = None
) -> dict:
    """
    Parse any part reference and return structured identifiers.

    Handles multiple input types:
    - PS number: "PS11752778" → exact match
    - Manufacturer #: "WPW10321304" → lookup and return PS number
    - PartSelect URL: "partselect.com/PS11752778..." → extract PS number
    - Session reference: "this part" with session context → resolve from context
    - Text search: "ice maker" → return search candidates

    Args:
        input: The user's part reference (PS#, manufacturer#, URL, text, or "this part")
        session_context: Optional dict with current_part, current_model, appliance_type

    Returns:
        Dictionary with:
        - resolved: bool - whether we found a specific part
        - ps_number: str | None - the resolved PS number
        - manufacturer_part_number: str | None - if matched via manufacturer #
        - url: str | None - if extracted from URL
        - confidence: "exact" | "matched" | "session" | "search" | "not_found"
        - candidates: list - if multiple matches (for search or partial match)
        - part_name: str | None - name of resolved part
        - appliance_type: str | None - appliance type of resolved part
    """
    db = get_supabase_client()
    input_clean = input.strip()

    # 1. Check for session reference ("this part", "the part", etc.)
    session_refs = ["this part", "the part", "that part", "it", "this one"]
    if session_context and any(ref in input_clean.lower() for ref in session_refs):
        current_part = session_context.get("current_part")
        if current_part:
            # Validate the session part still exists
            result = db.validate_part(current_part)
            if result.get("found"):
                return {
                    "resolved": True,
                    "ps_number": current_part,
                    "confidence": "session",
                    "part_name": result.get("part_name"),
                    "candidates": []
                }

    # 2. Check for PartSelect URL
    url_patterns = [
        r'partselect\.com/PS(\d+)',  # partselect.com/PS11752778
        r'PS(\d+)',  # Just PS number in a URL or text
    ]
    for pattern in url_patterns:
        match = re.search(pattern, input_clean, re.IGNORECASE)
        if match:
            ps_number = f"PS{match.group(1)}"
            result = db.validate_part(ps_number)
            if result.get("found"):
                return {
                    "resolved": True,
                    "ps_number": ps_number,
                    "confidence": "exact",
                    "url": input_clean if "partselect" in input_clean.lower() else None,
                    "part_name": result.get("part_name"),
                    "candidates": []
                }

    # 3. Check for PS number format
    ps_match = re.match(r'^PS\d+$', input_clean, re.IGNORECASE)
    if ps_match:
        ps_number = input_clean.upper()
        result = db.validate_part(ps_number)
        if result.get("found"):
            return {
                "resolved": True,
                "ps_number": ps_number,
                "confidence": "exact",
                "part_name": result.get("part_name"),
                "candidates": []
            }
        return {
            "resolved": False,
            "ps_number": ps_number,
            "confidence": "not_found",
            "message": f"Part {ps_number} not found in database",
            "candidates": []
        }

    # 4. Check for manufacturer part number (alphanumeric, often starts with letter)
    # Common patterns: WPW10321304, W10321304, 8194001, etc.
    if re.match(r'^[A-Z0-9\-]+$', input_clean, re.IGNORECASE) and len(input_clean) >= 5:
        # Try exact manufacturer number match
        part = db.find_by_manufacturer_number(input_clean.upper())
        if part:
            return {
                "resolved": True,
                "ps_number": part.get("ps_number"),
                "manufacturer_part_number": part.get("manufacturer_part_number"),
                "confidence": "matched",
                "part_name": part.get("part_name"),
                "appliance_type": part.get("appliance_type"),
                "candidates": []
            }

        # Try partial match
        candidates = db.find_by_manufacturer_number_partial(input_clean.upper())
        if candidates:
            if len(candidates) == 1:
                return {
                    "resolved": True,
                    "ps_number": candidates[0].get("ps_number"),
                    "manufacturer_part_number": candidates[0].get("manufacturer_part_number"),
                    "confidence": "matched",
                    "part_name": candidates[0].get("part_name"),
                    "appliance_type": candidates[0].get("appliance_type"),
                    "candidates": []
                }
            return {
                "resolved": False,
                "confidence": "search",
                "message": f"Found {len(candidates)} parts matching '{input_clean}'",
                "candidates": candidates
            }

    # 5. Fall back to text search
    search_result = db.search_parts(query=input_clean, limit=5)
    if search_result:
        if len(search_result) == 1:
            return {
                "resolved": True,
                "ps_number": search_result[0].get("ps_number"),
                "confidence": "search",
                "part_name": search_result[0].get("part_name"),
                "appliance_type": search_result[0].get("appliance_type"),
                "candidates": []
            }
        return {
            "resolved": False,
            "confidence": "search",
            "message": f"Found {len(search_result)} parts matching '{input_clean}'",
            "candidates": search_result
        }

    return {
        "resolved": False,
        "confidence": "not_found",
        "message": f"No parts found matching '{input_clean}'",
        "candidates": []
    }


@registry.register(category="resolution")
def resolve_model(input: str) -> dict:
    """
    Parse a model number reference with fuzzy matching.

    Matching strategy (in order):
    1. Exact match (case-insensitive)
    2. Partial match (ILIKE %input%)

    Args:
        input: The user's model reference (e.g., "WDT780SAEM1", "WDT780")

    Returns:
        Dictionary with:
        - resolved: bool - whether we found a specific model
        - model_number: str | None - the resolved model number
        - brand: str | None - brand of the model
        - description: str | None - model description
        - confidence: "exact" | "partial" | "not_found"
        - candidates: list - other matches if partial
    """
    db = get_supabase_client()
    input_clean = input.strip().upper()

    # Try exact match first
    result = db.validate_model(input_clean)
    if result.get("found"):
        return {
            "resolved": True,
            "model_number": input_clean,
            "brand": result.get("brand"),
            "description": result.get("description"),
            "confidence": "exact",
            "candidates": []
        }

    # Try fuzzy matching
    candidates = db.find_model_fuzzy(input_clean)
    if candidates:
        if len(candidates) == 1:
            return {
                "resolved": True,
                "model_number": candidates[0].get("model_number"),
                "brand": candidates[0].get("brand"),
                "description": candidates[0].get("description"),
                "confidence": "partial",
                "candidates": []
            }
        return {
            "resolved": False,
            "confidence": "partial",
            "message": f"Found {len(candidates)} models matching '{input_clean}'",
            "candidates": candidates
        }

    return {
        "resolved": False,
        "confidence": "not_found",
        "message": f"No models found matching '{input_clean}'",
        "candidates": []
    }


# =============================================================================
# Atomic Data Tools - Clean identifiers → data
# =============================================================================


@registry.register(category="search")
def search_parts(
    query: str | None = None,
    appliance_type: str | None = None,
    part_type: str | None = None,
    brand: str | None = None,
    max_price: float | None = None,
    in_stock_only: bool = False
) -> list[dict]:
    """
    Search for parts by text query and/or filters.

    Use this when you need to browse or filter parts. For resolving user input
    (PS numbers, URLs, manufacturer numbers, "this part"), use resolve_part() instead.

    Args:
        query: Text search in part name/description (e.g., "ice maker", "water filter")
        appliance_type: Filter by appliance type (e.g., "refrigerator", "dishwasher")
        part_type: Filter by part category (e.g., "Ice Maker Assembly", "Water Filter")
        brand: Filter by brand (e.g., "Whirlpool", "Samsung")
        max_price: Maximum price filter
        in_stock_only: Only return in-stock items

    Returns:
        List of parts with: ps_number, part_name, part_type, part_price,
        average_rating, num_reviews, availability, brand, appliance_type
    """
    db = get_supabase_client()
    return db.search_parts(
        query=query,
        appliance_type=appliance_type,
        part_type=part_type,
        brand=brand,
        max_price=max_price,
        in_stock_only=in_stock_only
    )


@registry.register(category="part")
def get_part(ps_number: str) -> dict:
    """
    Get full details for a part by its PS number.

    Returns all stored information including:
    - Basic: part_name, part_type, manufacturer_part_number, part_manufacturer
    - Pricing: part_price
    - Description: part_description
    - Installation: install_difficulty, install_time, install_video_url
    - Ratings: average_rating, num_reviews
    - Metadata: appliance_type, brand, manufactured_for, availability

    Note: Check the appliance_type field in the result to verify the part is for
    the correct appliance type (e.g., refrigerator, dishwasher).

    Args:
        ps_number: The PS number (e.g., "PS11752778")

    Returns:
        Full part details or error message if not found
    """
    try:
        db = get_supabase_client()
        result = db.get_part_by_ps_number(ps_number)
        if not result:
            return {"error": f"Part {ps_number} not found in database", "ps_number": ps_number}

        # Check if part is for a supported appliance type
        appliance_type = result.get("appliance_type", "").lower() if result.get("appliance_type") else ""
        if appliance_type and appliance_type not in ["refrigerator", "dishwasher"]:
            return {
                "error": f"Part {ps_number} is for a {appliance_type}, not a refrigerator or dishwasher.",
                "ps_number": ps_number,
                "appliance_type": appliance_type,
                "out_of_scope": True
            }

        return result
    except Exception as e:
        return {"error": f"Database error looking up {ps_number}: {str(e)}", "ps_number": ps_number}


@registry.register(category="part")
def check_compatibility(ps_number: str, model_number: str) -> dict:
    """
    Check if a specific part is compatible with an appliance model.

    IMPORTANT: Use this to verify compatibility before recommending a part.
    Wrong parts waste customer money and don't fix the problem.

    Args:
        ps_number: The part's PS number (e.g., "PS11752778")
        model_number: The appliance model number (e.g., "WDT780SAEM1")

    Returns:
        Dictionary with 'compatible' boolean and model details if compatible
    """
    db = get_supabase_client()

    # First check if the part is for a supported appliance type
    part_info = db.get_part_by_ps_number(ps_number)
    if part_info:
        appliance_type = part_info.get("appliance_type", "").lower() if part_info.get("appliance_type") else ""
        if appliance_type and appliance_type not in ["refrigerator", "dishwasher"]:
            return {
                "error": f"Part {ps_number} is for a {appliance_type}, not a refrigerator or dishwasher.",
                "appliance_type": appliance_type,
                "out_of_scope": True
            }

    return db.check_compatibility(ps_number, model_number)


@registry.register(category="part")
def get_compatible_parts(
    model_number: str,
    part_type: str | None = None,
    brand: str | None = None
) -> list[dict]:
    """
    Get all parts compatible with a specific appliance model.

    Use when customer provides their model number and wants to browse available parts.

    Args:
        model_number: The appliance model number (e.g., "WDT780SAEM1")
        part_type: Optional filter by part category
        brand: Optional filter by brand

    Returns:
        List of compatible parts with details
    """
    db = get_supabase_client()
    return db.get_compatible_parts(model_number, part_type, brand)


@registry.register(category="part")
def get_compatible_models(
    ps_number: str,
    brand: str | None = None
) -> list[dict]:
    """
    Get all appliance models that are compatible with a specific part.

    Use when customer has a part number and wants to know which models it fits.
    This is the reverse of get_compatible_parts.

    Args:
        ps_number: The part's PS number (e.g., "PS11752778")
        brand: Optional filter by brand (e.g., "Whirlpool")

    Returns:
        List of compatible models with model_number, brand, and description
    """
    db = get_supabase_client()
    results = db.get_compatible_models(ps_number, brand)

    if not results:
        return {"message": f"No compatible models found for part {ps_number}", "models": []}

    return {
        "part_number": ps_number,
        "compatible_model_count": len(results),
        "models": results
    }


@registry.register(category="symptom")
def get_symptoms(appliance_type: str, symptom: str | None = None) -> list[dict]:
    """
    Get symptom info for an appliance type.

    If symptom is provided, uses LLM matching to find the best matching symptom
    and returns just that one. Otherwise returns all symptoms.

    Args:
        appliance_type: Either "refrigerator" or "dishwasher"
        symptom: Optional symptom description to match (e.g., "ice maker not working")

    Returns:
        List of symptoms (or single matching symptom), including:
        - symptom name and description
        - percentage (how common)
        - related parts
        - difficulty level
        - video URL for troubleshooting
        - symptom_url (link to symptom page)
    """
    db = get_supabase_client()
    return db.get_symptoms(appliance_type, symptom)


@registry.register(category="symptom")
def get_repair_instructions(
    appliance_type: str,
    symptom: str,
    part_type: str | None = None
) -> dict:
    """
    Get step-by-step repair/diagnostic instructions for a symptom.

    Use when helping customer troubleshoot a problem (NOT for installation).
    Returns instructions for each potentially relevant part type.

    IMPORTANT: This tool is for TROUBLESHOOTING SYMPTOMS like "Ice maker not making ice",
    "Dishwasher not draining", etc. It is NOT for installation instructions.
    For installation help, use get_part() to get install_video_url and install_difficulty,
    plus search_qna() and search_repair_stories() for community tips.

    Args:
        appliance_type: Either "refrigerator" or "dishwasher"
        symptom: The problem description (e.g., "Ice maker not making ice")
        part_type: Optional filter for specific part (e.g., "Water Inlet Valve")

    Returns:
        Dictionary with:
        - instructions: List of step-by-step guides per part type
        - video_url: YouTube troubleshooting video
        - symptom_url: PartSelect repair page
        - difficulty: Overall difficulty level
    """
    try:
        db = get_supabase_client()
        result = db.get_repair_instructions(appliance_type, symptom, part_type)
        if not result:
            return {"error": f"No repair instructions found for symptom '{symptom}' on {appliance_type}"}
        return result
    except Exception as e:
        return {"error": f"Error getting repair instructions: {str(e)}"}
