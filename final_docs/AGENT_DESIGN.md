# PartSelect Chat Agent - Agent Architecture Design Document

## Overview

This document details the design and architecture of the PartSelect chat agent, an AI-powered assistant that helps customers with refrigerator and dishwasher parts. The agent is built using **LangGraph** for orchestration, **Claude** (Anthropic) for language understanding, and a combination of SQL and vector databases for data retrieval.

The current production system uses the **v2 architecture** - a simplified, flexible design that replaced an earlier more rigid multi-agent approach.

---

## The Problem We're Solving

PartSelect sells appliance parts online. Customers come with questions like:
- "Tell me about part PS11752778"
- "Is this compatible with my WDT780SAEM1 model?"
- "My ice maker stopped working, what should I check?"
- "How do I install this water valve?"

The challenge is that these queries vary wildly in complexity - some need a single database lookup, others require chaining multiple data sources, and some need troubleshooting guidance built from repair data.

Building a rigid intent-classification system would be brittle. Instead, we wanted something that could **figure out** what tools to use based on the query, handle edge cases gracefully, and stay focused on the domain (no answering questions about microwaves or the weather).

---

## Architecture Evolution: Why We Have v1 and v2

### v1: The Multi-Agent Approach (Original Design)

The initial design was inspired by classic multi-agent patterns: a **Planner** that would analyze each query, classify it as "simple" or "complex", then route to either:
- An **Executor** for simple single-tool queries
- **Worker agents** that could run tools in parallel for complex queries

Finally, a **Synthesizer** would combine results into a coherent response.

```
┌─────────────────────────────────────────────────────────────────┐
│                         V1 ARCHITECTURE                          │
└─────────────────────────────────────────────────────────────────┘

     User Query
         │
         ▼
  ┌─────────────┐
  │ Scope Check │ ─── Out of Scope ──► END (rejection message)
  └─────────────┘
         │ In Scope
         ▼
  ┌─────────────┐
  │   Planner   │ ◄── Haiku model analyzes query
  │   (haiku)   │     Decides: simple vs complex
  └─────────────┘
        │ │
        │ └──── Complex ────┐
        │                   │
   Simple                   ▼
        │           ┌─────────────┐
        ▼           │   Workers   │ ◄── Parallel execution
  ┌──────────┐      │   (haiku)   │     Multiple tool calls
  │ Executor │      └─────────────┘
  │  (haiku) │              │
  └──────────┘              │
        │                   │
        └───────┬───────────┘
                ▼
        ┌─────────────┐
        │ Synthesizer │ ◄── Sonnet model for quality
        │  (sonnet)   │     Generates final response
        └─────────────┘
                │
                ▼
               END
```

**The Problems With v1:**
1. **The simple/complex distinction was fuzzy.** What makes a query "complex"? Comparing two parts? Asking for installation help? The planner had to make arbitrary decisions.
2. **Pre-planning tool calls was limiting.** For complex queries, the planner had to predict which tools would be needed upfront - but the right tools often depend on what earlier tools return.
3. **More moving parts = more failure modes.** The routing logic between nodes added complexity without proportional benefit.
4. **Adding tools meant updating multiple files** - the tool, the planner's knowledge of it, and sometimes the routing logic.

### v2: The Simplified ReAct Approach (Current)

The key insight was: **just let the LLM figure it out**.

Instead of a planner deciding upfront, we use a single **Executor** powered by a ReAct agent that can call tools iteratively until it has what it needs. The LLM sees all available tools, the user's query, and any session context - then it decides which tools to call and in what order.

```
┌─────────────────────────────────────────────────────────────────┐
│                         V2 ARCHITECTURE                          │
└─────────────────────────────────────────────────────────────────┘

     User Query
         │
         ▼
  ┌─────────────────┐
  │   Scope Check   │ ─── Out of Scope ──► END (rejection)
  │ (text-based)    │
  └─────────────────┘
         │ In Scope
         ▼
  ┌─────────────────┐
  │    Executor     │ ◄── Single ReAct agent (Haiku)
  │    (ReAct)      │     Calls tools dynamically
  │                 │     Loops until done
  └─────────────────┘
         │
         ▼
  ┌─────────────────┐
  │ Secondary Scope │ ◄── Validates fetched data
  │     Check       │     Catches out-of-scope parts
  └─────────────────┘
         │ In Scope
         ▼
  ┌─────────────────┐
  │   Synthesizer   │ ◄── Sonnet model for quality
  │    (sonnet)     │     Formats the final response
  └─────────────────┘
         │
         ▼
        END
```

**What Changed:**
- Removed the Planner node entirely
- Removed the Workers node (parallel execution)
- Removed the simple/complex routing logic
- Added a Secondary Scope Check (data-based validation)

**Why This Works Better:**

The ReAct pattern gives the Executor a natural loop: observe (what tools are available, what the query is), think (what do I need), act (call a tool), observe again (what did I get back), think again (do I need more?), etc.

For a simple query like "tell me about PS11752778", the Executor calls `get_part()` once and stops. For "compare PS11752778 and PS11752779", it calls `get_part()` twice. For "my ice maker isn't working, what parts might I need for my WDT780SAEM1?", it might call `get_symptoms()` then `get_compatible_parts()`.

The LLM figures this out based on well-designed prompts with workflow patterns - no rigid routing needed.

---

## The LangGraph Flow (v2)

Here's the actual graph structure in code-visual form:

```
         ┌───────────┐
         │ __start__ │
         └─────┬─────┘
               │
               ▼
       ┌─────────────┐
       │ scope_check │
       └──────┬──────┘
              │
      ┌───────┴───────┐
      │               │
 (in scope)      (out of scope)
      │               │
      ▼               ▼
 ┌──────────┐       END ─► Rejection message
 │ executor │
 └────┬─────┘
      │
      ▼
┌───────────────────┐
│secondary_scope_   │
│     check         │
└────────┬──────────┘
         │
   ┌─────┴─────┐
   │           │
(pass)     (reject)
   │           │
   ▼           ▼
┌───────────┐  END ─► Out-of-scope part message
│synthesizer│
└─────┬─────┘
      │
      ▼
     END
```

Each node is an async Python function that receives the `AgentState` and returns a dict of updates to that state.

---

## Deep Dive: Each Node

### 1. Scope Check Node

**Purpose:** Fast rejection of obviously off-topic queries.

**How It Works:**
1. First tries **rule-based patterns** (regex matching for keywords like "refrigerator", "dishwasher", "PS" numbers, common part names)
2. If rules are inconclusive, falls back to **LLM classification** using Haiku

The rule-based check catches obvious cases fast (~0ms) while the LLM handles ambiguity.

**Example Pattern Matching:**
```python
IN_SCOPE_KEYWORDS = [
    r"\brefrigerator\b", r"\bfridge\b", r"\bdishwasher\b",
    r"\bps\d+\b",  # PS numbers like PS11752778
    r"\bice\s*maker\b", r"\bwater\s*filter\b",
    # ... etc
]

OUT_OF_SCOPE_KEYWORDS = [
    r"\bwashing\s*machine\b", r"\bdryer\b", r"\bmicrowave\b",
    # ... etc
]
```

**Why Both Methods?**
- Rules alone miss context ("tell me more about that" - is it in scope? depends on conversation history)
- LLM alone is slower and costs money for every query
- Hybrid gives us speed for common cases and accuracy for edge cases

### 2. Executor Node

**Purpose:** The brain of the operation. Uses a ReAct agent to call tools and gather information.

**Key Design Decisions:**

**a) Model Choice: Haiku for Speed**
The Executor uses Claude Haiku (not Sonnet). Tool calling needs to be fast, and Haiku is good enough for deciding "I should call get_part() with this PS number." We save Sonnet for the final synthesis where quality matters most.

**b) Workflow Patterns in Prompts**
Instead of complex routing logic, we teach the Executor via prompts. The prompt includes patterns like:

```
### Pattern 1: Part Lookup
User wants to FIND or BUY a specific part (PS number, manufacturer number, URL)
1. If not a PS number → call resolve_part() first
2. For basic part info → call ONLY get_part()
3. For additional info (if explicitly requested):
   - Quality/reviews → search_reviews()
   - Q&A/technical → search_qna()
   - Installation help → search_repair_stories()

### Pattern 2: Symptom/Troubleshooting
User describes a problem ("ice maker not working")
2a. General symptom → call get_symptoms()
2b. Specific part check → call get_repair_instructions()

### Pattern 3: Search/Browse
User wants to find parts → call search_parts()

### Pattern 4: Compatibility Check
User asks if part fits model → call check_compatibility()
```

These patterns guide the LLM without being rigid rules. The LLM can deviate when it makes sense.

**c) Automatic Fallback Scraping**
When `get_part()` returns "not found", the Executor automatically triggers `scrape_part_live()` to fetch the part directly from PartSelect in real-time. This takes 5-30 seconds but ensures we can answer questions about any part on the site, not just what's in our database.

**d) Session Context Injection**
The Executor prompt includes recent conversation history and discussed parts so it understands references like "this part" or "the first one."

### 3. Secondary Scope Check Node

**Purpose:** Catch out-of-scope parts that slipped through the first check.

**The Problem It Solves:**
User asks: "Tell me about PS16688554"
- Primary scope check: PASS (it's a PS number, looks like a part)
- Executor fetches the part...
- Part data reveals: "DeWALT Pole, Middle Extension" - it's a chainsaw part!

The secondary scope check examines actual data from tool results and rejects anything that's not refrigerator/dishwasher.

**How It Works:**
1. Scans all tool result messages
2. Looks for `appliance_type` fields
3. If any part has appliance_type outside ["refrigerator", "dishwasher"], rejects the entire query
4. Also removes rejected parts from session state (so user can't reference them later)

**LLM Classification for Unknown Types:**
When scraping parts not in our database, the appliance_type might be unknown. The system uses a Haiku LLM call to classify based on part name, description, compatible model names, and review content.

### 4. Synthesizer Node

**Purpose:** Take raw tool results and create a helpful, well-formatted response.

**Model Choice: Sonnet for Quality**
This is where quality matters most - the customer sees this response. We use Claude Sonnet here for better language and formatting.

**Response Guidelines (from prompt):**
- Be concise but complete
- Include practical info (price, availability, difficulty ratings)
- Link formatting with emojis (🎥 for videos, 🔗 for other links)
- Always include PS numbers for every part mentioned (critical for part cards)
- For large compatibility lists (50+ models), summarize by brand instead of listing all

**Streaming Support:**
The Synthesizer supports token-by-token streaming via SSE, so users see the response typing out in real-time rather than waiting for the full generation.

---

## The Tool System

### Tool Registry Pattern

Tools are registered via a decorator pattern that auto-generates documentation:

```python
@registry.register(category="part")
def get_part(ps_number: str) -> dict:
    """
    Get full details for a part by its PS number.
    ...
    """
```

When a new tool is added, it's automatically:
1. Available to the Executor's ReAct agent
2. Documented in the Executor prompt
3. Grouped by category in the tool docs

**Categories:**
- `resolution` - Convert messy input (URLs, manufacturer numbers, "this part") into clean PS numbers
- `part` - Fetch part data, check compatibility
- `symptom` - Troubleshooting and repair guidance
- `search` - Browse/filter parts
- `vector` - Semantic search over Q&A, reviews, repair stories
- `scrape` - Live scraping fallback

### Tool Design Philosophy: Resolution vs Data

A key design insight: separate **parsing messy user input** from **fetching clean data**.

**Resolution tools** handle the mess:
- `resolve_part("WPW10321304")` → looks up manufacturer number, returns `{ps_number: "PS11752778"}`
- `resolve_part("https://partselect.com/PS11752778")` → extracts PS from URL
- `resolve_part("this part", session_context)` → checks session for recent part

**Data tools** expect clean inputs:
- `get_part("PS11752778")` → always takes a PS number
- `check_compatibility("PS11752778", "WDT780SAEM1")` → takes PS number and model

This keeps data tools simple and lets the LLM compose them naturally.

### Complete Tool Inventory

| Tool | Category | Purpose |
|------|----------|---------|
| `resolve_part` | resolution | Parse part references → PS number |
| `resolve_model` | resolution | Parse model references with fuzzy match |
| `get_part` | part | Get full part details |
| `check_compatibility` | part | Check if part fits model |
| `get_compatible_parts` | part | All parts for a model |
| `get_compatible_models` | part | All models for a part |
| `get_symptoms` | symptom | List symptoms for appliance type |
| `get_repair_instructions` | symptom | Step-by-step diagnostic guides |
| `search_parts` | search | Text/filter search |
| `search_parts_semantic` | search | Vector similarity search |
| `search_qna` | vector | Search part Q&A |
| `search_repair_stories` | vector | Search customer repair experiences |
| `search_reviews` | vector | Search customer reviews |
| `scrape_part_live` | scrape | Real-time scraping fallback |

---

## Data Architecture

### SQL Tables (Supabase/PostgreSQL)

**`parts`** - The product catalog
- ps_number (PK), part_name, part_type, price, description
- install_difficulty, install_time, install_video_url
- average_rating, num_reviews, availability
- appliance_type, brand

**`model_compatibility`** - Part-to-model relationships
- part_id (FK), model_number, brand, description
- Composite PK: (part_id, model_number)

**`repair_symptoms`** - Common problems
- appliance_type, symptom, percentage (how common)
- related parts, video_url, difficulty

**`repair_instructions`** - Step-by-step diagnostics
- appliance_type, symptom, part_type
- instructions text

### Vector Tables (pgvector)

**`qna_embeddings`** - Customer Q&A from part pages
- Embedded for semantic search
- Linked to parts by ps_number

**`repair_stories_embeddings`** - Customer repair narratives
- "I fixed my ice maker by replacing the water inlet valve..."
- Difficulty ratings, repair time

**`reviews_embeddings`** - Product reviews
- Rating, title, content
- Embedded for "is this part good?" queries

### Why This Split?

SQL for **ground truth** answers:
- "What's the price?" → exact lookup
- "Does this fit my model?" → exact match

Vector for **"find me something relevant"**:
- "What do people say about installation?" → semantic similarity
- "Has anyone had clicking noises?" → find similar experiences

---

## Session Management

### State Across Turns

```python
class SessionState:
    all_discussed_parts: list[str]  # PS numbers from this conversation
    conversation_history: list[Message]  # Last 10 messages
```

**Why Track Discussed Parts?**
When user says "this part" or "the first one", the Executor needs to know what parts were recently mentioned. The session maintains a list that's updated after each turn.

**Filtering After Response:**
After the Synthesizer generates a response, we extract which PS numbers it actually mentions and filter the session's discussed parts to only those. This prevents accumulating stale references.

### Conversation History

We keep the last 10 messages (5 exchanges) in session. This gets injected into both the Scope Check (for understanding follow-up queries) and the Executor (for context).

---

## The Live Scraping Fallback

### The Problem

We can't possibly have every part pre-scraped. PartSelect has millions of parts across all appliance types. But we want to answer questions about any part a user might ask about.

### The Solution

When `get_part()` returns "not found", the Executor's fallback logic kicks in:

```python
if tool_name == 'get_part' and "not found" in error_msg:
    # Auto-trigger live scrape
    scraped_data = scrape_tool.invoke({"ps_number": ps_number})
    # Inject into messages for synthesizer
    messages.append(ToolMessage(content=json.dumps(scraped_data), ...))
```

The scraper:
1. Opens headless Chrome
2. Navigates to PartSelect homepage
3. Searches for the PS number
4. Follows redirect to part page
5. Extracts all data (part info, compatible models, Q&A, reviews, repair stories)
6. Returns comprehensive dict

**Performance:** 5-30 seconds. Slow, but better than "I don't know."

**Comprehensive Data Strategy:**
The scrape returns EVERYTHING so follow-up questions don't need another scrape. The Executor prompt explicitly says:
> "After a successful scrape, DON'T call get_compatible_models() - use the _compatible_models field from the scrape result."

---

## Cost Optimization

### Model Selection by Role

| Node | Model | Why |
|------|-------|-----|
| Scope Check (LLM fallback) | Haiku | Fast, cheap, simple yes/no |
| Executor | Haiku | Good enough for tool selection |
| LLM Appliance Classifier | Haiku | Quick classification |
| Synthesizer | Sonnet | Quality matters for final response |

**Rough Costs (per 1M tokens):**
- Haiku: ~$0.25 input / $1.25 output
- Sonnet: ~$3 input / $15 output

By using Haiku for all the "thinking" work and Sonnet only for the final polish, we get quality where users see it while keeping costs down.

### Avoiding Expensive Tool Calls

The Executor prompt explicitly warns:
> "get_compatible_models() is EXPENSIVE (can fetch thousands of models) - only call when user explicitly asks 'what models does this fit?'"

For a simple "tell me about PS12345", we don't auto-fetch compatibility unless asked.

---

## Frontend Integration

### API Endpoints

```
POST /chat          - Non-streaming, returns full response
POST /chat/stream   - SSE streaming, returns tokens progressively
GET  /health        - Health check
```

### Part Cards

When the Synthesizer mentions PS numbers in its response, the backend extracts matching parts from tool results and sends them as structured `PartCard` objects:

```json
{
  "message": "The Ice Maker Assembly (PS11752778) costs $89.99...",
  "parts": [
    {
      "ps_number": "PS11752778",
      "part_name": "Ice Maker Assembly",
      "part_price": 89.99,
      "average_rating": 4.8,
      "availability": "In Stock",
      "part_url": "https://..."
    }
  ]
}
```

The frontend renders these as clickable cards with key info and "View on PartSelect" links.

**Critical Rule:** Part cards only display for PS numbers explicitly mentioned in the response text. This prevents showing irrelevant parts from intermediate tool calls.

---

## Tradeoffs and Decisions

### Why ReAct Over Pre-Planning?

**Pro:** Flexibility. The LLM can adapt to unexpected situations, chain tools in orders we didn't anticipate, and handle edge cases naturally.

**Con:** Less deterministic. The same query might take slightly different tool paths on different runs. For a customer support application, this is fine - correctness matters more than exact reproducibility.

### Why Two Scope Checks?

We could just have one scope check after fetching data. But that means:
- Wasting LLM calls on obvious spam ("what's the weather?")
- Slower rejection for clearly off-topic queries

The two-stage approach gives us fast rejection for obvious cases and thorough validation for edge cases.

### Why Not Parallel Tool Execution?

v1 had worker agents for parallel execution. We removed this because:
1. Most queries don't benefit (single part lookup, single symptom query)
2. LangGraph's ReAct pattern is inherently sequential
3. The complexity wasn't worth the marginal speed gain
4. When we do need multiple parts, the ReAct loop handles them sequentially just fine

If performance becomes critical for "compare these 5 parts" queries, we could add parallel execution back - but at a tool level, not an agent level.

### Why Haiku for Executor?

Initially we considered Sonnet for everything. But Sonnet is 10-15x more expensive, and the Executor's job is relatively simple: "look at this query, look at these tools, pick which one to call." Haiku handles this well.

The quality-sensitive part is the Synthesizer, which generates the customer-facing response. That's where Sonnet shines.

---

## Extensibility

### Adding a New Tool

1. Create the function with `@registry.register(category="...")` decorator
2. Write a clear docstring (first line becomes tool description in prompts)
3. Done - it's automatically available to the Executor

```python
@registry.register(category="part")
def get_part_diagram(ps_number: str) -> dict:
    """Get installation diagram for a part."""
    # implementation
```

### Adding a New Appliance Type

1. Update scope check keywords (in_scope patterns)
2. Add appliance type to the secondary scope check allowed list
3. Scrape data for the new appliance type
4. The agent automatically handles it - no prompt changes needed

### Adding Order Support

The architecture was designed with this in mind. New tools like:
- `check_order_status(order_id)`
- `start_return(order_id, reason)`
- `get_shipping_estimate(ps_number, zip_code)`

Would plug in via the registry pattern. The Executor would learn to use them through prompt patterns.

---

## What's Not Included (Future Work)

1. **Persistence:** Scraped parts aren't saved to the database. Each scrape is one-time.
2. **User Authentication:** No login, no order history lookup.
3. **Cart/Checkout:** Currently just informational, no transaction support.
4. **Proactive Verification:** No "verifier" node to double-check facts before responding.
5. **Analytics:** No tracking of query types, tool usage, or success rates.
6. **Rate Limiting:** No protection against scraping abuse.

---

## Summary

The PartSelect agent uses a **simplified LangGraph architecture** that prioritizes flexibility over rigid workflows. By trusting a well-prompted ReAct agent to figure out which tools to call, we get a system that handles the variety of customer queries without complex routing logic.

Key architectural decisions:
- **Two-stage scope checking** for fast rejection and thorough validation
- **ReAct executor** instead of pre-planned workflows
- **Haiku for thinking, Sonnet for output** to optimize cost/quality
- **Live scraping fallback** to handle any part on the site
- **Tool registry pattern** for easy extensibility

The v2 architecture is simpler to maintain, easier to extend, and handles edge cases more gracefully than the original v1 multi-agent design.
