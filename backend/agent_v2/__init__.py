"""
Agent V2 - Simplified architecture.

Key differences from v1:
- No planner/workers split - single ReAct executor handles everything
- Tool registry for cleaner tool management
- Workflow patterns in prompt instead of rigid classification
- 100% standalone - no modifications to v1 code

Usage:
    from backend.agent_v2 import run_agent, run_agent_streaming, SessionState

    response, session, part = await run_agent("Tell me about PS11752778", session)
"""
from backend.agent_v2.graph import run_agent, run_agent_streaming
from backend.agent_v2.state import SessionState, AgentState, Message

__all__ = [
    "run_agent",
    "run_agent_streaming",
    "SessionState",
    "AgentState",
    "Message",
]
