# Agent Architecture Plan

## Decisions Made
- **Backend Framework:** FastAPI
- **Agent Framework:** LangGraph
- **Streaming:** Yes
- **Session Persistence:** Yes
- **Architecture:** Multi-agent (Planner → Workers → Synthesizer)

---

## Overview

Build a **multi-agent LangGraph system** with specialized agents for different roles:
- **Planner Agent** (haiku) - Fast query analysis and task decomposition
- **Worker Agents** (haiku) - Parallel execution of subtasks with tools
- **Synthesizer Agent** (sonnet) - Combines results into coherent response

Benefits:
- Parallel execution for complex queries
- Specialized models for each role (cost/speed optimization)
- Clear separation of concerns
- Natural state management via LangGraph

---

## Architecture: Multi-Agent LangGraph Workflow

```
┌─────────────────────────────────────────────────────────────────┐
│  User Query + Session State                                      │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  SCOPE CHECK NODE (rule-based or haiku)                          │
│  - Quick check: is this about refrigerators/dishwashers?         │
│  - OUT_OF_SCOPE → rejection response                             │
│  - IN_SCOPE → continue to planner                                │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  PLANNER AGENT (haiku - fast & cheap)                            │
│  - Analyzes query complexity                                     │
│  - SIMPLE → route to single executor                             │
│  - COMPLEX → decompose into parallel subtasks                    │
│  Output: { type: "simple" } OR { type: "complex", subtasks: [...]}│
└─────────────────────────────────────────────────────────────────┘
                     │                    │
            (complex)                  (simple)
                     │                    │
                     ▼                    ▼
┌─────────────────────────────┐  ┌─────────────────────────────────┐
│  WORKER AGENTS (parallel)   │  │  EXECUTOR AGENT (haiku + tools) │
│  (haiku + tools each)       │  │  - Single query execution       │
│                             │  │  - Tool loop until done         │
│  Worker 1 ──┐               │  │  - Returns result               │
│  Worker 2 ──┼── parallel    │  └─────────────────────────────────┘
│  Worker 3 ──┘               │                   │
└─────────────────────────────┘                   │
          │                                       │
          ▼                                       │
┌─────────────────────────────┐                   │
│  SYNTHESIZER AGENT (sonnet) │◄──────────────────┘
│  - Combines worker results  │
│  - OR formats executor result│
│  - Generates final response │
│  - Updates session state    │
└─────────────────────────────┘
                │
                ▼
┌─────────────────────────────────────────────────────────────────┐
│  STREAM RESPONSE                                                 │
│  - Stream synthesizer tokens to frontend via SSE                 │
└─────────────────────────────────────────────────────────────────┘
```

### Agent Roles

| Agent | Model | Purpose | Tools Access |
|-------|-------|---------|--------------|
| Scope Check | haiku/rules | Gate off-topic queries | None |
| Planner | haiku | Analyze & decompose queries | None |
| Executor | haiku | Handle simple single-task queries | All tools |
| Workers | haiku | Execute subtasks in parallel | All tools |
| Synthesizer | sonnet | Combine results, final response | None |

### Why This Architecture?

1. **Cost Optimization**: haiku for planning/execution ($0.25/M), sonnet only for synthesis ($3/M)
2. **Speed**: Parallel workers for complex queries, fast haiku for simple ones
3. **Quality**: sonnet synthesizer ensures coherent, high-quality final responses
4. **Separation of Concerns**: Each agent has a focused role

---

## Tools Design

The tool architecture separates **resolution** (parsing messy user input) from **atomic data fetching** (clean identifiers → data). This prevents the planner from becoming a pile of regex/edge cases and lets the LLM compose atomic tools naturally.

### Resolution Tools (Parse Messy Input → Clean Identifiers)

| Tool | Purpose | Parameters |
|------|---------|------------|
| `resolve_part` | Parse any part reference (PS#, manufacturer#, URL, "this part", text) → returns ps_number | `input`, `session_context?` |
| `resolve_model` | Parse model number with fuzzy matching → returns model_number | `input` |

**`resolve_part` handles:**
- PS number: "PS11752778" → exact match
- Manufacturer #: "WPW10321304" → lookup and return PS number
- PartSelect URL: "partselect.com/PS11752778..." → extract PS number
- Session reference: "this part" with session context → resolve from context
- Text search: "ice maker" → return search candidates

**`resolve_model` handles:**
- Exact match (case-insensitive)
- Partial/fuzzy match for typos (e.g., "WDT780" → "WDT780SAEM1")

### Atomic Data Tools (Clean Identifiers → Data)

| Tool | Purpose | Parameters |
|------|---------|------------|
| `search_parts` | Browse/filter parts by text query and filters | `query?`, `appliance_type?`, `part_type?`, `brand?`, `max_price?`, `in_stock_only?` |
| `get_part` | Get full part info by PS number | `ps_number` |
| `check_compatibility` | Check if part fits model | `ps_number`, `model_number` |
| `get_compatible_parts` | All parts for a model (MODEL → PARTS) | `model_number`, `part_type?`, `brand?` |
| `get_compatible_models` | All models for a part (PART → MODELS) | `ps_number`, `brand?` |
| `get_symptoms` | List symptoms for appliance type | `appliance_type` |
| `get_repair_instructions` | Get diagnostic steps with video links | `appliance_type`, `symptom`, `part_type?` |

**Tool Return Details:**

`get_part` returns ALL stored fields:
- part_name, part_type, manufacturer_part_number, part_manufacturer
- part_price, part_description
- install_difficulty, install_time, install_video_url
- part_url, average_rating, num_reviews
- appliance_type, brand, manufactured_for
- availability, replaces_parts

`search_parts` returns (for browsing/filtering):
- ps_number, part_name, part_type, part_price
- average_rating, num_reviews (quality indicators)
- availability, brand, appliance_type

`get_repair_instructions` returns:
- instructions (step-by-step text)
- video_url (YouTube tutorial)
- symptom_url (PartSelect page link)
- difficulty level

### Vector Tools (Semantic Search)

| Tool | Purpose | Parameters |
|------|---------|------------|
| `search_qna` | Find relevant Q&A | `query`, `ps_number?`, `limit?` |
| `search_repair_stories` | Find similar repair experiences | `query`, `ps_number?`, `limit?` |

### LLM-Composed Workflows

The LLM composes atomic tools for complex tasks. For example:

**Installation guidance** (previously a composite tool):
1. `get_part(ps_number)` → install_difficulty, install_time, install_video_url
2. `search_qna("how to install", ps_number)` → installation Q&A
3. `search_repair_stories("installing", ps_number)` → installation experiences

---

## Fallback Scraping System

**Status**: ✅ Implemented (Dec 25, 2025)

When parts are not found in the database, the system automatically scrapes PartSelect in real-time as a fallback mechanism.

### Overview

The fallback scraper provides an MVP solution for handling parts not in the database without requiring full database population. When a user asks about a part that doesn't exist in the database, the system:

1. Detects the "not found" error
2. Automatically scrapes PartSelect's website
3. Returns comprehensive data (part info, compatible models, Q&A, reviews, repair stories)
4. Presents the data to the user seamlessly

### Architecture

```
get_part(PS123) → DB returns "not found"
        ↓
Executor detects error pattern
        ↓
Auto-trigger scrape_part_live(PS123)
        ↓
Navigate to PartSelect homepage → Search for PS123 → Redirect to part page
        ↓
Scrape full data (5-30 seconds)
        ↓
Return comprehensive part data to agent
        ↓
Agent uses scraped data in response
```

### Implementation Files

| File | Type | Purpose |
|------|------|---------|
| `/backend/agent_v2/tools/scrape_tools.py` | **NEW** | Core scraping tool implementation (~200 lines) |
| `/backend/agent_v2/nodes/executor.py` | **MODIFIED** | Automatic fallback detection and triggering (lines 134-199) |
| `/backend/agent_v2/tools/__init__.py` | **MODIFIED** | Import scrape_tools for registration |
| `/backend/agent_v2/prompts.py` | **MODIFIED** | Pattern 6 - instructs agent on scraped data usage |
| `/backend/test_scrape_live.py` | **NEW** | Manual testing script |

### Tool: `scrape_part_live()`

**Registration**: `@registry.register(category="scrape")`

**Input**:
- `ps_number` (str): PS number to scrape (e.g., "PS11752778")

**Output**: Dictionary with comprehensive part data:
```python
{
  # Standard part fields (same as get_part)
  "ps_number": "PS11752778",
  "part_name": "Ice Maker Assembly",
  "part_price": "129.99",
  "average_rating": "4.5",
  "num_reviews": "234",
  # ... all other part fields ...

  # Metadata flags
  "_scraped_live": True,
  "_model_compatibility_count": 15,
  "_qna_count": 8,
  "_stories_count": 12,
  "_reviews_count": 234,

  # Full related data
  "_compatible_models": [{"brand": "Whirlpool", "model_number": "...", ...}, ...],
  "_qna_data": [{"question": "...", "answer": "...", ...}, ...],
  "_repair_stories": [{"story": "...", "difficulty": "...", ...}, ...],
  "_reviews_data": [{"rating": 5, "review_text": "...", ...}, ...]
}
```

**Scraping Process**:
1. Validate PS number format (must start with "PS")
2. Setup headless Chrome with optimizations (disable images)
3. Navigate to `https://www.partselect.com/`
4. Find search input (`input.js-headerNavSearch`)
5. Enter PS number and submit (Keys.RETURN)
6. Wait for redirect to part page (timeout: 15s)
7. Call existing `scrape_part_page()` for full data extraction
8. Include all data (models, Q&A, stories, reviews) in response
9. Cleanup WebDriver in finally block

**Error Handling**:
- Invalid PS number format → return error dict
- Search input not found → return error dict
- No redirect (part doesn't exist) → return error dict
- Scraping timeout → return error dict
- Always cleanup WebDriver to prevent memory leaks

### Automatic Fallback Mechanism

**Location**: `executor.py`, lines 134-199

**Trigger Conditions**:
1. Tool `get_part()` returns an error
2. Error message contains "not found"
3. PS number is available in error response
4. Scrape hasn't already been called by agent

**Behavior**:
```python
# Detect get_part failure
if tool_name == 'get_part' and "not found" in error_msg:
    # Invoke scrape_part_live using tool's invoke method
    scraped_data = scrape_tool.invoke({"ps_number": ps_number})

    # Inject scraped data into messages for synthesizer
    messages.append(ToolMessage(
        content=json.dumps(scraped_data),
        tool_call_id=f"scrape_{ps_number}",
        name="scrape_part_live"
    ))
```

**Key Design Decisions**:
1. **Proper tool invocation**: Use `scrape_tool.invoke()` (LangChain method) not direct function call
2. **Prevent duplicates**: Check if agent already called `scrape_part_live` to avoid double-scraping
3. **Inject as tool message**: Makes scraped data look like normal tool results to synthesizer

### Comprehensive Data Strategy

**Problem**: When a part is scraped, we get compatible models, Q&A, reviews, and stories. But if the user asks a follow-up question like "what models does this fit?", the agent might try to call `get_compatible_models()` which would return empty (part not in DB) and trigger another scrape.

**Solution**: **Smart Prompting (Option B)**

1. **Include all data in scrape result**:
   - `_compatible_models` - full list of compatible models
   - `_qna_data` - full list of Q&A entries
   - `_repair_stories` - full list of repair stories
   - `_reviews_data` - full list of reviews

2. **Instruct agent via Pattern 6 prompt**:
   ```markdown
   When `scrape_part_live()` returns successfully, the scraped data includes ALL related data.

   DO NOT call these tools after a successful scrape:
   - ❌ get_compatible_models(ps_number) - use _compatible_models from scrape result
   - ❌ search_qna(ps_number, query) - use _qna_data from scrape result
   - ❌ search_repair_stories(ps_number, query) - use _repair_stories from scrape result
   - ❌ search_reviews(ps_number, query) - use _reviews_data from scrape result
   ```

**Benefits**:
- ✅ Clean architecture - respects tool boundaries
- ✅ Simple implementation - just prompt guidance, no code changes to other tools
- ✅ Efficient - prevents duplicate scraping for follow-up questions
- ✅ Honest - agent knows exactly what data is available and where to find it

**Alternative Approaches Considered**:
- **Inject all tool results**: Fake tool messages for `get_compatible_models()` etc. (❌ violates abstractions, confusing logs)
- **Session caching**: Store scraped data in session, modify all tools to check cache (✅ cleanest but requires modifying 4-5 tools)

### Performance Characteristics

| Metric | Value |
|--------|-------|
| Normal DB query | 50-200ms |
| Live scrape | 5,000-30,000ms (5-30 seconds) |
| Memory per scrape | ~150-200MB (WebDriver) |
| Max concurrent scrapes | 2 (thread pool limit) |
| Network per scrape | ~500KB-2MB |

**Optimizations**:
- Headless Chrome (no GUI rendering)
- Disable images (faster page loads)
- Reuse existing scraper infrastructure
- Proper cleanup in finally blocks
- Thread pool to limit concurrent scrapes

### Testing

**Manual test script**: `/backend/test_scrape_live.py`

```bash
# Test valid part
python -m backend.test_scrape_live PS11752778

# Test non-existent part
python -m backend.test_scrape_live PS99999999

# Test invalid format
python -m backend.test_scrape_live INVALID123
```

**Test Results**:
- ✅ Tool registration (14 tools total, scrape_part_live is #14)
- ✅ Invalid format handling (returns error dict)
- ✅ Successful scraping (PS16688554 - extracted all data in ~12s)
- ✅ Non-existent part handling (timeout, graceful error)
- ✅ WebDriver cleanup (no memory leaks)
- ✅ Executor fallback (auto-triggers when DB returns not found)

### Future Enhancements

**Not included in MVP**:
1. **Persistence**: Save scraped data to database for future queries
2. **Session caching**: Store scraped data in session state for current conversation
3. **Rate limiting**: Track scrape frequency, prevent abuse
4. **Async scraping**: Use asyncio for better performance
5. **Batch scraping**: Scrape multiple parts in parallel
6. **Monitoring**: Track scrape success rate, timing, errors

### Example Usage

**Scenario**: User asks about part not in database

```
User: "Tell me about PS16688554"

1. Agent calls get_part("PS16688554")
2. DB returns: {"error": "Part PS16688554 not found in database"}
3. Executor detects "not found", triggers scrape_part_live("PS16688554")
4. Scraper:
   - Navigates to PartSelect homepage
   - Searches for PS16688554
   - Redirects to part page
   - Extracts all data (3 models, 1 Q&A, 1 review)
5. Returns complete part data to agent
6. Synthesizer generates response

Response: "PS16688554 is a Pole, Middle Extension N611762 from DeWALT.
It costs $46.40 and is currently in stock. This part has a 5.0 star
rating from 1 review. [PartCard displayed]"

Follow-up: "what are its compatible models?"

1. Agent sees _scraped_live: true in previous scrape result
2. Uses _compatible_models data directly (no additional scrape!)
3. Returns: "This part fits 3 models: [lists models]"
```

### Success Criteria

✅ Tool successfully scrapes parts not in database
✅ Automatic fallback works transparently
✅ Returns data in correct format for frontend
✅ Error handling is graceful (no crashes)
✅ WebDriver cleanup prevents memory leaks
✅ Response time is acceptable (5-30s for scraping)
✅ Agent can use scraped data for follow-up questions
✅ No duplicate scraping within conversation

### Task Decomposition (Planner Agent)

The Planner Agent (not a tool) handles task decomposition:

1. Receives user query
2. Analyzes complexity:
   - **Simple**: Single intent, one tool call needed → route to Executor
   - **Complex**: Multiple parts, comparison, multi-step → decompose
3. For complex queries, outputs structured subtasks:
   ```json
   {
     "type": "complex",
     "subtasks": [
       {"description": "Get details for PS11752778", "tool": "get_part", "params": {"ps_number": "PS11752778"}},
       {"description": "Get details for PS11752779", "tool": "get_part", "params": {"ps_number": "PS11752779"}}
     ],
     "synthesis_hint": "Compare these two parts: price, ratings, availability"
   }
   ```
4. Worker agents execute subtasks in parallel
5. Synthesizer combines results using the hint

---

## Session State

Maintain context across conversation turns using **multi-appliance** design:

```python
session_state = {
    # Per-appliance context (can track both fridge and dishwasher)
    "appliances": {
        "refrigerator": {
            "model_number": None,      # e.g., "WDT780SAEM1"
            "brand": None,             # e.g., "Whirlpool"
            "current_symptom": None,   # e.g., "ice maker not working"
            "discussed_parts": []      # Parts discussed for this appliance
        },
        "dishwasher": {
            "model_number": None,
            "brand": None,
            "current_symptom": None,
            "discussed_parts": []
        }
    },

    # Current focus - which appliance we're actively discussing
    "current_focus": None,  # "refrigerator" or "dishwasher"

    # Cross-appliance tracking
    "all_discussed_parts": []  # All parts across both appliances (for comparisons)
}
```

**Design rationale:**
- **Multi-appliance tracking** - user can discuss fridge and dishwasher in same conversation
- **Current focus** - still have one "active" appliance for context-dependent questions
- **Per-appliance state** - each appliance has its own model, brand, symptom, parts
- When user mentions a new appliance, we switch `current_focus` but don't lose the other's context

**Usage:**
- When user asks about "my fridge" → use `appliances.refrigerator` context
- When user asks about "my dishwasher" → use `appliances.dishwasher` context
- Ambiguous question like "is this compatible?" → use `current_focus` to know which appliance
- "Tell me about both" → agent can access both appliance contexts

---

## Example Flows

### Flow 1: Simple Query - "Tell me about PS11752778"
```
1. Scope Check: ✓ in scope (mentions part number)
2. Planner: { type: "simple" } - single intent, one tool needed
3. Executor: calls resolve_part("PS11752778") → {resolved: true, ps_number: "PS11752778"}
           then get_part(ps_number="PS11752778")
4. Synthesizer: formats response with key details (streams to user)
```

### Flow 2: Context Query - "Is this compatible with my WDT780SAEM1?"
```
1. Scope Check: ✓ in scope
2. Planner: { type: "simple" } - uses ps_number from session state
3. Executor: calls resolve_part("this part", session_context) → {resolved: true, ps_number: "PS11752778"}
           then check_compatibility(ps_number="PS11752778", model_number="WDT780SAEM1")
4. Synthesizer: "Yes, this part is compatible with your WDT780SAEM1"
```

### Flow 3: Complex Query - "Compare PS11752778 and PS11752779"
```
1. Scope Check: ✓ in scope
2. Planner: {
     type: "complex",
     subtasks: [
       { tool: "get_part", params: { ps_number: "PS11752778" }},
       { tool: "get_part", params: { ps_number: "PS11752779" }}
     ],
     synthesis_hint: "Compare price, ratings, availability, features"
   }
3. Workers: Execute BOTH in parallel
   - Worker 1 → get_part("PS11752778") → result A
   - Worker 2 → get_part("PS11752779") → result B
4. Synthesizer: Combines results with comparison table (streams to user)
```

### Flow 4: Troubleshooting - "My ice maker isn't working"
```
1. Scope Check: ✓ in scope
2. Planner: { type: "complex" } - needs repair info + community stories
3. Workers (parallel):
   - Worker 1: get_repair_instructions(appliance_type="refrigerator", symptom="Ice maker not making ice")
   - Worker 2: search_repair_stories(query="ice maker not working")
4. Synthesizer: Troubleshooting guide with community tips, may ask about model
```

### Flow 5: Installation Help - "How do I install PS11752778?"
```
1. Scope Check: ✓ in scope
2. Planner: { type: "complex" } - needs part details + Q&A + stories (LLM composes)
3. Workers (parallel):
   - Worker 1: get_part("PS11752778") → install_video_url, difficulty
   - Worker 2: search_qna("how to install", ps_number="PS11752778")
   - Worker 3: search_repair_stories("installing", ps_number="PS11752778")
4. Synthesizer: Installation guide with video link and community tips
```

### Flow 6: Resolution Flow - Manufacturer Number
```
User: "Tell me about WPW10321304"
1. Scope Check: ✓ in scope
2. Planner: { type: "simple" }
3. Executor: calls resolve_part("WPW10321304") → {resolved: true, ps_number: "PS11752778", confidence: "matched"}
           then get_part(ps_number="PS11752778")
4. Synthesizer: "I found the part matching manufacturer number WPW10321304..."
```

### Flow 7: Resolution Flow - Not Found
```
User: "Is PS999999 compatible with my fridge?"
1. Scope Check: ✓ in scope
2. Planner: { type: "simple" }
3. Executor: calls resolve_part("PS999999") → {resolved: false, confidence: "not_found"}
4. Synthesizer: "I couldn't find part number PS999999 in our database. Could you double-check the part number?"
```

---

## File Structure

```
backend/
├── __init__.py
├── main.py                  # FastAPI app with SSE streaming endpoint
├── agent/
│   ├── __init__.py
│   ├── graph.py             # LangGraph multi-agent graph definition
│   ├── nodes/
│   │   ├── __init__.py
│   │   ├── scope_check.py   # Scope validation node
│   │   ├── planner.py       # Query analysis & decomposition (haiku)
│   │   ├── executor.py      # Single-task execution with tools (haiku)
│   │   ├── workers.py       # Parallel subtask execution (haiku)
│   │   └── synthesizer.py   # Result combination & response (sonnet)
│   ├── state.py             # Multi-agent state schema
│   └── prompts.py           # System prompts for each agent
├── tools/
│   ├── __init__.py
│   ├── sql_tools.py         # SQL-based tools (parts, compatibility, symptoms)
│   ├── vector_tools.py      # Vector search tools (Q&A, repair stories)
│   ├── executor.py          # Tool execution logic
│   └── definitions.py       # Tool schemas for Claude
├── db/
│   ├── __init__.py
│   └── supabase_client.py   # Supabase connection + queries
└── config.py                # Environment config (API keys, URLs)
```

---

## Summary

### Architecture: Multi-Agent System
- **5 Agents**: Scope Check → Planner → Executor/Workers → Synthesizer
- **Models**: haiku for speed (planning, execution), sonnet for quality (synthesis)
- **Parallel execution** for complex multi-part queries

### Tools: 11 Total

| Category | Tools |
|----------|-------|
| Resolution | `resolve_part`, `resolve_model` |
| Part Data | `search_parts`, `get_part` |
| Compatibility | `check_compatibility`, `get_compatible_parts`, `get_compatible_models` |
| Repair | `get_symptoms`, `get_repair_instructions` |
| Vector Search | `search_qna`, `search_repair_stories` |

### Key Design Principles
1. **Resolution vs Data**: Separate parsing messy input (resolution tools) from fetching clean data (atomic tools)
2. **LLM Composition**: Let the LLM compose atomic tools for complex workflows (e.g., installation = get_part + search_qna + search_repair_stories)
3. **Session Context**: Resolution tools accept session context to handle references like "this part"

### Key Benefits
1. **Cost efficient**: haiku ($0.25/M) for most work, sonnet ($3/M) only for final response
2. **Fast**: Parallel workers for complex queries
3. **Quality**: Sonnet synthesizer ensures coherent responses
4. **Clean planner**: Resolution tools prevent planner from becoming regex/edge-case heavy
5. **Extensible**: Add new tools or agents as needed
