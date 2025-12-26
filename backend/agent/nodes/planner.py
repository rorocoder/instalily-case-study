"""
Planner Node

Analyzes query complexity and creates execution plan.
Routes simple queries to executor, complex queries to parallel workers.
"""
import json
import re
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage
from backend.config import get_settings
from backend.agent.state import AgentState, PlannerOutput, Subtask
from backend.agent.prompts import PLANNER_PROMPT


def format_session_context(state: AgentState) -> str:
    """Format session state for the prompt."""
    session = state.session
    parts = []

    # Include conversation history for context
    if state.conversation_history:
        parts.append("## Recent Conversation")
        for msg in state.conversation_history[-4:]:  # Last 2 exchanges
            role = "User" if msg.role == "user" else "Assistant"
            content = msg.content[:300] + "..." if len(msg.content) > 300 else msg.content
            parts.append(f"{role}: {content}")
        parts.append("")  # Blank line

    # Show discussed parts with clear references
    if session.all_discussed_parts:
        parts.append("## Parts from Previous Response")
        # First part is the top/best recommendation (search results are sorted by rating)
        first_part = session.all_discussed_parts[0]
        parts.append(f"**Top/first recommendation: {first_part}** (use this for 'top recommendation', 'best', 'first one')")

        # Last part is the most recently mentioned
        if len(session.all_discussed_parts) > 1:
            last_part = session.all_discussed_parts[-1]
            parts.append(f"**Most recently mentioned: {last_part}** (use this for 'this part', 'it', 'last one')")

            # Show full list if multiple parts
            parts.append(f"All discussed parts (in order): {', '.join(session.all_discussed_parts[:10])}")

    if session.current_focus:
        parts.append(f"Appliance type: {session.current_focus}")

    for appliance_type, ctx in session.appliances.items():
        if ctx.model_number or ctx.current_symptom:
            if ctx.model_number:
                parts.append(f"User's {appliance_type} model: {ctx.model_number}")
            if ctx.brand:
                parts.append(f"Brand: {ctx.brand}")
            if ctx.current_symptom:
                parts.append(f"Current symptom: {ctx.current_symptom}")

    if not parts:
        return "No previous context. User has not discussed any parts yet."

    return "\n".join(parts)


def parse_planner_response(response: str) -> PlannerOutput:
    """Parse the LLM response into a PlannerOutput."""
    # Try to extract JSON from the response
    try:
        # Find JSON in the response
        json_match = re.search(r'\{[\s\S]*\}', response)
        if json_match:
            data = json.loads(json_match.group())

            query_type = data.get("query_type", "simple")
            if query_type not in ["simple", "complex", "out_of_scope"]:
                query_type = "simple"

            subtasks = []
            for st in data.get("subtasks", []):
                # Only add subtasks that have a valid tool name
                tool_name = st.get("tool")
                if tool_name and isinstance(tool_name, str) and tool_name.strip():
                    subtasks.append(Subtask(
                        description=st.get("description", ""),
                        tool=tool_name.strip(),
                        params=st.get("params", {}) or {}
                    ))

            # If we have complex type but no valid subtasks, fall back to simple
            if query_type == "complex" and not subtasks:
                query_type = "simple"

            return PlannerOutput(
                query_type=query_type,
                reasoning=data.get("reasoning"),
                subtasks=subtasks,
                synthesis_hint=data.get("synthesis_hint")
            )
    except json.JSONDecodeError:
        pass

    # Default to simple if parsing fails
    return PlannerOutput(
        query_type="simple",
        reasoning="Failed to parse planner response, defaulting to simple execution"
    )


async def planner_node(state: AgentState) -> dict:
    """
    Planner node - analyzes query and creates execution plan.

    Returns plan with:
    - query_type: "simple" or "complex"
    - subtasks: list of tool calls for complex queries
    - synthesis_hint: guidance for the synthesizer
    """
    from backend.agent.logging import log_node_start, log_node_result, log_decision

    log_node_start("PLANNER", state.user_query)

    settings = get_settings()

    llm = ChatAnthropic(
        model=settings.HAIKU_MODEL,
        api_key=settings.ANTHROPIC_API_KEY,
        max_tokens=1000,
    )

    session_context = format_session_context(state)

    prompt = PLANNER_PROMPT.format(
        query=state.user_query,
        session_context=session_context
    )

    response = await llm.ainvoke([HumanMessage(content=prompt)])
    plan = parse_planner_response(response.content)

    # Log the plan
    log_node_result("PLANNER", {
        "query_type": plan.query_type,
        "reasoning": plan.reasoning,
        "num_subtasks": len(plan.subtasks),
    })

    if plan.query_type == "complex":
        log_decision(f"Route to WORKERS ({len(plan.subtasks)} parallel tasks)")
        for i, st in enumerate(plan.subtasks):
            print(f"    Subtask {i+1}: {st.tool}({st.params})")
        if plan.synthesis_hint:
            print(f"    Synthesis hint: {plan.synthesis_hint}")
    else:
        log_decision("Route to EXECUTOR (simple query)")

    return {"plan": plan}
