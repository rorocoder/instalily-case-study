"""
Tool definitions and schemas for the agent system.
"""
from .sql_tools import (
    # Resolution tools
    resolve_part,
    resolve_model,
    # Atomic data tools
    search_parts,
    get_part,
    check_compatibility,
    get_compatible_parts,
    get_compatible_models,
    get_symptoms,
    get_repair_instructions,
)
from .vector_tools import (
    search_qna,
    search_repair_stories,
)


def get_all_tools() -> list:
    """
    Get all tools available to the agent system.

    Returns a list of LangChain tools that can be bound to an LLM.
    """
    return [
        # Resolution tools - parse messy input → clean identifiers
        resolve_part,
        resolve_model,
        # Part search and lookup
        search_parts,
        get_part,
        # Compatibility tools
        check_compatibility,
        get_compatible_parts,
        get_compatible_models,
        # Repair/troubleshooting tools
        get_symptoms,
        get_repair_instructions,
        # Semantic search tools
        search_qna,
        search_repair_stories,
    ]


def get_tool_descriptions() -> str:
    """Get formatted descriptions of all tools for prompts."""
    tools = get_all_tools()
    descriptions = []

    for tool in tools:
        desc = f"- **{tool.name}**: {tool.description.split(chr(10))[0]}"
        descriptions.append(desc)

    return "\n".join(descriptions)
