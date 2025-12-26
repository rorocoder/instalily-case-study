"""
Tools for agent_v2.

Exports tool access functions from the registry.
"""
# Import tools to register them with the registry
from backend.agent_v2.tools import sql_tools  # noqa: F401
from backend.agent_v2.tools import vector_tools  # noqa: F401
from backend.agent_v2.tools import scrape_tools  # noqa: F401

# Export registry functions
from backend.agent_v2.tools.registry import registry


def get_all_tools() -> list:
    """Get all registered tools."""
    return registry.get_all_tools()


def get_tool_map() -> dict:
    """Get tool name -> tool function mapping."""
    return registry.get_tool_map()


def get_tool_docs() -> str:
    """Get auto-generated tool documentation for prompts."""
    return registry.generate_tool_docs()
