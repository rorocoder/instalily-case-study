"""
Synthesizer Node

Combines results from executor or workers into a coherent response.
Uses the more capable Sonnet model for quality output.
"""
import json
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage
from backend.config import get_settings
from backend.agent.state import AgentState
from backend.agent.prompts import SYNTHESIZER_PROMPT


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
    session = state.session
    parts = []

    # Add conversation history first
    history = format_conversation_history(state)
    if history:
        parts.append(history)

    if session.current_focus:
        parts.append(f"User is asking about their {session.current_focus}")
        ctx = session.get_current_appliance()
        if ctx:
            if ctx.model_number:
                parts.append(f"User's model: {ctx.model_number}")
            if ctx.brand:
                parts.append(f"Brand: {ctx.brand}")

    return "\n".join(parts) if parts else "No specific appliance context."


def extract_primary_part(state: AgentState) -> dict | None:
    """
    Extract primary part data from tool results.

    Looks for get_part() tool calls and returns the first part found.
    Returns dict with part fields or None if no part found.
    """
    # Check executor result for get_part calls
    if state.executor_result and isinstance(state.executor_result, dict):
        messages = state.executor_result.get("messages", [])
        print(f"[DEBUG] extract_primary_part: Found {len(messages)} messages in executor_result")
        for i, msg in enumerate(messages):
            print(f"[DEBUG] Message {i}: type={getattr(msg, 'type', 'NO_TYPE')}, has_name={hasattr(msg, 'name')}, name={getattr(msg, 'name', 'NO_NAME')}")
            if hasattr(msg, 'type') and msg.type == 'tool' and hasattr(msg, 'name'):
                # Look for get_part tool calls
                if msg.name == 'get_part':
                    # Parse content - might be string or dict
                    content = msg.content
                    if isinstance(content, str):
                        try:
                            part_data = json.loads(content)
                        except json.JSONDecodeError:
                            continue
                    else:
                        part_data = content

                    if isinstance(part_data, dict):
                        # Extract only the fields we need for the card
                        return {
                            "ps_number": part_data.get("ps_number", ""),
                            "part_name": part_data.get("part_name", ""),
                            "manufacturer_part_number": part_data.get("manufacturer_part_number"),
                            "part_price": part_data.get("part_price", 0.0),
                            "average_rating": part_data.get("average_rating"),
                            "num_reviews": part_data.get("num_reviews"),
                            "brand": part_data.get("brand", ""),
                            "availability": part_data.get("availability", "Unknown"),
                            "part_url": part_data.get("part_url", ""),
                            "image_url": None  # Placeholder for now
                        }

    # Check worker results for get_part calls
    if state.worker_results:
        for result in state.worker_results:
            if isinstance(result, dict) and result.get("result"):
                res = result["result"]
                # Check if this is a part dict (has ps_number field)
                if isinstance(res, dict) and "ps_number" in res:
                    return {
                        "ps_number": res.get("ps_number", ""),
                        "part_name": res.get("part_name", ""),
                        "manufacturer_part_number": res.get("manufacturer_part_number"),
                        "part_price": res.get("part_price", 0.0),
                        "average_rating": res.get("average_rating"),
                        "num_reviews": res.get("num_reviews"),
                        "brand": res.get("brand", ""),
                        "availability": res.get("availability", "Unknown"),
                        "part_url": res.get("part_url", ""),
                        "image_url": None  # Placeholder for now
                    }

    return None


def format_results(state: AgentState) -> str:
    """Format execution results for the synthesizer."""
    parts = []

    # Check if we have executor result (simple query)
    if state.executor_result:
        parts.append("## Executor Result (Simple Query)")

        if isinstance(state.executor_result, dict):
            # Extract the final content and tool results
            final_content = state.executor_result.get("final_content")
            messages = state.executor_result.get("messages", [])

            # Look for tool results in messages
            for msg in messages:
                if hasattr(msg, 'type') and msg.type == 'tool':
                    parts.append(f"\n### Tool: {msg.name}")
                    parts.append(f"```json\n{json.dumps(msg.content, indent=2) if isinstance(msg.content, (dict, list)) else msg.content}\n```")

            if final_content:
                parts.append(f"\n### Agent's Analysis:\n{final_content}")
        else:
            parts.append(str(state.executor_result))

    # Check if we have worker results (complex query)
    if state.worker_results:
        parts.append("\n## Worker Results (Complex Query)")

        if state.plan and state.plan.synthesis_hint:
            parts.append(f"\n**Synthesis guidance:** {state.plan.synthesis_hint}")

        for i, result in enumerate(state.worker_results, 1):
            if isinstance(result, dict):
                desc = result.get("description", f"Subtask {i}")
                parts.append(f"\n### {desc}")

                if result.get("error"):
                    parts.append(f"Error: {result['error']}")
                elif result.get("result") is not None:
                    res = result["result"]
                    if isinstance(res, (dict, list)):
                        parts.append(f"```json\n{json.dumps(res, indent=2)}\n```")
                    else:
                        parts.append(str(res))
            else:
                parts.append(f"\n### Subtask {i}\n{result}")

    if not parts:
        parts.append("No results available.")

    return "\n".join(parts)


async def synthesizer_node(state: AgentState) -> dict:
    """
    Synthesizer node - creates the final response.

    Combines all gathered information into a helpful, well-formatted response.
    Uses Sonnet for higher quality synthesis.
    """
    from backend.agent.logging import log_node_start, log_node_result, log_flow_complete

    log_node_start("SYNTHESIZER")

    settings = get_settings()

    llm = ChatAnthropic(
        model=settings.SONNET_MODEL,
        api_key=settings.ANTHROPIC_API_KEY,
        max_tokens=2048,
    )

    session_context = format_session_context(state)
    results = format_results(state)

    print(f"  Using model: {settings.SONNET_MODEL}")
    print(f"  Input data size: {len(results)} chars")

    prompt = SYNTHESIZER_PROMPT.format(
        query=state.user_query,
        session_context=session_context,
        results=results
    )

    response = await llm.ainvoke([HumanMessage(content=prompt)])

    # Extract primary part if available
    primary_part = extract_primary_part(state)

    log_node_result("SYNTHESIZER", {
        "response_length": len(response.content),
        "has_part_card": primary_part is not None,
    })
    log_flow_complete(response.content)

    return {
        "final_response": response.content,
        "primary_part": primary_part
    }


async def synthesizer_node_streaming(state: AgentState):
    """
    Streaming version of synthesizer - yields tokens as they're generated.

    Use this for the streaming endpoint.
    """
    from backend.agent.logging import log_node_start, log_flow_complete

    log_node_start("SYNTHESIZER (streaming)")

    settings = get_settings()

    llm = ChatAnthropic(
        model=settings.SONNET_MODEL,
        api_key=settings.ANTHROPIC_API_KEY,
        max_tokens=2048,
    )

    session_context = format_session_context(state)
    results = format_results(state)

    print(f"  Using model: {settings.SONNET_MODEL}")
    print(f"  Input data size: {len(results)} chars")
    print(f"  Streaming response...")

    prompt = SYNTHESIZER_PROMPT.format(
        query=state.user_query,
        session_context=session_context,
        results=results
    )

    full_response = ""
    async for chunk in llm.astream([HumanMessage(content=prompt)]):
        if chunk.content:
            full_response += chunk.content
            yield chunk.content

    log_flow_complete(full_response)
