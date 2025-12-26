"""
Scope Check Node

Determines if a query is about refrigerators/dishwashers (in scope)
or something else (out of scope).

Uses fast rules first, then falls back to LLM if needed.
"""
import re
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage
from backend.config import get_settings
from backend.agent.state import AgentState
from backend.agent.prompts import SCOPE_CHECK_PROMPT, OUT_OF_SCOPE_RESPONSE


# Keywords that strongly indicate in-scope queries
IN_SCOPE_KEYWORDS = [
    # Appliance types
    r"\brefrigerator\b", r"\bfridge\b", r"\bdishwasher\b",
    r"\bfreezer\b", r"\bice\s*maker\b",
    # Part-related
    r"\bps\d+\b",  # PS numbers like PS11752778
    r"\bpart\s*(number|#)?\b",
    r"\bcompatib(le|ility)\b",
    r"\binstall(ation)?\b",
    r"\breplace(ment)?\b",
    # Common parts
    r"\bwater\s*filter\b", r"\bice\s*maker\b", r"\bdoor\s*(bin|shelf|gasket)\b",
    r"\bcompressor\b", r"\bthermostat\b", r"\bdefrost\b",
    r"\bdrain\s*(pump|hose)\b", r"\bspray\s*arm\b", r"\brack\b",
    # Symptoms
    r"\bleaking\b", r"\bnot\s*(cooling|freezing|working|draining)\b",
    r"\bnoisy\b", r"\bwon'?t\s*(start|run|drain)\b",
    # Brands (common refrigerator/dishwasher brands)
    r"\bwhirlpool\b", r"\bge\b", r"\bsamsung\b", r"\blg\b",
    r"\bkitchenaid\b", r"\bmaytag\b", r"\bfrigidaire\b", r"\bbosch\b",
    r"\bkenmore\b",
]

# Keywords that strongly indicate out-of-scope queries
OUT_OF_SCOPE_KEYWORDS = [
    r"\bwashing\s*machine\b", r"\bwasher\b(?!\s*(dish))",  # washer but not dishwasher
    r"\bdryer\b", r"\boven\b", r"\bstove\b", r"\bmicrowave\b",
    r"\bair\s*condition(er|ing)?\b", r"\bhvac\b",
    r"\bweather\b", r"\bnews\b", r"\bsports\b",
]


def rule_based_scope_check(query: str) -> str | None:
    """
    Fast rule-based check for obvious cases.

    Returns:
        "IN_SCOPE", "OUT_OF_SCOPE", or None if unclear
    """
    query_lower = query.lower()

    # Check for strong out-of-scope signals first
    for pattern in OUT_OF_SCOPE_KEYWORDS:
        if re.search(pattern, query_lower):
            return "OUT_OF_SCOPE"

    # Check for in-scope signals
    for pattern in IN_SCOPE_KEYWORDS:
        if re.search(pattern, query_lower, re.IGNORECASE):
            return "IN_SCOPE"

    # Unclear - need LLM
    return None


async def llm_scope_check(query: str, conversation_history: list = None) -> str:
    """Use LLM for ambiguous queries, with conversation context."""
    settings = get_settings()

    llm = ChatAnthropic(
        model=settings.HAIKU_MODEL,
        api_key=settings.ANTHROPIC_API_KEY,
        max_tokens=10,
    )

    # Build context from conversation history
    context = ""
    if conversation_history:
        context = "\n\nRecent conversation (for context):\n"
        for msg in conversation_history[-4:]:  # Last 2 exchanges
            role = "User" if msg.role == "user" else "Assistant"
            # Truncate long messages
            content = msg.content[:500] + "..." if len(msg.content) > 500 else msg.content
            context += f"{role}: {content}\n"
        context += "\nIf the current query is a follow-up to this conversation about refrigerators/dishwashers, it's IN_SCOPE.\n"

    prompt = SCOPE_CHECK_PROMPT.format(query=query) + context
    response = await llm.ainvoke([HumanMessage(content=prompt)])

    result = response.content.strip().upper()

    if "IN_SCOPE" in result:
        return "IN_SCOPE"
    return "OUT_OF_SCOPE"


async def scope_check_node(state: AgentState) -> dict:
    """
    Scope check node - determines if query is about fridges/dishwashers.

    First tries fast rule-based check, falls back to LLM if needed.
    LLM gets conversation history so it can understand follow-up queries.
    """
    from backend.agent.logging import log_node_start, log_node_result, log_decision

    log_node_start("SCOPE CHECK", state.user_query)

    query = state.user_query
    session = state.session

    # Try rules first (fast) - only for clear-cut cases
    result = rule_based_scope_check(query)
    method = "rules"

    # Fall back to LLM if unclear - LLM gets conversation context to understand follow-ups
    if result is None:
        log_decision("Rules inconclusive, using LLM")
        result = await llm_scope_check(query, session.conversation_history)
        method = "llm"

    if result == "IN_SCOPE":
        log_node_result("SCOPE CHECK", {"result": "IN_SCOPE", "method": method})
        log_decision("Proceeding to Planner", f"Query is about refrigerators/dishwashers (via {method})")
        return {
            "is_in_scope": True,
            "scope_rejection_message": None,
        }
    else:
        log_node_result("SCOPE CHECK", {"result": "OUT_OF_SCOPE", "method": method})
        log_decision("Rejecting query", "Not about refrigerators or dishwashers")
        return {
            "is_in_scope": False,
            "scope_rejection_message": OUT_OF_SCOPE_RESPONSE,
            "final_response": OUT_OF_SCOPE_RESPONSE,
        }
