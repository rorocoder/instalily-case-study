"""
Executor Node

Handles simple queries with a tool-calling agent.
Uses ReAct pattern for single-task execution.
"""
import json
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from langgraph.prebuilt import create_react_agent
from backend.config import get_settings
from backend.agent.state import AgentState, SessionState
from backend.agent.prompts import EXECUTOR_PROMPT
from backend.tools import get_all_tools


def update_session_from_messages(session: SessionState, messages: list) -> SessionState:
    """
    Extract discussed parts and appliance info from tool results and update session.
    """
    # First, extract tool call arguments to get params for each tool
    tool_params = {}
    for msg in messages:
        if hasattr(msg, 'tool_calls') and msg.tool_calls:
            for tc in msg.tool_calls:
                tool_id = tc.get('id', '')
                tool_params[tool_id] = {
                    'name': tc.get('name', ''),
                    'args': tc.get('args', {})
                }

    for msg in messages:
        # Look for tool results (type='tool' messages)
        if hasattr(msg, 'type') and msg.type == 'tool':
            try:
                # Get tool name and params
                tool_name = getattr(msg, 'name', '')
                tool_call_id = getattr(msg, 'tool_call_id', '')
                params = tool_params.get(tool_call_id, {}).get('args', {})

                # Parse the tool result content
                content = msg.content
                if isinstance(content, str):
                    try:
                        data = json.loads(content)
                    except json.JSONDecodeError:
                        continue
                else:
                    data = content

                # Handle list of parts (e.g., from search_parts, get_symptoms)
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict):
                            _extract_and_add_part_from_executor(session, item, tool_name, params)
                # Handle single dict (e.g., from get_part)
                elif isinstance(data, dict):
                    _extract_and_add_part_from_executor(session, data, tool_name, params)

            except Exception:
                # Don't fail on parsing errors
                continue

    return session


def _extract_and_add_part_from_executor(session: SessionState, item: dict, tool_name: str | None = None, params: dict | None = None) -> None:
    """
    Extract ps_number and appliance_type from a result item and add to session.
    Also tracks symptoms when get_symptoms tool is used.
    """
    params = params or {}

    ps_number = item.get('ps_number')
    appliance_type = item.get('appliance_type')

    # Also check nested structures
    if not ps_number and 'part_info' in item:
        ps_number = item['part_info'].get('ps_number')
        appliance_type = item['part_info'].get('appliance_type')

    # Update session if we found a part that's in scope
    if ps_number:
        # Check if the result indicates out of scope
        is_out_of_scope = item.get('out_of_scope', False)

        # Only add to session if it's a refrigerator or dishwasher part
        if appliance_type in ['refrigerator', 'dishwasher'] and not is_out_of_scope:
            session.add_discussed_part(ps_number, appliance_type)
            session.current_focus = appliance_type
        elif not appliance_type and not is_out_of_scope:
            # If no appliance type specified but not out of scope, still track it
            session.add_discussed_part(ps_number, appliance_type)

    # Track symptoms from get_symptoms results
    symptom = item.get('symptom')
    if symptom:
        # Get appliance_type from result or from tool params (get_symptoms passes appliance_type as param)
        symptom_appliance = appliance_type
        if not symptom_appliance and tool_name == 'get_symptoms':
            symptom_appliance = params.get('appliance_type')

        if symptom_appliance in ['refrigerator', 'dishwasher']:
            session.current_focus = symptom_appliance
            ctx = session.appliances.get(symptom_appliance)
            if ctx:
                ctx.current_symptom = symptom


def format_session_context(state: AgentState) -> str:
    """Format session state for the executor prompt."""
    session = state.session
    parts = []

    # Include conversation history for context (CRITICAL for follow-up questions)
    if state.conversation_history:
        parts.append("## Recent Conversation")
        for msg in state.conversation_history[-4:]:  # Last 2 exchanges
            role = "User" if msg.role == "user" else "Assistant"
            content = msg.content[:300] + "..." if len(msg.content) > 300 else msg.content
            parts.append(f"{role}: {content}")
        parts.append("")  # Blank line

    # Show the current/last discussed part prominently
    if session.all_discussed_parts:
        current_part = session.all_discussed_parts[-1]
        parts.append(f"**Current part being discussed: {current_part}**")
        parts.append(f"(When user says 'this part', 'it', etc., they mean {current_part})")

    if session.current_focus:
        parts.append(f"Appliance type: {session.current_focus}")
        ctx = session.get_current_appliance()
        if ctx:
            if ctx.model_number:
                parts.append(f"User's model: {ctx.model_number}")
            if ctx.brand:
                parts.append(f"Brand: {ctx.brand}")
            if ctx.current_symptom:
                parts.append(f"**Current symptom being discussed: {ctx.current_symptom}**")
                parts.append(f"(Use this symptom with get_repair_instructions when user asks about checking a specific part)")

    if session.all_discussed_parts and len(session.all_discussed_parts) > 1:
        parts.append(f"Previously discussed parts: {', '.join(session.all_discussed_parts[-5:-1])}")

    if not parts:
        return "No previous context in this session. User has not discussed any parts yet."

    return "\n".join(parts)


async def executor_node(state: AgentState) -> dict:
    """
    Executor node - handles simple single-task queries.

    Uses a ReAct agent pattern with tool calling for straightforward queries.
    """
    from backend.agent.logging import log_node_start, log_node_result, log_tool_call, log_tool_result

    log_node_start("EXECUTOR", state.user_query)

    settings = get_settings()

    # Create the LLM with tool binding
    llm = ChatAnthropic(
        model=settings.HAIKU_MODEL,
        api_key=settings.ANTHROPIC_API_KEY,
        max_tokens=4096,
    )

    # Get all tools
    tools = get_all_tools()

    # Create a simple ReAct agent
    agent = create_react_agent(llm, tools)

    # Format the prompt with context
    session_context = format_session_context(state)
    system_prompt = EXECUTOR_PROMPT.format(
        session_context=session_context,
        query=state.user_query
    )

    # Build messages list with conversation history
    messages = [SystemMessage(content=system_prompt)]

    # Add conversation history if present
    if state.conversation_history:
        for msg in state.conversation_history:
            if msg.role == "user":
                messages.append(HumanMessage(content=msg.content))
            else:
                messages.append(AIMessage(content=msg.content))

    # Add current query
    messages.append(HumanMessage(content=state.user_query))

    # Run the agent
    result = await agent.ainvoke({"messages": messages})

    # Extract the final response and log tool calls
    messages = result.get("messages", [])

    # Log tool calls and results
    tool_calls_count = 0
    for msg in messages:
        if hasattr(msg, 'tool_calls') and msg.tool_calls:
            for tc in msg.tool_calls:
                log_tool_call(tc.get('name', 'unknown'), tc.get('args', {}))
                tool_calls_count += 1
        if hasattr(msg, 'type') and msg.type == 'tool':
            log_tool_result(getattr(msg, 'name', 'unknown'), msg.content)

    final_message = messages[-1] if messages else None

    executor_result = {
        "messages": messages,
        "final_content": final_message.content if final_message else None
    }

    # Update session with discussed parts from tool results
    updated_session = update_session_from_messages(state.session, messages)

    log_node_result("EXECUTOR", {
        "tool_calls": tool_calls_count,
        "has_response": bool(final_message),
        "discussed_parts": updated_session.all_discussed_parts,
    })

    return {
        "executor_result": executor_result,
        "session": updated_session,
    }
