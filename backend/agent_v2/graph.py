"""
LangGraph for agent_v2.

Simplified 3-node graph:
  scope_check → executor → synthesizer

No planner or workers - the executor handles everything via ReAct.
"""
from typing import Literal, AsyncGenerator
from langgraph.graph import StateGraph, END
from backend.agent_v2.state import AgentState, SessionState
from backend.agent_v2.nodes import (
    scope_check_node,
    secondary_scope_check_node,
    executor_node,
    synthesizer_node,
    synthesizer_node_streaming,
)


def route_after_scope_check(state: AgentState) -> Literal["executor", "end"]:
    """Route based on scope check result."""
    if state.is_in_scope:
        return "executor"
    return "end"


def route_after_secondary_scope_check(state: AgentState) -> Literal["synthesizer", "end"]:
    """Route based on secondary scope check result."""
    if state.has_out_of_scope_parts:
        return "end"
    return "synthesizer"


def create_graph() -> StateGraph:
    """
    Create the simplified agent_v2 graph.

    Graph structure:
    ```
    START → scope_check → [in_scope?]
                              │
              ┌───────────────┴───────────────┐
              │                               │
         (out of scope)                  (in scope)
              │                               │
              ▼                               ▼
             END                          executor
                                              │
                                              ▼
                                         synthesizer
                                              │
                                              ▼
                                             END
    ```
    """
    # Create the graph with our state type
    workflow = StateGraph(AgentState)

    # Add nodes
    workflow.add_node("scope_check", scope_check_node)
    workflow.add_node("executor", executor_node)
    workflow.add_node("secondary_scope_check", secondary_scope_check_node)
    workflow.add_node("synthesizer", synthesizer_node)

    # Set entry point
    workflow.set_entry_point("scope_check")

    # Add conditional edge after scope check
    workflow.add_conditional_edges(
        "scope_check",
        route_after_scope_check,
        {
            "executor": "executor",
            "end": END,
        }
    )

    # executor → secondary_scope_check
    workflow.add_edge("executor", "secondary_scope_check")

    # Add conditional edge after secondary scope check
    workflow.add_conditional_edges(
        "secondary_scope_check",
        route_after_secondary_scope_check,
        {
            "synthesizer": "synthesizer",
            "end": END,
        }
    )

    # synthesizer → END
    workflow.add_edge("synthesizer", END)

    return workflow.compile()


async def run_agent(
    query: str,
    session: SessionState | None = None,
) -> tuple[str, SessionState, list[dict]]:
    """
    Run the agent graph on a query.

    Args:
        query: User's question
        session: Optional session state from previous turns (includes conversation history)

    Returns:
        Tuple of (response_text, updated_session, parts_list)
    """
    print(f"\n{'='*60}")
    print(f"[AGENT V2] NEW QUERY: {query[:50]}{'...' if len(query) > 50 else ''}")
    print(f"{'='*60}")

    current_session = session or SessionState()

    # Log session state
    if current_session.all_discussed_parts:
        print(f"  Session parts: {current_session.all_discussed_parts}")

    graph = create_graph()

    # Initialize state - conversation history comes from session
    initial_state = AgentState(
        user_query=query,
        session=current_session,
        conversation_history=current_session.conversation_history,
    )

    # Run the graph
    result = await graph.ainvoke(initial_state)

    # Extract results
    response = result.get("final_response", "")
    if not response and result.get("scope_rejection_message"):
        response = result["scope_rejection_message"]

    updated_session = result.get("session", SessionState())
    parts = result.get("parts", [])

    # Filter session's discussed parts to only those mentioned in response
    from backend.agent_v2.nodes.synthesizer import extract_mentioned_ps_numbers
    mentioned_ps_numbers = extract_mentioned_ps_numbers(response)
    if mentioned_ps_numbers:
        updated_session.all_discussed_parts = [
            ps for ps in updated_session.all_discussed_parts
            if ps in mentioned_ps_numbers
        ]
    else:
        # If no parts mentioned, clear the list (response was about symptoms, etc.)
        updated_session.all_discussed_parts = []

    print(f"\n[AGENT V2] Response length: {len(response)} chars")
    print(f"  Session parts after filter: {updated_session.all_discussed_parts}")
    print(f"{'='*60}\n")

    return response, updated_session, parts


async def run_agent_streaming(
    query: str,
    session: SessionState | None = None,
    session_container: dict | None = None
) -> AsyncGenerator[str, None]:
    """
    Run the agent graph with streaming response.

    Yields response tokens as they're generated by the synthesizer.

    Args:
        query: User's question
        session: Optional session state from previous turns
        session_container: Optional dict to store updated session (key: "session")
                          Use this to retrieve the updated session after streaming.
    """
    print(f"\n{'='*60}")
    print(f"[AGENT V2] NEW QUERY (streaming): {query[:50]}{'...' if len(query) > 50 else ''}")
    print(f"{'='*60}")

    current_session = session or SessionState()

    # Log session state
    if current_session.all_discussed_parts:
        print(f"  Session parts: {current_session.all_discussed_parts}")

    # Initialize state
    current_state = AgentState(
        user_query=query,
        session=current_session,
        conversation_history=current_session.conversation_history,
    )

    # Step 1: Scope check
    scope_result = await scope_check_node(current_state)
    current_state = AgentState(**{**current_state.model_dump(), **scope_result})

    if not current_state.is_in_scope:
        yield current_state.scope_rejection_message or "I can only help with refrigerator and dishwasher questions."
        if session_container is not None:
            session_container["session"] = current_state.session
        return

    # Step 2: Executor
    executor_result = await executor_node(current_state)
    current_state = AgentState(**{**current_state.model_dump(), **executor_result})

    # Step 2.5: Secondary scope check
    secondary_scope_result = await secondary_scope_check_node(current_state)
    current_state = AgentState(**{**current_state.model_dump(), **secondary_scope_result})

    if current_state.has_out_of_scope_parts:
        yield current_state.final_response
        if session_container is not None:
            session_container["session"] = current_state.session
        return

    # Extract all parts from executor results
    from backend.agent_v2.nodes.synthesizer import extract_parts, extract_mentioned_ps_numbers
    all_parts = extract_parts(current_state)

    # Step 3: Stream the synthesizer and accumulate the full response
    full_response = ""
    async for token in synthesizer_node_streaming(current_state):
        full_response += token
        yield token

    # Filter parts to only those mentioned in the response
    mentioned_ps_numbers = extract_mentioned_ps_numbers(full_response)
    if mentioned_ps_numbers:
        parts = [p for p in all_parts if p["ps_number"] in mentioned_ps_numbers]
        # Also filter session's discussed parts
        current_state.session.all_discussed_parts = [
            ps for ps in current_state.session.all_discussed_parts
            if ps in mentioned_ps_numbers
        ]
    else:
        parts = []
        current_state.session.all_discussed_parts = []

    # Store updated session and parts for caller to retrieve
    if session_container is not None:
        session_container["session"] = current_state.session
        session_container["parts"] = parts

    print(f"\n[AGENT V2] Streaming complete")
    print(f"  Session parts after filter: {current_state.session.all_discussed_parts}")
    print(f"{'='*60}\n")
