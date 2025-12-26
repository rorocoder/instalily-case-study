"""
Secondary Scope Check Node for agent_v2.

Validates that parts fetched by the executor are for refrigerators/dishwashers.
This check happens AFTER tools have run, when appliance_type data is available.

Catches cases like:
- User asks "Tell me about PS12345" (passes primary text-based scope check)
- Executor fetches PS12345 and discovers it's a microwave part (via DB or LLM classification)
- Secondary check rejects with helpful message about the correct appliance type

For live-scraped parts without appliance_type in DB, the scraper uses LLM to classify
the appliance type based on part name, description, reviews, and Q&A data.
"""
import json
from backend.agent_v2.state import AgentState


def parse_tool_content(content: str | dict) -> dict | list | str:
    """
    Parse tool message content (could be JSON string or already parsed).

    Args:
        content: Tool message content

    Returns:
        Parsed content as dict, list, or string
    """
    if isinstance(content, str):
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return content
    return content


def build_rejection_message(out_of_scope_parts: list[dict]) -> str:
    """
    Build user-friendly rejection message explaining which parts are out-of-scope.

    Args:
        out_of_scope_parts: List of dicts with ps_number, appliance_type, part_name

    Returns:
        Formatted rejection message
    """
    if len(out_of_scope_parts) == 1:
        part = out_of_scope_parts[0]
        appliance = part['appliance_type'].title()
        ps_num = part['ps_number']
        name = part.get('part_name', 'This part')

        return f"""I'm sorry, but **{name} ({ps_num})** is a part for a **{appliance}**, not a refrigerator or dishwasher.

I can only help with **refrigerator** and **dishwasher** parts and repairs. If you have questions about fridge or dishwasher parts, I'd be happy to help!"""

    else:
        # Multiple out-of-scope parts
        parts_list = "\n".join([
            f"- **{p.get('part_name', 'Part')} ({p['ps_number']})** - {p['appliance_type'].title()}"
            for p in out_of_scope_parts
        ])

        return f"""I'm sorry, but the parts you asked about are not for refrigerators or dishwashers:

{parts_list}

I can only help with **refrigerator** and **dishwasher** parts and repairs. If you have questions about fridge or dishwasher parts, I'd be happy to help!"""


def secondary_scope_check_node(state: AgentState) -> dict:
    """
    Check if any parts fetched by executor are out-of-scope.

    Scans tool results for:
    1. Parts with appliance_type not in ['refrigerator', 'dishwasher']
    2. Parts with out_of_scope: True flag (already set by get_part())

    If ANY out-of-scope parts found → reject entire query (strict mode)

    Also removes out-of-scope parts from the session's all_discussed_parts list
    to prevent users from referencing them in follow-up queries.

    Args:
        state: Current agent state with executor_result

    Returns:
        Dictionary with:
        - has_out_of_scope_parts: bool
        - out_of_scope_parts: list[dict]
        - final_response: str (if rejecting)
        - session: Updated session with out-of-scope parts removed
    """
    out_of_scope_parts = []

    # Parse executor_result messages
    if not state.executor_result:
        # No executor results - pass through
        return {
            "has_out_of_scope_parts": False,
            "out_of_scope_parts": [],
        }

    messages = state.executor_result.get("messages", [])
    print(f"  [SECONDARY SCOPE CHECK] Scanning {len(messages)} tool results...")

    for msg in messages:
        # Only process tool result messages
        if not hasattr(msg, 'type') or msg.type != 'tool':
            continue

        content = parse_tool_content(msg.content)

        # Case 1: Check for explicit out_of_scope flag (set by get_part(), check_compatibility())
        if isinstance(content, dict) and content.get('out_of_scope'):
            out_of_scope_parts.append({
                "ps_number": content.get("ps_number", "Unknown"),
                "appliance_type": content.get("appliance_type", "unknown"),
                "part_name": content.get("part_name", "Unknown Part")
            })
            continue

        # Case 2: Check appliance_type field in single part result
        if isinstance(content, dict) and content.get('appliance_type'):
            appliance_type = content['appliance_type'].lower().strip()
            # Only allow refrigerator and dishwasher - reject everything else
            # (including empty, "unknown", "chainsaw", "microwave", etc.)
            if appliance_type and appliance_type not in ['refrigerator', 'dishwasher']:
                out_of_scope_parts.append({
                    "ps_number": content.get("ps_number", "Unknown"),
                    "appliance_type": appliance_type if appliance_type else "unknown",
                    "part_name": content.get("part_name", "Unknown Part")
                })

        # Case 3: Check lists of parts (e.g., from search_parts, get_compatible_models)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get('appliance_type'):
                    appliance_type = item['appliance_type'].lower().strip()
                    # Only allow refrigerator and dishwasher
                    if appliance_type and appliance_type not in ['refrigerator', 'dishwasher']:
                        out_of_scope_parts.append({
                            "ps_number": item.get("ps_number", "Unknown"),
                            "appliance_type": appliance_type if appliance_type else "unknown",
                            "part_name": item.get("part_name", "Unknown Part")
                        })

        # Case 4: Check dict results with 'models' field (from get_compatible_models)
        elif isinstance(content, dict) and content.get('models'):
            # get_compatible_models returns {"part_number": PS#, "models": [...]}
            # The part itself might have appliance_type in the first model
            models = content.get('models', [])
            if models and isinstance(models, list) and len(models) > 0:
                first_model = models[0]
                if isinstance(first_model, dict) and first_model.get('appliance_type'):
                    appliance_type = first_model['appliance_type'].lower().strip()
                    # Only allow refrigerator and dishwasher
                    if appliance_type and appliance_type not in ['refrigerator', 'dishwasher']:
                        out_of_scope_parts.append({
                            "ps_number": content.get("part_number", "Unknown"),
                            "appliance_type": appliance_type if appliance_type else "unknown",
                            "part_name": "Unknown Part"  # Name not available in this format
                        })

    # Remove duplicates (same ps_number)
    seen_ps_numbers = set()
    unique_out_of_scope = []
    for part in out_of_scope_parts:
        ps_num = part['ps_number']
        if ps_num not in seen_ps_numbers:
            seen_ps_numbers.add(ps_num)
            unique_out_of_scope.append(part)

    if unique_out_of_scope:
        appliance_types = [p['appliance_type'] for p in unique_out_of_scope]
        print(f"  [SECONDARY SCOPE CHECK] REJECTED - found {len(unique_out_of_scope)} out-of-scope parts: {appliance_types}")

        # Remove out-of-scope parts from session's discussed parts
        out_of_scope_ps_numbers = {p['ps_number'] for p in unique_out_of_scope}
        updated_session = state.session.model_copy(deep=True)
        updated_session.all_discussed_parts = [
            ps for ps in updated_session.all_discussed_parts
            if ps not in out_of_scope_ps_numbers
        ]
        print(f"  [SECONDARY SCOPE CHECK] Removed {len(out_of_scope_ps_numbers)} parts from session")

        # Build rejection message
        rejection_message = build_rejection_message(unique_out_of_scope)

        return {
            "has_out_of_scope_parts": True,
            "out_of_scope_parts": unique_out_of_scope,
            "final_response": rejection_message,
            "session": updated_session,
        }

    print(f"  [SECONDARY SCOPE CHECK] PASSED - all parts are fridge/dishwasher")
    return {
        "has_out_of_scope_parts": False,
        "out_of_scope_parts": [],
    }
