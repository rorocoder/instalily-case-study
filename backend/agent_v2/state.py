"""
State definitions for the agent_v2 system.

Copied from backend/agent/state.py for isolation.
"""
from typing import Annotated, Any, Literal
from pydantic import BaseModel, Field
from langgraph.graph.message import add_messages


class Message(BaseModel):
    """A single conversation message."""
    role: Literal["user", "assistant"]
    content: str


class SessionState(BaseModel):
    """
    Persistent session state across conversation turns.
    """
    all_discussed_parts: list[str] = Field(default_factory=list)
    # Conversation history persisted across turns (last 10 messages)
    conversation_history: list[Message] = Field(default_factory=list)

    def add_discussed_part(self, ps_number: str):
        """Track a part that was discussed."""
        if ps_number not in self.all_discussed_parts:
            self.all_discussed_parts.append(ps_number)


class AgentState(BaseModel):
    """
    State that flows through the LangGraph.

    Simplified for v2 - no planner or workers.
    """
    # Conversation
    messages: Annotated[list, add_messages] = Field(default_factory=list)
    conversation_history: list[Message] = Field(default_factory=list)
    user_query: str = ""

    # Session context (persisted across turns)
    session: SessionState = Field(default_factory=SessionState)

    # Scope check result
    is_in_scope: bool = True
    scope_rejection_message: str | None = None

    # Secondary scope check results
    has_out_of_scope_parts: bool = False
    out_of_scope_parts: list[dict] = Field(default_factory=list)

    # Execution results (v2: only executor, no workers)
    executor_result: Any = None

    # Final response
    final_response: str = ""
    parts: list[dict] = Field(default_factory=list)

    # Streaming control
    should_stream: bool = True

    class Config:
        arbitrary_types_allowed = True
