"""
State definitions for the multi-agent system.
"""
from typing import Annotated, Any, Literal
from pydantic import BaseModel, Field
from langgraph.graph.message import add_messages


class Message(BaseModel):
    """A single conversation message."""
    role: Literal["user", "assistant"]
    content: str


class ApplianceContext(BaseModel):
    """Context for a single appliance type."""
    model_number: str | None = None
    brand: str | None = None
    current_symptom: str | None = None
    discussed_parts: list[str] = Field(default_factory=list)


class SessionState(BaseModel):
    """
    Persistent session state across conversation turns.

    Supports multi-appliance tracking - user can discuss both
    refrigerator and dishwasher in the same conversation.
    """
    appliances: dict[str, ApplianceContext] = Field(
        default_factory=lambda: {
            "refrigerator": ApplianceContext(),
            "dishwasher": ApplianceContext(),
        }
    )
    current_focus: Literal["refrigerator", "dishwasher"] | None = None
    all_discussed_parts: list[str] = Field(default_factory=list)
    # Conversation history persisted across turns (last 10 messages)
    conversation_history: list[Message] = Field(default_factory=list)

    def get_current_appliance(self) -> ApplianceContext | None:
        """Get the context for the currently focused appliance."""
        if self.current_focus:
            return self.appliances.get(self.current_focus)
        return None

    def add_discussed_part(self, ps_number: str, appliance_type: str | None = None):
        """Track a part that was discussed."""
        if ps_number not in self.all_discussed_parts:
            self.all_discussed_parts.append(ps_number)

        target = appliance_type or self.current_focus
        if target and target in self.appliances:
            if ps_number not in self.appliances[target].discussed_parts:
                self.appliances[target].discussed_parts.append(ps_number)


class Subtask(BaseModel):
    """A subtask created by the planner for complex queries."""
    description: str = ""
    tool: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    result: Any = None
    completed: bool = False


class PlannerOutput(BaseModel):
    """Output from the planner agent."""
    query_type: Literal["simple", "complex", "out_of_scope"]
    reasoning: str | None = None
    subtasks: list[Subtask] = Field(default_factory=list)
    synthesis_hint: str | None = None


class AgentState(BaseModel):
    """
    State that flows through the LangGraph.

    This is the complete state passed between nodes.
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

    # Planner output
    plan: PlannerOutput | None = None

    # Execution results
    executor_result: Any = None
    worker_results: list[Any] = Field(default_factory=list)

    # Final response
    final_response: str = ""
    primary_part: dict | None = None

    # Streaming control
    should_stream: bool = True

    class Config:
        arbitrary_types_allowed = True
