"""
Synthesizer Node for agent_v2.

Takes executor results and generates the final response.
Uses Sonnet for higher quality synthesis.
"""
import json
import re
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage
from backend.config import get_settings
from backend.agent_v2.state import AgentState
from backend.agent_v2.prompts import format_synthesizer_prompt


def extract_mentioned_ps_numbers(response: str) -> set[str]:
    """Extract PS numbers mentioned in the synthesizer response.

    Used to filter part cards to only show parts that were actually
    recommended in the response text.
    """
    # Match PS followed by any digits (e.g., PS382661, PS8760080, PS11752778)
    pattern = r'PS\d+'
    matches = re.findall(pattern, response, re.IGNORECASE)
    return {m.upper() for m in matches}


def format_conversation_history(state: AgentState) -> str:
    """Format conversation history for context."""
    if not state.conversation_history:
        return ""

    parts = ["## Previous Conversation\n"]
    for msg in state.conversation_history[-6:]:  # Last 3 exchanges max
        role = "User" if msg.role == "user" else "Assistant"
        # Truncate long messages
        content = msg.content[:500] + "..." if len(msg.content) > 500 else msg.content
        parts.append(f"**{role}:** {content}\n")

    return "\n".join(parts)


def format_session_context(state: AgentState) -> str:
    """Format session state for the synthesizer."""
    # Just include conversation history for context
    history = format_conversation_history(state)
    return history if history else "No prior context."


def extract_parts(state: AgentState) -> list[dict]:
    """
    Extract all parts from tool results.

    Looks at ALL tool results and extracts any objects that look like parts
    (have ps_number, part_name, part_price fields).
    Returns list of dicts with part fields (empty list if none found).
    """
    if not state.executor_result or not isinstance(state.executor_result, dict):
        return []

    messages = state.executor_result.get("messages", [])
    parts = []
    seen_ps_numbers = set()  # Avoid duplicates

    def try_extract_part(item: dict) -> dict | None:
        """Try to extract part card data from a dict if it looks like a part."""
        if not isinstance(item, dict):
            return None
        if item.get("error"):
            return None

        # Must have ps_number and part_name to be a valid part
        ps_number = item.get("ps_number")
        part_name = item.get("part_name")
        if not ps_number or not part_name:
            return None

        return {
            "ps_number": ps_number,
            "part_name": part_name,
            "manufacturer_part_number": item.get("manufacturer_part_number"),
            "part_price": item.get("part_price", 0.0),
            "average_rating": item.get("average_rating"),
            "num_reviews": item.get("num_reviews"),
            "brand": item.get("brand", ""),
            "availability": item.get("availability", "Unknown"),
            "part_url": item.get("part_url", ""),
            "image_url": None
        }

    for msg in messages:
        if not (hasattr(msg, 'type') and msg.type == 'tool'):
            continue

        # Parse content
        content = msg.content
        if isinstance(content, str):
            try:
                data = json.loads(content)
            except json.JSONDecodeError:
                continue
        else:
            data = content

        # Handle list results (from get_compatible_parts, search_parts, etc.)
        if isinstance(data, list):
            for item in data:
                part = try_extract_part(item)
                if part and part["ps_number"] not in seen_ps_numbers:
                    seen_ps_numbers.add(part["ps_number"])
                    parts.append(part)
        # Handle single dict results (from get_part, etc.)
        elif isinstance(data, dict):
            part = try_extract_part(data)
            if part and part["ps_number"] not in seen_ps_numbers:
                seen_ps_numbers.add(part["ps_number"])
                parts.append(part)

    return parts


def format_results(state: AgentState) -> str:
    """Format execution results for the synthesizer."""
    parts = []

    if not state.executor_result:
        parts.append("No results available.")
        return "\n".join(parts)

    parts.append("## Executor Results")

    if isinstance(state.executor_result, dict):
        messages = state.executor_result.get("messages", [])

        # Look for tool results in messages
        for msg in messages:
            if hasattr(msg, 'type') and msg.type == 'tool':
                tool_name = getattr(msg, 'name', 'unknown')
                parts.append(f"\n### Tool: {tool_name}")

                content = msg.content
                if isinstance(content, str):
                    try:
                        parsed = json.loads(content)
                        parts.append(f"```json\n{json.dumps(parsed, indent=2)}\n```")
                    except json.JSONDecodeError:
                        parts.append(content)
                elif isinstance(content, (dict, list)):
                    parts.append(f"```json\n{json.dumps(content, indent=2)}\n```")
                else:
                    parts.append(str(content))

        # Get final AI message if present
        for msg in reversed(messages):
            if hasattr(msg, 'type') and msg.type == 'ai' and hasattr(msg, 'content'):
                if msg.content and not getattr(msg, 'tool_calls', None):
                    parts.append(f"\n### Agent's Analysis:\n{msg.content}")
                    break
    else:
        parts.append(str(state.executor_result))

    return "\n".join(parts)


async def synthesizer_node(state: AgentState) -> dict:
    """
    Synthesizer node - creates the final response.

    Combines all gathered information into a helpful, well-formatted response.
    Uses Sonnet for higher quality synthesis.
    """
    settings = get_settings()

    print(f"  [SYNTHESIZER] Generating response...")

    llm = ChatAnthropic(
        model=settings.SONNET_MODEL,
        api_key=settings.ANTHROPIC_API_KEY,
        max_tokens=2048,
    )

    session_context = format_session_context(state)
    results = format_results(state)

    print(f"  [SYNTHESIZER] Using model: {settings.SONNET_MODEL}")
    print(f"  [SYNTHESIZER] Input data size: {len(results)} chars")

    prompt = format_synthesizer_prompt(
        query=state.user_query,
        session_context=session_context,
        results=results
    )

    response = await llm.ainvoke([HumanMessage(content=prompt)])

    # Extract all parts from tool results
    all_parts = extract_parts(state)

    # Filter to only parts mentioned in the response
    mentioned_ps_numbers = extract_mentioned_ps_numbers(response.content)

    if mentioned_ps_numbers:
        # Only show parts that were explicitly mentioned
        parts = [p for p in all_parts if p["ps_number"] in mentioned_ps_numbers]
    else:
        # If no PS numbers mentioned, don't show any part cards
        # (response is probably about symptoms, not specific parts)
        parts = []

    print(f"  [SYNTHESIZER] Response length: {len(response.content)} chars")
    print(f"  [SYNTHESIZER] Part cards: {len(parts)} (filtered from {len(all_parts)} total)")

    return {
        "final_response": response.content,
        "parts": parts
    }


async def synthesizer_node_streaming(state: AgentState):
    """
    Streaming version of synthesizer - yields tokens as they're generated.

    Use this for the streaming endpoint.
    """
    settings = get_settings()

    print(f"  [SYNTHESIZER] Streaming response...")

    llm = ChatAnthropic(
        model=settings.SONNET_MODEL,
        api_key=settings.ANTHROPIC_API_KEY,
        max_tokens=2048,
    )

    session_context = format_session_context(state)
    results = format_results(state)

    print(f"  [SYNTHESIZER] Using model: {settings.SONNET_MODEL}")
    print(f"  [SYNTHESIZER] Input data size: {len(results)} chars")

    prompt = format_synthesizer_prompt(
        query=state.user_query,
        session_context=session_context,
        results=results
    )

    full_response = ""
    async for chunk in llm.astream([HumanMessage(content=prompt)]):
        if chunk.content:
            full_response += chunk.content
            yield chunk.content

    print(f"  [SYNTHESIZER] Streamed {len(full_response)} chars")
