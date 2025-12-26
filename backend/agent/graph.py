"""
LangGraph multi-agent orchestration.

This module defines the graph that routes queries through:
1. Scope Check - Filter out off-topic queries
2. Planner - Analyze complexity and create plan
3. Executor OR Workers - Execute based on complexity
4. Synthesizer - Generate final response
"""
from typing import Literal, AsyncGenerator
from langgraph.graph import StateGraph, END
from backend.agent.state import AgentState, SessionState
from backend.agent.nodes import (
    scope_check_node,
    planner_node,
    executor_node,
    workers_node,
    synthesizer_node,
)
from backend.agent.nodes.synthesizer import synthesizer_node_streaming


def route_after_scope_check(state: AgentState) -> Literal["planner", "end"]:
    """Route based on scope check result."""
    if state.is_in_scope:
        return "planner"
    return "end"


def route_after_planner(state: AgentState) -> Literal["executor", "workers"]:
    """Route based on planner's complexity assessment."""
    if state.plan and state.plan.query_type == "complex":
        return "workers"
    return "executor"


def create_graph() -> StateGraph:
    """
    Create the multi-agent LangGraph.

    Graph structure:
    ```
    START → scope_check → [in_scope?]
                              │
              ┌───────────────┴───────────────┐
              │                               │
         (out of scope)                  (in scope)
              │                               │
              ▼                               ▼
             END                          planner
                                              │
                              ┌───────────────┴───────────────┐
                              │                               │
                          (simple)                        (complex)
                              │                               │
                              ▼                               ▼
                          executor                        workers
                              │                               │
                              └───────────────┬───────────────┘
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
    workflow.add_node("planner", planner_node)
    workflow.add_node("executor", executor_node)
    workflow.add_node("workers", workers_node)
    workflow.add_node("synthesizer", synthesizer_node)

    # Set entry point
    workflow.set_entry_point("scope_check")

    # Add conditional edges
    workflow.add_conditional_edges(
        "scope_check",
        route_after_scope_check,
        {
            "planner": "planner",
            "end": END,
        }
    )

    workflow.add_conditional_edges(
        "planner",
        route_after_planner,
        {
            "executor": "executor",
            "workers": "workers",
        }
    )

    # Both executor and workers lead to synthesizer
    workflow.add_edge("executor", "synthesizer")
    workflow.add_edge("workers", "synthesizer")

    # Synthesizer leads to end
    workflow.add_edge("synthesizer", END)

    return workflow.compile()


async def run_agent(
    query: str,
    session: SessionState | None = None,
) -> tuple[str, SessionState, dict | None]:
    """
    Run the agent graph on a query.

    Args:
        query: User's question
        session: Optional session state from previous turns (includes conversation history)

    Returns:
        Tuple of (response_text, updated_session, primary_part)
    """
    from backend.agent.logging import log_header, log_session_state

    log_header(f"NEW QUERY: {query[:50]}{'...' if len(query) > 50 else ''}")

    # Log session state for debugging
    current_session = session or SessionState()
    log_session_state(current_session)

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
    primary_part = result.get("primary_part")

    return response, updated_session, primary_part


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
    from backend.agent.logging import log_header, log_session_state

    log_header(f"NEW QUERY: {query[:50]}{'...' if len(query) > 50 else ''}")

    # Log session state for debugging
    current_session = session or SessionState()
    log_session_state(current_session)

    graph = create_graph()

    # Initialize state - conversation history comes from session
    initial_state = AgentState(
        user_query=query,
        session=current_session,
        conversation_history=current_session.conversation_history,
    )

    # Run the graph up to synthesizer
    # We need to manually step through to stream the final node
    current_state = initial_state

    # Step 1: Scope check
    scope_result = await scope_check_node(current_state)
    current_state = AgentState(**{**current_state.model_dump(), **scope_result})

    if not current_state.is_in_scope:
        yield current_state.scope_rejection_message or "I can only help with refrigerator and dishwasher questions."
        if session_container is not None:
            session_container["session"] = current_state.session
        return

    # Step 2: Planner
    plan_result = await planner_node(current_state)
    current_state = AgentState(**{**current_state.model_dump(), **plan_result})

    # Step 3: Executor or Workers
    if current_state.plan and current_state.plan.query_type == "complex":
        worker_result = await workers_node(current_state)
        current_state = AgentState(**{**current_state.model_dump(), **worker_result})
    else:
        executor_result = await executor_node(current_state)
        current_state = AgentState(**{**current_state.model_dump(), **executor_result})

    # Extract primary part before streaming (so it's available in done event)
    from backend.agent.nodes.synthesizer import extract_primary_part
    primary_part = extract_primary_part(current_state)

    # Step 4: Stream the synthesizer
    async for token in synthesizer_node_streaming(current_state):
        yield token

    # Store updated session and primary_part for caller to retrieve
    if session_container is not None:
        session_container["session"] = current_state.session
        session_container["primary_part"] = primary_part
