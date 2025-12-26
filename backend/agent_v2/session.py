"""
Session update logic for agent_v2.

Extracts part numbers, symptoms, and appliance types from tool results
and updates the session state accordingly.
"""
import json
from backend.agent_v2.state import SessionState


def update_session_from_tool_results(session: SessionState, messages: list) -> SessionState:
    """
    Extract ps_numbers, symptoms, appliance types from tool results.

    Args:
        session: Current session state
        messages: List of messages from the executor (includes tool results)

    Returns:
        Updated session state
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
        if not hasattr(msg, 'type') or msg.type != 'tool':
            continue

        try:
            # Get tool name and params
            tool_name = getattr(msg, 'name', '')
            tool_call_id = getattr(msg, 'tool_call_id', '')
            params = tool_params.get(tool_call_id, {}).get('args', {})

            content = msg.content
            if isinstance(content, str):
                try:
                    data = json.loads(content)
                except json.JSONDecodeError:
                    continue
            else:
                data = content

            items = data if isinstance(data, list) else [data]

            for item in items:
                if isinstance(item, dict):
                    _extract_and_update(session, item, tool_name, params)
        except Exception:
            continue

    return session


def _extract_and_update(
    session: SessionState,
    item: dict,
    tool_name: str,
    params: dict
) -> None:
    """
    Extract relevant fields from a tool result and update session.

    Args:
        session: Session state to update
        item: Single result item from tool
        tool_name: Name of the tool that produced this result
        params: Parameters that were passed to the tool
    """
    ps_number = item.get('ps_number')
    appliance_type = item.get('appliance_type')

    # Also check nested structures
    if not ps_number and 'part_info' in item:
        ps_number = item['part_info'].get('ps_number')
        appliance_type = item['part_info'].get('appliance_type')

    # Check if explicitly out of scope
    is_out_of_scope = item.get('out_of_scope', False)

    # Track parts (only for refrigerator/dishwasher or unknown)
    if ps_number:
        if appliance_type in ['refrigerator', 'dishwasher']:
            session.add_discussed_part(ps_number)
        elif not appliance_type and not is_out_of_scope:
            # If no appliance type specified but not out of scope, still track it
            session.add_discussed_part(ps_number)
