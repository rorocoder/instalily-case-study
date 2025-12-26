# Architecture Simplification Plan (v2)

## Goal
Simplify the multi-agent system by removing rigid workflow classification. Let a well-prompted ReAct agent handle all queries flexibly, chaining tools naturally based on the query type.

## Key Insight
Instead of a planner deciding "simple vs complex" and pre-planning tool calls, let the LLM figure out the right tool sequence. There are multiple entry points (part lookup, symptom, search) that can chain into each other - the LLM is good at this when properly prompted.

---

## Directory Structure (ZERO MODIFICATIONS to existing code)

```
backend/
  agent/                    # EXISTING - COMPLETELY UNTOUCHED
    nodes/
      scope_check.py
      planner.py
      executor.py
      workers.py
      synthesizer.py
    graph.py
    state.py
    prompts.py

  tools/                    # EXISTING - COMPLETELY UNTOUCHED
    sql_tools.py
    vector_tools.py
    __init__.py

  agent_v2/                 # NEW - 100% STANDALONE
    __init__.py
    tools/                  # COPY of tools with registry pattern
      __init__.py
      registry.py           # Tool registry
      sql_tools.py          # Copied + uses registry
      vector_tools.py       # Copied + uses registry
    nodes/
      __init__.py
      scope_check.py        # Copied from agent/nodes/
      executor.py           # New simplified ReAct executor
      synthesizer.py        # Simplified synthesizer
    graph.py                # Simpler graph: scope_check → executor → synthesizer
    prompts.py              # Clean prompts with workflow patterns
    state.py                # Copied from agent/state.py
    session.py              # Session update logic
```

**Swapping between agents:**
```python
# In your code that imports the agent:
USE_V2_AGENT = True  # Toggle this to switch

if USE_V2_AGENT:
    from backend.agent_v2 import run_agent, run_agent_streaming
else:
    from backend.agent import run_agent, run_agent_streaming
```

**Key principle: ZERO modifications to existing files.** The v2 agent is completely self-contained.

---

## The New Architecture

```
┌─────────────┐
│ Scope Check │  ← Same as before - is this about fridges/dishwashers?
└─────────────┘
       ↓
┌─────────────┐
│  Executor   │  ← Single ReAct agent with all tools
│  (ReAct)    │     Chains tools naturally based on query
└─────────────┘
       ↓
┌─────────────┐
│ Synthesizer │  ← Format final response
└─────────────┘
```

**Removed:**
- Planner node (no more simple/complex classification)
- Workers node (no more parallel execution)
- Conditional routing

**The ReAct executor handles everything:**
- Part lookups: resolve → get_part → check_compatibility
- Symptoms: get_symptoms → get_repair_instructions
- Search: search_parts → user picks one → get_part
- Mixed: Any combination the LLM deems appropriate

---

## Phase 1: Tool Registry (in agent_v2/tools/)

**Create `backend/agent_v2/tools/registry.py`:**

```python
from dataclasses import dataclass
from typing import Callable
from langchain_core.tools import tool as langchain_tool

@dataclass
class ToolMetadata:
    name: str
    description: str
    category: str  # "resolution", "part", "symptom", "search"

class ToolRegistry:
    _tools: dict[str, Callable] = {}
    _metadata: dict[str, ToolMetadata] = {}

    @classmethod
    def register(cls, category: str = "part"):
        def decorator(func):
            lc_tool = langchain_tool(func)
            cls._tools[func.__name__] = lc_tool
            cls._metadata[func.__name__] = ToolMetadata(
                name=func.__name__,
                description=func.__doc__.split('\n')[0].strip() if func.__doc__ else "",
                category=category,
            )
            return lc_tool
        return decorator

    @classmethod
    def get_all_tools(cls) -> list:
        return list(cls._tools.values())

    @classmethod
    def get_tool_map(cls) -> dict[str, Callable]:
        return cls._tools.copy()

    @classmethod
    def generate_tool_docs(cls) -> str:
        """Auto-generate tool documentation for prompts."""
        sections = {
            "resolution": "### Resolution Tools\nUse these first to convert user input into identifiers.",
            "part": "### Part Tools\nRequire a PS number. Use after resolution.",
            "symptom": "### Symptom/Repair Tools\nFor troubleshooting workflows. Don't require PS number.",
            "search": "### Search Tools\nFor browsing/filtering parts.",
        }

        lines = []
        for category in ["resolution", "part", "symptom", "search"]:
            tools_in_cat = [m for m in cls._metadata.values() if m.category == category]
            if tools_in_cat:
                lines.append(sections.get(category, f"### {category.title()} Tools"))
                for meta in tools_in_cat:
                    lines.append(f"- `{meta.name}`: {meta.description}")
                lines.append("")

        return "\n".join(lines)

registry = ToolRegistry()
```

**Copy and update tools in agent_v2/tools/:**
```python
# backend/agent_v2/tools/sql_tools.py
# (Copy of backend/tools/sql_tools.py with registry decorators)
from backend.agent_v2.tools.registry import registry

@registry.register(category="resolution")
def resolve_part(input: str, session_context: dict | None = None) -> dict:
    """Parse any part reference (PS#, manufacturer#, URL, text) into a PS number."""
    ...  # Same implementation as original

@registry.register(category="part")
def get_part(ps_number: str) -> dict:
    """Get complete details for a part by PS number."""
    ...

# ... etc - all tools copied with @registry.register() decorator
```

---

## Phase 2: New Executor Prompt (The Core Change)

**`backend/agent_v2/prompts.py`:**

```python
from backend.agent_v2.tools.registry import registry

EXECUTOR_PROMPT = """You are a helpful assistant for PartSelect, an appliance parts retailer.
You help with refrigerator and dishwasher parts - finding parts, checking compatibility, and troubleshooting.

## Available Tools

{tool_docs}

## Workflow Patterns

Recognize what the user needs and chain tools accordingly:

### Pattern 1: Part Lookup
User mentions a specific part (PS number, manufacturer number, URL, "water filter", etc.)
1. If not a PS number → call `resolve_part()` first
2. Use the PS number for: `get_part()`, `check_compatibility()`, `search_qna()`, `search_repair_stories()`

### Pattern 2: Symptom/Troubleshooting
User describes a problem ("ice maker not working", "dishwasher won't drain")
1. Call `get_symptoms(appliance_type, symptom)` → returns parts to check, video, difficulty
2. If user asks about a specific part type → call `get_repair_instructions(appliance, symptom, part_type)`
3. Don't call part tools unless user asks about a specific part

### Pattern 3: Search/Browse
User wants to find parts ("find me a water filter", "cheap dishwasher racks")
1. Call `search_parts()` with appropriate filters
2. If user picks one → resolve to PS number → use part tools

### Pattern 4: Compatibility Check
User asks if something fits their model
1. Get the PS number (from session or resolve_part)
2. Call `check_compatibility(ps_number, model_number)`

## Session Context

{session_context}

## Key Rules

1. **Always use tools** - never answer without calling at least one tool
2. **Chain naturally** - if you need a PS number for a tool, get it first via resolve_part or session
3. **Check appliance_type** in results - only help with refrigerator/dishwasher parts
4. **Use session context** - if user says "this part", use the PS number from session
5. **Don't over-call** - if get_symptoms gives you what you need, don't also call get_repair_instructions

## Current Query

{query}

Use the appropriate tools to help the customer.
"""

def format_executor_prompt(query: str, session_context: str) -> str:
    return EXECUTOR_PROMPT.format(
        tool_docs=registry.generate_tool_docs(),
        session_context=session_context,
        query=query
    )
```

---

## Phase 3: Simplified Graph

**`backend/agent_v2/graph.py`:**

```python
from langgraph.graph import StateGraph, END
from backend.agent_v2.state import AgentState
from backend.agent_v2.nodes.scope_check import scope_check_node  # Copied from v1
from backend.agent_v2.nodes.executor import executor_node
from backend.agent_v2.nodes.synthesizer import synthesizer_node

def route_after_scope_check(state: AgentState) -> str:
    return "executor" if state.is_in_scope else "end"

def create_graph() -> StateGraph:
    workflow = StateGraph(AgentState)

    # Reuse scope check from v1
    workflow.add_node("scope_check", scope_check_node)
    workflow.add_node("executor", executor_node)
    workflow.add_node("synthesizer", synthesizer_node)

    workflow.set_entry_point("scope_check")

    workflow.add_conditional_edges(
        "scope_check",
        route_after_scope_check,
        {"executor": "executor", "end": END}
    )

    # Simple linear flow - no conditional routing
    workflow.add_edge("executor", "synthesizer")
    workflow.add_edge("synthesizer", END)

    return workflow.compile()
```

**Graph visualization:**
```
         +-----------+
         | __start__ |
         +-----------+
               ↓
        +-------------+
        | scope_check |
        +-------------+
         ↓           ↓
    (in scope)   (out of scope)
         ↓           ↓
    +----------+    END
    | executor |
    +----------+
         ↓
    +-------------+
    | synthesizer |
    +-------------+
         ↓
        END
```

---

## Phase 4: Executor Node

**`backend/agent_v2/nodes/executor.py`:**

```python
from langchain_anthropic import ChatAnthropic
from langgraph.prebuilt import create_react_agent
from backend.config import get_settings
from backend.agent_v2.tools.registry import registry
from backend.agent_v2.prompts import format_executor_prompt
from backend.agent_v2.state import AgentState
from backend.agent_v2.session import update_session_from_tool_results

def format_session_context(state: AgentState) -> str:
    """Format session for the prompt."""
    session = state.session
    parts = []

    # Conversation history
    if state.conversation_history:
        parts.append("## Recent Conversation")
        for msg in state.conversation_history[-6:]:
            role = "User" if msg.role == "user" else "Assistant"
            content = msg.content[:300] + "..." if len(msg.content) > 300 else msg.content
            parts.append(f"**{role}:** {content}")

    # Current context
    if session.current_focus:
        ctx = session.appliances.get(session.current_focus)
        if ctx:
            parts.append(f"\n## Current Context: {session.current_focus}")
            if ctx.current_symptom:
                parts.append(f"Current symptom: {ctx.current_symptom}")
            if ctx.model_number:
                parts.append(f"User's model: {ctx.model_number}")

    # Recently discussed parts
    if session.all_discussed_parts:
        parts.append(f"\n## Recently Discussed Parts")
        parts.append(f"PS numbers: {', '.join(session.all_discussed_parts[-5:])}")
        parts.append(f"(Use these when user says 'this part', 'the first one', etc.)")

    return "\n".join(parts) if parts else "No prior context."

async def executor_node(state: AgentState) -> dict:
    """
    Single ReAct executor - handles all query types.

    The LLM decides which tools to call and in what order.
    """
    settings = get_settings()

    llm = ChatAnthropic(
        model=settings.HAIKU_MODEL,
        api_key=settings.ANTHROPIC_API_KEY,
        max_tokens=1024,
    )

    tools = registry.get_all_tools()
    agent = create_react_agent(llm, tools)

    session_context = format_session_context(state)
    prompt = format_executor_prompt(state.user_query, session_context)

    result = await agent.ainvoke({"messages": [{"role": "user", "content": prompt}]})

    # Extract tool results and update session
    messages = result.get("messages", [])
    updated_session = update_session_from_tool_results(state.session, messages)

    return {
        "executor_result": result,
        "session": updated_session,
    }
```

---

## Phase 5: Session Logic

**`backend/agent_v2/session.py`:**

```python
from backend.agent_v2.state import SessionState
import json

def update_session_from_tool_results(session: SessionState, messages: list) -> SessionState:
    """
    Extract ps_numbers, symptoms, appliance types from tool results.
    """
    for msg in messages:
        if not hasattr(msg, 'type') or msg.type != 'tool':
            continue

        try:
            content = msg.content
            if isinstance(content, str):
                data = json.loads(content)
            else:
                data = content

            items = data if isinstance(data, list) else [data]
            tool_name = getattr(msg, 'name', '')

            for item in items:
                if isinstance(item, dict):
                    _extract_and_update(session, item, tool_name)
        except:
            continue

    return session

def _extract_and_update(session: SessionState, item: dict, tool_name: str) -> None:
    """Extract relevant fields from a tool result."""
    ps_number = item.get('ps_number')
    appliance_type = item.get('appliance_type')

    # Track parts
    if ps_number and appliance_type in ['refrigerator', 'dishwasher', None]:
        session.add_discussed_part(ps_number, appliance_type)
        if appliance_type:
            session.current_focus = appliance_type

    # Track symptoms
    symptom = item.get('symptom')
    if symptom and appliance_type in ['refrigerator', 'dishwasher']:
        session.current_focus = appliance_type
        ctx = session.appliances.get(appliance_type)
        if ctx:
            ctx.current_symptom = symptom
```

---

## Migration Plan

### Step 1: Create agent_v2 directory structure
```bash
mkdir -p backend/agent_v2/tools
mkdir -p backend/agent_v2/nodes
```

### Step 2: Create tool registry and copy tools
1. Create `backend/agent_v2/tools/__init__.py`
2. Create `backend/agent_v2/tools/registry.py`
3. Copy and update `backend/agent_v2/tools/sql_tools.py` (from backend/tools/)
4. Copy and update `backend/agent_v2/tools/vector_tools.py` (from backend/tools/)

### Step 3: Create agent core files
1. Copy `backend/agent_v2/state.py` (from backend/agent/state.py)
2. Create `backend/agent_v2/session.py`
3. Create `backend/agent_v2/prompts.py`
4. Create `backend/agent_v2/__init__.py`

### Step 4: Create nodes
1. Create `backend/agent_v2/nodes/__init__.py`
2. Copy `backend/agent_v2/nodes/scope_check.py` (from backend/agent/nodes/)
3. Create `backend/agent_v2/nodes/executor.py`
4. Create `backend/agent_v2/nodes/synthesizer.py`

### Step 5: Create graph
1. Create `backend/agent_v2/graph.py`
2. **Test:** Verify v2 agent works

### Step 6: Iterate
1. Test edge cases
2. Tune v2 prompts based on behavior
3. Compare accuracy/speed between v1 and v2

---

## Files Summary

**Create (all in backend/agent_v2/):**
```
backend/agent_v2/
├── __init__.py
├── graph.py
├── prompts.py
├── session.py
├── state.py
├── nodes/
│   ├── __init__.py
│   ├── scope_check.py      # Copied from agent/nodes/
│   ├── executor.py
│   └── synthesizer.py
└── tools/
    ├── __init__.py
    ├── registry.py
    ├── sql_tools.py        # Copied from tools/
    └── vector_tools.py     # Copied from tools/
```

**Modify: NONE**
- All existing files remain completely untouched
- v1 agent continues to work exactly as before
- Swap between v1/v2 by changing your import statement

---

## Comparison: v1 vs v2

| Aspect | v1 (Current) | v2 (New) |
|--------|--------------|----------|
| **Query classification** | Planner decides simple/complex | None - LLM figures it out |
| **Execution** | Executor (ReAct) OR Workers (parallel) | Single ReAct executor |
| **Tool selection** | Planner pre-plans for complex | LLM decides dynamically |
| **Flexibility** | Rigid workflow paths | Fluid tool chaining |
| **Code complexity** | 4 nodes + conditional routing | 3 nodes, linear flow |
| **Adding tools** | 5 files to change | 1 file (registry decorator) |
| **Isolation** | N/A | 100% standalone, zero shared code |

**When to use v1:**
- If v2 makes poor tool choices
- If you need guaranteed parallel execution
- If you need deterministic behavior

**When to use v2:**
- Simpler maintenance
- More natural handling of edge cases
- Easier to add new tools
- Can experiment without risk to v1

---

## Secondary Scope Check (Added Feature)

### Problem
The primary scope check (at the start of the graph) only validates based on query text. It can't detect when a part is actually for a different appliance type because that information is only available AFTER fetching the part data.

**Example failure case:**
```
User: "Tell me about PS16688554"
→ Primary scope check: PASS (has "PS" keyword)
→ Executor: Fetches PS16688554
→ Part data: "DeWALT Pole, Middle Extension N611762" (chainsaw part!)
→ Synthesizer: Generates response about chainsaw part ❌
```

### Solution: Two-Stage Scope Validation

#### Stage 1: Primary Scope Check (Query-Based)
- **Location**: `scope_check_node` - first node in graph
- **Input**: User query text
- **Method**: Regex patterns + LLM classification
- **Purpose**: Fast rejection of obviously out-of-scope queries

#### Stage 2: Secondary Scope Check (Data-Based)
- **Location**: `secondary_scope_check_node` - after executor, before synthesizer
- **Input**: Tool results with actual part data (including `appliance_type`)
- **Method**: Scans all tool results for `appliance_type` field
- **Purpose**: Catch parts that passed primary check but are actually out-of-scope

### Updated Graph Flow

```
┌─────────────┐
│ Scope Check │  ← Primary: text-based validation
│  (Primary)  │
└─────────────┘
       ↓
┌─────────────┐
│  Executor   │  ← Fetches part data (appliance_type now known)
│  (ReAct)    │
└─────────────┘
       ↓
┌─────────────┐
│ Scope Check │  ← Secondary: data-based validation ✨ NEW
│ (Secondary) │
└─────────────┘
   ↓         ↓
 (OK)    (REJECT)
   ↓         ↓
┌──────┐   END
│Synth │
└──────┘
```

### LLM-Based Appliance Type Classification

For live-scraped parts that don't have `appliance_type` in the database, we use LLM classification:

**Process:**
1. Live scraper fetches part from PartSelect (not in our DB)
2. Scraper extracts: name, description, reviews, Q&A, compatible models
3. If `appliance_type` is empty → call LLM classifier
4. LLM analyzes all available text and returns appliance type
5. Secondary scope check validates using the classified type

**LLM Classifier** (`classify_appliance_type_with_llm` in `scrape_tools.py`):
```python
def classify_appliance_type_with_llm(part_data: dict) -> str:
    """
    Use LLM to classify what type of appliance a part is for.

    Analyzes:
    - Part name (e.g., "DeWALT Pole Middle Extension")
    - Manufacturer (e.g., "DeWALT")
    - Description
    - Compatible model descriptions
    - Sample reviews and Q&A

    Returns:
    - "refrigerator", "dishwasher", "chainsaw", "microwave", etc.
    - "unknown" if classification fails
    """
```

**Example:**
```
Part: "DeWALT Pole, Middle Extension N611762"
Models: "DCPS620B Cordless Pole Saw"
Reviews: "Works great for trimming branches..."

LLM → "chainsaw" or "pole saw"
Secondary Scope Check → REJECT ✅
```

### Session Cleanup

When parts are rejected as out-of-scope, they're also removed from the session to prevent users from referencing them in follow-up queries:

```python
# In secondary_scope_check_node
if unique_out_of_scope:
    # Remove out-of-scope parts from session
    out_of_scope_ps_numbers = {p['ps_number'] for p in unique_out_of_scope}
    updated_session.all_discussed_parts = [
        ps for ps in updated_session.all_discussed_parts
        if ps not in out_of_scope_ps_numbers
    ]

    return {
        "has_out_of_scope_parts": True,
        "final_response": rejection_message,
        "session": updated_session,  # Cleaned session
    }
```

**Why this matters:**
```
Without cleanup:
User: "Tell me about PS16688554" (chainsaw)
→ REJECTED, but PS16688554 added to session
User: "Tell me more about that part"
→ Agent tries to discuss chainsaw part ❌

With cleanup:
User: "Tell me about PS16688554" (chainsaw)
→ REJECTED, PS16688554 removed from session
User: "Tell me more about that part"
→ No parts in session to reference ✅
```

### Implementation Files

**New files:**
- `backend/agent_v2/nodes/secondary_scope_check.py` - Validation logic

**Modified files:**
- `backend/agent_v2/tools/scrape_tools.py` - Added LLM classifier
- `backend/agent_v2/state.py` - Added scope check state fields
- `backend/agent_v2/graph.py` - Added secondary check node to flow
- `backend/agent_v2/nodes/__init__.py` - Export new node

### Validation Logic

The secondary scope check examines all tool results and checks for:

1. **Explicit out_of_scope flag** - Set by `get_part()` when appliance_type doesn't match
2. **Appliance_type field** - Only allows "refrigerator" and "dishwasher"
3. **Lists of parts** - Validates each part in search results
4. **Compatible models** - Checks appliance_type from model data

**Strict mode:** If ANY part is out-of-scope, the ENTIRE query is rejected.

### User-Friendly Rejection Messages

```python
def build_rejection_message(out_of_scope_parts: list[dict]) -> str:
    """Build user-friendly rejection explaining appliance type mismatch."""

# Single part:
"I'm sorry, but Pole, Middle Extension (PS16688554) is a part for
a Chainsaw, not a refrigerator or dishwasher."

# Multiple parts:
"I'm sorry, but the parts you asked about are not for refrigerators
or dishwashers:
- Pole, Middle Extension (PS16688554) - Chainsaw
- Turntable Motor (PS12345) - Microwave"
```

### Benefits

1. **Catches edge cases** - Parts with misleading names that pass text-based checks
2. **Intelligent classification** - LLM can identify appliance types from context
3. **Clean state management** - No orphaned references to rejected parts
4. **User-friendly** - Explains exactly what appliance type the part is for
5. **Strict validation** - Prevents any out-of-scope parts from being discussed

### Testing

**Test cases:**
1. Out-of-scope part (DB): Part in database with explicit appliance_type
2. Out-of-scope part (scraped): Part scraped live, classified by LLM
3. Mixed query: Multiple parts where some are in-scope, some out
4. In-scope parts: Normal operation (should pass through)
5. Follow-up references: Ensure rejected parts can't be referenced
