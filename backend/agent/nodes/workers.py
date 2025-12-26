"""
Workers Node

Executes subtasks in parallel for complex queries.
Each subtask calls a specific tool with given parameters.
"""
import asyncio
from typing import Any
from backend.agent.state import AgentState, Subtask, SessionState
from backend.tools import (
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
    search_qna,
    search_repair_stories,
)


# Map tool names to actual tool functions
TOOL_MAP = {
    # Resolution tools
    "resolve_part": resolve_part,
    "resolve_model": resolve_model,
    # Part search and lookup
    "search_parts": search_parts,
    "get_part": get_part,
    # Compatibility tools
    "check_compatibility": check_compatibility,
    "get_compatible_parts": get_compatible_parts,
    "get_compatible_models": get_compatible_models,
    # Repair/troubleshooting tools
    "get_symptoms": get_symptoms,
    "get_repair_instructions": get_repair_instructions,
    # Semantic search tools
    "search_qna": search_qna,
    "search_repair_stories": search_repair_stories,
}


def update_session_from_results(session: SessionState, results: list[dict]) -> SessionState:
    """
    Extract discussed parts and appliance info from worker results and update session.
    """
    for result_dict in results:
        if result_dict.get("error"):
            continue

        result = result_dict.get("result")
        if result is None:
            continue

        # Get tool name and params for context (needed for symptom tracking)
        tool_name = result_dict.get("tool")
        params = result_dict.get("params", {})

        # Handle list of parts (e.g., from search_parts, get_symptoms)
        if isinstance(result, list):
            for item in result:
                if isinstance(item, dict):
                    _extract_and_add_part(session, item, tool_name, params)
        # Handle single dict (e.g., from get_part)
        elif isinstance(result, dict):
            _extract_and_add_part(session, result, tool_name, params)

    return session


def _extract_and_add_part(session: SessionState, item: dict, tool_name: str | None = None, params: dict | None = None) -> None:
    """
    Extract ps_number and appliance_type from a result item and add to session.
    Also tracks symptoms when get_symptoms tool is used.
    """
    params = params or {}

    # Extract PS number if present
    ps_number = item.get('ps_number')
    appliance_type = item.get('appliance_type')

    # Also check nested structures
    if not ps_number and 'part_info' in item:
        ps_number = item['part_info'].get('ps_number')
        appliance_type = item['part_info'].get('appliance_type')

    # Update session if we found a part
    if ps_number:
        session.add_discussed_part(ps_number, appliance_type)

        # Set current focus if we detected appliance type
        if appliance_type in ['refrigerator', 'dishwasher']:
            session.current_focus = appliance_type

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


async def execute_subtask(subtask: Subtask) -> dict[str, Any]:
    """Execute a single subtask."""
    tool_name = subtask.tool
    params = subtask.params

    if tool_name not in TOOL_MAP:
        return {
            "description": subtask.description,
            "error": f"Unknown tool: {tool_name}",
            "result": None
        }

    tool = TOOL_MAP[tool_name]

    try:
        # Tools are LangChain tools, invoke them properly
        result = await asyncio.to_thread(tool.invoke, params)
        return {
            "description": subtask.description,
            "tool": tool_name,
            "params": params,
            "result": result,
            "error": None
        }
    except Exception as e:
        return {
            "description": subtask.description,
            "tool": tool_name,
            "params": params,
            "result": None,
            "error": str(e)
        }


async def workers_node(state: AgentState) -> dict:
    """
    Workers node - executes subtasks in parallel.

    Takes the plan from the planner and runs all subtasks concurrently.
    Returns a list of results for the synthesizer.
    """
    from backend.agent.logging import log_node_start, log_node_result, log_tool_call, log_tool_result

    log_node_start("WORKERS")

    if not state.plan or not state.plan.subtasks:
        log_node_result("WORKERS", {"status": "no subtasks"})
        return {"worker_results": []}

    subtasks = state.plan.subtasks
    print(f"  Executing {len(subtasks)} subtasks in parallel...")

    # Log each subtask
    for st in subtasks:
        log_tool_call(st.tool, st.params)

    # Execute all subtasks in parallel
    tasks = [execute_subtask(st) for st in subtasks]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Handle any exceptions that occurred
    processed_results = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            processed_results.append({
                "description": subtasks[i].description,
                "error": str(result),
                "result": None
            })
            log_tool_result(subtasks[i].tool, f"ERROR: {result}", success=False)
        else:
            processed_results.append(result)
            error = result.get("error")
            if error:
                log_tool_result(result.get("tool", "unknown"), f"ERROR: {error}", success=False)
            else:
                log_tool_result(result.get("tool", "unknown"), result.get("result"), success=True)

    # Update session with discussed parts from results
    updated_session = update_session_from_results(state.session, processed_results)

    log_node_result("WORKERS", {
        "completed": len(processed_results),
        "errors": sum(1 for r in processed_results if r.get("error")),
        "discussed_parts": updated_session.all_discussed_parts,
    })

    return {
        "worker_results": processed_results,
        "session": updated_session,
    }
