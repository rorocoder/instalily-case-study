"""
Logging utilities for agent debugging.

Provides colored console output to trace query flow through the multi-agent system.
"""
import json
import sys
from datetime import datetime
from typing import Any

# ANSI color codes
COLORS = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    # Node colors
    "scope": "\033[94m",      # Blue
    "planner": "\033[95m",    # Magenta
    "executor": "\033[93m",   # Yellow
    "workers": "\033[96m",    # Cyan
    "synthesizer": "\033[92m", # Green
    # Status colors
    "success": "\033[92m",    # Green
    "error": "\033[91m",      # Red
    "warning": "\033[93m",    # Yellow
    "info": "\033[97m",       # White
}


def _colorize(text: str, color: str) -> str:
    """Apply color to text."""
    return f"{COLORS.get(color, '')}{text}{COLORS['reset']}"


def _timestamp() -> str:
    """Get current timestamp."""
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def _format_value(value: Any, max_length: int = 200) -> str:
    """Format a value for display, truncating if necessary."""
    if value is None:
        return "None"

    if isinstance(value, dict):
        try:
            formatted = json.dumps(value, indent=2, default=str)
        except:
            formatted = str(value)
    elif isinstance(value, list):
        try:
            formatted = json.dumps(value, indent=2, default=str)
        except:
            formatted = str(value)
    else:
        formatted = str(value)

    if len(formatted) > max_length:
        return formatted[:max_length] + "..."
    return formatted


def log_header(title: str):
    """Log a section header."""
    print(f"\n{'='*60}")
    print(_colorize(f"  {title}", "bold"))
    print(f"{'='*60}")


def log_node_start(node_name: str, query: str = None):
    """Log when a node starts executing."""
    color = node_name.lower().replace("_", "").replace(" ", "")
    if color not in COLORS:
        color = "info"

    print(f"\n{_timestamp()} {_colorize(f'[{node_name.upper()}]', color)} {_colorize('Starting...', 'dim')}")
    if query:
        print(f"  Query: {_colorize(query[:100] + '...' if len(query) > 100 else query, 'info')}")


def log_node_result(node_name: str, result: dict, key_fields: list[str] = None):
    """Log the result of a node."""
    color = node_name.lower().replace("_", "").replace(" ", "")
    if color not in COLORS:
        color = "info"

    print(f"{_timestamp()} {_colorize(f'[{node_name.upper()}]', color)} {_colorize('Completed', 'success')}")

    if key_fields:
        for field in key_fields:
            if field in result:
                value = _format_value(result[field])
                print(f"  {field}: {value}")
    else:
        for key, value in result.items():
            print(f"  {key}: {_format_value(value)}")


def log_decision(decision: str, reason: str = None):
    """Log a routing decision."""
    print(f"  {_colorize('→ Decision:', 'bold')} {decision}")
    if reason:
        print(f"    Reason: {_colorize(reason, 'dim')}")


def log_tool_call(tool_name: str, params: dict):
    """Log a tool call."""
    print(f"  {_colorize('🔧 Tool:', 'warning')} {tool_name}")
    print(f"    Params: {_format_value(params, 150)}")


def log_tool_result(tool_name: str, result: Any, success: bool = True):
    """Log a tool result."""
    status = _colorize("✓", "success") if success else _colorize("✗", "error")
    print(f"  {status} {tool_name}: {_format_value(result, 200)}")


def log_error(message: str, exception: Exception = None):
    """Log an error."""
    print(f"{_timestamp()} {_colorize('[ERROR]', 'error')} {message}")
    if exception:
        print(f"  Exception: {_colorize(str(exception), 'error')}")


def log_state_summary(state: Any):
    """Log a summary of the current state."""
    print(f"\n{_colorize('State Summary:', 'dim')}")

    if hasattr(state, 'user_query'):
        print(f"  Query: {state.user_query[:50]}...")

    if hasattr(state, 'is_in_scope'):
        status = _colorize("✓ In Scope", "success") if state.is_in_scope else _colorize("✗ Out of Scope", "error")
        print(f"  Scope: {status}")

    if hasattr(state, 'plan') and state.plan:
        print(f"  Plan Type: {state.plan.query_type}")
        if state.plan.subtasks:
            print(f"  Subtasks: {len(state.plan.subtasks)}")

    if hasattr(state, 'session'):
        session = state.session
        if session.current_focus:
            print(f"  Focus: {session.current_focus}")
        if session.all_discussed_parts:
            print(f"  Discussed Parts: {session.all_discussed_parts}")


def log_flow_complete(response_preview: str = None):
    """Log that the flow is complete."""
    print(f"\n{_timestamp()} {_colorize('[COMPLETE]', 'success')} Flow finished")
    if response_preview:
        preview = response_preview[:150] + "..." if len(response_preview) > 150 else response_preview
        print(f"  Response: {preview}")
    print(f"{'='*60}\n")


def log_session_state(session):
    """Log detailed session state for debugging."""
    print(f"\n{_colorize('━━━ SESSION STATE ━━━', 'bold')}")

    # Current focus
    focus = session.current_focus or "(none)"
    print(f"  {_colorize('Current Focus:', 'info')} {focus}")

    # All discussed parts
    if session.all_discussed_parts:
        print(f"  {_colorize('All Discussed Parts:', 'info')} {session.all_discussed_parts}")
    else:
        print(f"  {_colorize('All Discussed Parts:', 'dim')} (none)")

    # Conversation history
    if session.conversation_history:
        print(f"  {_colorize('Conversation History:', 'info')} {len(session.conversation_history)} messages")
    else:
        print(f"  {_colorize('Conversation History:', 'dim')} (none)")

    # Per-appliance context
    for appliance_type, ctx in session.appliances.items():
        has_data = ctx.model_number or ctx.brand or ctx.current_symptom or ctx.discussed_parts
        if has_data:
            print(f"\n  {_colorize(f'[{appliance_type.upper()}]', 'warning')}")
            if ctx.model_number:
                print(f"    Model: {ctx.model_number}")
            if ctx.brand:
                print(f"    Brand: {ctx.brand}")
            if ctx.current_symptom:
                print(f"    Symptom: {ctx.current_symptom}")
            if ctx.discussed_parts:
                print(f"    Parts: {ctx.discussed_parts}")

    print(f"{_colorize('━━━━━━━━━━━━━━━━━━━━━', 'dim')}\n")
