# PartSelect Chat Agent — Project Overview

A conversational AI that helps customers find appliance parts, check compatibility, and troubleshoot problems. Built as a case study for Instalily.

---

## The Problem

Customers arrive at PartSelect with a broken refrigerator or dishwasher. They need to figure out which part is broken, find it in a catalog of thousands, verify it fits their specific model, and feel confident enough to purchase. The traditional experience requires knowing part numbers or navigating complex category pages.

This agent flips that experience. Customers describe their problem in natural language and get guided to the right solution.

---

## What I Built

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              FRONTEND                                        │
│                         React Chat Interface                                 │
│                                                                              │
│   • Chat messages with markdown rendering                                    │
│   • Part cards (price, rating, availability, buy link)                       │
│   • Thinking indicator for longer responses                                  │
└─────────────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              BACKEND                                         │
│                        FastAPI + LangGraph                                   │
│                                                                              │
│   ┌──────────────────────────────────────────────────────────────────────┐  │
│   │                        Agent Pipeline                                 │  │
│   │                                                                       │  │
│   │    Scope Check ───► Executor (ReAct) ───► Secondary Check ───► Synth │  │
│   │    (is this about      (calls tools       (validate the      (format │  │
│   │     appliances?)        as needed)         fetched data)     output) │  │
│   └──────────────────────────────────────────────────────────────────────┘  │
│                                     │                                        │
│                                     ▼                                        │
│   ┌────────────────────────────────────────────────────────────────────────┐│
│   │                           Tool Layer                                   ││
│   │  SQL Tools          Vector Tools          Fallback                     ││
│   │  • get_part         • search_qna          • scrape_part_live           ││
│   │  • check_compat     • search_reviews      (real-time if not in DB)     ││
│   │  • get_symptoms     • search_stories                                   ││
│   └────────────────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                             DATABASE                                         │
│                    Supabase (PostgreSQL + pgvector)                         │
│                                                                              │
│    SQL Tables                              Vector Tables                     │
│    • parts (~6K products)                  • qna_embeddings (~10K)          │
│    • model_compatibility (~1.8M rows)      • reviews_embeddings (~11K)      │
│    • repair_symptoms, instructions         • repair_stories (~8K)           │
└─────────────────────────────────────────────────────────────────────────────┘
```

### The Data Pipeline

I built two Selenium scrapers that collect data from PartSelect:

```
PartSelect Website
       │
       ├── Part Scraper ──► parts.csv, model_compatibility.csv,
       │                    qna.csv, reviews.csv, repair_stories.csv
       │
       └── Repair Scraper ► repair_symptoms.csv, repair_instructions.csv
                │
                ▼
       CSV files (incremental writes, crash-safe)
                │
                ▼
       Loader + Embedding Generation
                │
                ▼
       Supabase (idempotent upserts)
```

The scrapers handle the messy parts: infinite-scroll compatibility tables (some parts fit 6,000+ models), anti-bot countermeasures, and inconsistent page layouts. Data writes incrementally so progress survives crashes.

---

## Key Design Decisions

### ReAct Over Pre-Planning

I started with a classic planner-executor architecture where the LLM decides upfront which tools to call. This failed because:
- Real queries blend intents ("Is this compatible AND how hard to install?")
- Pre-planning can't adapt when a tool returns unexpected results
- Adding tools required updating multiple places

The final design uses ReAct: the LLM observes the query, reasons about what it needs, calls a tool, observes the result, and repeats until done. No rigid routing. The LLM adapts naturally.

### Two-Stage Scope Checking

The agent only handles refrigerators and dishwashers. Scope validation happens twice:

1. **Text-based check** (fast) — Regex patterns catch obvious off-topic queries ("What's the weather?")

2. **Data-based check** (after tool calls) — Validates the actual fetched data. A PS number might look valid but return a chainsaw part.

Neither alone is sufficient. Text analysis can't know what a PS number refers to without fetching data, but fetching data for every query (including spam) wastes resources.

### Hybrid SQL + Vector Storage

I use Supabase (PostgreSQL + pgvector) for everything:

| Query Type | Storage | Example |
|------------|---------|---------|
| Exact lookups | SQL tables | "What's the price of PS11752778?" |
| Semantic search | Vector embeddings | "Is this part easy to install?" |

The parts table has both structured columns and an embedding column. Compatibility checks are SQL lookups. Finding customer experiences ("what do people say about installation?") uses vector similarity.

I went with 384-dimensional local embeddings (all-MiniLM-L6-v2) over OpenAI because:
- No API costs when embedding thousands of records
- No rate limits during data loading
- Good enough for finding similar customer experiences

### Model Selection by Role

```
Scope Check (fallback): Claude Haiku — simple yes/no
Executor (tool calling): Claude Haiku — fast, cheap, reasoning isn't complex
Synthesizer (response):  Claude Sonnet — quality matters for customer-facing text
```

This optimizes cost without sacrificing output quality.

### Live Scraping Fallback

When a user asks about a part not in the database, the system automatically scrapes PartSelect in real-time (5-30 seconds). I grab everything upfront—compatibility, Q&A, reviews, repair stories—so follow-up questions don't trigger additional scrapes.

---

## How It Handles the Example Queries

**"How can I install part number PS11752778?"**
```
→ Scope Check: PASS (PS number = appliance part)
→ Executor calls get_part() → install_difficulty, install_time, video_url
→ Executor calls search_repair_stories("installation", ps_number)
→ Synthesizer: difficulty rating, time estimate, video link, customer tips
```

**"Is this part compatible with my WDT780SAEM1?"**
```
→ Scope Check: PASS
→ Executor checks session for "this part" → finds PS11752778
→ Executor calls check_compatibility(PS11752778, WDT780SAEM1)
→ Synthesizer: clear yes/no with context
```

**"My ice maker isn't working. How can I fix it?"**
```
→ Scope Check: PASS (refrigerator symptom)
→ Executor calls get_symptoms("refrigerator", "ice maker not making ice")
→ Returns: parts to check, frequency (29% of cases), video URL
→ Synthesizer: lists all parts to check, troubleshooting video link
  (Doesn't give detailed instructions unless user asks about a specific part)
```

---

## Project Structure

```
├── backend/
│   ├── agent_v2/               # Agent implementation
│   │   ├── graph.py            # LangGraph workflow
│   │   ├── prompts.py          # System prompts
│   │   ├── nodes/              # Scope check, executor, synthesizer
│   │   └── tools/              # SQL, vector, and scrape tools
│   └── main.py                 # FastAPI endpoints
│
├── src/                        # React frontend
│   ├── components/
│   │   ├── ChatWindow.js       # Main chat interface
│   │   └── PartCard.js         # Product card display
│   └── api/api.js              # Backend communication
│
├── scrapers/
│   ├── part_scraper.py         # Product catalog scraper
│   ├── repair_scraper.py       # Symptom/instruction scraper
│   └── config.py               # Scraper settings
│
├── database/
│   ├── schema.sql              # Full database schema
│   └── load_data.py            # CSV → Supabase loader
│
├── data/                       # Scraped CSV files
│
└── final_docs/                 # Detailed design documentation
```

---

## Data Model

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           SQL-PRIMARY TABLES                                 │
│                                                                              │
│   ┌──────────────────────┐        ┌──────────────────────────────┐          │
│   │        parts         │        │     model_compatibility      │          │
│   ├──────────────────────┤        ├──────────────────────────────┤          │
│   │ ps_number (PK)       │◄───────│ part_id (FK)                 │          │
│   │ part_name            │        │ model_number                 │          │
│   │ part_price           │        │ brand                        │          │
│   │ appliance_type       │        ├──────────────────────────────┤          │
│   │ average_rating       │        │ ~1.8M rows                   │          │
│   │ embedding (384)      │        │ (300x larger than parts)     │          │
│   └──────────────────────┘        └──────────────────────────────┘          │
│                                                                              │
│   ┌──────────────────────┐        ┌──────────────────────────────┐          │
│   │   repair_symptoms    │        │   repair_instructions        │          │
│   ├──────────────────────┤        ├──────────────────────────────┤          │
│   │ appliance_type       │◄───────│ symptom                      │          │
│   │ symptom              │        │ part_type                    │          │
│   │ percentage           │        │ instructions (step-by-step)  │          │
│   │ parts (to check)     │        └──────────────────────────────┘          │
│   └──────────────────────┘                                                   │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                          VECTOR-PRIMARY TABLES                               │
│                        (Semantic search via pgvector)                        │
│                                                                              │
│   ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐          │
│   │  qna_embeddings  │  │reviews_embeddings│  │repair_stories_...│          │
│   ├──────────────────┤  ├──────────────────┤  ├──────────────────┤          │
│   │ ps_number (FK)   │  │ ps_number (FK)   │  │ ps_number (FK)   │          │
│   │ question         │  │ rating           │  │ difficulty       │          │
│   │ answer           │  │ content          │  │ instruction      │          │
│   │ embedding (384)  │  │ embedding (384)  │  │ embedding (384)  │          │
│   └──────────────────┘  └──────────────────┘  └──────────────────┘          │
│                                                                              │
│   All linked to parts table via ps_number foreign key                        │
└─────────────────────────────────────────────────────────────────────────────┘
```

The compatibility table is 300x larger than the parts table—a single ice maker can fit 6,000+ models. I stored this as a flat `(part_id, model_number)` table because:
- Correctness over cleverness (explicit relationships, no inference)
- Single indexed lookup for "does this fit my model?"
- At current scale, PostgreSQL handles 1.8M rows fine

---

## What I Learned

### Session Context Matters

Users say "this part", "compare them", "which is easiest". Without session state tracking discussed parts and established context, every query needs explicit identifiers. The session tracks:
- Recently discussed parts (for pronoun resolution)
- Conversation history (for scope check context)
- Established symptom context (for troubleshooting follow-ups)

### Pattern 2a vs 2b Was Critical

Users ask about symptoms in two distinct ways:

- **2a (overview):** "My ice maker isn't working" → List parts to check, don't overwhelm with instructions
- **2b (specific):** "How do I check the water inlet valve?" → Step-by-step diagnostic

Early versions confused these. Getting this distinction into the prompts was important.

### PS Numbers in Response = Part Cards

The frontend extracts PS numbers from response text to show part cards. If the LLM describes a part without the PS number, no card appears. Required explicit prompt instruction: "ALWAYS include PS numbers for EVERY part you mention."

### Comprehensive Scraping Pays Off

When live-scraping, I grab everything: compatibility, Q&A, reviews, repair stories. This means:
- First query: 15s (scraping)
- "What models does it fit?": 2s (already have it)
- "What do customers say?": 2s (already have it)

Minimal scraping would mean 15s for each follow-up.

---

## Trade-offs I Made

| Decision | Trade-off |
|----------|-----------|
| Single Supabase for SQL + vectors | Simpler sync, but dedicated vector DB would be faster at scale |
| 384-dim local embeddings | No API costs, but less semantic precision than 1536-dim |
| Flat compatibility table | Simple and correct, but 1.8M rows (could compress with model families) |
| Haiku for tool calling | Cheaper and faster, but occasionally less accurate reasoning |
| First-page only for reviews/Q&A | Faster scraping, but popular parts have 100s of entries we miss |
| In-memory sessions | Simple, but server restart loses all sessions |

---

## Scalability & Future Work

### What Would Break First

| At Scale | Issue |
|----------|-------|
| 50K+ parts | model_compatibility grows to ~15M rows |
| 500K+ vectors | pgvector needs tuning or migration to Pinecone |
| 100+ concurrent users | Need session persistence (Redis), rate limiting |
| Real-time prices | Scraping can't keep up; need API partnership |

### Short-Term Improvements

**Persist live-scraped parts** — Currently scraped data is returned but not saved. Adding database persistence creates a self-expanding catalog. The upsert infrastructure already exists.

**Add `last_scraped_at` timestamps** — Currently no way to know how stale data is. Enables differential updates.

**Sitemap-based change detection** — PartSelect publishes sitemaps with `lastmod`. Compare against our timestamps, only re-scrape changed pages.

**Enable streaming display** — Backend supports SSE streaming; frontend waits for complete responses. Wiring this up improves perceived performance.

### Medium-Term

**Verifier node** — Add validation between Executor and Synthesizer to cross-check factual claims against tool results. The two dangerous failure modes in e-commerce are false compatibility claims and made-up prices.

**Richer session state** — Track user's model, established symptom context, comparison sets. Enables more natural conversations.

**Model-centric queries** — Users often start with "I have a WDT780SAEM1, what parts are available?" Add fuzzy model resolution and reverse lookup.

**Parallel tool execution** — For "compare these 5 parts", run tool calls in parallel. Estimated 30-50% speedup for comparison queries.

### Long-Term

**Order support** — Integrate with PartSelect's order system for status checks, returns, shipping estimates. Architecture anticipates this with the tool registry pattern.

**More appliance types** — Microwaves, washers, dryers. Requires config updates, scraping runs, and scope keyword additions. Schema already supports `appliance_type`.

**Dedicated vector database** — At 500K+ vectors, migrate Q&A/reviews/stories to Pinecone or Weaviate. Keep SQL in Supabase. Vector search is abstracted behind RPC functions so migration requires no agent code changes.

**Data partnership** — Scraping has inherent limitations (staleness, rate limits, fragility). A direct data feed from PartSelect would be faster, more reliable, and officially supported.

---

## Running the Project

```bash
# Set up environment
cp .env.example .env

# Install dependencies
pip install -r requirements.txt
npm install

# Terminal 1: Backend
python3 -m backend.main

# Terminal 2: Frontend
npm start

# Open http://localhost:3000
```

---

## Detailed Documentation

For deeper dives, see [final_docs/](final_docs/):
- **AGENT_DESIGN.md** — Agent architecture, node details, tool system, edge cases
- **DATABASE_DESIGN.md** — Schema design, SQL vs vector trade-offs, the compatibility scale problem
- **FUTURE_WORK.md** — Comprehensive improvement roadmap with code examples
