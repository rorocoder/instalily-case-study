# PartSelect Chat Agent - Presentation Script (Condensed)

**Duration:** ~8 minutes

---

## 1. Data Collection: The Scraping Strategy (~1.5 min)

### What We Scraped

Two Selenium-based scrapers collect data from PartSelect:

**Parts Scraper** — Navigates category pages, visits each part page, extracts:
- Core info (PS number, name, price, availability, ratings)
- Compatibility table (infinite-scroll, up to 6,000+ models per part)
- Q&A, reviews, repair stories

**Repair Scraper** — Scrapes the repair help section:
- Symptoms by appliance type ("ice maker not working")
- Part types to check for each symptom
- Step-by-step diagnostic instructions
- Video URLs

### Scale & Challenges

| Data Type | Volume |
|-----------|--------|
| Parts | ~6,000 |
| Model compatibility | ~1.8M rows |
| Q&A entries | ~10,000 |
| Reviews | ~11,500 |
| Repair stories | ~8,500 |

**Biggest challenge:** Compatibility tables. Some parts fit 6,000+ models. Each requires scrolling through infinite-scroll containers — a single part can take 2-3 minutes just for compatibility data.

**Live scraping fallback:** When a user asks about a part not in our database, we scrape PartSelect in real-time (5-30 seconds). We grab everything upfront so follow-up questions don't need additional scrapes.

---

## 2. Database Schema (~1.5 min)

### Tech Choice: Supabase (PostgreSQL + pgvector)

**Why one platform?** Keeps SQL and vector data together. No sync complexity between separate databases. At our scale (~36K vectors), pgvector performs well.

### Schema Overview

**SQL-Primary Tables** (exact lookups):

```sql
-- Core product catalog
parts (
    ps_number TEXT PRIMARY KEY,    -- "PS11752778"
    part_name, part_type, part_price,
    appliance_type,                -- "refrigerator" | "dishwasher"
    average_rating, review_count,
    embedding vector(384)          -- Also has vector for semantic search
)

-- 1.8M rows - the big one
model_compatibility (
    part_id TEXT,                  -- FK to parts
    model_number TEXT,             -- "WDT780SAEM1"
    brand, description,
    PRIMARY KEY (part_id, model_number)
)

-- Troubleshooting content
repair_symptoms (appliance_type, symptom, percentage, parts, video_url)
repair_instructions (appliance_type, symptom, part_type, instructions)
```

**Vector-Primary Tables** (semantic search):

```sql
qna_embeddings (ps_number, question, answer, embedding vector(384))
reviews_embeddings (ps_number, title, content, rating, embedding)
repair_stories_embeddings (ps_number, title, content, difficulty, embedding)
```

### Embedding Choice

**all-MiniLM-L6-v2** (384 dimensions) — Local inference, no API costs, no rate limits. Good enough for finding similar customer experiences.

---

## 3. Agent Architecture (~1.5 min)

### The Flow

```
User Query → Scope Check → Executor (ReAct) → Secondary Scope Check → Synthesizer
```

**Scope Check** — Is this about refrigerators or dishwashers?
- Rule-based patterns first (fast, ~0ms)
- LLM fallback for ambiguous cases (~300ms)

**Executor** — ReAct pattern: observe query + tools → reason → call tool → observe result → repeat until done. No pre-planning — the LLM figures out which tools to call dynamically.

**Secondary Scope Check** — Validates fetched data. Catches edge cases like "PS16688554" which looks valid but returns a chainsaw part.

**Synthesizer** — Formats the final response. Uses Sonnet for quality where customers see it.

### Model Selection

| Node | Model | Why |
|------|-------|-----|
| Scope Check (fallback) | Haiku | Simple yes/no |
| Executor | Haiku | Tool selection doesn't need Sonnet |
| Synthesizer | Sonnet | Customer-facing quality matters |

---

## 4. Executor Query Patterns (~1 min)

The executor prompt defines workflow patterns:

**Pattern 1: Part Lookup**
"Tell me about PS11752778" → `get_part()`

**Pattern 2a: Symptom Overview**
"My ice maker isn't working" → `get_symptoms()` → List parts to check, video link

**Pattern 2b: Specific Troubleshooting**
"How do I check the water inlet valve?" → `get_repair_instructions()` → Step-by-step diagnostic

**Pattern 3: Compatibility**
"Does this fit my WDT780SAEM1?" → `check_compatibility()`

**Pattern 4: Follow-ups**
"Compare them" / "Which is easiest?" → Use session context to identify parts → Call tools for each

**Pattern 5: Part Not in Database**
`get_part()` returns not found → Auto-triggers `scrape_part_live()` → Use scraped data directly

---

## 5. Tools Overview (~1 min)

### Core Tools

| Tool | Purpose | Key Inputs |
|------|---------|------------|
| `get_part(ps_number)` | Fetch part details | PS number |
| `resolve_part(identifier)` | Convert manufacturer # or URL to PS number | Any identifier |
| `check_compatibility(ps_number, model_number)` | Yes/no compatibility | Part + model |
| `get_compatible_models(ps_number)` | All models a part fits | PS number |
| `search_parts(query, filters)` | Find parts by search | Query, part_type, brand, etc. |

### Symptom Tools

| Tool | Purpose | Key Inputs |
|------|---------|------------|
| `get_symptoms(appliance_type, symptom)` | Get symptom info + parts to check | Appliance, symptom text |
| `get_repair_instructions(appliance, symptom, part_type)` | Diagnostic steps for specific part | Appliance, symptom, part type |

### Semantic Search Tools

| Tool | Purpose | Key Inputs |
|------|---------|------------|
| `search_qna(query, ps_number)` | Find relevant Q&A | Search query, optional PS filter |
| `search_reviews(query, ps_number)` | Find relevant reviews | Search query, optional PS filter |
| `search_repair_stories(query, ps_number)` | Find DIY experiences | Search query, optional PS filter |

### Fallback Tool

| Tool | Purpose | Key Inputs |
|------|---------|------------|
| `scrape_part_live(ps_number)` | Real-time scrape when not in DB | PS number |

---

## 6. Project Assessment (~1.5 min)

### Tech Stack Summary

- **Frontend:** React (simple chat UI with part cards)
- **Backend:** FastAPI + Uvicorn (async, SSE streaming)
- **AI:** Claude (Haiku + Sonnet) via LangGraph
- **Database:** Supabase (PostgreSQL + pgvector)
- **Scraping:** Selenium (headless Chrome)
- **Embeddings:** sentence-transformers (local)

### What Works Well

1. **ReAct over rigid routing** — LLM adapts to query complexity naturally
2. **Two-stage scope checking** — Text-based for speed, data-based for accuracy
3. **Hybrid SQL + vector** — Exact answers when needed, semantic search when helpful
4. **Comprehensive live scraping** — One slow scrape beats three slow scrapes
5. **Session context** — Natural conversation with "this part", "compare them"

### Limitations & Trade-offs

1. **Scraping is slow** — Live scrapes take 10-30 seconds. Anti-bot measures limit speed.
2. **Data staleness** — No incremental updates. Prices could be outdated.
3. **No persistence of scraped parts** — Same part scraped again tomorrow.
4. **Session in memory** — Server restart loses all sessions.
5. **First-page only** — Reviews/Q&A don't paginate past first page.

### What I'd Improve

**Short-term:**
- Persist live-scraped parts to database
- Add `last_scraped_at` timestamps
- Sitemap-based differential scraping

**Medium-term:**
- Verifier node to fact-check claims before synthesis
- Richer session state (user model, symptom context)
- Redis for session persistence

**Long-term:**
- Order support integration
- More appliance types
- Dedicated vector database if scale demands it

---

## Closing

The system works because it keeps things simple where possible:
- Let the LLM figure out tool selection (ReAct)
- Validate at the right layer (text vs data)
- Be honest about limitations
- Scrape comprehensively when you have to scrape

Happy to dive deeper into any area.
