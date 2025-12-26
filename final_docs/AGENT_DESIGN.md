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

**Purpose:** Uses a ReAct agent to call tools and gather information.

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
- Part data reveals: "DeWALT Pole, Middle Extension" - it's a chainsaw part

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
- `resolve_part("this part", session_context)` → checks session for recent part

**Data tools** expect clean inputs:
- `get_part("PS11752778")` → always takes a PS number
- `check_compatibility("PS11752778", "WDT780SAEM1")` → takes PS number and model

This keeps data tools simple and lets the LLM compose them naturally. This is useful for us since PS number is an entry point into almost all of our tool calls and data. So finding ways to resolve to a PS number is imporant. 

### Complete Tool Inventory

| Tool | Category | Input Strictness | Purpose |
|------|----------|------------------|---------|
| `resolve_part` | resolution | **Flexible** - accepts PS#, manufacturer#, URL, "this part", natural language | Parse messy input → PS number |
| `resolve_model` | resolution | **Flexible** - fuzzy matching on partial model numbers | Parse model references with fuzzy match |
| `get_part` | part | **Strict** - requires exact PS number | Get full part details |
| `check_compatibility` | part | **Strict** - requires exact PS# and model# | Check if part fits model |
| `get_compatible_parts` | part | **Strict** - requires exact model number | All parts for a model |
| `get_compatible_models` | part | **Strict** - requires exact PS number | All models for a part |
| `get_symptoms` | symptom | **Flexible** - LLM matches natural language to canonical symptoms | List symptoms for appliance type |
| `get_repair_instructions` | symptom | **Flexible** - symptom uses LLM matching | Step-by-step diagnostic guides |
| `search_parts` | search | **Flexible** - accepts natural language queries and filters | Text/filter search |
| `search_parts_semantic` | search | **Flexible** - semantic/vector similarity on descriptions | Vector similarity search |
| `search_qna` | vector | **Hybrid** - ps_number strict, query flexible | Search part Q&A |
| `search_repair_stories` | vector | **Hybrid** - ps_number strict, query flexible | Search customer repair experiences |
| `search_reviews` | vector | **Hybrid** - ps_number strict, query flexible | Search customer reviews |
| `scrape_part_live` | scrape | **Strict** - requires exact PS number | Real-time scraping fallback |

**Input Strictness Explained:**
- **Strict**: Requires exact identifiers (PS numbers, model numbers). Will fail or return errors on malformed input.
- **Flexible**: Accepts natural language, partial matches, or messy user input. Uses regex, fuzzy matching, or LLM to interpret.
- **Hybrid**: Some parameters are strict (usually `ps_number`), others accept natural language (usually `query`).

**The Pattern:** `resolve_part()` is the gateway - it converts messy user input into clean PS numbers that strict tools can use. The LLM learns to call resolution tools first, then pass clean identifiers to data tools.

---

## Data Architecture

### SQL (Primary) Tables

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

### Vector (Primary) Tables 

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

Supabase allows us to combine SQL and vector embeddings into one. 

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
The scrape returns EVERYTHING (reviews, qna, part details, compatible models, etc) so follow-up questions don't need another scrape. The Executor prompt explicitly says:
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
- Slower rejection for clearly off-topic queries (we'd be wasting time)

The two-stage approach gives us fast rejection for obvious cases and thorough validation for edge cases.

### Why Not Parallel Tool Execution?

v1 had a Planner → Worker architecture designed for parallel execution. We removed it because **it fundamentally didn't work**.

**The Core Problem: Planning Is Hard**

The Planner was supposed to analyze a query and generate a structured execution plan - which tools to call, with what arguments, in what order. But this required the Planner to:

1. **Understand the full tool catalog** and their interdependencies
2. **Predict what data would be needed** before seeing any results
3. **Generate exact arguments** for tools it hadn't called yet
4. **Handle conditional logic** ("if part not found, then scrape")

In practice, the Planner couldn't reliably do any of this. It would:
- Call `get_part()` without first resolving the PS number
- Forget to include required parameters like `ps_number` for vector searches
- Generate overly ambitious plans that fetched unnecessary data
- Miss edge cases that only become apparent after seeing tool results

**Example of Planner Failure:**

User: "Tell me about the water filter for my WDT780SAEM1"

What the Planner generated:
```json
{
  "tasks": [
    {"tool": "get_compatible_parts", "args": {"model_number": "WDT780SAEM1", "part_type": "water filter"}},
    {"tool": "search_reviews", "args": {"query": "water filter quality"}}
  ]
}
```

Problems:
- `search_reviews` needs a `ps_number`, but we don't have one yet
- What if multiple water filters are compatible? Which one do we review?
- The plan assumes success - no fallback if no parts found

**What ReAct Does Better:**

The ReAct pattern solves this by making decisions *after* seeing results:

1. Call `get_compatible_parts(model_number="WDT780SAEM1", part_type="water filter")`
2. See results: 3 water filters returned
3. Decide: User probably wants the top-rated one, call `get_part(ps_number="PS...")` for that
4. If user asks about reviews, *now* we have the PS number to call `search_reviews()`

The LLM adapts to what it learns. It can't plan perfectly upfront, but it can react intelligently to each step.

**Secondary Reasons:**

1. Most queries don't benefit from parallelism anyway (single part lookup, single symptom)
2. The added complexity created more bugs than it solved
3. Debugging was a nightmare - which worker failed? Why?
4. LangGraph's ReAct loop handles sequential multi-tool calls cleanly

**When We'd Reconsider:**

If "compare these 5 parts" queries become a significant percentage of traffic AND latency becomes unacceptable, we could add parallel execution at the *tool level* - not the agent level. The Executor could detect comparison queries and fan out tool calls. But the decision-making would still be reactive, not pre-planned.

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

## Edge Cases and Special Handling

Building an agent that handles real user queries means dealing with messy input, ambiguous requests, and unexpected situations. This section documents the edge cases we encountered during testing and how we solved them.

### Scope Checking Edge Cases

**1. The Follow-Up Query Problem**

User: "Tell me about part PS11752778"
*[Agent gives helpful response about an ice maker]*
User: "Is it easy to install?"

The second query has no refrigerator keywords. A naive scope check would reject it as out-of-scope. We solved this by:
- Passing conversation history to the LLM scope check
- Adding this prompt context: *"If the current query is a follow-up to this conversation about refrigerators/dishwashers, it's IN_SCOPE"*
- The rule-based check returns `None` for ambiguous queries, triggering the LLM fallback which sees the context

**2. PS Numbers Always Pass**

Any query containing a PS number pattern (`PS\d+`) automatically passes scope check via rules. This is intentional - if someone has a part number, they're clearly trying to look up a specific part, even if they don't mention appliances. The secondary scope check catches cases where the PS number turns out to be for a microwave.

**3. Brand Names as Signals**

Queries mentioning Whirlpool, Samsung, KitchenAid, etc. pass scope check even without explicit appliance mention. "My Whirlpool is making noise" is almost certainly about an appliance, even though it doesn't say which one. We err on the side of accepting these and let the conversation clarify.

---

### The Secondary Scope Check Discovery

This was our most surprising edge case discovery. Consider:

User: "Tell me about PS16688554"

Primary scope check: **PASS** (it's a PS number!)
Executor fetches part from database or scrapes it...
Result: "DeWALT Pole, Middle Extension" - it's a chainsaw part!

PartSelect sells parts for many appliance types. PS numbers don't encode the appliance type. We needed a second gate that checks *actual data*, not just the query text.

**How Secondary Scope Check Works:**

1. Scans all tool result messages after the Executor runs
2. Looks for `appliance_type` fields in returned data
3. Rejects anything not in `['refrigerator', 'dishwasher']`
4. Cleans up session state (removes rejected parts so user can't reference them)

**The LLM Appliance Classifier:**

For live-scraped parts not in our database, the `appliance_type` field might be empty. We use Haiku to classify:

```python
# Context gathered for classification
- Part name: "DeWALT Pole, Middle Extension"
- Description: "For pole saws and chainsaws..."
- Compatible models: "DCPH820BH, DCPS620B..."
- Sample reviews mentioning "chainsaw", "yard work"
```

The LLM returns "chainsaw" and the part is rejected with a helpful message: *"I'm sorry, but DeWALT Pole, Middle Extension (PS16688554) is a part for a Chainsaw, not a refrigerator or dishwasher."*

---

### Session Reference Handling

**Single Part References**

"This part", "the part", "that one" - resolved via `session.all_discussed_parts[-1]` (most recent).

**Multiple Part References**

Harder. "Which of these is easiest to install?" or "Compare them" requires identifying ALL recently discussed parts.

The Executor prompt explicitly handles this:
```
**Multiple part reference** ("these parts", "their installations", "which is easiest"):
1. Identify ALL PS numbers from session context
2. Call the appropriate tool for EACH part - do not just pick one!
```

We found the LLM would sometimes lazily just pick the first part if not explicitly instructed otherwise.

**Session Cleanup After Out-of-Scope Rejection**

If user asks about a chainsaw part, we reject it - but we also remove that PS number from session. Otherwise:

User: "Tell me about PS16688554" (chainsaw)
*[Rejected with helpful message]*
User: "How about this part instead: PS11752778" (ice maker)
*[Agent adds to session: [PS16688554, PS11752778]]*
User: "Compare them"
*[Agent tries to compare chainsaw and ice maker!]*

By cleaning session state, follow-ups work correctly.

---

### Executor Edge Cases

**1. The Automatic Scrape Fallback**

When `get_part()` returns `"error": "Part PS12345 not found in database"`, the Executor automatically triggers `scrape_part_live()`. But we had to prevent double-scraping:

```python
# Check if scrape was already called by the agent
scrape_already_called = any(
    hasattr(msg, 'name') and msg.name == 'scrape_part_live'
    for msg in messages
    if hasattr(msg, 'type') and msg.type == 'tool'
)

if not scrape_already_called:
    # Only then trigger the fallback
```

Sometimes the LLM would explicitly call `scrape_part_live()` before getting the `get_part()` error back. Without this check, we'd scrape twice.

**2. Don't Re-Query After Scraping**

After a successful scrape, the returned data includes `_compatible_models`, `_qna_data`, `_repair_stories`, `_reviews_data`. If user asks "what models does this fit?", we need to use the scraped data, not call `get_compatible_models()` (which would return empty since the part isn't in DB).

The prompt explicitly instructs:
```
**DO NOT call additional database tools after a successful scrape:**
- ❌ get_compatible_models(ps_number) - use _compatible_models from scrape instead
- ❌ search_qna(ps_number, query) - use _qna_data instead
```

This was a frequent failure mode before we added explicit instructions.

**3. Maintaining Symptom Context**

User: "My ice maker isn't working"
*[Agent identifies symptom: "Ice maker not making ice"]*
User: "How do I check the water valve?"

The second query should use the established symptom context, not start fresh. The Executor prompt includes:
```
**MAINTAIN SYMPTOM CONTEXT**: Continue using the SAME symptom for all related
part checks - don't switch symptoms unless the user explicitly asks about
a different problem
```

Without this, asking about different parts would reset context and produce inconsistent repair instructions.

---

### Tool-Level Edge Cases

**1. resolve_part() Input Normalization**

Users provide part references in many formats:
- PS numbers: `"PS11752778"`, `"ps11752778"`, `"PS 11752778"`
- Manufacturer numbers: `"WPW10321304"`, `"W10321304"`
- Session references: `"this part"`, `"the one we discussed"`
- Text descriptions: `"water filter"`, `"ice maker assembly"`

The `resolve_part()` tool handles each case in priority order:
1. Session reference check (if context provided)
2. PS number format validation
3. Manufacturer number exact match
4. Manufacturer number partial/fuzzy match
5. Text search fallback

**2. LLM-Based Symptom Matching**

User describes problems in many ways:
- "Ice maker not working"
- "No ice coming out"
- "Ice maker stopped making ice"
- "Fridge won't make ice"

Our database has canonical symptom names like "Ice maker not making ice". We use Haiku to map user language to database entries:

```python
prompt = f"""Given the user's problem: "{user_symptom}"
Match to one of these:
- Ice maker not making ice
- Ice maker making too much ice
- Refrigerator not cooling
- ...

Respond with the EXACT matching symptom or NONE."""
```

This fuzzy matching dramatically improved symptom-based troubleshooting.

**4. Empty Query Handling for Vector Tools**

If user just asks "show me Q&A for this part" without a specific question, we skip semantic search and return all Q&A sorted by helpfulness:

```python
if not query or query.strip() == "":
    results = db.get_qna_by_ps_number(ps_number, limit=limit)
else:
    query_embedding = generate_embedding(query)
    results = db.search_qna(query_embedding=query_embedding, ...)
```

**5. Embedding Model Recovery**

SentenceTransformer can occasionally enter a corrupted state (meta tensor errors). We handle this gracefully:

```python
try:
    embedding = model.encode(text)
except RuntimeError as e:
    if "meta tensor" in str(e):
        _embedding_model = None  # Reset
        model = get_embedding_model()  # Recreate
        embedding = model.encode(text)  # Retry
```

---

### Synthesizer Edge Cases

**1. Part Card Filtering**

Not every part that appears in tool results should become a part card. If user asks about symptoms, we might fetch related parts for context but shouldn't show cards for all of them.

Solution: Extract PS numbers mentioned in the final response text and only show cards for those:

```python
mentioned_ps_numbers = extract_mentioned_ps_numbers(response.content)

if mentioned_ps_numbers:
    parts = [p for p in all_parts if p["ps_number"] in mentioned_ps_numbers]
else:
    parts = []  # No cards for symptom-only responses
```

This prevents overwhelming users with irrelevant part cards.

**2. PS Numbers MUST Be in Response**

The inverse problem: the Synthesizer might describe a part without including its PS number. Then no card appears. The prompt explicitly states:

```
**ALWAYS include PS numbers for EVERY part you mention** - This is CRITICAL.
Every single part you recommend or discuss MUST include its PS number in
parentheses (e.g., "Water Valve (PS12070506)").

Part cards ONLY display for parts with PS numbers in your response.
No PS number = no card.
```

**3. Large Result Summarization**

User: "What models does this water filter fit?"
*[Database returns 2,847 compatible models]*

Listing them all would be overwhelming and unhelpful. The prompt instructs:
```
If 50+ compatible models: Don't list them all - summarize with count and
group by brand
- "This part fits 2,847 models including Whirlpool (856 models),
   KitchenAid (423), Maytag (312)..."
```

**4. Don't Ask for Information You Can't Use**

Early versions would say things like "If you can provide your model number, I can check compatibility" even when the conversation didn't need it. The prompt now says:

```
**NEVER ask for information you can't actually use or process**
- Don't ask for model numbers unless the tool results indicate you need them
- Don't make up instructions about physical appliances
- If the tool results don't provide an answer, say so directly
```

---

### Live Scraping Edge Cases

**1. PS Number Format Validation**

```python
if not ps_number.strip().startswith("PS"):
    return {"error": "Invalid PS number format. Must start with 'PS'"}
```

Prevents wasting time trying to scrape malformed input.

**2. Non-Existent Part Detection**

When you search for a PS number on PartSelect, it either:
- Redirects to the part page (exists)
- Shows search results / no results (doesn't exist)

We detect this by waiting for URL redirect:
```python
try:
    WebDriverWait(driver, 15).until(EC.url_contains("partselect.com/PS"))
except TimeoutException:
    return {"error": f"Part {ps_number} not found on PartSelect"}
```

**3.Verify Part Scraped**

We verify that our scraped PS matched our requested one: 
```python
scraped_ps = part_data.get("ps_number", "")
if scraped_ps != ps_number_clean:
    part_data["_scrape_warning"] = f"Requested {ps_number} but got {scraped_ps}"
```

**5. Appliance Classification for Unknown Types**

Scraped parts might not have appliance_type in the extracted data. We classify using context:
```python
if not current_appliance_type:
    classified_type = classify_appliance_type_with_llm(part_data)
    part_data["appliance_type"] = classified_type
    part_data["_appliance_type_source"] = "llm_classified"
```

The classifier uses part name, description, compatible model descriptions, and sample reviews to infer type.

---

### Prompt Engineering Lessons

**Pattern 2a vs 2b: The Symptom-Troubleshooting Split**

We initially had one "symptom" pattern, but user queries differ:
- "My ice maker isn't working" → Show overview: common causes, parts to check, frequency
- "How do I check the water valve?" → Give step-by-step diagnostic instructions

The first should NOT call `get_repair_instructions()`. The second should NOT return part cards (it's troubleshooting, not shopping). We split into explicit Pattern 2a and 2b.

**The Expensive Tool Warning**

`get_compatible_models()` can return thousands of results. Early versions called it automatically for basic part lookups. Now the prompt warns:

```
**IMPORTANT:** Do NOT call compatibility tools unless:
- User explicitly asks "what models does this fit?"
- Compatibility tools are EXPENSIVE - only use when specifically requested
```

**The "Don't Over-Inform" Rule**

The Synthesizer would add unsolicited information: "You might also want to consider..." or "Additionally, here are some tips...". The prompt now says:

```
Do not give too much information beyond what they asked for. Answer their
question directly and fully but don't waste text on not relevant details.
```

**All Parts in Symptom Responses**

The symptom data has a `parts` field like: `"Water Inlet Valve, Ice Maker Assembly, Water Filter"`. Early versions would only mention one part. The prompt now emphasizes:

```
IMPORTANT: The 'parts' field is comma-separated text - list every single
part mentioned, don't cherry-pick just one
```

---


#### 12. Input Validation at API Layer

```python
class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    session_id: str | None = None
    session_state: dict | None = None
```

**Why 2000 Character Limit?**
- Prevents abuse (huge inputs)
- Keeps token counts reasonable
- Real user queries are almost never this long

**Session State Restoration:**
```python
def get_or_create_session(session_id: str | None, session_state: dict | None):
    if session_id and session_id in sessions:
        return session_id, sessions[session_id]

    if session_state:
        try:
            session = SessionState(**session_state)
            new_id = session_id or str(uuid.uuid4())
            sessions[new_id] = session
            return new_id, session
        except Exception:
            pass  # Fall through to create new

    # Create new session
    new_id = session_id or str(uuid.uuid4())
    sessions[new_id] = SessionState()
    return new_id, sessions[new_id]
```

**Why Try/Except on session_state?** Frontend might send malformed data. Silent fallback to new session is better than crashing.

#### 13. Streaming Response Accumulation

```python
async def generate_sse_events(query: str, session: SessionState, session_id: str):
    full_response = ""  # Accumulate for history
    session_container = {}  # Mutable container for streaming

    async for token in run_agent_streaming(
        query=query,
        session=session,
        session_container=session_container
    ):
        full_response += token
        yield {"event": "token", "data": json.dumps({"token": token})}

    # After streaming complete
    updated_session = session_container.get("session", session)

    # Accumulate history with FULL response
    updated_session.conversation_history.append(
        Message(role="assistant", content=full_response)
    )
```

**Why session_container Dict?** Python's async generators can't return values. We pass a mutable container that the streaming function populates with the final session state and parts list.

#### 14. The resolve_part() Priority Chain

```python
def resolve_part(input: str, session_context: dict | None = None) -> dict:
    input_clean = input.strip()

    # Priority 1: Session reference ("this part", "the part")
    session_refs = ["this part", "the part", "that part", "it", "this one"]
    if session_context and any(ref in input_clean.lower() for ref in session_refs):
        current_part = session_context.get("current_part")
        if current_part:
            result = db.validate_part(current_part)
            if result.get("found"):
                return {"resolved": True, "ps_number": current_part, "confidence": "session", ...}

    # Priority 2: PartSelect URL extraction
    url_patterns = [
        r'partselect\.com/PS(\d+)',
        r'PS(\d+)',
    ]
    for pattern in url_patterns:
        match = re.search(pattern, input_clean, re.IGNORECASE)
        if match:
            ps_number = f"PS{match.group(1)}"
            result = db.validate_part(ps_number)
            if result.get("found"):
                return {"resolved": True, "ps_number": ps_number, "confidence": "exact", ...}

    # Priority 3: PS number format
    ps_match = re.match(r'^PS\d+$', input_clean, re.IGNORECASE)
    if ps_match:
        ps_number = input_clean.upper()
        result = db.validate_part(ps_number)
        ...

    # Priority 4: Manufacturer number (exact)
    if re.match(r'^[A-Z0-9\-]+$', input_clean, re.IGNORECASE) and len(input_clean) >= 5:
        part = db.find_by_manufacturer_number(input_clean.upper())
        if part:
            return {"resolved": True, "ps_number": part["ps_number"], "confidence": "matched", ...}

        # Priority 5: Manufacturer number (partial)
        candidates = db.find_by_manufacturer_number_partial(input_clean.upper())
        if candidates:
            ...

    # Priority 6: Text search fallback
    search_result = db.search_parts(query=input_clean, limit=5)
    ...
```

**Why This Order?** Session references are most specific (user is referring to established context). PS numbers and URLs are explicit identifiers. Manufacturer numbers require lookup. Text search is the fuzzy fallback.

---

### Additional Edge Cases Discovered in Production Testing

#### 15. The "How to" Ambiguity

User: "How do I check if my ice maker is broken?"

This could mean:
- Pattern 2a: General symptom info ("ice maker not working")
- Pattern 2b: Specific part troubleshooting ("how to check ice maker assembly")

**Solution:** Added explicit disambiguation in Pattern 2b:
```
NOTE: If user asks "how to check/test/install" a part, that's Pattern 2b (troubleshooting),
NOT Pattern 1! Especially if the earlier context was discussing appliance symptoms.
```

#### 16. The Multiple Tool Call Race

When the Executor needs to compare 4 parts, it might call `search_reviews()` for all 4 in parallel:

```
[TOOL 1] search_reviews(ps_number=PS111, query="installation")
[TOOL 2] search_reviews(ps_number=PS222, query="installation")
[TOOL 3] search_reviews(ps_number=PS333, query="installation")
[TOOL 4] search_reviews(ps_number=PS444, query="installation")
```

**Potential Issue:** The ReAct agent might return before all calls complete.

**Mitigation:** LangGraph's ReAct implementation waits for all tool calls before proceeding. No code fix needed, but worth documenting.

#### 17. The Empty Tool Result Handling

When a vector search returns no results:
```python
results = db.search_qna(...)
return results or []  # Never return None
```

**Why?** The Synthesizer expects lists. `None` causes `len(None)` errors. All tools return empty lists/dicts for "no results" cases.

#### 18. The Part Price Edge Cases

Some parts have:
- `part_price: null` (unknown price)
- `part_price: 0` (free? error?)
- `part_price: "129.99"` (string instead of float)

**Handling:**
```python
class PartCard(BaseModel):
    part_price: float  # Pydantic coerces strings

# In extract_parts()
"part_price": item.get("part_price", 0.0),  # Default to 0 if missing
```

#### 19. The Brand Name Normalization

Brands in our database: "Whirlpool", "WHIRLPOOL", "whirlpool"

**Filtering uses ILIKE:**
```python
q = q.ilike("brand", f"%{brand}%")  # Case-insensitive
```

But display should be consistent. We trust the database value and don't normalize in code.

#### 20. The Markdown Injection Prevention

User messages are echoed in conversation history. A malicious user could inject:
```
Tell me about PS12345 **[click here](javascript:alert('xss'))**
```

**Frontend Mitigation:**
```javascript
// ChatWindow.js uses marked() for markdown
// External links are sanitized:
renderer.link = (href, title, text) => {
    const html = originalLinkRenderer(href, title, text);
    return html.replace(/^<a /, '<a target="_blank" rel="noopener noreferrer" ');
};
```

**Note:** `rel="noopener noreferrer"` prevents the new page from accessing `window.opener`. Full XSS prevention would require additional sanitization.

---

## Prompting Rules In Depth

The prompts are the soul of this agent. Code handles structure and flow, but prompts define behavior. This section breaks down how we instruct the LLM at each stage.

### The Executor Prompt Architecture

The Executor prompt is ~220 lines and follows a deliberate structure:

```
1. Role Definition (~5 lines)
2. Available Tools (~auto-generated)
3. Workflow Patterns (~100 lines)
4. Session Context (~dynamic)
5. Key Rules (~30 lines)
6. Current Query (~1 line)
```

**Why This Order Matters:**

LLMs weight information by position. Role comes first to establish identity. Tools come early so the model knows what's available before seeing patterns. Patterns come before rules because patterns are "what to do" and rules are "what NOT to do." The query comes last so it's freshest in context.

### Workflow Patterns: Teaching Through Examples

Instead of rigid if-then logic in code, we teach the LLM patterns:

```
### Pattern 1: Part Lookup
User wants to FIND or BUY a specific part (PS number, manufacturer number, URL)
Examples: "Tell me about PS12345", "I need a water filter"
1. If not a PS number → call resolve_part() first
2. For basic part info → call ONLY get_part()
3. For additional info (if explicitly requested):
   - Quality/reviews → search_reviews()
   - Q&A/technical → search_qna()
   - Installation help → search_repair_stories()
```

**Why Patterns Beat Hard-Coded Logic:**

1. **Flexibility**: The LLM can interpolate between patterns. A query that's 60% Pattern 1 and 40% Pattern 4 gets handled naturally.
2. **Graceful Degradation**: If a tool fails, the LLM adapts. Hard-coded logic would crash or return errors.
3. **Easy Updates**: Adding a new capability = adding a new pattern to the prompt. No code changes.
4. **Context Awareness**: The LLM considers conversation history when matching patterns. Code would need explicit state machines.

**The Anti-Pattern Instructions:**

Just as important as "what to do" is "what NOT to do":

```
**IMPORTANT:** Do NOT call compatibility tools unless:
- User explicitly asks "what models does this fit?"
- Compatibility tools are EXPENSIVE - only call when specifically requested
```

We found the LLM would over-fetch data "just in case." Explicit anti-patterns fixed this.

### Pattern Hierarchy and Precedence

Patterns aren't equal. We order them by frequency and importance:

1. **Pattern 1: Part Lookup** - Most common query type
2. **Pattern 1b: Quality/Buying** - Subset of part lookup
3. **Pattern 2a/2b: Symptoms** - Split for different behaviors
4. **Pattern 3: Search** - Less common
5. **Pattern 4: Compatibility** - Explicit user request
6. **Pattern 5: Follow-ups** - Context-dependent
7. **Pattern 6: Scraping Fallback** - Rare but critical

The LLM naturally tries earlier patterns first, which aligns with our query distribution.

### The Synthesizer Prompt Philosophy

The Synthesizer has a different job: take raw data and create a human response. Its prompt emphasizes *output quality* over *decision-making*:

```
## Response Guidelines

1. **Be concise but complete** - Answer directly, include relevant details
2. **Format for readability** - Line breaks, bolding, numbered lists for steps
3. **Include practical info** - price, availability, difficulty ratings
4. **Be honest about limitations** - If info is missing, say so
5. **Don't repeat the query** - Jump straight into the answer
```

**The "Don't" Rules:**

Many synthesizer rules are prohibitions learned from testing:

```
- Don't suggest next steps (users found it patronizing)
- Don't ask for info you can't use (creates false expectations)
- Don't include extra info beyond what was requested
- Don't mention PS numbers without including them (breaks part cards)
```

### Dynamic Prompt Components

Parts of the prompt are generated at runtime:

**Tool Documentation (Auto-Generated):**
```python
def generate_tool_docs(self) -> str:
    sections = {
        "resolution": "### Resolution Tools\nUse these first...",
        "part": "### Part Tools\nRequire a PS number...",
        # ...
    }
    for category in ["resolution", "part", "symptom", "search", "vector"]:
        tools_in_cat = [m for m in self._metadata.values() if m.category == category]
        for meta in tools_in_cat:
            lines.append(f"- `{meta.name}`: {meta.description}")
```

When you add a new tool with `@registry.register()`, it automatically appears in the prompt.

**Session Context (Formatted Per-Request):**
```python
def format_session_context(state: AgentState) -> str:
    parts = []
    if state.conversation_history:
        parts.append("## Recent Conversation")
        for msg in state.conversation_history[-6:]:
            parts.append(f"**{role}:** {content}")

    if session.all_discussed_parts:
        parts.append(f"\n## Recently Discussed Parts")
        parts.append(f"PS numbers: {', '.join(session.all_discussed_parts[-5:])}")
```

The LLM sees exactly what it needs for context resolution.

### Key Rules: The Hard Constraints

The "Key Rules" section contains non-negotiable behaviors:

```
1. **Always use tools** - never answer without calling at least one tool
2. **Chain naturally** - if you need a PS number, get it first via resolve_part
3. **Check appliance_type** - only help with refrigerator/dishwasher parts
4. **Use session context** - if user says "this part", use PS from session
5. **Don't call expensive tools unnecessarily** - get_compatible_models only when asked
6. **Never over-call for symptoms** - get_symptoms only, not get_repair_instructions
7. **Don't re-fetch established context** - use existing symptom, don't call again
8. **Know when to use each vector tool** - reviews for quality, qna for specs, stories for installation
```

These are "always" or "never" rules that override pattern-matching.

### Prompt Versioning Lessons

We iterated through many prompt versions. Key learnings:

| Version | Problem | Fix |
|---------|---------|-----|
| v1 | LLM called every tool "just to be helpful" | Added explicit "call ONLY X" instructions |
| v2 | Compatibility fetched for every part query | Added "EXPENSIVE" warning |
| v3 | Follow-ups only used first part from session | Added "for EACH part" instruction |
| v4 | Symptom queries also returned repair instructions | Split Pattern 2a vs 2b |
| v5 | Synthesizer asked for model numbers randomly | Added "don't ask for info you can't use" |
| v6 | Part cards missing for mentioned parts | Added "ALWAYS include PS numbers" in caps |

Each version fixed specific failure modes observed in testing.

---

## Modularity and Extensibility

The system was designed for extension from day one. Here's how each component can be modified or expanded.

### Adding a New Tool (5-Minute Process)

**Step 1: Create the function with decorator**

```python
# In backend/agent_v2/tools/sql_tools.py (or new file)

@registry.register(category="part")
def get_installation_video(ps_number: str) -> dict:
    """
    Get the installation video URL and transcript for a part.

    Use when the user asks specifically for video help or installation guidance.
    Returns video URL, duration, difficulty rating, and key steps from transcript.

    Args:
        ps_number: The PS number (e.g., "PS11752778")

    Returns:
        Dictionary with video_url, duration_minutes, difficulty, key_steps
    """
    db = get_supabase_client()
    part = db.get_part_by_ps_number(ps_number)

    if not part:
        return {"error": f"Part {ps_number} not found"}

    return {
        "ps_number": ps_number,
        "video_url": part.get("install_video_url"),
        "duration_minutes": part.get("install_time"),
        "difficulty": part.get("install_difficulty"),
        # Could add transcript extraction here
    }
```

**Step 2: Import in __init__.py (if new file)**

```python
# In backend/agent_v2/tools/__init__.py
from backend.agent_v2.tools import new_tools_file  # noqa: F401
```

**That's it.** The tool is now:
- Registered in the tool registry
- Automatically documented in the Executor prompt
- Available to the ReAct agent

**Why This Works:**

The `@registry.register()` decorator:
1. Wraps the function with LangChain's `@tool` decorator
2. Extracts the first line of docstring as description
3. Stores in registry dict with category metadata
4. `get_all_tools()` returns all registered tools to the ReAct agent
5. `generate_tool_docs()` creates prompt documentation grouped by category

### Adding a New Workflow Pattern

If the new tool requires specific usage patterns, add to the Executor prompt:

```python
# In backend/agent_v2/prompts.py, add to EXECUTOR_PROMPT:

### Pattern 7: Installation Video Request
User asks specifically for video help or wants to watch installation
Examples: "Is there a video for this?", "Show me how to install", "I need visual help"
1. Get the PS number (from session or resolve_part)
2. Call `get_installation_video(ps_number)`
3. Present the video link prominently with duration and difficulty
```

### Adding a New Node to the Graph

For more complex additions that need their own processing step:

**Step 1: Create the node function**

```python
# In backend/agent_v2/nodes/validator.py

async def validator_node(state: AgentState) -> dict:
    """
    Validate that executor results make sense before synthesizing.

    Catches issues like:
    - Contradictory tool results
    - Missing required data
    - Suspicious patterns (e.g., $0.00 prices)
    """
    # Validation logic here

    return {
        "validation_passed": True,
        "validation_warnings": [],
    }
```

**Step 2: Add to graph**

```python
# In backend/agent_v2/graph.py

from backend.agent_v2.nodes import validator_node

# Add node
workflow.add_node("validator", validator_node)

# Modify edges
workflow.add_edge("executor", "validator")  # Was: executor → secondary_scope_check
workflow.add_edge("validator", "secondary_scope_check")
```

**Step 3: Update state if needed**

```python
# In backend/agent_v2/state.py

class AgentState(BaseModel):
    # ... existing fields ...
    validation_passed: bool = True
    validation_warnings: list[str] = Field(default_factory=list)
```

### Adding a New Appliance Type

To extend from refrigerator/dishwasher to, say, washing machines:

**Step 1: Update scope check patterns**

```python
# In backend/agent_v2/nodes/scope_check.py

IN_SCOPE_KEYWORDS = [
    # ... existing ...
    r"\bwashing\s*machine\b", r"\bwasher\b", r"\blaundry\b",
    r"\bdrum\b", r"\bagitator\b",  # washing machine parts
]

# Remove from OUT_OF_SCOPE_KEYWORDS
OUT_OF_SCOPE_KEYWORDS = [
    # Remove: r"\bwashing\s*machine\b", r"\bwasher\b",
    r"\bdryer\b", r"\boven\b",  # Still out of scope
]
```

**Step 2: Update secondary scope check**

```python
# In backend/agent_v2/nodes/secondary_scope_check.py

ALLOWED_APPLIANCE_TYPES = ['refrigerator', 'dishwasher', 'washing machine']

if appliance_type not in ALLOWED_APPLIANCE_TYPES:
    # Reject
```

**Step 3: Scrape data for new appliance type**

```python
# Run scraper with new config
python -m scrapers.main --appliance-type "washing machine"
```

**Step 4: Update prompts (optional)**

```python
# In SCOPE_CHECK_PROMPT
IN_SCOPE includes:
- Questions about refrigerator, dishwasher, OR washing machine parts
```

The agent will automatically handle the new appliance type once data exists.

### Adding Order/Transaction Support

The architecture anticipates this. Here's the approach:

**Step 1: Create order tools**

```python
@registry.register(category="order")
def check_order_status(order_id: str, email: str) -> dict:
    """Check the status of an existing order."""
    # API call to order system

@registry.register(category="order")
def get_shipping_estimate(ps_number: str, zip_code: str) -> dict:
    """Get estimated shipping time and cost for a part."""

@registry.register(category="order")
def add_to_cart(ps_number: str, quantity: int = 1) -> dict:
    """Add a part to the user's cart."""
```

**Step 2: Update registry categories**

```python
# In registry.py
sections = {
    # ... existing ...
    "order": "### Order Tools\nFor cart, checkout, and order status.",
}
```

**Step 3: Add workflow patterns**

```python
### Pattern 8: Order Status
User asks about an existing order
Examples: "Where's my order?", "Order #12345 status", "When will it arrive?"
1. Ask for order ID and email if not provided
2. Call `check_order_status(order_id, email)`
3. Present status, tracking info, and estimated arrival

### Pattern 9: Add to Cart
User wants to purchase a part
Examples: "I'll take it", "Add this to my cart", "Buy PS12345"
1. Confirm the PS number from session or query
2. Call `add_to_cart(ps_number)`
3. Confirm addition and provide cart summary
```

**Step 4: Handle authentication**

For order support, you'd need to add session-based auth:

```python
class SessionState(BaseModel):
    # ... existing ...
    user_id: str | None = None
    is_authenticated: bool = False
    cart_items: list[str] = Field(default_factory=list)
```

### The Extension Philosophy

The system follows these principles:

1. **Decorator-Based Registration**: New capabilities register themselves
2. **Prompt-Driven Behavior**: Business logic lives in prompts, not code
3. **State as Data Class**: New fields are just Pydantic attributes
4. **Graph as Composition**: Nodes are independent, edges define flow
5. **Tools are Pure Functions**: No side effects, easy to test

This means most extensions don't require understanding the whole system—just the piece you're modifying.

---

## Alternatives Considered and Why We Didn't Take Them

Every design choice has tradeoffs. Here's what we considered and rejected, with honest assessments.

### Alternative 1: OpenAI Function Calling Instead of LangChain Tools

**What It Is:**
OpenAI's native function calling (and Anthropic's tool use) lets you define functions directly in the API call without LangChain overhead.

**Why We Didn't:**
1. **LangGraph Integration**: LangGraph's ReAct agent works seamlessly with LangChain tools. Using native tool calling would mean writing our own ReAct loop.
2. **Registry Pattern**: Our decorator-based registration is cleaner than maintaining JSON schemas manually.
3. **Portability**: LangChain tools work across models. If we switch from Anthropic to OpenAI, tools still work.

**Honest Con of Our Approach:**
- Extra abstraction layer adds ~50ms latency per tool call
- LangChain's error messages can be opaque
- Debugging requires understanding both LangChain and our code

**When to Reconsider:**
If latency becomes critical (sub-second responses required), native function calling removes one abstraction layer.

### Alternative 2: Pure RAG Instead of SQL + Vector Split

**What It Is:**
Instead of PostgreSQL tables + pgvector, put everything in a vector database and retrieve via similarity search.

**Why We Didn't:**
1. **Exact Lookups**: "Does PS11752778 fit model WDT780SAEM1?" needs an exact yes/no, not "here are similar compatibility records."
2. **Filtering**: SQL filters (price < $50, in_stock = true) are trivial. Vector DBs need metadata filtering which is less flexible.
3. **Joins**: Compatibility requires joining parts → models. Vector DBs don't do relational joins.
4. **Consistency**: SQL gives ACID guarantees. Vector similarity is probabilistic.

**Honest Con of Our Approach:**
- Maintaining two data systems (SQL + pgvector) is more complex
- Embedding generation adds processing time during data ingestion
- Schema changes require migrations

**When to Reconsider:**
If the use case shifts toward more open-ended "find me something like X" queries, a vector-first approach with metadata filtering could simplify architecture.

### Alternative 3: Multi-Agent Parallelism (Like V1)

**What It Is:**
V1 had parallel worker agents that could fetch multiple data sources simultaneously.

**Why We Dropped It:**
1. **Complexity vs Benefit**: Most queries need 1-3 tool calls. Parallelism saves milliseconds, not seconds.
2. **Ordering Dependencies**: Often tool B needs output from tool A. True parallelism is rare.
3. **Debugging Nightmare**: Parallel execution makes logs non-linear and race conditions possible.
4. **Cost**: Parallel LLM calls multiply API costs.

**Honest Con of Sequential:**
- "Compare these 5 parts" takes 5x as long as it could with parallelism
- Heavy queries (many tool calls) feel slow

**When to Reconsider:**
If comparison queries become a major use case (e.g., shopping cart with 10 items needing reviews), add targeted parallelism for those specific patterns.

### Alternative 4: Fine-Tuned Model Instead of Prompting

**What It Is:**
Train a custom model on PartSelect data rather than prompting a general model.

**Why We Didn't:**
1. **Data Requirements**: Fine-tuning needs thousands of examples. We don't have labeled query→response pairs at scale.
2. **Iteration Speed**: Prompt changes deploy instantly. Fine-tuning takes hours/days.
3. **Tool Use**: Fine-tuned models often lose general capabilities. Ours needs to use tools intelligently.
4. **Cost**: Fine-tuning Anthropic models isn't available. OpenAI fine-tuning is expensive for production.

**Honest Con of Prompting:**
- Longer prompts = more tokens = higher cost per query
- Prompt can't capture everything; edge cases slip through
- Model updates from Anthropic can change behavior unexpectedly

**When to Reconsider:**
If query volume reaches millions/month, fine-tuning on a smaller model could reduce per-query costs significantly.

### Alternative 5: Streaming Throughout (Not Just Synthesizer)

**What It Is:**
Stream scope check, executor thoughts, tool calls, and synthesis—not just the final response.

**Why We Didn't:**
1. **User Experience**: Seeing "Calling tool: get_part..." is technical noise, not value.
2. **Latency Perception**: Showing intermediate steps can make things feel *slower* even if total time is same.
3. **Implementation Complexity**: Streaming from ReAct agent requires custom message handling.

**Honest Con of Final-Only Streaming:**
- Users wait 3-8 seconds seeing only "Thinking..." before text appears
- No feedback during long scraping operations

**When to Reconsider:**
Add streaming status updates (not full content) for operations over 5 seconds: "Checking our database..." → "Part not found, searching PartSelect..." → "Found it! Generating response..."

### Alternative 6: Caching Layer for Tool Results

**What It Is:**
Cache tool results in Redis. If someone asks about PS11752778 twice, don't hit the database twice.

**Why We Didn't (Yet):**
1. **Freshness**: Part prices, availability, reviews change. Stale cache = wrong answers.
2. **Complexity**: Cache invalidation is hard. When does PS11752778's cache expire?
3. **Session Already Helps**: Within a conversation, session state prevents redundant fetches.

**Honest Con of No Caching:**
- Popular parts get fetched thousands of times
- Database load scales linearly with traffic
- Same user asking about same part in new session refetches everything

**When to Reconsider:**
Implement caching when you have:
- Traffic patterns showing repeated queries (>10% cache hit potential)
- Clear cache TTL policies (e.g., 1 hour for prices, 24 hours for descriptions)
- Monitoring to detect stale cache issues

### Alternative 7: Structured Output Parsing

**What It Is:**
Use Pydantic models or JSON mode to force LLM outputs into structured formats.

**Why We Didn't:**
1. **Synthesizer Needs Flexibility**: The final response is prose, not structured data.
2. **Executor Already Structured**: ReAct agent outputs follow LangChain's structure.
3. **Scope Check Is Simple**: "IN_SCOPE" or "OUT_OF_SCOPE" doesn't need parsing overhead.

**Honest Con of Unstructured Synthesis:**
- Part card extraction uses regex on response text (fragile)
- Response format can vary between runs
- Hard to enforce consistent structure (e.g., always include price first)

**When to Reconsider:**
If response format consistency becomes critical (e.g., for downstream parsing or A/B testing), add structured output for specific fields while keeping prose flexible.

---

## Scaling to Production

The current architecture handles moderate traffic. Here's the roadmap for serious scale.

### Current Bottlenecks

| Component | Current State | Breaking Point |
|-----------|--------------|----------------|
| Session Storage | In-memory dict | Server restart loses all sessions |
| Database Connections | New connection per request | ~100 concurrent users |
| Live Scraping | Synchronous, blocks request | 1 scrape at a time per server |
| Model Serving | Direct API calls | Rate limits, cost scales linearly |
| Monitoring | Print statements | No visibility in production |

### Tier 1: Production-Ready (100-1000 users/day)

**1. Redis for Session Storage**

```python
# Replace in-memory sessions dict
import redis

redis_client = redis.Redis(host='localhost', port=6379, db=0)

def get_session(session_id: str) -> SessionState:
    data = redis_client.get(f"session:{session_id}")
    if data:
        return SessionState.parse_raw(data)
    return SessionState()

def save_session(session_id: str, session: SessionState):
    redis_client.setex(
        f"session:{session_id}",
        timedelta(hours=24),  # TTL
        session.json()
    )
```

**2. Database Connection Pooling**

```python
# In db/supabase_client.py
from supabase import create_client, ClientOptions

def get_supabase_client() -> SupabaseClient:
    options = ClientOptions(
        postgrest_client_timeout=10,
        storage_client_timeout=30,
    )
    # Connection pool handled by Supabase client
    return create_client(url, key, options=options)
```

**3. Basic Monitoring**

```python
# Add structured logging
import structlog

logger = structlog.get_logger()

async def executor_node(state: AgentState) -> dict:
    logger.info(
        "executor_started",
        query=state.user_query[:50],
        session_parts=len(state.session.all_discussed_parts),
    )

    start = time.time()
    result = await agent.ainvoke(...)

    logger.info(
        "executor_completed",
        duration_ms=(time.time() - start) * 1000,
        tool_count=len([m for m in result["messages"] if m.type == "tool"]),
    )
```

**4. Rate Limiting**

```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter

@app.post("/chat")
@limiter.limit("10/minute")  # 10 requests per minute per IP
async def chat(request: ChatRequest):
    ...
```

### Tier 2: Scalable (1000-10000 users/day)

**1. Async Scraping Queue**

```python
# Use Celery or similar for background scraping
from celery import Celery

celery_app = Celery('scraper', broker='redis://localhost:6379/1')

@celery_app.task
def scrape_part_async(ps_number: str) -> dict:
    """Background scraping task."""
    return scrape_part_live(ps_number)

# In executor, when part not found:
if not scrape_already_called:
    task = scrape_part_async.delay(ps_number)
    # Return partial response immediately
    # Client polls or websocket gets update when done
```

**2. Result Caching**

```python
from functools import lru_cache
import hashlib

def cache_key(tool_name: str, **kwargs) -> str:
    content = f"{tool_name}:{json.dumps(kwargs, sort_keys=True)}"
    return hashlib.md5(content.encode()).hexdigest()

async def cached_tool_call(tool_name: str, **kwargs) -> dict:
    key = cache_key(tool_name, **kwargs)

    cached = redis_client.get(f"tool:{key}")
    if cached:
        return json.loads(cached)

    result = await original_tool_call(tool_name, **kwargs)

    # Cache with TTL based on tool type
    ttl = CACHE_TTL.get(tool_name, 3600)  # Default 1 hour
    redis_client.setex(f"tool:{key}", ttl, json.dumps(result))

    return result
```

**3. Horizontal Scaling**

```yaml
# docker-compose.yml for multi-instance
services:
  api:
    build: .
    deploy:
      replicas: 3
    environment:
      - REDIS_URL=redis://redis:6379
    depends_on:
      - redis
      - db

  redis:
    image: redis:alpine

  nginx:
    image: nginx
    ports:
      - "80:80"
    # Load balance across api instances
```

**4. Embedding Precomputation**

```python
# Batch job to precompute embeddings for new parts
async def precompute_embeddings():
    parts_without_embeddings = db.get_parts_without_embeddings()

    for batch in chunks(parts_without_embeddings, 100):
        embeddings = model.encode([p['part_name'] for p in batch])
        db.update_embeddings(batch, embeddings)
```

### Tier 3: High Scale (10000+ users/day)

**1. Model Caching/Batching**

```python
# Use vLLM or similar for efficient inference
from vllm import LLM, SamplingParams

# Batch multiple requests
class ModelBatcher:
    def __init__(self, batch_size=8, max_wait_ms=100):
        self.queue = asyncio.Queue()
        self.batch_size = batch_size

    async def generate(self, prompt: str) -> str:
        future = asyncio.Future()
        await self.queue.put((prompt, future))
        return await future

    async def process_batches(self):
        while True:
            batch = []
            # Collect up to batch_size or max_wait
            while len(batch) < self.batch_size:
                try:
                    item = await asyncio.wait_for(
                        self.queue.get(),
                        timeout=self.max_wait_ms/1000
                    )
                    batch.append(item)
                except asyncio.TimeoutError:
                    break

            if batch:
                prompts = [p for p, _ in batch]
                results = self.llm.generate(prompts)
                for (_, future), result in zip(batch, results):
                    future.set_result(result)
```

**2. Read Replicas for Database**

```python
# Route read queries to replicas
class DatabaseRouter:
    def __init__(self):
        self.primary = create_client(PRIMARY_URL, KEY)
        self.replicas = [
            create_client(REPLICA_1_URL, KEY),
            create_client(REPLICA_2_URL, KEY),
        ]
        self.replica_index = 0

    def get_read_client(self):
        client = self.replicas[self.replica_index]
        self.replica_index = (self.replica_index + 1) % len(self.replicas)
        return client

    def get_write_client(self):
        return self.primary
```

**3. Cost Optimization via Model Routing**

```python
# Use cheaper models for simple queries
async def route_to_model(query: str, complexity: str) -> str:
    if complexity == "simple":
        # Haiku for simple lookups
        return settings.HAIKU_MODEL
    elif complexity == "medium":
        # Sonnet for most queries
        return settings.SONNET_MODEL
    else:
        # Opus for complex reasoning (if needed)
        return settings.OPUS_MODEL

# Estimate complexity before execution
async def estimate_complexity(query: str) -> str:
    # Simple heuristics
    if re.match(r'^(tell me about|what is) PS\d+', query, re.I):
        return "simple"
    if len(query.split()) > 30:
        return "complex"
    return "medium"
```

**4. Analytics Pipeline**

```python
# Emit events for analytics
from dataclasses import dataclass
from datetime import datetime

@dataclass
class QueryEvent:
    timestamp: datetime
    session_id: str
    query: str
    tools_used: list[str]
    response_time_ms: float
    model_used: str
    token_count: int
    was_helpful: bool | None  # User feedback

async def emit_event(event: QueryEvent):
    # Send to analytics (Segment, Amplitude, custom pipeline)
    await analytics_client.track(event)
```

### Cost Projections at Scale

| Daily Users | Queries/Day | API Cost/Month | Infra Cost/Month |
|-------------|-------------|----------------|------------------|
| 100 | 500 | ~$50 | ~$20 (basic VPS) |
| 1,000 | 5,000 | ~$500 | ~$100 (managed DB + Redis) |
| 10,000 | 50,000 | ~$5,000 | ~$500 (multi-instance + CDN) |
| 100,000 | 500,000 | ~$30,000* | ~$2,000 (k8s cluster) |

*At 100k users, you'd want model caching, batching, and potentially fine-tuning to reduce per-query costs.

### The "Keep It Simple" Principle

Don't over-engineer prematurely. The current architecture:
- Handles the case study requirements
- Is simple enough to debug
- Can be extended incrementally

Add complexity only when:
1. You have traffic data showing actual bottlenecks
2. You have monitoring showing where time/money is spent
3. You have users complaining about specific issues

Most systems never reach Tier 3. Build for Tier 1, plan for Tier 2, dream about Tier 3.

---

## The Dream Architecture: No Constraints

If we had unlimited time and resources, what would the *ideal* PartSelect agent look like? This section describes the best possible implementation—not as a roadmap, but as a north star for what a production-grade, enterprise-scale AI agent could become.

### Philosophy: From Reactive to Proactive

The current system is **reactive**: user asks, agent responds. The dream architecture is **proactive**: the agent anticipates needs, learns from interactions, verifies its own answers, and continuously improves.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        DREAM ARCHITECTURE OVERVIEW                          │
└─────────────────────────────────────────────────────────────────────────────┘

                              ┌─────────────────┐
                              │  Load Balancer  │
                              │    (Global)     │
                              └────────┬────────┘
                                       │
                    ┌──────────────────┼──────────────────┐
                    │                  │                  │
                    ▼                  ▼                  ▼
            ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
            │   Region A   │   │   Region B   │   │   Region C   │
            │   (US-East)  │   │   (US-West)  │   │   (EU)       │
            └──────┬───────┘   └──────┬───────┘   └──────┬───────┘
                   │                  │                  │
                   └──────────────────┼──────────────────┘
                                      │
                    ┌─────────────────┴─────────────────┐
                    │         Orchestration Layer       │
                    │  ┌─────────┐ ┌─────────┐ ┌─────┐ │
                    │  │ Router  │ │ Planner │ │ Meta│ │
                    │  │         │ │         │ │Agent│ │
                    │  └─────────┘ └─────────┘ └─────┘ │
                    └─────────────────┬─────────────────┘
                                      │
       ┌──────────────┬───────────────┼───────────────┬──────────────┐
       │              │               │               │              │
       ▼              ▼               ▼               ▼              ▼
┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐
│  Executor  │ │  Verifier  │ │ Synthesizer│ │ Personaliz-│ │  Learner   │
│   Agent    │ │   Agent    │ │   Agent    │ │ation Agent │ │   Agent    │
└────────────┘ └────────────┘ └────────────┘ └────────────┘ └────────────┘
       │              │               │               │              │
       └──────────────┴───────────────┼───────────────┴──────────────┘
                                      │
                    ┌─────────────────┴─────────────────┐
                    │          Data Layer               │
                    │  ┌───────┐ ┌───────┐ ┌─────────┐ │
                    │  │Primary│ │Vector │ │ Feature │ │
                    │  │  DB   │ │  DB   │ │  Store  │ │
                    │  └───────┘ └───────┘ └─────────┘ │
                    │  ┌───────┐ ┌───────┐ ┌─────────┐ │
                    │  │ Cache │ │ Event │ │ Model   │ │
                    │  │ Layer │ │ Stream│ │ Registry│ │
                    │  └───────┘ └───────┘ └─────────┘ │
                    └──────────────────────────────────┘
```

---

### 1. Intelligent Multi-Model Orchestration

**Current State:** Haiku for Executor, Sonnet for Synthesizer. Static assignment.

**Dream State:** Dynamic model routing based on query complexity, user tier, latency requirements, and cost budget.

```python
class IntelligentModelRouter:
    """Routes queries to optimal model based on multiple factors."""

    def __init__(self):
        self.models = {
            "instant": "claude-3-haiku",      # <500ms, simple lookups
            "standard": "claude-3.5-sonnet",  # 1-3s, most queries
            "deep": "claude-3-opus",          # 5-10s, complex reasoning
            "specialized": "fine-tuned-partselect-v1",  # Domain-specific
        }

        # Learned routing weights from historical data
        self.complexity_classifier = load_model("query_complexity_v3.pkl")
        self.latency_predictor = load_model("latency_predictor_v2.pkl")

    async def route(self, query: str, context: QueryContext) -> ModelSelection:
        # Estimate query complexity
        complexity = self.complexity_classifier.predict(query)

        # Check user tier and SLA
        if context.user_tier == "enterprise":
            max_latency = 2000  # ms
            cost_weight = 0.2
        else:
            max_latency = 5000
            cost_weight = 0.8

        # Predict latency for each model
        latencies = {
            model: self.latency_predictor.predict(query, model)
            for model in self.models
        }

        # Score each model
        scores = {}
        for model, latency in latencies.items():
            if latency > max_latency:
                scores[model] = -1  # Disqualify
            else:
                quality = MODEL_QUALITY_SCORES[model]
                cost = MODEL_COSTS[model]
                scores[model] = quality - (cost * cost_weight) - (latency * 0.001)

        # Select best model
        best_model = max(scores, key=scores.get)

        # Log for learning
        await self.log_routing_decision(query, context, best_model, scores)

        return ModelSelection(
            model=best_model,
            predicted_latency=latencies[best_model],
            fallback=self._select_fallback(scores, best_model)
        )

    def _select_fallback(self, scores: dict, primary: str) -> str:
        """Select fallback model if primary fails."""
        remaining = {k: v for k, v in scores.items() if k != primary and v > 0}
        return max(remaining, key=remaining.get) if remaining else "standard"
```

**Key Features:**
- **Learned Complexity Classification**: Train a classifier on historical queries to predict complexity
- **Latency Prediction**: Model that predicts response time based on query characteristics
- **Cost-Quality Tradeoff**: Configurable balance between response quality and API costs
- **User Tier Awareness**: Enterprise users get better models, faster responses
- **Automatic Fallback**: If primary model fails or times out, seamlessly switch

---

### 2. Semantic Query Caching

**Current State:** No caching. Every query hits the database and LLM.

**Dream State:** Semantic cache that recognizes similar queries and returns cached results.

```python
class SemanticCache:
    """Cache that matches semantically similar queries, not just exact matches."""

    def __init__(self, similarity_threshold: float = 0.92):
        self.embedding_model = load_embedding_model("all-MiniLM-L6-v2")
        self.index = faiss.IndexFlatIP(384)  # Inner product for cosine sim
        self.cache_store = Redis()
        self.threshold = similarity_threshold

    async def get(self, query: str, context: CacheContext) -> CacheResult | None:
        """Check if a semantically similar query exists in cache."""
        # Generate query embedding
        query_embedding = self.embedding_model.encode(query)

        # Search for similar cached queries
        distances, indices = self.index.search(
            query_embedding.reshape(1, -1),
            k=5  # Top 5 candidates
        )

        for dist, idx in zip(distances[0], indices[0]):
            if dist < self.threshold:
                continue  # Not similar enough

            # Get cached entry
            cached = await self.cache_store.get(f"query:{idx}")
            if not cached:
                continue

            cached_data = json.loads(cached)

            # Validate cache freshness
            if self._is_stale(cached_data, context):
                continue

            # Validate context compatibility
            if not self._context_compatible(cached_data, context):
                continue

            # Cache hit!
            await self.log_cache_hit(query, cached_data, dist)
            return CacheResult(
                response=cached_data["response"],
                parts=cached_data["parts"],
                confidence=dist,
                cache_age=time.time() - cached_data["timestamp"]
            )

        return None  # Cache miss

    def _is_stale(self, cached: dict, context: CacheContext) -> bool:
        """Check if cached data is too old."""
        age = time.time() - cached["timestamp"]

        # Different TTLs for different data types
        if cached.get("has_price_info"):
            return age > 3600  # 1 hour for prices
        if cached.get("has_availability_info"):
            return age > 1800  # 30 min for availability
        return age > 86400  # 24 hours for static info

    def _context_compatible(self, cached: dict, context: CacheContext) -> bool:
        """Check if cached response applies to current context."""
        # If user has discussed parts, cache might not apply
        if context.session_parts and not cached.get("generic_response"):
            return False

        # If cached response mentions specific models, check relevance
        if cached.get("model_specific") and context.user_model:
            return context.user_model in cached.get("models_mentioned", [])

        return True

    async def set(self, query: str, response: str, parts: list, metadata: dict):
        """Cache a query-response pair."""
        query_embedding = self.embedding_model.encode(query)

        # Add to FAISS index
        idx = self.index.ntotal
        self.index.add(query_embedding.reshape(1, -1))

        # Store in Redis
        cache_data = {
            "query": query,
            "response": response,
            "parts": parts,
            "timestamp": time.time(),
            **metadata
        }
        await self.cache_store.setex(
            f"query:{idx}",
            86400 * 7,  # 7 day max TTL
            json.dumps(cache_data)
        )
```

**Why This Matters:**
- "Tell me about PS11752778" and "What's the info on PS11752778?" hit the same cache
- "Is this water filter good?" after discussing PS11752778 matches "Is PS11752778 a good water filter?"
- Reduces LLM calls by 30-50% for popular parts
- Dramatically reduces database load

**Cache Invalidation Strategy:**
```python
class CacheInvalidator:
    """Listens to data changes and invalidates affected cache entries."""

    async def on_price_update(self, ps_number: str, new_price: float):
        """Invalidate cache entries mentioning this part's price."""
        affected = await self.find_entries_mentioning(ps_number, "price")
        await self.invalidate_batch(affected)

    async def on_review_added(self, ps_number: str):
        """Invalidate cache entries about this part's reviews."""
        affected = await self.find_entries_mentioning(ps_number, "review")
        await self.invalidate_batch(affected)

    async def on_availability_change(self, ps_number: str):
        """Invalidate availability-related cache entries."""
        affected = await self.find_entries_mentioning(ps_number, "availability")
        await self.invalidate_batch(affected)
```

---

### 3. The Verifier Agent

**Current State:** No verification. Synthesizer output goes directly to user.

**Dream State:** A dedicated Verifier Agent that fact-checks responses before sending.

```python
class VerifierAgent:
    """
    Verifies synthesizer output for accuracy, consistency, and safety.

    Catches:
    - Hallucinated prices or specs
    - Incorrect compatibility claims
    - Contradictions with tool results
    - Unsafe recommendations
    """

    def __init__(self):
        self.llm = ChatAnthropic(model="claude-3-haiku")  # Fast verification
        self.fact_extractor = FactExtractor()
        self.safety_checker = SafetyChecker()

    async def verify(
        self,
        response: str,
        tool_results: list[dict],
        query: str
    ) -> VerificationResult:
        # Extract factual claims from response
        claims = await self.fact_extractor.extract(response)

        verification_tasks = [
            self._verify_prices(claims.prices, tool_results),
            self._verify_compatibility(claims.compatibility, tool_results),
            self._verify_specs(claims.specifications, tool_results),
            self._check_safety(response, query),
            self._check_consistency(claims, tool_results),
        ]

        results = await asyncio.gather(*verification_tasks)

        # Aggregate results
        all_issues = []
        confidence_scores = []

        for result in results:
            all_issues.extend(result.issues)
            confidence_scores.append(result.confidence)

        overall_confidence = min(confidence_scores)  # Weakest link

        return VerificationResult(
            passed=len(all_issues) == 0,
            confidence=overall_confidence,
            issues=all_issues,
            suggested_corrections=self._generate_corrections(all_issues)
        )

    async def _verify_prices(
        self,
        price_claims: list[PriceClaim],
        tool_results: list[dict]
    ) -> VerificationPartial:
        """Verify that mentioned prices match tool results."""
        issues = []

        for claim in price_claims:
            # Find the part in tool results
            actual_price = self._find_price_in_results(
                claim.ps_number,
                tool_results
            )

            if actual_price is None:
                issues.append(Issue(
                    type="unverifiable_price",
                    claim=f"${claim.price} for {claim.ps_number}",
                    severity="medium"
                ))
            elif abs(actual_price - claim.price) > 0.01:
                issues.append(Issue(
                    type="incorrect_price",
                    claim=f"${claim.price} for {claim.ps_number}",
                    actual=f"${actual_price}",
                    severity="high"
                ))

        return VerificationPartial(
            issues=issues,
            confidence=1.0 if not issues else 0.5
        )

    async def _verify_compatibility(
        self,
        compat_claims: list[CompatClaim],
        tool_results: list[dict]
    ) -> VerificationPartial:
        """Verify compatibility claims match tool results."""
        issues = []

        for claim in compat_claims:
            actual = self._find_compatibility_in_results(
                claim.ps_number,
                claim.model_number,
                tool_results
            )

            if actual is None:
                # Claim not supported by evidence
                issues.append(Issue(
                    type="unsupported_compatibility",
                    claim=f"{claim.ps_number} fits {claim.model_number}",
                    severity="high"
                ))
            elif actual != claim.is_compatible:
                issues.append(Issue(
                    type="incorrect_compatibility",
                    claim=f"Compatible: {claim.is_compatible}",
                    actual=f"Compatible: {actual}",
                    severity="critical"
                ))

        return VerificationPartial(
            issues=issues,
            confidence=1.0 if not issues else 0.3
        )

    async def _check_safety(
        self,
        response: str,
        query: str
    ) -> VerificationPartial:
        """Check for unsafe recommendations."""
        issues = []

        # Check for dangerous DIY advice
        dangerous_patterns = [
            (r"disconnect.*power", "electrical_safety"),
            (r"gas line", "gas_safety"),
            (r"water.*main", "plumbing_safety"),
        ]

        for pattern, safety_type in dangerous_patterns:
            if re.search(pattern, response, re.I):
                # Verify safety warnings are included
                if not self._has_safety_warning(response, safety_type):
                    issues.append(Issue(
                        type="missing_safety_warning",
                        context=f"Mentions {safety_type} without warning",
                        severity="critical"
                    ))

        return VerificationPartial(
            issues=issues,
            confidence=1.0 if not issues else 0.7
        )
```

**Verification Pipeline:**
```
Response Generated
       │
       ▼
┌─────────────┐
│   Extract   │ ─── Pull out prices, specs, compatibility claims
│    Facts    │
└──────┬──────┘
       │
       ▼
┌─────────────┐
│   Cross-    │ ─── Compare claims against tool results
│  Reference  │
└──────┬──────┘
       │
       ▼
┌─────────────┐
│   Safety    │ ─── Check for dangerous advice without warnings
│    Check    │
└──────┬──────┘
       │
       ▼
┌─────────────┐
│  Confidence │ ─── Calculate overall confidence score
│    Score    │
└──────┬──────┘
       │
       ├─── confidence > 0.9 ──► Send to user
       │
       ├─── 0.7 < confidence < 0.9 ──► Send with caveat
       │
       └─── confidence < 0.7 ──► Regenerate or escalate
```

---

### 4. Self-Improving Prompt System

**Current State:** Static prompts. Updates require code deployment.

**Dream State:** Prompts that learn and improve from user feedback and outcomes.

```python
class AdaptivePromptSystem:
    """
    Prompts that evolve based on:
    - User feedback (thumbs up/down)
    - Conversation outcomes (did user buy? ask follow-up?)
    - A/B test results
    - Error patterns
    """

    def __init__(self):
        self.prompt_store = PromptVersionStore()
        self.ab_test_engine = ABTestEngine()
        self.feedback_analyzer = FeedbackAnalyzer()

    async def get_prompt(
        self,
        prompt_type: str,
        context: PromptContext
    ) -> str:
        # Check if user is in an A/B test
        variant = await self.ab_test_engine.get_variant(
            prompt_type,
            context.user_id
        )

        if variant:
            return variant.prompt_text

        # Get best-performing prompt for this context
        return await self.prompt_store.get_best(
            prompt_type,
            context.query_type,
            context.user_segment
        )

    async def record_outcome(
        self,
        prompt_version: str,
        outcome: Outcome
    ):
        """Record the outcome of using a prompt version."""
        await self.prompt_store.record_outcome(prompt_version, outcome)

        # Check if we have enough data to update rankings
        if await self.prompt_store.has_sufficient_data(prompt_version):
            await self._maybe_update_rankings(prompt_version)

    async def _maybe_update_rankings(self, prompt_version: str):
        """Update prompt rankings based on accumulated outcomes."""
        stats = await self.prompt_store.get_stats(prompt_version)

        # Calculate success metrics
        success_rate = stats.positive_feedback / stats.total_uses
        conversion_rate = stats.purchases / stats.total_uses
        follow_up_rate = stats.follow_up_questions / stats.total_uses

        # Lower follow-up rate is better (user got what they needed)
        score = (
            success_rate * 0.4 +
            conversion_rate * 0.3 +
            (1 - follow_up_rate) * 0.3
        )

        await self.prompt_store.update_score(prompt_version, score)


class PromptEvolution:
    """Generates new prompt variants based on performance data."""

    def __init__(self):
        self.llm = ChatAnthropic(model="claude-3-opus")
        self.prompt_store = PromptVersionStore()

    async def generate_variant(
        self,
        base_prompt: str,
        failure_patterns: list[FailurePattern]
    ) -> str:
        """Generate an improved prompt variant based on failure analysis."""

        analysis_prompt = f"""Analyze these prompt failure patterns and suggest improvements:

Current Prompt:
{base_prompt}

Failure Patterns:
{self._format_failures(failure_patterns)}

Generate an improved version of the prompt that addresses these failures.
Explain your changes."""

        response = await self.llm.ainvoke(analysis_prompt)

        # Extract the improved prompt
        improved = self._extract_prompt(response.content)

        # Store as new variant for A/B testing
        variant_id = await self.prompt_store.create_variant(
            base_prompt_id=base_prompt.id,
            new_text=improved,
            rationale=response.content
        )

        return variant_id

    async def analyze_failures(
        self,
        prompt_version: str,
        time_window: timedelta
    ) -> list[FailurePattern]:
        """Identify patterns in prompt failures."""
        failures = await self.prompt_store.get_failures(
            prompt_version,
            time_window
        )

        # Cluster failures by type
        clusters = self._cluster_failures(failures)

        patterns = []
        for cluster in clusters:
            pattern = FailurePattern(
                description=self._describe_cluster(cluster),
                frequency=len(cluster) / len(failures),
                examples=cluster[:3],
                suggested_fix=await self._suggest_fix(cluster)
            )
            patterns.append(pattern)

        return patterns
```

**A/B Testing Infrastructure:**
```python
class ABTestEngine:
    """Run controlled experiments on prompt variants."""

    async def create_experiment(
        self,
        name: str,
        control_prompt: str,
        variant_prompts: list[str],
        traffic_split: dict[str, float],  # {"control": 0.5, "variant_a": 0.25, ...}
        success_metric: str,  # "positive_feedback", "conversion", etc.
        min_sample_size: int = 1000
    ) -> Experiment:
        experiment = Experiment(
            name=name,
            control=control_prompt,
            variants=variant_prompts,
            traffic_split=traffic_split,
            success_metric=success_metric,
            min_sample_size=min_sample_size,
            status="running"
        )

        await self.experiment_store.save(experiment)
        return experiment

    async def check_significance(self, experiment_id: str) -> SignificanceResult:
        """Check if experiment has reached statistical significance."""
        experiment = await self.experiment_store.get(experiment_id)
        results = await self.get_results(experiment_id)

        # Chi-squared test for categorical outcomes
        # or t-test for continuous metrics
        p_value = self._calculate_p_value(results)

        if p_value < 0.05 and results.total_samples >= experiment.min_sample_size:
            winner = max(results.variants, key=lambda v: v.success_rate)
            return SignificanceResult(
                significant=True,
                winner=winner.name,
                p_value=p_value,
                lift=winner.success_rate - results.control.success_rate
            )

        return SignificanceResult(
            significant=False,
            samples_needed=experiment.min_sample_size - results.total_samples
        )
```

---

### 5. Real-Time Data Synchronization

**Current State:** Static database. Scraper runs periodically. Live scraping is slow fallback.

**Dream State:** Real-time sync with PartSelect via webhooks and change data capture.

```python
class RealTimeDataSync:
    """
    Keep local data in sync with PartSelect in real-time.

    Methods:
    1. Webhook receiver for push updates (if PartSelect provides)
    2. Change data capture from scraping delta
    3. Priority queue for popular parts
    """

    def __init__(self):
        self.db = Database()
        self.cache = SemanticCache()
        self.scraper = IncrementalScraper()
        self.priority_queue = PriorityQueue()

    async def handle_webhook(self, event: WebhookEvent):
        """Handle real-time updates from PartSelect."""
        if event.type == "price_change":
            await self._handle_price_change(event)
        elif event.type == "availability_change":
            await self._handle_availability_change(event)
        elif event.type == "new_review":
            await self._handle_new_review(event)

    async def _handle_price_change(self, event: WebhookEvent):
        ps_number = event.data["ps_number"]
        new_price = event.data["new_price"]

        # Update database
        await self.db.update_price(ps_number, new_price)

        # Invalidate cache
        await self.cache.invalidate_for_part(ps_number, ["price"])

        # Log for analytics
        await self.analytics.track_price_change(ps_number, new_price)

    async def run_incremental_sync(self):
        """Continuously sync data with priority-based scheduling."""
        while True:
            # Get next part to sync based on priority
            next_part = await self.priority_queue.pop()

            if next_part:
                await self._sync_part(next_part)
            else:
                # No priority items, do background sync
                await self._background_sync_batch()

            await asyncio.sleep(0.1)  # Rate limit

    async def _sync_part(self, ps_number: str):
        """Sync a single part with PartSelect."""
        # Fetch current data from PartSelect
        fresh_data = await self.scraper.scrape_part(ps_number)

        # Compare with local data
        local_data = await self.db.get_part(ps_number)
        changes = self._diff(local_data, fresh_data)

        if changes:
            # Update database
            await self.db.update_part(ps_number, fresh_data)

            # Invalidate affected cache entries
            await self.cache.invalidate_for_changes(ps_number, changes)

            # Emit change event
            await self.event_bus.emit("part_updated", {
                "ps_number": ps_number,
                "changes": changes
            })

    def prioritize_part(self, ps_number: str, reason: str):
        """Add a part to the priority sync queue."""
        priority = self._calculate_priority(ps_number, reason)
        self.priority_queue.push(ps_number, priority)

    def _calculate_priority(self, ps_number: str, reason: str) -> float:
        """Calculate sync priority based on various factors."""
        base_priority = REASON_PRIORITIES.get(reason, 0.5)

        # Boost for popular parts
        query_count = self.analytics.get_query_count(ps_number, hours=24)
        popularity_boost = min(query_count / 100, 1.0)

        # Boost for parts with recent errors
        error_count = self.error_tracker.get_count(ps_number, hours=1)
        error_boost = min(error_count / 5, 1.0)

        return base_priority + (popularity_boost * 0.3) + (error_boost * 0.2)


class PopularityTracker:
    """Track part popularity for sync prioritization."""

    async def record_query(self, ps_number: str):
        """Record that a part was queried."""
        await self.redis.zincrby("part_popularity:24h", 1, ps_number)

    async def get_top_parts(self, n: int = 100) -> list[str]:
        """Get the most queried parts."""
        return await self.redis.zrevrange("part_popularity:24h", 0, n-1)

    async def schedule_popular_parts_sync(self):
        """Ensure popular parts are always fresh."""
        top_parts = await self.get_top_parts(100)

        for ps_number in top_parts:
            self.sync.prioritize_part(ps_number, "popular")
```

---

### 6. Personalization Engine

**Current State:** All users get the same experience. No memory across sessions.

**Dream State:** Personalized responses based on user history, preferences, and behavior.

```python
class PersonalizationEngine:
    """
    Personalizes responses based on:
    - User's appliance models (from past queries)
    - Purchase history (if authenticated)
    - Expertise level (inferred from language)
    - Preferred response style
    """

    def __init__(self):
        self.user_store = UserProfileStore()
        self.expertise_classifier = ExpertiseClassifier()
        self.preference_learner = PreferenceLearner()

    async def get_user_context(self, user_id: str) -> UserContext:
        """Build comprehensive user context for personalization."""
        profile = await self.user_store.get(user_id)

        return UserContext(
            known_models=profile.appliance_models,
            expertise_level=profile.expertise_level,
            response_style=profile.preferred_style,
            purchase_history=profile.purchases,
            past_issues=profile.resolved_issues,
            preferred_brands=profile.brand_preferences
        )

    async def personalize_prompt(
        self,
        base_prompt: str,
        user_context: UserContext
    ) -> str:
        """Add personalization context to prompt."""
        personalization = []

        if user_context.known_models:
            personalization.append(
                f"User's known appliances: {', '.join(user_context.known_models)}"
            )

        if user_context.expertise_level == "expert":
            personalization.append(
                "User is technically experienced - use precise terminology, "
                "skip basic explanations, focus on specifics."
            )
        elif user_context.expertise_level == "beginner":
            personalization.append(
                "User is a beginner - explain terms, be encouraging, "
                "emphasize safety and difficulty ratings."
            )

        if user_context.response_style == "concise":
            personalization.append(
                "User prefers brief responses - be direct, minimize filler."
            )
        elif user_context.response_style == "detailed":
            personalization.append(
                "User prefers detailed responses - include context and options."
            )

        if user_context.past_issues:
            recent_issue = user_context.past_issues[-1]
            personalization.append(
                f"User recently had: {recent_issue.symptom} on {recent_issue.model}"
            )

        if personalization:
            return base_prompt + "\n\n## User Context\n" + "\n".join(personalization)

        return base_prompt

    async def learn_from_interaction(
        self,
        user_id: str,
        query: str,
        response: str,
        feedback: Feedback | None
    ):
        """Update user profile based on interaction."""
        profile = await self.user_store.get(user_id)

        # Extract appliance models mentioned
        models = self._extract_models(query)
        for model in models:
            if model not in profile.appliance_models:
                profile.appliance_models.append(model)

        # Update expertise estimate
        expertise_signals = self._analyze_expertise(query)
        profile.expertise_level = self.expertise_classifier.update(
            profile.expertise_level,
            expertise_signals
        )

        # Learn response style preference from feedback
        if feedback:
            profile.preferred_style = self.preference_learner.update(
                profile.preferred_style,
                response,
                feedback
            )

        await self.user_store.save(profile)


class ExpertiseClassifier:
    """Classify user expertise level from their language."""

    def classify(self, query: str) -> str:
        signals = self._extract_signals(query)

        expert_signals = [
            "uses part numbers directly",
            "mentions specific components",
            "asks about diagnostics",
            "uses technical terms correctly"
        ]

        beginner_signals = [
            "asks what something is",
            "uncertain language",
            "asks about difficulty",
            "mentions safety concerns"
        ]

        expert_score = sum(1 for s in expert_signals if s in signals)
        beginner_score = sum(1 for s in beginner_signals if s in signals)

        if expert_score > beginner_score + 1:
            return "expert"
        elif beginner_score > expert_score + 1:
            return "beginner"
        return "intermediate"
```

---

### 7. Comprehensive Observability

**Current State:** Print statements. No structured logging or tracing.

**Dream State:** Full observability with distributed tracing, metrics, and debugging tools.

```python
class ObservabilityPlatform:
    """
    Complete visibility into agent behavior:
    - Distributed tracing across all components
    - Real-time metrics and dashboards
    - Conversation replay and debugging
    - Anomaly detection
    """

    def __init__(self):
        self.tracer = OpenTelemetryTracer()
        self.metrics = PrometheusMetrics()
        self.logger = StructuredLogger()
        self.replay_store = ConversationReplayStore()

    @contextmanager
    def trace_request(self, request_id: str, user_id: str):
        """Create a trace span for the entire request."""
        with self.tracer.start_span("agent_request") as span:
            span.set_attribute("request_id", request_id)
            span.set_attribute("user_id", user_id)

            # Record start time
            start = time.time()

            try:
                yield span
            finally:
                # Record metrics
                duration = time.time() - start
                self.metrics.request_duration.observe(duration)
                self.metrics.request_count.inc()

    def trace_node(self, node_name: str):
        """Decorator to trace individual graph nodes."""
        def decorator(func):
            @wraps(func)
            async def wrapper(*args, **kwargs):
                with self.tracer.start_span(f"node.{node_name}") as span:
                    start = time.time()

                    try:
                        result = await func(*args, **kwargs)
                        span.set_status(StatusCode.OK)
                        return result
                    except Exception as e:
                        span.set_status(StatusCode.ERROR, str(e))
                        span.record_exception(e)
                        raise
                    finally:
                        duration = time.time() - start
                        self.metrics.node_duration.labels(node=node_name).observe(duration)

            return wrapper
        return decorator

    async def record_conversation(
        self,
        session_id: str,
        query: str,
        response: str,
        trace_id: str,
        metadata: dict
    ):
        """Store conversation for replay and debugging."""
        await self.replay_store.save({
            "session_id": session_id,
            "timestamp": time.time(),
            "query": query,
            "response": response,
            "trace_id": trace_id,
            "metadata": metadata
        })

    async def replay_conversation(self, session_id: str) -> list[dict]:
        """Replay a conversation for debugging."""
        return await self.replay_store.get_session(session_id)


class AnomalyDetector:
    """Detect unusual patterns that might indicate problems."""

    def __init__(self):
        self.baseline_store = BaselineStore()
        self.alert_manager = AlertManager()

    async def check_anomalies(self):
        """Continuous anomaly detection."""
        checks = [
            self._check_latency_anomaly,
            self._check_error_rate_anomaly,
            self._check_tool_failure_anomaly,
            self._check_cache_hit_anomaly,
        ]

        for check in checks:
            anomaly = await check()
            if anomaly:
                await self.alert_manager.send_alert(anomaly)

    async def _check_latency_anomaly(self) -> Anomaly | None:
        """Detect unusual response times."""
        current_p99 = await self.metrics.get_p99_latency(minutes=5)
        baseline_p99 = await self.baseline_store.get("latency_p99")

        if current_p99 > baseline_p99 * 2:  # 2x baseline
            return Anomaly(
                type="latency_spike",
                severity="warning",
                current_value=current_p99,
                baseline_value=baseline_p99,
                message=f"P99 latency {current_p99}ms is 2x baseline {baseline_p99}ms"
            )

        return None

    async def _check_error_rate_anomaly(self) -> Anomaly | None:
        """Detect unusual error rates."""
        current_rate = await self.metrics.get_error_rate(minutes=5)
        baseline_rate = await self.baseline_store.get("error_rate")

        if current_rate > baseline_rate + 0.05:  # 5% above baseline
            return Anomaly(
                type="error_spike",
                severity="critical",
                current_value=current_rate,
                baseline_value=baseline_rate,
                message=f"Error rate {current_rate:.1%} is above baseline {baseline_rate:.1%}"
            )

        return None
```

**Dashboard Metrics:**
```python
# Key metrics to track
METRICS = {
    # Latency
    "request_duration_seconds": Histogram(
        buckets=[0.1, 0.5, 1, 2, 5, 10, 30]
    ),
    "node_duration_seconds": Histogram(
        labels=["node"],
        buckets=[0.01, 0.05, 0.1, 0.5, 1, 5]
    ),
    "tool_duration_seconds": Histogram(
        labels=["tool"],
        buckets=[0.01, 0.05, 0.1, 0.5, 1, 5]
    ),

    # Throughput
    "requests_total": Counter(labels=["status"]),
    "tool_calls_total": Counter(labels=["tool", "status"]),

    # Quality
    "verification_score": Histogram(buckets=[0.1, 0.3, 0.5, 0.7, 0.9, 1.0]),
    "user_feedback": Counter(labels=["feedback_type"]),

    # Cache
    "cache_hits_total": Counter(),
    "cache_misses_total": Counter(),

    # Cost
    "llm_tokens_total": Counter(labels=["model", "type"]),  # type: input/output
    "llm_cost_dollars": Counter(labels=["model"]),
}
```

---

### 8. Multi-Modal Support

**Current State:** Text only. No images or voice.

**Dream State:** Accept images of parts, voice queries, and return visual responses.

```python
class MultiModalAgent:
    """
    Handle queries across modalities:
    - Text queries (current)
    - Image queries ("What part is this?")
    - Voice queries (transcribed to text)
    - Visual responses (part diagrams, comparison charts)
    """

    def __init__(self):
        self.vision_model = ClaudeVision()
        self.voice_transcriber = WhisperTranscriber()
        self.diagram_generator = DiagramGenerator()

    async def process_image_query(
        self,
        image: bytes,
        query: str | None
    ) -> ImageQueryResult:
        """Process a query that includes an image."""
        # Use vision model to understand the image
        image_analysis = await self.vision_model.analyze(
            image,
            prompt="""Analyze this appliance part image.
            Identify:
            1. What type of part this appears to be
            2. Any visible part numbers or labels
            3. The likely appliance type (refrigerator, dishwasher, etc.)
            4. Condition/damage if visible"""
        )

        # Extract identifiable information
        part_info = self._extract_part_info(image_analysis)

        if part_info.ps_number:
            # Direct match - fetch part details
            return await self._fetch_identified_part(part_info.ps_number)

        if part_info.part_type:
            # Search by part type
            matches = await self._search_similar_parts(
                part_info.part_type,
                part_info.appliance_type
            )
            return ImageQueryResult(
                identified=False,
                suggestions=matches,
                analysis=image_analysis
            )

        return ImageQueryResult(
            identified=False,
            message="Couldn't identify the part. Can you provide more details?",
            analysis=image_analysis
        )

    async def process_voice_query(self, audio: bytes) -> str:
        """Transcribe and process voice query."""
        # Transcribe audio
        transcript = await self.voice_transcriber.transcribe(audio)

        # Process as text query
        return transcript

    async def generate_visual_response(
        self,
        response_type: str,
        data: dict
    ) -> bytes:
        """Generate visual content for response."""
        if response_type == "comparison_chart":
            return await self.diagram_generator.comparison_chart(
                parts=data["parts"],
                metrics=["price", "rating", "difficulty"]
            )

        if response_type == "installation_diagram":
            return await self.diagram_generator.installation_steps(
                steps=data["steps"],
                part_image=data.get("part_image")
            )

        if response_type == "compatibility_matrix":
            return await self.diagram_generator.compatibility_matrix(
                part=data["part"],
                models=data["models"]
            )
```

---

### 9. Human-in-the-Loop Escalation

**Current State:** Agent handles everything or fails. No human escalation.

**Dream State:** Graceful escalation to human agents when AI can't help.

```python
class EscalationManager:
    """
    Manage escalation to human agents when:
    - AI confidence is low
    - User explicitly requests human
    - Complex issues beyond AI capability
    - Sensitive situations (complaints, safety)
    """

    def __init__(self):
        self.confidence_threshold = 0.6
        self.escalation_queue = EscalationQueue()
        self.human_agent_pool = HumanAgentPool()

    async def should_escalate(
        self,
        query: str,
        response: str,
        confidence: float,
        context: ConversationContext
    ) -> EscalationDecision:
        # Check explicit escalation triggers
        if self._wants_human(query):
            return EscalationDecision(
                should_escalate=True,
                reason="user_requested",
                priority="high"
            )

        # Check confidence threshold
        if confidence < self.confidence_threshold:
            return EscalationDecision(
                should_escalate=True,
                reason="low_confidence",
                priority="medium",
                ai_response=response  # Include AI attempt for human reference
            )

        # Check for sensitive topics
        if self._is_sensitive(query, context):
            return EscalationDecision(
                should_escalate=True,
                reason="sensitive_topic",
                priority="high"
            )

        # Check for repeated failures
        if context.consecutive_unhelpful >= 3:
            return EscalationDecision(
                should_escalate=True,
                reason="repeated_failure",
                priority="medium"
            )

        return EscalationDecision(should_escalate=False)

    def _wants_human(self, query: str) -> bool:
        """Detect if user wants to talk to a human."""
        human_triggers = [
            r"\bhuman\b", r"\bagent\b", r"\bperson\b",
            r"\brepresentative\b", r"\bsomeone\b",
            r"\btalk to\b", r"\bspeak with\b"
        ]
        return any(re.search(t, query, re.I) for t in human_triggers)

    def _is_sensitive(self, query: str, context: ConversationContext) -> bool:
        """Detect sensitive situations."""
        sensitive_patterns = [
            r"\bcomplaint\b", r"\brefund\b", r"\bangry\b",
            r"\blawyer\b", r"\blegal\b", r"\binjur(y|ed)\b",
            r"\bdanger(ous)?\b", r"\bfire\b", r"\bsmoke\b"
        ]
        return any(re.search(p, query, re.I) for p in sensitive_patterns)

    async def escalate(
        self,
        decision: EscalationDecision,
        context: ConversationContext
    ) -> EscalationResult:
        """Execute escalation to human agent."""
        # Create escalation ticket
        ticket = EscalationTicket(
            priority=decision.priority,
            reason=decision.reason,
            conversation_history=context.history,
            user_info=context.user,
            ai_analysis=decision.ai_response
        )

        # Find available human agent
        agent = await self.human_agent_pool.get_available(
            skills=self._required_skills(decision),
            priority=decision.priority
        )

        if agent:
            # Direct handoff
            await agent.assign(ticket)
            return EscalationResult(
                status="handed_off",
                agent=agent,
                estimated_wait="0 minutes"
            )
        else:
            # Queue for callback
            position = await self.escalation_queue.add(ticket)
            wait_time = await self.escalation_queue.estimate_wait(position)
            return EscalationResult(
                status="queued",
                position=position,
                estimated_wait=wait_time
            )
```

---

### 10. Automated Quality Evaluation

**Current State:** Manual testing. No automated quality checks.

**Dream State:** Continuous automated evaluation against golden datasets and generated tests.

```python
class QualityEvaluator:
    """
    Continuous evaluation of agent quality:
    - Golden dataset regression testing
    - LLM-as-judge evaluation
    - Synthetic test generation
    - Production traffic sampling
    """

    def __init__(self):
        self.golden_dataset = GoldenDataset()
        self.judge_model = ChatAnthropic(model="claude-3-opus")
        self.test_generator = SyntheticTestGenerator()

    async def run_regression_tests(self) -> RegressionReport:
        """Run agent against golden dataset."""
        results = []

        for test_case in self.golden_dataset.get_all():
            # Run agent
            response, _, parts = await run_agent(
                test_case.query,
                test_case.session_context
            )

            # Evaluate
            evaluation = await self._evaluate_response(
                query=test_case.query,
                expected=test_case.expected_response,
                actual=response,
                expected_parts=test_case.expected_parts,
                actual_parts=parts
            )

            results.append(TestResult(
                test_case=test_case,
                response=response,
                evaluation=evaluation
            ))

        return RegressionReport(
            total=len(results),
            passed=sum(1 for r in results if r.evaluation.passed),
            failed=[r for r in results if not r.evaluation.passed],
            score=sum(r.evaluation.score for r in results) / len(results)
        )

    async def _evaluate_response(
        self,
        query: str,
        expected: str,
        actual: str,
        expected_parts: list[str],
        actual_parts: list[dict]
    ) -> Evaluation:
        """Use LLM-as-judge to evaluate response quality."""
        judge_prompt = f"""Evaluate this customer service response.

Query: {query}

Expected Response (reference):
{expected}

Actual Response:
{actual}

Evaluate on these criteria (1-5 scale):
1. Accuracy: Does it contain correct information?
2. Completeness: Does it answer the full question?
3. Helpfulness: Would a customer find this useful?
4. Tone: Is it professional and appropriate?
5. Conciseness: Is it appropriately brief without missing info?

Also note any:
- Factual errors
- Missing information
- Unnecessary information
- Tone issues

Respond in JSON format."""

        response = await self.judge_model.ainvoke(judge_prompt)
        scores = json.loads(response.content)

        # Check part cards
        expected_ps = set(expected_parts)
        actual_ps = {p["ps_number"] for p in actual_parts}
        parts_correct = expected_ps == actual_ps

        return Evaluation(
            accuracy=scores["accuracy"],
            completeness=scores["completeness"],
            helpfulness=scores["helpfulness"],
            tone=scores["tone"],
            conciseness=scores["conciseness"],
            parts_correct=parts_correct,
            score=sum(scores.values()) / 5,
            passed=sum(scores.values()) / 5 >= 3.5 and parts_correct,
            issues=scores.get("issues", [])
        )

    async def generate_synthetic_tests(
        self,
        category: str,
        count: int
    ) -> list[TestCase]:
        """Generate synthetic test cases for a category."""
        return await self.test_generator.generate(
            category=category,
            count=count,
            existing_tests=await self.golden_dataset.get_by_category(category)
        )


class SyntheticTestGenerator:
    """Generate realistic test cases for evaluation."""

    async def generate(
        self,
        category: str,
        count: int,
        existing_tests: list[TestCase]
    ) -> list[TestCase]:
        prompt = f"""Generate {count} realistic test cases for a PartSelect chat agent.

Category: {category}

Existing examples for reference:
{self._format_examples(existing_tests[:5])}

Generate diverse test cases covering:
- Different phrasings
- Edge cases
- Common mistakes
- Various expertise levels

For each test case provide:
1. User query
2. Expected response (what a good answer should include)
3. Expected PS numbers in response
4. Difficulty level (easy/medium/hard)

Respond in JSON format."""

        response = await self.llm.ainvoke(prompt)
        return [TestCase(**tc) for tc in json.loads(response.content)]
```

---

### The Complete Dream Architecture

Putting it all together:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           QUERY INGRESS                                      │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐                        │
│  │   Text   │ │  Voice   │ │  Image   │ │   API    │                        │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘                        │
│       └────────────┴────────────┴────────────┘                              │
│                           │                                                  │
└───────────────────────────┼──────────────────────────────────────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        INTELLIGENT ROUTING                                   │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐                         │
│  │   Semantic   │ │    Model     │ │  Escalation  │                         │
│  │    Cache     │ │   Router     │ │   Detector   │                         │
│  └──────┬───────┘ └──────┬───────┘ └──────┬───────┘                         │
│         │ Cache Hit?     │ Model          │ Human?                          │
│         │                │ Selection      │                                  │
└─────────┼────────────────┼────────────────┼──────────────────────────────────┘
          │                │                │
          │ Miss           │                │ Yes
          ▼                ▼                ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         AGENT ORCHESTRATION                                  │
│                                                                              │
│  ┌────────────┐    ┌────────────┐    ┌────────────┐    ┌────────────┐       │
│  │   Scope    │───▶│  Executor  │───▶│  Verifier  │───▶│Synthesizer │       │
│  │   Check    │    │  (ReAct)   │    │   Agent    │    │   Agent    │       │
│  └────────────┘    └────────────┘    └────────────┘    └────────────┘       │
│        │                 │                 │                 │               │
│        │                 ▼                 │                 │               │
│        │          ┌────────────┐           │                 │               │
│        │          │   Tools    │           │                 │               │
│        │          │ (14 total) │           │                 │               │
│        │          └────────────┘           │                 │               │
│        │                                   │                 │               │
│        └───────────────────────────────────┴─────────────────┘               │
│                              │                                               │
└──────────────────────────────┼───────────────────────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        PERSONALIZATION                                       │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐                         │
│  │    User      │ │   Response   │ │   Visual     │                         │
│  │   Profile    │ │   Styling    │ │  Generation  │                         │
│  └──────────────┘ └──────────────┘ └──────────────┘                         │
└─────────────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          DATA LAYER                                          │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐          │
│  │ Primary  │ │ Replicas │ │  Vector  │ │  Cache   │ │  Event   │          │
│  │    DB    │ │   (3x)   │ │    DB    │ │  (Redis) │ │  Stream  │          │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘          │
│                                                                              │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐                                     │
│  │  Model   │ │  Prompt  │ │ Feature  │                                     │
│  │ Registry │ │  Store   │ │  Store   │                                     │
│  └──────────┘ └──────────┘ └──────────┘                                     │
└─────────────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                       OBSERVABILITY & LEARNING                               │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐        │
│  │  Distributed │ │    Metrics   │ │   Quality    │ │    Prompt    │        │
│  │   Tracing    │ │  & Alerting  │ │  Evaluator   │ │   Evolver    │        │
│  └──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘        │
│                                                                              │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐                         │
│  │   A/B Test   │ │   Anomaly    │ │  Conversation│                         │
│  │    Engine    │ │  Detector    │ │    Replay    │                         │
│  └──────────────┘ └──────────────┘ └──────────────┘                         │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

### Why This Matters

The dream architecture isn't just about handling more traffic—it's about building a fundamentally better product:

| Dimension | Current | Dream |
|-----------|---------|-------|
| **Accuracy** | Good (prompt-based) | Excellent (verified) |
| **Speed** | 3-8 seconds | 0.5-3 seconds (cached) |
| **Cost** | ~$0.10/query | ~$0.02/query (optimized) |
| **Reliability** | Single point of failure | Multi-region HA |
| **Learning** | Manual iteration | Continuous improvement |
| **Personalization** | None | User-specific responses |
| **Modalities** | Text only | Text, voice, images |
| **Escalation** | None | Seamless human handoff |
| **Observability** | Print statements | Full tracing & metrics |

The gap between current and dream isn't primarily technical—it's about investment. Each component is achievable with known technologies. The question is whether the business value justifies the engineering cost.

---

### Implementation Priority

If building toward the dream, here's the order of priority based on impact vs effort:

**High Impact, Lower Effort (Do First):**
1. Semantic caching - 30-50% cost reduction
2. Basic observability - Essential for everything else
3. Verifier agent - Quality improvement
4. User feedback loop - Enables learning

**High Impact, Higher Effort (Do Second):**
5. Model routing - Cost optimization
6. Real-time sync - Data freshness
7. Personalization - User experience
8. Quality evaluation - Continuous improvement

**Transformative, High Effort (Do When Ready):**
9. Self-improving prompts - Autonomous improvement
10. Multi-modal support - New capabilities
11. Human escalation - Complete solution
12. Full dream architecture - Enterprise scale

The dream is achievable. It just takes time, resources, and the right priorities.

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
