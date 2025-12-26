"""
Tool Registry for agent_v2.

Single source of truth for tool registration. Tools use @registry.register()
decorator to automatically register themselves with metadata.
"""
from dataclasses import dataclass, field
from typing import Callable
from langchain_core.tools import tool as langchain_tool


@dataclass
class ToolMetadata:
    """Metadata about a registered tool."""
    name: str
    description: str
    category: str  # "resolution", "part", "symptom", "search", "vector"


class ToolRegistry:
    """
    Registry for all tools available to the agent.

    Usage:
        @registry.register(category="part")
        def get_part(ps_number: str) -> dict:
            '''Get part details.'''
            ...
    """
    _tools: dict[str, Callable] = field(default_factory=dict)
    _metadata: dict[str, ToolMetadata] = field(default_factory=dict)

    def __init__(self):
        self._tools = {}
        self._metadata = {}

    def register(self, category: str = "part"):
        """
        Decorator to register a tool with the registry.

        Args:
            category: Tool category for documentation grouping
        """
        def decorator(func):
            # Wrap with langchain @tool decorator
            lc_tool = langchain_tool(func)

            # Extract first non-empty line of docstring as description
            description = ""
            if func.__doc__:
                for line in func.__doc__.split('\n'):
                    stripped = line.strip()
                    if stripped:
                        description = stripped
                        break

            # Store in registry
            self._tools[func.__name__] = lc_tool
            self._metadata[func.__name__] = ToolMetadata(
                name=func.__name__,
                description=description,
                category=category,
            )

            return lc_tool
        return decorator

    def get_all_tools(self) -> list:
        """Get all registered tools as a list."""
        return list(self._tools.values())

    def get_tool_map(self) -> dict[str, Callable]:
        """Get tool name -> tool function mapping."""
        return self._tools.copy()

    def generate_tool_docs(self) -> str:
        """
        Auto-generate tool documentation for prompts.

        Groups tools by category with descriptions.
        """
        sections = {
            "resolution": "### Resolution Tools\nUse these first to convert user input into identifiers.",
            "part": "### Part Tools\nRequire a PS number. Use after resolution.",
            "symptom": "### Symptom/Repair Tools\nFor troubleshooting workflows. Don't require PS number.",
            "search": "### Search Tools\nFor browsing/filtering parts.",
            "vector": "### Q&A and Stories\nSemantic search for part-specific Q&A and repair stories. Require PS number.",
        }

        lines = []
        for category in ["resolution", "part", "symptom", "search", "vector"]:
            tools_in_cat = [m for m in self._metadata.values() if m.category == category]
            if tools_in_cat:
                lines.append(sections.get(category, f"### {category.title()} Tools"))
                for meta in tools_in_cat:
                    lines.append(f"- `{meta.name}`: {meta.description}")
                lines.append("")

        return "\n".join(lines)


# Global registry instance
registry = ToolRegistry()
