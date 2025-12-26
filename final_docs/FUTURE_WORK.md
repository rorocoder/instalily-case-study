# Future Work and Scalability Roadmap

## Overview

This document outlines improvements, extensions, and scalability considerations for the PartSelect Chat Agent. It's organized from near-term enhancements that build on the current architecture to longer-term changes that would require more significant investment.

The current system is a solid foundation: a ReAct agent that handles varied queries, a hybrid SQL/vector database, resilient scrapers, and a clean frontend. But there's meaningful work to do to make this production-ready for scale.

---

## Immediate Improvements (Low Effort, High Impact)

### 1. Persist Live-Scraped Parts to Database

**Current State:** When a user asks about a part not in our database, the `scrape_part_live()` tool fetches it in real-time. This data is returned to the user but then discarded.

**Problem:** If another user asks about the same part tomorrow, we scrape it again. Wasteful.

**Solution:** After successful live scrapes, insert the data into Supabase:

```python
def scrape_part_live(ps_number: str) -> dict:
    data = _perform_scrape(ps_number)

    if data and "error" not in data:
        # Persist to database for future queries
        try:
            supabase_client.upsert_part(data)
            supabase_client.upsert_compatibility(ps_number, data.get("_compatible_models", []))
            # ... upsert Q&A, reviews, stories
        except Exception as e:
            logger.warning(f"Failed to persist scraped data: {e}")
            # Continue anyway - we still have the data for this request

    return data
```

This creates a self-expanding database: every user query that triggers a scrape makes future queries faster.

**Complexity:** Low - the upsert infrastructure already exists in `load_data.py`.

---

### 2. Add a Verifier Node

**Current State:** The Synthesizer generates responses based on tool results. There's no explicit verification that claims are accurate.

**Problem:** The two biggest failure modes in e-commerce agents are:
1. Claiming compatibility without evidence ("Yes, this fits your model" when the data doesn't confirm it)
2. Making up information (wrong prices, non-existent part numbers)

**Solution:** Add a verification step between Executor and Synthesizer:

```
Executor → Verifier → Synthesizer
```

The Verifier node would:
1. Extract factual claims from the Executor's findings
2. Cross-reference against tool results
3. Flag unsupported claims for removal or hedging
4. Add confidence scores to assertions

Example verification rules:
- "Part X fits model Y" → Must have explicit match in compatibility data
- "Price is $X" → Must match `part_price` field exactly
- "In stock" → Must have `availability` field confirming

This is more conservative than the current approach but prevents the worst failures.

**Complexity:** Medium - requires defining claim extraction and verification logic.

---

### 3. Enable Streaming Response Display

**Current State:** The backend supports SSE streaming, but the frontend waits for complete responses.

**Problem:** Some responses (complex troubleshooting, multi-part comparisons) take 10+ seconds. Users stare at "Thinking..." with no feedback.

**Solution:** Wire up the existing streaming infrastructure:

```javascript
// ChatWindow.js
const handleStreamingResponse = async (userMessage) => {
  let partialMessage = "";

  await api.getAIMessageStreaming(
    userMessage,
    (token) => {
      partialMessage += token;
      setMessages(prev => {
        const updated = [...prev];
        updated[updated.length - 1].content = partialMessage;
        return updated;
      });
    },
    (finalResponse) => {
      // Add part cards when complete
      setMessages(prev => {
        const updated = [...prev];
        updated[updated.length - 1].partCards = finalResponse.parts;
        return updated;
      });
    }
  );
};
```

Part cards would appear after streaming completes (since they require full response to extract PS numbers).

**Complexity:** Low - infrastructure exists, just needs UI integration.

---

### 4. Search by Manufacturer Part Number

**Current State:** `resolve_part()` handles PS numbers, URLs, and session references. Manufacturer numbers (like "WPW10321304") require a database lookup.

**Problem:** Many customers know their manufacturer part number (it's printed on the old part) but not the PS number.

**Solution:** Add an index and query path for manufacturer numbers:

```sql
CREATE INDEX idx_parts_manufacturer_number ON parts(manufacturer_part_number);
```

```python
def resolve_part(reference: str, session_context: dict = None) -> dict:
    # ... existing PS number and URL handling ...

    # Try manufacturer part number lookup
    if not reference.upper().startswith("PS"):
        result = db.get_part_by_manufacturer_number(reference)
        if result:
            return {"ps_number": result["ps_number"], "source": "manufacturer_number"}

    # ... rest of resolution logic ...
```

**Complexity:** Low - just database index and query addition.

---

### 5. Improve Session Persistence

**Current State:** Sessions are stored in an in-memory Python dict. Server restart = all sessions lost.

**Problem:** Scaling to multiple API instances requires shared session storage.

**Solution:** Move sessions to Redis:

```python
import redis

redis_client = redis.Redis(host='localhost', port=6379, db=0)

def get_session(session_id: str) -> SessionState:
    data = redis_client.get(f"session:{session_id}")
    if data:
        return SessionState.parse_raw(data)
    return SessionState()

def save_session(session_id: str, state: SessionState):
    redis_client.setex(
        f"session:{session_id}",
        3600,  # 1 hour TTL
        state.json()
    )
```

Benefits:
- Sessions survive server restarts
- Multiple API instances can share sessions
- Built-in TTL handles cleanup

**Complexity:** Low-Medium - requires Redis infrastructure.

---

## Medium-Term Enhancements

### 6. Differential Scraping with Change Detection

**Current State:** To update the database, we re-scrape everything. A full scrape takes ~17 minutes for 2,000 parts.

**Problem:** Most data doesn't change day-to-day. Prices, reviews, and Q&A update more frequently than part specs.

**Solution:** Implement sitemap-based change detection:

```python
def get_changed_urls(sitemap_url: str, last_check: datetime) -> list[str]:
    """Parse sitemap, return URLs modified since last_check."""
    response = requests.get(sitemap_url)
    tree = ET.fromstring(response.content)

    changed = []
    for url_elem in tree.findall(".//{http://www.sitemaps.org/schemas/sitemap/0.9}url"):
        loc = url_elem.find("{http://www.sitemaps.org/schemas/sitemap/0.9}loc").text
        lastmod = url_elem.find("{http://www.sitemaps.org/schemas/sitemap/0.9}lastmod")

        if lastmod is not None:
            modified = datetime.fromisoformat(lastmod.text)
            if modified > last_check:
                changed.append(loc)

    return changed
```

Scraper changes:
1. Check sitemap for modified URLs since last run
2. Only scrape changed pages
3. Store `last_modified` timestamp per part

This reduces daily refresh from 17 minutes to potentially seconds (for only changed content).

**Complexity:** Medium - requires sitemap parsing and timestamp tracking.

---

### 7. Add Blog Post Scraping and Search

**Current State:** We scrape parts, repair symptoms, and customer content (Q&A, reviews, stories). Blog posts are not captured.

**Problem:** PartSelect's blog has valuable repair guides, tips, and educational content that could help with troubleshooting queries.

**Solution:**

1. Create blog scraper:
```python
class BlogScraper:
    def scrape_blog_post(self, url: str) -> dict:
        return {
            "blog_id": extract_slug(url),
            "title": self._get_title(),
            "content": self._get_content(),
            "category": self._get_category(),  # "refrigerator", "dishwasher", etc.
            "published_date": self._get_date(),
            "tags": self._get_tags()
        }
```

2. Create vector table:
```sql
CREATE TABLE blog_embeddings (
    id SERIAL PRIMARY KEY,
    blog_id TEXT UNIQUE,
    title TEXT,
    content TEXT,
    category TEXT,
    published_date DATE,
    embedding vector(384)
);
```

3. Add search tool:
```python
@registry.register(category="vector")
def search_blogs(query: str, category: str = None) -> list[dict]:
    """Search blog posts for repair guides and tips."""
    embedding = generate_embedding(query)
    return db.search_blogs(embedding, category)
```

4. Update Executor prompt with Pattern 5: Knowledge Search.

**Complexity:** Medium - new scraper, table, and tool.

---

### 8. Paginate Through Reviews/Q&A/Stories

**Current State:** We only scrape the first page of customer content (~10 items per part).

**Problem:** Popular parts have hundreds of reviews and Q&A entries. We're missing potentially valuable content.

**Solution:** Add pagination to content extraction:

```python
def scrape_all_qna(driver, ps_number: str, max_pages: int = 10) -> list[dict]:
    all_qna = []
    page = 1

    while page <= max_pages:
        qna = extract_qna_page(driver)
        if not qna:
            break
        all_qna.extend(qna)

        if not click_next_page(driver, "qna"):
            break
        page += 1
        time.sleep(0.5)

    return all_qna
```

Trade-offs:
- **Pro:** More comprehensive data, better semantic search results
- **Con:** Longer scrape times, more storage, potential diminishing returns (first page is "most helpful")

Recommendation: Paginate reviews (quality varies less), limit Q&A to 3-5 pages.

**Complexity:** Medium - requires pagination logic per content type.

---

### 9. Model-Centric Queries

**Current State:** The agent is part-centric. Users ask about parts, check part compatibility with models.

**Problem:** Some users start with their model: "What parts are available for my WDT780SAEM1?" or "Show me everything that fits a Whirlpool Gold Series."

**Solution:** Add model resolution and search tools:

```python
@registry.register(category="resolution")
def resolve_model(reference: str) -> dict:
    """
    Parse model number references into standardized model numbers.
    Handles fuzzy matching for typos and partial numbers.
    """
    # Exact match
    exact = db.get_model(reference)
    if exact:
        return {"model_number": exact["model_number"], "brand": exact["brand"]}

    # Fuzzy match
    candidates = db.fuzzy_search_models(reference)
    if len(candidates) == 1:
        return {"model_number": candidates[0]["model_number"], "confidence": "fuzzy"}
    elif len(candidates) > 1:
        return {"ambiguous": True, "candidates": candidates[:5]}

    return {"error": f"Model {reference} not found"}

@registry.register(category="search")
def search_parts_for_model(model_number: str, part_type: str = None) -> list[dict]:
    """Find all parts compatible with a specific model, optionally filtered by type."""
    return db.get_compatible_parts(model_number, part_type)
```

This requires scraping more model metadata (currently we only store model numbers in compatibility table).

**Complexity:** Medium-High - needs model data enrichment.

---

### 10. Parallel Tool Execution

**Current State:** The ReAct executor calls tools sequentially.

**Problem:** For queries like "Compare PS11752778 and PS11752779", the agent calls `get_part()` twice in sequence. These could run in parallel.

**Solution:** Detect independent tool calls and batch them:

```python
async def execute_tools_parallel(tool_calls: list[ToolCall]) -> list[ToolResult]:
    # Group by dependency
    independent = [tc for tc in tool_calls if not tc.depends_on]

    # Execute independent calls in parallel
    async with asyncio.TaskGroup() as tg:
        tasks = [tg.create_task(execute_tool(tc)) for tc in independent]

    return [t.result() for t in tasks]
```

This requires:
1. Modifying the executor to detect parallel opportunities
2. Async tool implementations
3. Result aggregation logic

Estimated speedup: 30-50% for multi-part queries.

**Complexity:** High - significant executor changes.

---

## Adding New Appliance Types: A Deep Dive

This section covers the full complexity of expanding appliance type support, including the nuanced scoping challenges that arise when your database contains more data than your agent is authorized to serve.

### Current State: Why Scoping is "Easy" Right Now

The current system has a simplified scoping situation:

```
┌─────────────────────────────────────────────────────────────┐
│                    CURRENT STATE                             │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│   Database contains:     Agent serves:                       │
│   ┌─────────────────┐   ┌─────────────────┐                 │
│   │ Refrigerator    │   │ Refrigerator    │                 │
│   │ Dishwasher      │   │ Dishwasher      │                 │
│   └─────────────────┘   └─────────────────┘                 │
│                                                              │
│   These are IDENTICAL - no mismatch to handle               │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

**Why this is easy:**
- Primary scope check rejects "microwave" queries → no DB lookup happens
- Secondary scope check mostly catches live-scraped edge cases (chainsaws via PS number)
- Database queries naturally return only in-scope data because that's all we have

**The edge cases we DO handle:**
- Live scraping can return any appliance type (user asks about PS number for a chainsaw)
- Secondary scope check + LLM classifier catches these
- But these are rare - most queries hit the database, which only has in-scope data

### The Mixed-Scope Database Challenge

Imagine we scrape microwaves to prepare for future support, but the agent shouldn't serve microwave queries yet:

```
┌─────────────────────────────────────────────────────────────┐
│                    MIXED-SCOPE STATE                         │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│   Database contains:     Agent serves:                       │
│   ┌─────────────────┐   ┌─────────────────┐                 │
│   │ Refrigerator    │   │ Refrigerator    │                 │
│   │ Dishwasher      │   │ Dishwasher      │                 │
│   │ Microwave  ←────┼───┼── NOT SERVED    │                 │
│   │ Washing Machine │   │                 │                 │
│   └─────────────────┘   └─────────────────┘                 │
│                                                              │
│   Database has MORE than agent is authorized to serve       │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

**New problems that arise:**

1. **Search returns out-of-scope results:**
   User: "I need a motor"
   `search_parts("motor")` returns refrigerator motors, dishwasher motors, AND microwave motors.
   Agent shouldn't mention microwave motors even though they're in the database.

2. **PS number lookups find out-of-scope parts:**
   User: "Tell me about PS12345678"
   `get_part("PS12345678")` returns a microwave part that's in our database.
   Currently, secondary scope check would catch this - but now it's catching DATABASE data, not just scraped data.

3. **Compatibility queries cross boundaries:**
   A model number might be compatible with both refrigerator parts and microwave parts.
   `get_compatible_parts("MODEL123")` could return both.

4. **Semantic search finds cross-domain content:**
   User: "appliance making buzzing noise"
   Vector search might find microwave repair stories alongside refrigerator ones.

### Solution Architecture: Scope-Aware Database Queries

The fix is pushing scope filtering into the database layer, not just post-processing:

```python
# BEFORE: Scope filtering only at agent level
def search_parts(query: str, part_type: str = None) -> list[dict]:
    results = db.search_parts(query, part_type)
    return results  # Might include microwaves!

# AFTER: Scope filtering at database level
def search_parts(
    query: str,
    part_type: str = None,
    allowed_appliance_types: list[str] = None  # NEW PARAMETER
) -> list[dict]:
    if allowed_appliance_types is None:
        allowed_appliance_types = get_allowed_types_from_config()

    results = db.search_parts(
        query,
        part_type,
        appliance_type_filter=allowed_appliance_types  # Filter at SQL level
    )
    return results
```

**Database query changes:**

```sql
-- BEFORE: No appliance filter
SELECT * FROM parts
WHERE part_type ILIKE '%motor%'
ORDER BY average_rating DESC
LIMIT 10;

-- AFTER: Explicit appliance filter
SELECT * FROM parts
WHERE part_type ILIKE '%motor%'
  AND appliance_type IN ('refrigerator', 'dishwasher')  -- Added filter
ORDER BY average_rating DESC
LIMIT 10;
```

**Every database function needs this treatment:**

| Function | Current | With Scope Filter |
|----------|---------|-------------------|
| `search_parts()` | No filter | `AND appliance_type IN (...)` |
| `get_part()` | No filter | Check appliance_type in result |
| `get_compatible_parts()` | No filter | `JOIN parts ... WHERE appliance_type IN (...)` |
| `search_qna()` | No filter | `JOIN parts ON ps_number WHERE appliance_type IN (...)` |
| `get_symptoms()` | Has `appliance_type` param | Already filtered |
| `search_repair_stories()` | No filter | `JOIN parts ... WHERE appliance_type IN (...)` |

### The Scope Configuration System

Instead of hardcoding allowed types everywhere, centralize in configuration:

```yaml
# config/scope.yaml
scope:
  # What appliance types the agent is authorized to serve
  allowed_types:
    - refrigerator
    - dishwasher

  # What's in the database (for admin/monitoring purposes)
  available_types:
    - refrigerator
    - dishwasher
    - microwave      # Scraped but not served
    - washing_machine # Scraped but not served

  # Keywords for primary scope check (rule-based)
  keywords:
    refrigerator:
      in_scope:
        - "refrigerator"
        - "fridge"
        - "freezer"
        - "ice maker"
        - "ice dispenser"
        - "water filter"
        - "crisper"
      out_scope: []

    dishwasher:
      in_scope:
        - "dishwasher"
        - "dish washer"
        - "rinse aid"
        - "spray arm"
        - "dish rack"
      out_scope: []

    microwave:
      in_scope:
        - "microwave"
        - "magnetron"
        - "turntable"
      out_scope: []  # These become OUT_OF_SCOPE keywords when microwave not in allowed_types

    washing_machine:
      in_scope:
        - "washer"
        - "washing machine"
        - "agitator"
        - "laundry"
      out_scope: []

  # Special handling
  ambiguous_keywords:
    - "motor"       # Could be any appliance
    - "pump"        # Could be any appliance
    - "filter"      # Could be any appliance
    - "door"        # Could be any appliance
```

**Scope check logic using config:**

```python
def load_scope_config():
    with open("config/scope.yaml") as f:
        return yaml.safe_load(f)

def get_scope_keywords(config):
    allowed = set(config["scope"]["allowed_types"])

    in_scope_keywords = []
    out_scope_keywords = []

    for appliance, keywords in config["scope"]["keywords"].items():
        if appliance in allowed:
            in_scope_keywords.extend(keywords["in_scope"])
        else:
            # Keywords for non-allowed types become OUT_OF_SCOPE
            out_scope_keywords.extend(keywords["in_scope"])

    return in_scope_keywords, out_scope_keywords

# Example result when allowed = ["refrigerator", "dishwasher"]:
# in_scope = ["refrigerator", "fridge", "ice maker", "dishwasher", ...]
# out_scope = ["microwave", "magnetron", "washer", "washing machine", ...]
```

### Scraping Strategy for Mixed-Scope Database

**Option A: Separate Data Directories**

Keep scraped data isolated by appliance type:

```
data/
├── refrigerator/
│   ├── parts.csv
│   ├── model_compatibility.csv
│   ├── qna.csv
│   └── ...
├── dishwasher/
│   ├── parts.csv
│   └── ...
├── microwave/           # Scraped but not loaded to prod DB
│   ├── parts.csv
│   └── ...
└── washing_machine/     # Scraped but not loaded to prod DB
    ├── parts.csv
    └── ...
```

**Loader only loads allowed types:**

```python
def load_all_data(allowed_types: list[str]):
    for appliance_type in allowed_types:
        data_dir = f"data/{appliance_type}"
        if os.path.exists(data_dir):
            load_parts(f"{data_dir}/parts.csv")
            load_compatibility(f"{data_dir}/model_compatibility.csv")
            # ...
```

**Pros:** Clean separation, easy to add new types by just loading their directory
**Cons:** Can't do cross-type queries even for admin purposes

**Option B: Single Database with Type Column (Current Approach)**

All data in one table, filtered by `appliance_type` column:

```sql
-- All parts in one table
SELECT COUNT(*), appliance_type FROM parts GROUP BY appliance_type;
-- refrigerator: 1,500
-- dishwasher: 800
-- microwave: 2,000 (scraped but agent won't serve)
```

**Pros:** Simpler schema, enables cross-type analytics
**Cons:** Must remember to filter everywhere, risk of data leakage

**Option C: Hybrid with Views**

Create filtered views for the agent, full tables for admin:

```sql
-- Full table (admin access)
CREATE TABLE parts_all (
    ps_number TEXT PRIMARY KEY,
    appliance_type TEXT,
    -- ... all columns
);

-- Filtered view (agent access)
CREATE VIEW parts AS
SELECT * FROM parts_all
WHERE appliance_type IN ('refrigerator', 'dishwasher');

-- Agent queries `parts` view, never sees microwave data
-- Admin queries `parts_all` for analytics
```

**Update view when expanding scope:**

```sql
CREATE OR REPLACE VIEW parts AS
SELECT * FROM parts_all
WHERE appliance_type IN ('refrigerator', 'dishwasher', 'microwave');
```

**Pros:** Agent code doesn't need scope filters, impossible to accidentally leak
**Cons:** More complex DB setup, view updates require migrations

### Scraping New Appliance Types: Step by Step

**Step 1: Add Scraper Configuration**

```python
# scrapers/config.py
APPLIANCE_CONFIGS = {
    "refrigerator": {
        "base_url": "https://www.partselect.com/Refrigerator-Parts.htm",
        "related_section_pattern": "Refrigerator Parts",
        "symptom_url": "https://www.partselect.com/Repair/Refrigerator/"
    },
    "dishwasher": {
        "base_url": "https://www.partselect.com/Dishwasher-Parts.htm",
        "related_section_pattern": "Dishwasher Parts",
        "symptom_url": "https://www.partselect.com/Repair/Dishwasher/"
    },
    # NEW
    "microwave": {
        "base_url": "https://www.partselect.com/Microwave-Parts.htm",
        "related_section_pattern": "Microwave Parts",
        "symptom_url": "https://www.partselect.com/Repair/Microwave/"
    },
    "washing_machine": {
        "base_url": "https://www.partselect.com/Washing-Machine-Parts.htm",
        "related_section_pattern": "Washing Machine Parts",
        "symptom_url": "https://www.partselect.com/Repair/Washing-Machine/"
    }
}
```

**Step 2: Verify Site Structure is Compatible**

Not all appliance types have identical page structures. Before scraping:

```bash
# Test single page extraction
python -m scrapers.dev.test_single_page --appliance microwave --url "https://..."
```

Check:
- Does the brand listing page have the same HTML structure?
- Do part pages have the same layout?
- Is the compatibility table the same infinite-scroll pattern?
- Are Q&A/reviews/repair stories in the same locations?

**Potential differences to handle:**
- Different CSS selectors for some elements
- Different number of review pages
- Different symptom page structure in repair section

**Step 3: Run Scraper for New Type**

```bash
# Scrape microwave parts (output to data/microwave/)
python -m scrapers.run_scraper microwave --output-dir data/microwave

# Scrape microwave repair content
python -m scrapers.repair_scraper microwave --output-dir data/microwave

# Verify output
ls -la data/microwave/
# parts.csv, model_compatibility.csv, qna.csv, reviews.csv, repair_stories.csv
# repair_symptoms.csv, repair_instructions.csv
```

**Step 4: Load to Database (But Don't Enable in Agent)**

```python
# database/load_data.py
def load_appliance_data(appliance_type: str, data_dir: str):
    """Load data for a specific appliance type."""
    # Load parts with explicit appliance_type
    load_parts(f"{data_dir}/parts.csv", appliance_type=appliance_type)
    load_compatibility(f"{data_dir}/model_compatibility.csv")
    load_qna(f"{data_dir}/qna.csv")
    # ...

# Run for microwave
load_appliance_data("microwave", "data/microwave")
```

At this point:
- Database has microwave data
- Agent scope config still only allows refrigerator/dishwasher
- Agent will reject microwave queries at primary scope check
- If user somehow gets a microwave PS number, secondary scope check rejects it

**Step 5: Test Scope Isolation**

Before enabling, verify isolation works:

```python
# Test that microwave data doesn't leak
def test_microwave_isolation():
    # Primary scope should reject
    assert not is_in_scope("my microwave isn't heating")

    # Search shouldn't return microwave parts
    results = search_parts("motor", allowed_types=["refrigerator", "dishwasher"])
    assert all(r["appliance_type"] != "microwave" for r in results)

    # PS number lookup for microwave part should be caught
    microwave_ps = "PS_MICROWAVE_PART"  # Known microwave PS from test data
    result = get_part(microwave_ps)
    # Secondary scope check should reject this
```

**Step 6: Enable New Appliance Type**

When ready to go live:

```yaml
# config/scope.yaml
scope:
  allowed_types:
    - refrigerator
    - dishwasher
    - microwave      # NEWLY ADDED
```

```python
# Update scope check keywords
IN_SCOPE_KEYWORDS = [
    # ... existing ...
    r"\bmicrowave\b",
    r"\bmagnetron\b",
    r"\bturntable\b",
    r"\bmicrowave\s*oven\b",
]

# Update secondary scope allowed list
ALLOWED_APPLIANCE_TYPES = ["refrigerator", "dishwasher", "microwave"]
```

**Step 7: Update Agent Prompts**

The Executor and Synthesizer prompts reference appliance types:

```python
# prompts.py
EXECUTOR_PROMPT = """
You are a helpful assistant for PartSelect, specializing in:
- Refrigerator parts
- Dishwasher parts
- Microwave parts  # NEW

When discussing symptoms, consider common issues for each appliance type...
"""
```

### Handling Ambiguous Queries in Mixed-Scope

Some queries don't specify appliance type:

User: "I need a new motor"
User: "My pump is broken"
User: "Looking for a door gasket"

**Strategy 1: Ask for Clarification**

```python
# Executor detects ambiguous query
if query_mentions_generic_part and not query_mentions_appliance:
    return {
        "needs_clarification": True,
        "message": "I can help you find a motor. Which appliance is this for - refrigerator, dishwasher, or microwave?"
    }
```

**Strategy 2: Search All Allowed Types, Present Grouped**

```python
results = search_parts("motor", allowed_types=["refrigerator", "dishwasher", "microwave"])

# Group by appliance type in response
response = """
I found motors for several appliances:

**Refrigerator Motors:**
- Evaporator Fan Motor (PS12345) - $45.99
- Condenser Fan Motor (PS23456) - $52.99

**Dishwasher Motors:**
- Drain Pump Motor (PS34567) - $38.99

**Microwave Motors:**
- Turntable Motor (PS45678) - $22.99

Which appliance do you need the motor for?
"""
```

**Strategy 3: Use Conversation Context**

If previous messages mentioned an appliance, assume same type:

```python
def infer_appliance_from_context(session: SessionState) -> str | None:
    for msg in reversed(session.conversation_history):
        for appliance in ALLOWED_APPLIANCE_TYPES:
            if appliance in msg.content.lower():
                return appliance
    return None

# In executor
appliance = infer_appliance_from_context(session)
if appliance:
    results = search_parts("motor", appliance_type=appliance)
else:
    # Fall back to clarification or grouped results
```

### The "Washer" Problem: Keyword Collisions

Adding washing machines creates a collision:

```
"dishwasher" contains "washer"
"washing machine" / "washer" for laundry
```

**Current regex problem:**
```python
OUT_OF_SCOPE = [r"\bwasher\b"]  # Intended to block washing machines
# But this also blocks "dishwasher"!
```

**Solution: Negative lookahead/lookbehind:**
```python
OUT_OF_SCOPE = [
    r"\bwasher\b(?<!\bdish)",     # "washer" not preceded by "dish"
    r"(?<!\bdish)\bwasher\b",     # Alternative
    r"\bwashing\s+machine\b",     # Explicit "washing machine"
    r"\blaundry\b",               # Laundry context
    r"\bclothes\s+washer\b",      # Explicit clothes washer
]

IN_SCOPE = [
    r"\bdishwasher\b",            # Explicit dishwasher
    r"\bdish\s+washer\b",         # "dish washer" as two words
]
```

**Better approach: Check in-scope first, then out-scope:**
```python
def rule_based_scope_check(query: str) -> bool | None:
    query_lower = query.lower()

    # Check explicit IN_SCOPE patterns first
    for pattern in IN_SCOPE_PATTERNS:
        if re.search(pattern, query_lower):
            return True  # Definitely in scope

    # Then check OUT_OF_SCOPE patterns
    for pattern in OUT_OF_SCOPE_PATTERNS:
        if re.search(pattern, query_lower):
            return False  # Definitely out of scope

    return None  # Ambiguous, fall back to LLM
```

This way "dishwasher" matches IN_SCOPE before "washer" can match OUT_OF_SCOPE.

### Rollout Strategy: Gradual Enablement

Don't flip the switch for all users at once:

**Phase 1: Internal Testing**
```yaml
scope:
  allowed_types:
    - refrigerator
    - dishwasher
  beta_types:
    - microwave  # Only for beta testers
```

```python
def get_allowed_types(session: SessionState) -> list[str]:
    allowed = config["scope"]["allowed_types"]
    if session.is_beta_user:
        allowed = allowed + config["scope"]["beta_types"]
    return allowed
```

**Phase 2: Percentage Rollout**
```python
def get_allowed_types(session: SessionState) -> list[str]:
    allowed = config["scope"]["allowed_types"]

    # 10% of users get microwave
    if hash(session.session_id) % 100 < 10:
        allowed = allowed + ["microwave"]

    return allowed
```

**Phase 3: Full Rollout**
```yaml
scope:
  allowed_types:
    - refrigerator
    - dishwasher
    - microwave  # Now for everyone
```

### Summary: Adding Appliance Types Checklist

| Step | Component | Action |
|------|-----------|--------|
| 1 | Scraper Config | Add new type to `APPLIANCE_CONFIGS` |
| 2 | Test Scraper | Verify site structure compatibility |
| 3 | Run Scraper | Generate CSV files for new type |
| 4 | Load Data | Import to database with `appliance_type` column |
| 5 | Verify Isolation | Confirm scope filtering prevents leakage |
| 6 | Update Scope Config | Add to `allowed_types` |
| 7 | Update Keywords | Add in-scope keywords, handle collisions |
| 8 | Update Prompts | Reference new appliance type |
| 9 | Beta Test | Gradual rollout to subset of users |
| 10 | Full Enable | Move to production for all users |

---

## Scalability Roadmap

### Current Scale

| Component | Current | Bottleneck At |
|-----------|---------|---------------|
| Parts | ~2,000 | 50,000+ |
| Compatibility records | ~500,000 | 5,000,000+ |
| Vector entries | ~30,000 | 500,000+ |
| Concurrent users | ~10 | 100+ |
| API instances | 1 | 10+ |

### Short-Term Scaling (10x Current Load)

**Database:**
- Increase IVFFlat `lists` parameter from 100 to 500
- Add read replica for query load distribution
- Implement query result caching (Redis, 5-minute TTL)

```python
def get_part_cached(ps_number: str) -> dict:
    cache_key = f"part:{ps_number}"
    cached = redis_client.get(cache_key)
    if cached:
        return json.loads(cached)

    result = db.get_part(ps_number)
    redis_client.setex(cache_key, 300, json.dumps(result))
    return result
```

**API:**
- Deploy multiple FastAPI instances behind load balancer
- Move sessions to Redis (covered above)
- Add rate limiting per IP/session

**Scraping:**
- Schedule off-peak (nights, weekends)
- Implement differential scraping (covered above)

### Medium-Term Scaling (100x Current Load)

**Vector Database Migration:**

At 500K+ vectors, pgvector becomes a bottleneck. Options:

| Option | Pros | Cons |
|--------|------|------|
| Pinecone | Managed, fast, scales infinitely | Cost, vendor lock-in |
| Weaviate | Self-hosted, feature-rich | Operational complexity |
| Qdrant | Fast, Rust-based | Newer, smaller community |
| HNSW in pgvector | Stay on Postgres | Memory-intensive |

Migration path:
1. Abstract vector operations behind interface
2. Implement Pinecone/Weaviate adapter
3. Dual-write during migration
4. Switch reads, then deprecate pgvector tables

```python
class VectorStore(ABC):
    @abstractmethod
    def search(self, embedding: list[float], limit: int) -> list[dict]:
        pass

class PgVectorStore(VectorStore):
    def search(self, embedding, limit):
        return supabase.rpc("search_qna", {"embedding": embedding, "limit": limit})

class PineconeStore(VectorStore):
    def search(self, embedding, limit):
        return pinecone_index.query(vector=embedding, top_k=limit)
```

**Database Sharding:**

If `model_compatibility` grows to tens of millions of rows:
- Shard by `part_id` (hash-based)
- Each shard handles ~1M compatibility records
- Compatibility checks route to correct shard

**Distributed Scraping:**

For 100K+ parts:
1. Deploy scraper workers on multiple machines
2. Coordinator distributes URLs via queue (Redis, RabbitMQ)
3. Workers report results back
4. Central aggregator loads to database

```
           ┌─────────────────┐
           │   Coordinator   │
           │  (URL queue)    │
           └────────┬────────┘
                    │
        ┌───────────┼───────────┐
        │           │           │
   ┌────▼────┐ ┌────▼────┐ ┌────▼────┐
   │ Worker1 │ │ Worker2 │ │ Worker3 │
   │ (scrape)│ │ (scrape)│ │ (scrape)│
   └────┬────┘ └────┬────┘ └────┬────┘
        │           │           │
        └───────────┼───────────┘
                    │
           ┌────────▼────────┐
           │   Aggregator    │
           │  (load to DB)   │
           └─────────────────┘
```

### Long-Term Scaling (1000x Current Load)

At this scale, consider:

1. **Data Partnership:** Direct feed from PartSelect instead of scraping
2. **Self-Hosted Infrastructure:** Move off Supabase to self-managed PostgreSQL clusters
3. **Multi-Region Deployment:** API and database presence in multiple regions
4. **CDN for Static Content:** Product images, frontend assets
5. **Dedicated ML Infrastructure:** Separate service for embeddings generation

---

## Feature Roadmap

### Order Support

The architecture was designed with transactions in mind. Implementation would include:

**New Tools:**
```python
@registry.register(category="order")
def check_order_status(order_id: str) -> dict:
    """Look up order status, shipping, tracking."""
    # Requires PartSelect order API integration

@registry.register(category="order")
def start_return(order_id: str, item_ps_number: str, reason: str) -> dict:
    """Initiate return process for an order item."""
    # Requires return workflow integration

@registry.register(category="order")
def get_shipping_estimate(ps_number: str, zip_code: str) -> dict:
    """Estimate shipping cost and delivery time."""
    # Requires shipping API integration
```

**Authentication:**
- OAuth integration with PartSelect accounts
- Session token storage
- Order history lookup by user ID

**Frontend:**
- Login/logout UI
- Order status cards
- Return initiation modal

### Cart Integration

**Approach 1: Deep Integration**
- Add to cart directly from chat
- View cart contents
- Checkout initiation
- Requires PartSelect cart API

**Approach 2: Cart Links**
- Generate "Add to Cart" URLs
- Link opens PartSelect with item in cart
- Lower integration effort
- Still provides value

```python
def generate_add_to_cart_url(ps_number: str, quantity: int = 1) -> str:
    return f"https://www.partselect.com/cart?add={ps_number}&qty={quantity}"
```

### Image-Based Part Identification

Users could upload a photo of a broken part for identification.

**Implementation:**
1. Image upload endpoint
2. Vision model (Claude, GPT-4V) for part description
3. Semantic search on description
4. Confirmation flow with user

```python
@registry.register(category="identification")
def identify_part_from_image(image_base64: str) -> dict:
    """Analyze image to identify appliance part."""

    # Call vision model
    description = claude_vision.analyze(
        image_base64,
        prompt="Describe this appliance part. What type of part is it? What appliance is it from?"
    )

    # Search for matching parts
    candidates = db.search_parts_semantic(description)

    return {
        "description": description,
        "candidates": candidates[:5],
        "needs_confirmation": True
    }
```

### Proactive Suggestions

Currently the agent only responds to queries. Proactive features could include:

- "Based on your ice maker issue, you might also want to check the water filter - it should be replaced every 6 months."
- "This part has a 4.2 rating. A similar part (PS12345) has 4.8 stars and costs $5 more. Want me to compare them?"
- "I notice you're looking at refrigerator parts. Would you like me to help you find parts for any other appliances?"

Implementation: Add suggestion generation step after Synthesizer based on query patterns and session history.

---

## Data Freshness: Tiered Scraping Strategy

One of the most significant architectural gaps is treating all data with the same update frequency. In reality, different data types change at very different rates.

### The Problem: Not All Data Ages Equally

| Data Type | How Often It Changes | Current Approach | Impact of Staleness |
|-----------|---------------------|------------------|---------------------|
| Part core info (name, type) | Almost never | Scraped once | Low |
| Prices | Daily/weekly | Scraped once | **High** - wrong price = customer frustration |
| Availability/stock | Hourly | Scraped once | **Critical** - selling out-of-stock items |
| Reviews | Daily (new ones added) | First page only, scraped once | Medium |
| Q&A | Weekly | First page only, scraped once | Medium |
| Compatibility | Rarely | Scraped once | Low |
| Repair symptoms | Almost never | Scraped once | Low |

**The worst failure mode:** User asks "Is this in stock?", agent says "Yes, it shows In Stock" based on data from 2 weeks ago, but the item sold out yesterday. User tries to buy, can't. Trust destroyed.

### Solution: Tiered Scraping with `last_seen_at` Tracking

**Step 1: Add timestamp tracking to all tables**

```sql
-- Track when we last scraped each piece of data
ALTER TABLE parts ADD COLUMN last_scraped_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE parts ADD COLUMN price_updated_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE parts ADD COLUMN availability_updated_at TIMESTAMP WITH TIME ZONE;

ALTER TABLE qna_embeddings ADD COLUMN last_seen_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE reviews_embeddings ADD COLUMN last_seen_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE repair_stories_embeddings ADD COLUMN last_seen_at TIMESTAMP WITH TIME ZONE;
```

**Step 2: Create tiered scraping jobs**

```python
# Tier 1: Price/Availability (every 4-6 hours)
class PriceAvailabilityScraper:
    """
    Lightweight scraper that only extracts price and stock status.
    Much faster than full part scrape - hits API or minimal page elements.
    """
    def scrape_price_availability(self, ps_number: str) -> dict:
        # Only fetch the price/availability section
        # Could potentially use PartSelect's cart API or add-to-cart endpoint
        # which often returns current price/stock without full page load
        return {
            "ps_number": ps_number,
            "part_price": self._extract_price(),
            "availability": self._extract_availability(),
            "price_updated_at": datetime.now(UTC)
        }

# Tier 2: Reviews/Q&A (daily)
class ContentRefreshScraper:
    """
    Scrapes new reviews and Q&A, adds to existing data.
    Doesn't need to re-scrape all content, just check for new items.
    """
    def scrape_new_content(self, ps_number: str, last_seen: datetime) -> dict:
        # Only get reviews/Q&A newer than last_seen
        # PartSelect sorts by date, so we can stop when we hit old content
        pass

# Tier 3: Full Refresh (weekly/monthly)
class FullPartScraper:
    """
    Complete rescrape of all part data including compatibility.
    Only run when sitemap indicates page changed or on schedule.
    """
    pass
```

**Step 3: Implement `last_seen_at` for stale data detection**

The `last_seen_at` pattern solves a subtle problem: how do we know if content was deleted?

```python
def update_qna_with_last_seen(db, ps_number: str, scraped_qna: list[dict]):
    """
    Update Q&A entries and mark when we last saw them.
    Entries not seen in 90+ days are probably deleted.
    """
    now = datetime.now(UTC)

    for qna in scraped_qna:
        db.table("qna_embeddings").upsert({
            **qna,
            "last_seen_at": now
        }, on_conflict="ps_number,question_id").execute()

    # Flag potentially deleted entries (not seen this scrape)
    # Don't delete immediately - they might just be on page 2
    db.table("qna_embeddings").update({
        "possibly_stale": True
    }).eq("ps_number", ps_number).lt("last_seen_at", now).execute()

def cleanup_stale_content(db, days_threshold: int = 90):
    """
    Remove content not seen in N days. Run periodically.
    """
    threshold = datetime.now(UTC) - timedelta(days=days_threshold)

    # Log before deleting
    stale_count = db.table("qna_embeddings").select("id", count="exact").lt("last_seen_at", threshold).execute()
    logger.info(f"Removing {stale_count.count} stale Q&A entries")

    db.table("qna_embeddings").delete().lt("last_seen_at", threshold).execute()
```

### The Resume Flag Problem: Why Incremental Updates Don't Work Today

The current `--resume` flag has a fundamental flaw:

```python
# Current behavior (problematic)
if ps_number in already_scraped_ids:
    continue  # COMPLETELY SKIP - never update existing data!
```

This means:
- Price changes? Never captured.
- New reviews? Never captured.
- Stock status changes? Never captured.

**The `--resume` flag conflates "crash recovery" with "incremental updates".**

For crash recovery, skipping already-scraped items is correct. For incremental updates, we need to RE-scrape items but only UPDATE changed fields.

**Proposed fix: Separate modes**

```python
# Scraper modes
class ScraperMode(Enum):
    FULL = "full"           # Scrape everything, overwrite all
    RESUME = "resume"       # Skip already-scraped (crash recovery)
    INCREMENTAL = "incr"    # Re-scrape, but only update if changed
    PRICES_ONLY = "prices"  # Only update price/availability fields

def should_scrape_part(ps_number: str, mode: ScraperMode, db) -> bool:
    if mode == ScraperMode.FULL:
        return True

    if mode == ScraperMode.RESUME:
        # Skip if we have ANY data for this part
        return not db.part_exists(ps_number)

    if mode == ScraperMode.INCREMENTAL:
        # Scrape if sitemap shows page was modified since our last scrape
        our_timestamp = db.get_part_last_scraped(ps_number)
        site_timestamp = sitemap.get_lastmod(ps_number)
        return site_timestamp > our_timestamp

    if mode == ScraperMode.PRICES_ONLY:
        # Always scrape, but only update price fields
        return True
```

---

## Session State: Beyond Just Parts

The current session tracks discussed parts well, but misses other contextual information that would improve conversation quality.

### Current Limitation

```python
class SessionState(BaseModel):
    all_discussed_parts: list[str]  # PS numbers
    conversation_history: list[Message]
```

This handles "this part" references, but fails for:
- "My Whirlpool" → Which model? We don't track established model context
- "The ice maker issue" → Which symptom? We don't track established symptom context
- "Is the other one compatible?" → Which other one? We only track parts, not comparison sets

### Proposed: Rich Session Context

```python
class SessionState(BaseModel):
    # What we have now
    all_discussed_parts: list[str]
    conversation_history: list[Message]

    # Part context
    primary_part: str | None = None  # The "main" part being discussed
    comparison_set: list[str] = []   # Parts being compared ("these", "them")

    # Model context
    user_model: str | None = None         # User's appliance model
    user_model_brand: str | None = None   # Extracted brand
    verified_compatible: list[str] = []   # Parts we've confirmed fit their model

    # Problem context
    current_symptom: str | None = None         # "Ice maker not making ice"
    symptom_appliance: str | None = None       # "refrigerator"
    parts_to_check: list[str] = []             # From symptom lookup

    # Shopping context (for future order support)
    cart_items: list[str] = []
    shipping_zip: str | None = None

class SessionManager:
    def update_from_tool_results(self, state: SessionState, tool_results: list) -> SessionState:
        """Extract context from tool results and update session."""

        for result in tool_results:
            # Update model context when user provides model
            if "model_number" in result and result.get("source") == "user_input":
                state.user_model = result["model_number"]
                state.user_model_brand = result.get("brand")

            # Track verified compatibility
            if result.get("is_compatible") == True:
                if result["ps_number"] not in state.verified_compatible:
                    state.verified_compatible.append(result["ps_number"])

            # Track symptom context
            if "symptom" in result and "parts" in result:
                state.current_symptom = result["symptom"]
                state.symptom_appliance = result.get("appliance_type")
                state.parts_to_check = result["parts"].split(", ")

        return state
```

### Why This Matters: The "Maintain Context" Problem

Without rich session state, conversations break:

**Without model tracking:**
```
User: "I have a WDT780SAEM1 dishwasher"
Agent: [Notes model, answers question]
User: "Does PS11752778 fit it?"
Agent: "Fit what? Could you provide your model number?"  ← FAIL
```

**Without symptom tracking:**
```
User: "My ice maker isn't working"
Agent: [Identifies symptom: "Ice maker not making ice", lists parts to check]
User: "Tell me about the water inlet valve"
Agent: [Fetches part info, forgets we're troubleshooting]
User: "How do I check if that's the problem?"
Agent: "What problem?" ← FAIL, lost symptom context
```

**With proper session state:**
```
User: "My ice maker isn't working"
Agent: [Sets state.current_symptom = "Ice maker not making ice"]
User: "Tell me about the water inlet valve"
Agent: [Knows we're in troubleshooting mode, fetches valve + repair instructions]
User: "How do I check if that's the problem?"
Agent: [Uses state.current_symptom to get specific diagnostic steps]
```

---

## The Symptom vs Part Search Confusion

There's an architectural tension in how symptoms and parts interact that causes confusion in tool selection.

### The Problem

Symptoms reference part *types* (like "Water Inlet Valve"), but our parts database has specific *parts* (like "PS11752778 - Whirlpool Water Inlet Valve for WDT780SAEM1").

When a user says "My ice maker isn't working", the flow should be:
1. `get_symptoms()` → Returns symptom with `parts: "Water Inlet Valve, Ice Maker Assembly, Water Filter"`
2. User asks about "the water inlet valve"
3. Now what? We have a part TYPE, not a specific part

**Current awkward handling:**
- Agent might call `search_parts(query="water inlet valve")` - returns many results
- Or might call `get_repair_instructions()` - gives diagnostic steps but no specific part
- No clean path from "symptom mentions part type" to "here's the specific part for your model"

### Solution: Bridge Symptom Part Types to Specific Parts

**Option A: Symptom-to-Part Resolver**

```python
@registry.register(category="symptom")
def get_parts_for_symptom(
    symptom: str,
    part_type: str,
    model_number: str = None
) -> list[dict]:
    """
    Given a symptom and the part type mentioned, find specific parts.
    If model_number provided, filter to compatible parts.

    Use when: User is troubleshooting and asks about a specific part type
    mentioned in the symptom results.
    """
    # Search for parts matching the type
    parts = db.search_parts(
        query=part_type,
        part_type_filter=part_type  # Exact type match
    )

    if model_number:
        # Filter to only parts compatible with their model
        compatible_ps_numbers = db.get_compatible_parts(model_number)
        parts = [p for p in parts if p["ps_number"] in compatible_ps_numbers]

    return parts[:5]  # Top 5 matches
```

**Option B: Normalize Part Types in Symptoms**

Instead of comma-separated text, create a proper junction table:

```sql
CREATE TABLE symptom_part_types (
    id SERIAL PRIMARY KEY,
    symptom_id INTEGER REFERENCES repair_symptoms(id),
    part_type TEXT,           -- "Water Inlet Valve"
    check_order INTEGER,      -- 1, 2, 3 (order to check)
    common_cause BOOLEAN,     -- Is this the most common cause?
    diagnostic_url TEXT       -- Link to specific diagnostic for this part
);

-- Then symptoms can properly link to parts
CREATE VIEW symptom_with_parts AS
SELECT
    s.*,
    array_agg(spt.part_type ORDER BY spt.check_order) as part_types_to_check
FROM repair_symptoms s
JOIN symptom_part_types spt ON s.id = spt.symptom_id
GROUP BY s.id;
```

**Option C: Part Type to PS Number Mapping**

For each part type, maintain a "canonical" or "most popular" part:

```sql
CREATE TABLE canonical_parts (
    part_type TEXT PRIMARY KEY,
    appliance_type TEXT,
    canonical_ps_number TEXT REFERENCES parts(ps_number),
    notes TEXT  -- "Best seller", "Most compatible", etc.
);

-- When symptom mentions "Water Inlet Valve", we can immediately suggest
-- the canonical water inlet valve for their appliance type
```

### The Ideal Flow

```
User: "My ice maker isn't working"
    ↓
get_symptoms("refrigerator", "ice maker not making ice")
    → Returns: parts_to_check = ["Water Inlet Valve", "Ice Maker Assembly", "Water Filter"]
    → Session: current_symptom = "Ice maker not making ice"
    ↓
User: "Tell me about the water inlet valve"
    ↓
Agent recognizes: We're in symptom context, user asking about a part type
    ↓
get_parts_for_symptom(
    symptom="Ice maker not making ice",
    part_type="Water Inlet Valve",
    model_number=session.user_model  # If known
)
    → Returns specific parts with prices, ratings
    ↓
Agent: "For your model, I'd recommend the Whirlpool Water Inlet Valve (PS12070506)
        at $45.99. It has a 4.7 rating. Here's how to test if yours is faulty..."
```

---

## The Verifier Node: Deep Dive

The Verifier node concept deserves more detailed treatment because it addresses the most dangerous failure modes in e-commerce.

### Why Verification Matters

In e-commerce conversations, some errors are worse than others:

| Error Type | Example | Impact | Frequency |
|------------|---------|--------|-----------|
| Minor | Wrong install time (30 min vs 45 min) | Low | Common |
| Moderate | Wrong rating (4.5 vs 4.7 stars) | Medium | Occasional |
| **Severe** | Wrong price ($89 vs $189) | High | Occasional |
| **Critical** | False compatibility claim | Very High | Rare but catastrophic |
| **Critical** | Invented part number | Very High | Rare |

The Synthesizer is good at language but has no grounding in truth. It might confidently state "Yes, this fits your model" when the tool results don't actually confirm that.

### Verification Architecture

```
                    ┌─────────────────────┐
                    │      Executor       │
                    │  (gathers data)     │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │      Verifier       │ ◄── NEW
                    │  (fact-checks)      │
                    └──────────┬──────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                │
         (all pass)    (soft warnings)    (hard failures)
              │                │                │
              ▼                ▼                ▼
        Synthesizer      Synthesizer      Rejection
        (normal)         (with caveats)   Response
```

### Verification Rules Engine

```python
from dataclasses import dataclass
from enum import Enum

class Severity(Enum):
    INFO = "info"           # Log but don't affect response
    WARNING = "warning"     # Add caveat to response
    ERROR = "error"         # Block claim, require hedge language
    CRITICAL = "critical"   # Reject entire response, return safe fallback

@dataclass
class VerificationRule:
    name: str
    severity: Severity
    check: Callable[[dict], bool]  # Returns True if violation detected
    fix: Callable[[dict], dict]    # How to fix the violation

VERIFICATION_RULES = [
    # Rule 1: Compatibility must be explicitly confirmed
    VerificationRule(
        name="unverified_compatibility",
        severity=Severity.CRITICAL,
        check=lambda ctx: (
            ctx.get("claims_compatibility") and
            not ctx.get("compatibility_verified")
        ),
        fix=lambda ctx: {
            **ctx,
            "compatibility_statement": "I couldn't verify compatibility. Please check PartSelect.com directly."
        }
    ),

    # Rule 2: Price must match tool results exactly
    VerificationRule(
        name="price_mismatch",
        severity=Severity.ERROR,
        check=lambda ctx: (
            ctx.get("stated_price") and
            ctx.get("stated_price") != ctx.get("actual_price")
        ),
        fix=lambda ctx: {
            **ctx,
            "stated_price": ctx.get("actual_price"),
            "price_caveat": "Prices may vary. Verify on PartSelect.com."
        }
    ),

    # Rule 3: PS numbers must exist in tool results
    VerificationRule(
        name="invented_ps_number",
        severity=Severity.CRITICAL,
        check=lambda ctx: any(
            ps not in ctx.get("known_ps_numbers", [])
            for ps in ctx.get("mentioned_ps_numbers", [])
        ),
        fix=lambda ctx: {
            **ctx,
            "remove_ps_numbers": [
                ps for ps in ctx.get("mentioned_ps_numbers", [])
                if ps not in ctx.get("known_ps_numbers", [])
            ]
        }
    ),

    # Rule 4: Stock claims must be verified
    VerificationRule(
        name="unverified_stock",
        severity=Severity.WARNING,
        check=lambda ctx: (
            ctx.get("claims_in_stock") and
            not ctx.get("stock_verified")
        ),
        fix=lambda ctx: {
            **ctx,
            "stock_caveat": "Stock status may have changed. Verify availability before ordering."
        }
    ),

    # Rule 5: Model numbers should exist
    VerificationRule(
        name="unknown_model",
        severity=Severity.WARNING,
        check=lambda ctx: (
            ctx.get("references_model") and
            not ctx.get("model_found_in_data")
        ),
        fix=lambda ctx: {
            **ctx,
            "model_caveat": f"I couldn't verify model {ctx.get('model_number')} in our database."
        }
    ),
]

async def verifier_node(state: AgentState) -> dict:
    """
    Verify claims in executor results before synthesis.
    """
    context = extract_verification_context(state.executor_result)
    violations = []

    for rule in VERIFICATION_RULES:
        if rule.check(context):
            violations.append({
                "rule": rule.name,
                "severity": rule.severity,
            })
            context = rule.fix(context)

    # Determine outcome
    critical_violations = [v for v in violations if v["severity"] == Severity.CRITICAL]

    if critical_violations:
        return {
            "verification_passed": False,
            "rejection_reason": critical_violations[0]["rule"],
            "safe_response": generate_safe_fallback(context)
        }

    return {
        "verification_passed": True,
        "verification_warnings": [v for v in violations if v["severity"] in (Severity.WARNING, Severity.ERROR)],
        "verified_context": context
    }
```

### Extracting Claims for Verification

Before we can verify, we need to know what claims the agent is making:

```python
def extract_verification_context(executor_result: dict) -> dict:
    """
    Extract verifiable claims from executor tool results.
    """
    context = {
        "known_ps_numbers": set(),
        "known_prices": {},
        "known_compatibility": {},
        "known_stock": {},
    }

    for tool_result in executor_result.get("tool_results", []):
        tool_name = tool_result.get("tool")
        data = tool_result.get("data", {})

        if tool_name == "get_part":
            ps = data.get("ps_number")
            context["known_ps_numbers"].add(ps)
            context["known_prices"][ps] = data.get("part_price")
            context["known_stock"][ps] = data.get("availability")

        if tool_name == "check_compatibility":
            key = (data.get("ps_number"), data.get("model_number"))
            context["known_compatibility"][key] = data.get("is_compatible")

    return context

def extract_claims_from_draft_response(response_text: str, context: dict) -> dict:
    """
    Parse a draft response to identify claims being made.
    """
    claims = {}

    # Find PS numbers mentioned
    claims["mentioned_ps_numbers"] = re.findall(r"PS\d+", response_text)

    # Find price claims
    price_matches = re.findall(r"\$(\d+\.?\d*)", response_text)
    claims["stated_prices"] = [float(p) for p in price_matches]

    # Detect compatibility claims
    compat_phrases = [
        r"(will|does|should) fit",
        r"is compatible",
        r"works with",
        r"fits your model",
    ]
    claims["claims_compatibility"] = any(
        re.search(phrase, response_text, re.I) for phrase in compat_phrases
    )

    # Detect stock claims
    stock_phrases = [r"in stock", r"available", r"ships today"]
    claims["claims_in_stock"] = any(
        re.search(phrase, response_text, re.I) for phrase in stock_phrases
    )

    return claims
```

### Integration with Synthesizer

The Synthesizer receives verification context and must respect it:

```python
SYNTHESIZER_PROMPT_WITH_VERIFICATION = """
{base_synthesizer_prompt}

## Verification Requirements

The Verifier has analyzed the tool results. You MUST respect these constraints:

{%- if verification_warnings %}
### Warnings (add caveats)
{% for warning in verification_warnings %}
- {{ warning.rule }}: {{ warning.caveat }}
{% endfor %}
{% endif %}

{%- if blocked_claims %}
### Blocked Claims (DO NOT make these claims)
{% for claim in blocked_claims %}
- {{ claim }}
{% endfor %}
{% endif %}

{%- if required_caveats %}
### Required Caveats (MUST include)
{% for caveat in required_caveats %}
- {{ caveat }}
{% endfor %}
{% endif %}
"""
```

---

## Sequential vs Parallel Tool Execution: The Dependency Problem

When tools depend on each other's outputs, parallel execution becomes impossible. Understanding these dependencies is crucial for optimization.

### Dependency Graph of Common Query Patterns

**Pattern 1: Part Lookup (No Dependencies)**
```
resolve_part("WPW10321304")  ──→  get_part("PS11752778")
         ↓                              ↓
    [PS number]                   [Part details]

These COULD run in parallel if we knew the PS number upfront.
But resolve_part must complete first if user gives manufacturer number.
```

**Pattern 2: Compatibility Check (Sequential)**
```
resolve_part("this part")  ──→  resolve_model("WDT780SAEM1")  ──→  check_compatibility(ps, model)
         ↓                              ↓                                ↓
    [PS number]                  [Model number]                   [Yes/No]

All sequential. Each step needs previous result.
```

**Pattern 3: Multi-Part Comparison (Parallel Opportunity)**
```
                    ┌── get_part("PS11752778") ──┐
                    │                            │
compare 3 parts ────┼── get_part("PS11752779") ──┼──→ synthesize comparison
                    │                            │
                    └── get_part("PS11752780") ──┘

These CAN run in parallel - no dependencies between them.
```

**Pattern 4: Symptom + Part Details (Mixed)**
```
get_symptoms("ice maker not working")
         ↓
    [parts: "Water Valve, Ice Maker"]
         ↓
    ┌────┴────┐
    ↓         ↓
search_parts("water valve")   search_parts("ice maker")  ← Parallel opportunity
    ↓         ↓
    └────┬────┘
         ↓
    [Multiple parts to present]
```

### Implementing Parallel Execution for Independent Calls

```python
from typing import Callable
import asyncio

class ParallelToolExecutor:
    """
    Execute independent tool calls in parallel.
    """

    def analyze_dependencies(self, tool_calls: list[dict]) -> list[list[dict]]:
        """
        Group tool calls into batches that can run in parallel.
        Returns list of batches, where each batch can run in parallel.
        """
        batches = []
        remaining = tool_calls.copy()
        resolved_outputs = set()

        while remaining:
            # Find calls that have all dependencies satisfied
            ready = [
                call for call in remaining
                if all(dep in resolved_outputs for dep in call.get("depends_on", []))
            ]

            if not ready:
                # Circular dependency or missing dependency
                raise ValueError("Cannot resolve tool dependencies")

            batches.append(ready)

            # Mark outputs as resolved
            for call in ready:
                resolved_outputs.add(call["output_name"])
                remaining.remove(call)

        return batches

    async def execute_batch(self, batch: list[dict]) -> list[dict]:
        """Execute a batch of independent calls in parallel."""
        tasks = [
            self.execute_single(call) for call in batch
        ]
        return await asyncio.gather(*tasks)

    async def execute_all(self, tool_calls: list[dict]) -> dict:
        """Execute all tool calls respecting dependencies."""
        batches = self.analyze_dependencies(tool_calls)
        all_results = {}

        for batch in batches:
            # Substitute resolved values into batch calls
            substituted = self.substitute_dependencies(batch, all_results)

            # Execute batch in parallel
            results = await self.execute_batch(substituted)

            # Store results for next batch
            for call, result in zip(batch, results):
                all_results[call["output_name"]] = result

        return all_results

# Example usage for "compare these 3 parts"
tool_calls = [
    {"tool": "get_part", "args": {"ps_number": "PS11752778"}, "output_name": "part1", "depends_on": []},
    {"tool": "get_part", "args": {"ps_number": "PS11752779"}, "output_name": "part2", "depends_on": []},
    {"tool": "get_part", "args": {"ps_number": "PS11752780"}, "output_name": "part3", "depends_on": []},
    {"tool": "search_reviews", "args": {"ps_number": "$part1.ps_number"}, "output_name": "reviews1", "depends_on": ["part1"]},
    {"tool": "search_reviews", "args": {"ps_number": "$part2.ps_number"}, "output_name": "reviews2", "depends_on": ["part2"]},
    {"tool": "search_reviews", "args": {"ps_number": "$part3.ps_number"}, "output_name": "reviews3", "depends_on": ["part3"]},
]

# Batch 1 (parallel): get_part x3
# Batch 2 (parallel): search_reviews x3
# Total: 2 sequential batches instead of 6 sequential calls
```

### When to Use Parallel Execution

| Query Pattern | Parallel Opportunity | Estimated Speedup |
|--------------|---------------------|-------------------|
| Single part lookup | None | 0% |
| Part + reviews + Q&A | Reviews & Q&A parallel | 30% |
| Compare 2 parts | Both get_part parallel | 40% |
| Compare 3+ parts | All get_part parallel | 50-60% |
| Symptom + multiple parts | Part searches parallel | 40% |
| Compatibility + part details | None (sequential deps) | 0% |

**Recommendation:** Implement parallel execution only for comparison queries and symptom flows initially. These are the highest-value cases.

---

## Time-Constrained Development: Fallback Strategies

The reality of building this system was time-constrained. When you can't scrape everything, you need intelligent fallbacks.

### The 80/20 of Data Coverage

Not all data is equally important:

```
Data Coverage Priority:
┌────────────────────────────────────────────────────────────┐
│  CRITICAL: Parts table (core product info)                 │ ← Must have
│  - PS number, name, price, availability                    │
│  - ~2,000 parts scraped                                    │
├────────────────────────────────────────────────────────────┤
│  HIGH: Model compatibility                                 │ ← Must have
│  - Part-to-model relationships                             │
│  - ~500,000 records scraped                                │
├────────────────────────────────────────────────────────────┤
│  MEDIUM: Repair symptoms & instructions                    │ ← Should have
│  - Troubleshooting content                                 │
│  - ~130 records scraped                                    │
├────────────────────────────────────────────────────────────┤
│  MEDIUM: Q&A, Reviews, Repair Stories                      │ ← Nice to have
│  - Customer content for semantic search                    │
│  - ~30,000 vector entries (first page only)                │
├────────────────────────────────────────────────────────────┤
│  LOW: Blog posts                                           │ ← Future work
│  - Educational content                                     │
│  - Not scraped yet                                         │
└────────────────────────────────────────────────────────────┘
```

### The Fallback Hierarchy

When data is missing, fallbacks activate in order:

```python
async def get_part_with_fallbacks(ps_number: str) -> dict:
    """
    Tiered fallback strategy for missing parts.
    """

    # Level 1: Database lookup (fastest, cheapest)
    result = db.get_part(ps_number)
    if result:
        return {"source": "database", "data": result}

    # Level 2: Check if we've scraped recently but part doesn't exist
    # (Prevents scraping for genuinely non-existent parts)
    if db.was_recently_scraped(ps_number):
        return {"source": "not_found", "error": "Part does not exist"}

    # Level 3: Live scrape (slow, but comprehensive)
    try:
        scraped = await scrape_part_live(ps_number)
        if scraped and "error" not in scraped:
            # Persist for future queries
            await db.upsert_scraped_part(scraped)
            return {"source": "live_scrape", "data": scraped}
    except ScraperError as e:
        logger.warning(f"Live scrape failed: {e}")

    # Level 4: Partial info from manufacturer number mapping
    manufacturer_match = db.search_by_manufacturer_pattern(ps_number)
    if manufacturer_match:
        return {
            "source": "partial_match",
            "data": manufacturer_match,
            "caveat": "Partial information only. Visit PartSelect.com for complete details."
        }

    # Level 5: Honest "I don't know"
    return {
        "source": "unknown",
        "error": f"I couldn't find information about {ps_number}.",
        "suggestion": f"Please search for {ps_number} directly on PartSelect.com"
    }
```

### What We Intentionally Didn't Scrape (And Why)

| Data Type | Why Not Scraped | Fallback Strategy |
|-----------|-----------------|-------------------|
| All review pages | Diminishing returns, first page is "most helpful" | Live scrape if user asks for more |
| All Q&A pages | Same as above | Live scrape if specific question not found |
| Blog posts | Lower priority, time constraint | Link to PartSelect blog |
| Model metadata | Only compatibility needed, not full model info | Fuzzy match on model number |
| Order/cart data | Would require auth, different domain | Inform user to visit site |
| Appliance-level symptoms | Part-level symptoms more actionable | Guide to general troubleshooting |

### The "Honest About Limitations" Prompt Pattern

When data is incomplete, the agent should be honest:

```python
SYNTHESIZER_PROMPT_ADDITIONS = """
## Handling Missing Information

When tool results indicate missing data:

1. **Acknowledge honestly**: "I don't have complete information about..."
2. **Provide what you have**: If partial data exists, share it with caveats
3. **Suggest alternatives**: "You can find more details at PartSelect.com/PS12345"
4. **Don't make up data**: NEVER invent prices, ratings, or compatibility

Examples:
- "I found basic info for this part, but I don't have customer reviews in my database.
   You can check reviews at [link]."
- "I couldn't verify compatibility with your specific model. To be safe,
   please verify on the product page before ordering."
"""
```

---

## Data Loading Improvements

The data loading pipeline has several opportunities for improvement.

### Current Pain Points

1. **All-or-nothing loading**: Can't easily load just one table
2. **No progress feedback**: Long-running load with no visibility
3. **Embedding regeneration**: Must re-embed everything if model changes
4. **Foreign key failures silent**: Orphaned records skipped without clear reporting

### Proposed Improvements

**1. Modular Loading with CLI**

```python
# Current: python -m database.load_data (loads everything)

# Proposed: Granular control
python -m database.load_data --table parts
python -m database.load_data --table compatibility --skip-if-exists
python -m database.load_data --table qna --regenerate-embeddings
python -m database.load_data --validate-only  # Dry run
```

**2. Progress Tracking**

```python
from tqdm import tqdm

def load_parts_with_progress(csv_path: str, batch_size: int = 50):
    rows = list(csv.DictReader(open(csv_path)))

    with tqdm(total=len(rows), desc="Loading parts") as pbar:
        for batch in chunks(rows, batch_size):
            try:
                db.upsert_batch("parts", batch)
                pbar.update(len(batch))
            except Exception as e:
                pbar.write(f"Error in batch: {e}")
                # Continue with next batch
```

**3. Embedding Cache**

```python
import hashlib
import pickle

class EmbeddingCache:
    """
    Cache embeddings to avoid regenerating for unchanged text.
    """
    def __init__(self, cache_dir: str = ".embedding_cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)

    def _cache_key(self, text: str, model_name: str) -> str:
        content = f"{model_name}:{text}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def get(self, text: str, model_name: str) -> list[float] | None:
        key = self._cache_key(text, model_name)
        cache_file = self.cache_dir / f"{key}.pkl"
        if cache_file.exists():
            return pickle.load(open(cache_file, "rb"))
        return None

    def set(self, text: str, model_name: str, embedding: list[float]):
        key = self._cache_key(text, model_name)
        cache_file = self.cache_dir / f"{key}.pkl"
        pickle.dump(embedding, open(cache_file, "wb"))

# Usage
cache = EmbeddingCache()

def generate_embedding_cached(text: str) -> list[float]:
    cached = cache.get(text, EMBEDDING_MODEL)
    if cached:
        return cached

    embedding = model.encode(text).tolist()
    cache.set(text, EMBEDDING_MODEL, embedding)
    return embedding
```

**4. Foreign Key Validation Report**

```python
def validate_and_report_foreign_keys(table: str, fk_column: str, parent_table: str):
    """
    Check foreign key validity and report issues.
    """
    # Get all FK values
    fk_values = set(row[fk_column] for row in pending_rows)

    # Get valid parent keys
    valid_parents = set(db.get_all_keys(parent_table))

    # Find orphans
    orphans = fk_values - valid_parents

    if orphans:
        report = {
            "table": table,
            "orphan_count": len(orphans),
            "sample_orphans": list(orphans)[:10],
            "action": "skipped"
        }
        logger.warning(f"FK validation: {report}")

        # Write to validation report file
        with open("load_validation_report.json", "a") as f:
            json.dump(report, f)
            f.write("\n")

    # Return only valid rows
    return [row for row in pending_rows if row[fk_column] in valid_parents]
```

**5. Idempotent Incremental Loading**

```python
def load_incrementally(csv_path: str, table: str, key_column: str):
    """
    Only load rows that don't exist or have changed.
    """
    # Get existing keys and their hashes
    existing = {
        row[key_column]: row.get("content_hash")
        for row in db.get_all_with_hash(table)
    }

    to_insert = []
    to_update = []
    skipped = 0

    for row in csv.DictReader(open(csv_path)):
        key = row[key_column]
        content_hash = hash_row_content(row)

        if key not in existing:
            row["content_hash"] = content_hash
            to_insert.append(row)
        elif existing[key] != content_hash:
            row["content_hash"] = content_hash
            to_update.append(row)
        else:
            skipped += 1

    logger.info(f"Insert: {len(to_insert)}, Update: {len(to_update)}, Skip: {skipped}")

    if to_insert:
        db.insert_batch(table, to_insert)
    if to_update:
        db.upsert_batch(table, to_update)
```

---

## Technical Debt and Cleanup

### Date Handling

**Current:** Dates stored as strings ("December 25, 2024")

**Fix:** Parse to proper DATE/TIMESTAMP columns:

```sql
ALTER TABLE reviews_embeddings ADD COLUMN review_date DATE;
UPDATE reviews_embeddings SET review_date = TO_DATE(date, 'Month DD, YYYY');
ALTER TABLE reviews_embeddings DROP COLUMN date;
ALTER TABLE reviews_embeddings RENAME COLUMN review_date TO date;
```

Enables time-based queries: "What are the most recent reviews?"

### Review ID Generation

**Current:** Hash of author + date + title (collision risk)

**Fix:** Use UUIDs or sequence IDs:

```python
review_id = str(uuid.uuid4())
# or
review_id = f"{ps_number}_{review_index}"
```

### Normalize Parts in Symptoms

**Current:** `repair_symptoms.parts` is comma-separated text

**Fix:** Create junction table:

```sql
CREATE TABLE symptom_parts (
    symptom_id INTEGER REFERENCES repair_symptoms(id),
    part_type TEXT,
    PRIMARY KEY (symptom_id, part_type)
);
```

Enables proper joins and part-type filtering.

### Error Categorization in Scrapers

**Current:** All exceptions treated equally

**Fix:** Distinguish error types:

```python
class ScraperError(Exception):
    pass

class PageNotFoundError(ScraperError):
    """404 - page doesn't exist, don't retry"""
    pass

class RateLimitError(ScraperError):
    """429 - back off and retry later"""
    pass

class ParseError(ScraperError):
    """HTML structure changed - alert for selector update"""
    pass
```

Different handling per error type.

---

## Monitoring and Observability

### Current Gaps

The system has limited observability:
- No structured logging
- No metrics collection
- No alerting
- No query analytics

### Recommended Stack

```
┌─────────────────────────────────────────────────────────────┐
│                      Observability                           │
├───────────────┬─────────────────┬─────────────────────────────┤
│    Logging    │     Metrics     │         Tracing            │
│               │                 │                             │
│  Structured   │  Prometheus /   │    OpenTelemetry /          │
│  JSON logs    │  Grafana        │    Jaeger                   │
│  → ELK Stack  │                 │                             │
└───────────────┴─────────────────┴─────────────────────────────┘
```

### Key Metrics to Track

**Agent Performance:**
- Query latency (p50, p95, p99)
- Tool call frequency by tool
- Scope rejection rate
- Live scrape trigger rate
- Token usage per query

**User Behavior:**
- Queries per session
- Part card click-through rate
- Session duration
- Common query patterns

**System Health:**
- API error rate
- Database query latency
- Embedding generation time
- Scraper success rate

### Alerting Rules

```yaml
alerts:
  - name: high_error_rate
    condition: error_rate > 5%
    window: 5m
    action: page_oncall

  - name: slow_responses
    condition: p95_latency > 30s
    window: 15m
    action: slack_alert

  - name: scraper_failures
    condition: scrape_success_rate < 90%
    window: 1h
    action: email_team
```

---

## Security Considerations

### Current State

- API keys in environment variables (good)
- No user authentication (intentional for demo)
- No rate limiting (vulnerability)
- Scraper user agents could be flagged

### Hardening for Production

**Rate Limiting:**
```python
from slowapi import Limiter
limiter = Limiter(key_func=get_remote_address)

@app.post("/chat")
@limiter.limit("20/minute")
async def chat(request: ChatRequest):
    # ...
```

**Input Sanitization:**
```python
def sanitize_user_input(message: str) -> str:
    # Remove potential prompt injection
    message = message.replace("IGNORE PREVIOUS INSTRUCTIONS", "")
    # Limit length
    message = message[:2000]
    return message
```

**Secrets Management:**
- Move from env vars to secrets manager (AWS Secrets Manager, HashiCorp Vault)
- Rotate API keys regularly
- Audit key usage

**Scraping Ethics:**
- Respect robots.txt (already doing)
- Consider reaching out to PartSelect for data partnership
- Terms of service review

---

## Testing Strategy

### Current Coverage

The project has development testing tools (`scrapers/dev/`) but limited automated tests.

### Recommended Test Suite

**Unit Tests:**
```python
# test_tools.py
def test_resolve_part_ps_number():
    result = resolve_part("PS11752778")
    assert result["ps_number"] == "PS11752778"

def test_resolve_part_url():
    result = resolve_part("https://partselect.com/PS11752778")
    assert result["ps_number"] == "PS11752778"

def test_scope_check_in_scope():
    assert is_in_scope("Tell me about refrigerator ice makers") == True

def test_scope_check_out_of_scope():
    assert is_in_scope("What's the weather like?") == False
```

**Integration Tests:**
```python
# test_agent.py
async def test_part_lookup_flow():
    response = await agent.invoke({"query": "Tell me about PS11752778"})
    assert "PS11752778" in response["message"]
    assert len(response["parts"]) > 0

async def test_compatibility_check():
    response = await agent.invoke({
        "query": "Does PS11752778 fit WDT780SAEM1?"
    })
    assert "compatible" in response["message"].lower() or "fit" in response["message"].lower()
```

**End-to-End Tests:**
```python
# test_e2e.py
def test_full_conversation():
    session = new_session()

    # First message
    r1 = chat(session, "I need an ice maker for my Whirlpool refrigerator")
    assert "ice maker" in r1.lower()

    # Follow-up with "this part"
    r2 = chat(session, "Does this part come with installation instructions?")
    assert "install" in r2.lower()
```

### Test Data

Create fixtures for:
- Sample parts with known attributes
- Compatibility relationships
- Q&A/review content
- Edge cases (long descriptions, special characters, missing fields)

---

## Conclusion

This roadmap balances quick wins with longer-term architectural improvements:

**Immediate (1-2 weeks):**
- Persist live-scraped data
- Enable streaming responses
- Add manufacturer number search
- Move sessions to Redis

**Medium-term (1-2 months):**
- Implement Verifier node
- Add differential scraping
- Scrape blog content
- Add model-centric queries

**Long-term (3-6 months):**
- Order support integration
- Multi-appliance expansion
- Dedicated vector database
- Distributed scraping
- Comprehensive monitoring

The current architecture is sound and extensible. These improvements build on that foundation rather than requiring rewrites. Each enhancement makes the system more robust, scalable, and useful for customers trying to fix their broken appliances.
