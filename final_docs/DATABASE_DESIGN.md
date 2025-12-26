# Database Design Document

## Overview

We chose a hybrid approach: structured SQL tables for exact lookups combined with vector embeddings for semantic search. This lets us answer questions like "Does PS11752778 fit my WDT780SAEM1?" with a precise yes/no, while also handling fuzzier queries like "my ice maker makes clicking sounds" by finding semantically similar repair experiences.

---

## Why Supabase?

We went with **Supabase** (PostgreSQL with pgvector) as our unified data platform. Here's why:

**The Pros:**
- **Single platform for everything** - Both relational data and vector embeddings live in the same database. No need to sync between a SQL database and a separate vector store like Pinecone.
- **PostgreSQL foundations** - database with decades of battle-testing. ACID compliance, proper foreign keys, complex queries
- **pgvector is good enough** - For our scale (thousands of parts, tens of thousands of Q&A entries), pgvector performs well. We don't need a dedicated vector database yet.
- **Generous free tier** - Good for development and prototyping without cost concerns.
- **Easy to migrate later** - If we outgrow pgvector, our agent calls a single retrieval interface (currently implemented as a Supabase RPC function). We can later re-implement the same interface as a service/Edge Function that queries Pinecone, so the agent code stays unchanged (but we will need a new sync pipeline and may adjust filtering/scoring semantics)

**The Cons:**
- **Not the fastest for pure vector search** - Dedicated vector databases like Pinecone or Weaviate would be faster for large-scale similarity search.
- **Supabase has quirks** - The 1000-row limit per query caught us off guard (more on this later).
- **Less control over infrastructure** - Being a managed service means we can't tune PostgreSQL settings directly.

**Why not a separate vector database?**

We considered running PostgreSQL for structured data and Pinecone for vectors. The main argument against: synchronization complexity. When a part gets updated, we'd need to update both stores and keep them consistent. With everything in Supabase, a single upsert handles both the row data and its embedding.

For a production system at massive scale, separating concerns might make sense. But for this case study scope, simplicity wins.

### Is Supabase Actually Scalable?

Short answer: yes, Supabase is scalable — but in a very specific, opinionated way. It scales exceptionally well up to a point, and then the tradeoffs become explicit rather than hidden.

**Supabase's scaling philosophy:**

Supabase scales by leaning into what PostgreSQL is already extremely good at, instead of reinventing distributed storage. That means:
- Vertical scaling first
- Selective horizontal scaling
- Clear escape hatches when you outgrow it

This is very different from "auto-shard everything from day one."

#### Where Supabase Scales Very Well

**1. Core database (Postgres)**

PostgreSQL can comfortably handle:
- Millions to tens of millions of rows
- Thousands of queries per second
- Large joins + transactional workloads
- Strong consistency

Supabase adds: managed upgrades, read replicas, connection pooling, and observability. For most startups and many mid-sized companies, this is more than enough.

**2. Read scaling**

Supabase supports read replicas for heavy read traffic, separation of reads vs writes, and cached query paths via PostgREST. This is ideal for dashboards, feeds, RAG-style retrieval, and analytics-heavy products.

**3. Vector search (pgvector)**

With pgvector:
- Scales well up to millions of embeddings
- Supports ANN indexes (IVFFlat / HNSW)
- Can be filtered with SQL (huge advantage over standalone vector DBs)

The typical sweet spot: RAG systems, semantic search, internal tooling, AI features tied closely to relational data. This is exactly our use case.

**4. Security & correctness at scale**

This is underrated but crucial:
- Row Level Security scales logically, not just physically
- Auth + data access rules don't get duplicated across systems
- Fewer edge cases as product complexity grows

This matters once you have many user types, complex permissions, or AI querying user-owned data.

#### Where Supabase Does Not Scale Infinitely

**1. Write-heavy horizontal scaling**

Supabase does not auto-shard writes. If you need massive write throughput (metrics ingestion, logs), globally distributed writes, or append-only firehoses — you will hit PostgreSQL limits. At that point you typically move hot write paths to Kafka / ClickHouse / Bigtable and keep Supabase as the source of truth.

**2. Extremely large vector workloads**

pgvector is great, but it's not Pinecone-at-100B-vectors scale. Index rebuilds and memory pressure become real. Latency can spike if not tuned carefully. Past a certain point, you split vectors into a dedicated system or shard by tenant/domain manually. Supabase doesn't hide this — it makes it obvious.

**3. Global low-latency everywhere**

Supabase is region-based. If you need sub-50ms latency worldwide or active-active multi-region writes, you'll need replication strategies or hybrid architectures.

#### The Design Philosophy

Supabase scales by **preserving simplicity as long as possible**. Instead of "we scale infinitely but everything is complex from day one," it chooses "you get a very long runway with a simple mental model, and clear exits when you need more."

This is why we chose it. For our use case:
- Relational + AI-enhanced data model ✓
- Correctness over premature distribution ✓
- Debuggability and safety ✓
- Building a real product, not infra for infra's sake ✓

**Practical rule of thumb:**

| Supabase is great for | Consider alternatives for |
|----------------------|---------------------------|
| Correctness over premature distribution | Global write-heavy data planes |
| Relational + AI-enhanced data | Hyperscale vector-only search |
| Debuggability and safety | Multi-region active-active writes |
| Product teams shipping fast | Infra-as-the-product systems |

**One-line verdict:** Supabase scales extremely well for product teams — and intentionally stops before becoming an unmaintainable distributed system.

For our PartSelect agent, we're approaching Supabase's comfortable operating range. With 1.8M compatibility rows and ~30K vector embeddings, we're still well within Supabase's sweet spot, though the compatibility table's size warrants attention. The clear migration paths (vector tables to Pinecone, writes to Kafka) give us confidence that we won't paint ourselves into a corner.

---

## The Data Model

Our schema reflects a key insight about appliance parts e-commerce: there's a  split between **structured facts** (prices, compatibility, specs) and **unstructured knowledge** (customer experiences, troubleshooting wisdom). We store the former in regular SQL tables and the latter in vector-enabled tables for semantic search.

### A Note on "SQL vs Vector"

**We're using PostgreSQL + pgvector for everything**. There's no separate vector database. The distinction between "SQL tables" and "Vector tables" is about **primary purpose**, not technology:

- **SQL-primary tables**: Queried mainly by exact key lookups (`WHERE ps_number = 'X'`)
- **Vector-primary tables**: Queried mainly by semantic similarity (`ORDER BY embedding <=> query_vector`)

The `parts` table is interesting because it's both - it has SQL columns for exact lookups AND an embedding column for semantic search. This allows us to be more flexible in finding parts from a semantic description rather than needing exact text matches (if our user doesnt provide PS numbers or wants to search through multiple parts). So for example, When a user searches for "refrigerator bins", we can find parts with `part_type = "Drawer"` through vector similarity even though "bins" never appears in our data. 

### Schema Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         SQL-PRIMARY TABLES                                   │
│                    (Exact Lookups, Some Have Embeddings)                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─────────────────────┐           ┌─────────────────────────────┐          │
│  │       parts         │           │    model_compatibility      │          │
│  │─────────────────────│           │─────────────────────────────│          │
│  │ ps_number (PK)      │◄──────────│ part_id (FK)                │          │
│  │ part_name           │           │ model_number                │          │
│  │ part_type           │           │ brand                       │          │
│  │ part_price          │           │ description                 │          │
│  │ appliance_type      │           │─────────────────────────────│          │
│  │ brand               │           │ PK: (part_id, model_number) │          │
│  │ embedding (384) ◄───┼── Also has vector for semantic search   │          │
│  │ ...                 │           └─────────────────────────────┘          │
│  └─────────────────────┘                                                     │
│                                                                              │
│  ┌─────────────────────┐           ┌─────────────────────────────┐          │
│  │   repair_symptoms   │           │    repair_instructions      │          │
│  │─────────────────────│           │─────────────────────────────│          │
│  │ id (PK)             │           │ id (PK)                     │          │
│  │ appliance_type      │           │ appliance_type              │          │
│  │ symptom             │◄──────────│ symptom                     │          │
│  │ percentage          │           │ part_type                   │          │
│  │ parts               │           │ instructions                │          │
│  │ video_url           │           │ part_category_url           │          │
│  └─────────────────────┘           └─────────────────────────────┘          │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                       VECTOR-PRIMARY TABLES                                  │
│                 (Semantic Search is Main Query Pattern)                      │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─────────────────────┐  ┌─────────────────────┐  ┌─────────────────────┐  │
│  │   qna_embeddings    │  │repair_stories_embed.│  │  reviews_embeddings │  │
│  │─────────────────────│  │─────────────────────│  │─────────────────────│  │
│  │ id (PK)             │  │ id (PK)             │  │ id (PK)             │  │
│  │ ps_number (FK)──────┼──│ ps_number (FK)──────┼──│ ps_number (FK)      │  │
│  │ question_id         │  │ story_id            │  │ review_id           │  │
│  │ question            │  │ title               │  │ rating              │  │
│  │ answer              │  │ instruction         │  │ title               │  │
│  │ helpful_count       │  │ difficulty          │  │ content             │  │
│  │ embedding (384)     │  │ embedding (384)     │  │ embedding (384)     │  │
│  └─────────────────────┘  └─────────────────────┘  └─────────────────────┘  │
│                                                                              │
│  All linked to parts table via ps_number foreign key                         │
│  All use 384-dim embeddings from all-MiniLM-L6-v2                            │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Table Deep Dives

### 1. `parts` - The Product Catalog

```sql
CREATE TABLE parts (
    ps_number TEXT PRIMARY KEY,              -- "PS11752778"
    part_name TEXT,                          -- "Whirlpool Ice Maker Assembly"
    part_type TEXT,                          -- "Ice Maker Assembly"
    manufacturer_part_number TEXT,           -- "WPW10321304"
    part_manufacturer TEXT,                  -- "Whirlpool"
    part_price DECIMAL(10, 2),               -- 89.99
    part_description TEXT,                   -- Long description
    install_difficulty TEXT,                 -- "Easy"
    install_time TEXT,                       -- "15-30 minutes"
    install_video_url TEXT,                  -- YouTube link
    part_url TEXT,                           -- PartSelect product page
    average_rating DECIMAL(3, 2),            -- 4.85
    num_reviews INTEGER,                     -- 127
    appliance_type TEXT NOT NULL,            -- "refrigerator" or "dishwasher"
    brand TEXT,                              -- "Whirlpool"
    manufactured_for TEXT,                   -- "Whirlpool, KitchenAid, Maytag"
    availability TEXT,                       -- "In Stock"
    replaces_parts TEXT,                     -- "AP6022403, W10331789"
    embedding vector(384),                   -- Semantic search embedding
    created_at TIMESTAMP WITH TIME ZONE
);
```

**Why `ps_number` as the Primary Key?**

PartSelect assigns every part a unique PS number (like PS11752778). This is stable, unique, and what customers often reference. 

**The Embedding Column**

The `embedding` column stores a 384-dimensional vector generated from `part_name + part_type + part_description`. This enables semantic search - when a user asks for "refrigerator bins", we can find parts with `part_type` "Drawer or Glides" even though the word "bins" never appears in our data.

We chose 384 dimensions (using the all-MiniLM-L6-v2 model) as a pragmatic trade-off between search quality and storage/compute cost. It's a small, fast model that runs locally without any API calls.

**Trade-off: Denormalization**

You’ll notice fields like 'brand' appear both in 'parts' and 'model_compatibility'. This is intentional denormalization. We could normalize into a separate 'brands' table (or rely on joining back to 'parts'), but it adds join/lookup complexity for limited benefit.

This matters especially because compatibility checks can return very large result sets (sometimes thousands of parts). If 'model_compatibility' only stored part_id, we would need to hydrate each result (e.g., via a 'get_part' call per part) to display basic info—creating an N+1 pattern and significantly increasing latency and cost. Storing a small set of commonly-needed fields directly in the compatibility rows lets us answer “is this compatible?” + “what is it?” in one pass, and only fetch full part details when necessary.

---

### 2. `model_compatibility` - The Many-to-Many Relationship

This table answers the critical question: "Does this part fit my model?"

```sql
CREATE TABLE model_compatibility (
    part_id TEXT NOT NULL REFERENCES parts(ps_number),
    model_number TEXT NOT NULL,              -- "WDT780SAEM1"
    brand TEXT,                              -- "Whirlpool"
    description TEXT,                        -- "24 Inch Wide Built-In Dishwasher"
    PRIMARY KEY (part_id, model_number)
);
```

**Why a Composite Primary Key?**

Compatibility is a relationship, not an entity. A part can fit thousands of models (we discovered PS12728638 fits over 6,000 models!). Using `(part_id, model_number)` as the composite key:

1. Prevents duplicate entries by construction
2. Makes "is this compatible?" a single indexed lookup
3. Semantically represents what the table actually is (a relationship)

---

### 3. `repair_symptoms` and `repair_instructions` - Troubleshooting Knowledge

These tables power the diagnostic flow when customers describe problems.

```sql
CREATE TABLE repair_symptoms (
    id SERIAL PRIMARY KEY,
    appliance_type TEXT NOT NULL,            -- "refrigerator"
    symptom TEXT NOT NULL,                   -- "Ice maker not making ice"
    symptom_description TEXT,                -- Detailed explanation
    percentage DECIMAL(5, 2),                -- 29.0 (% of customers with this issue)
    video_url TEXT,                          -- YouTube troubleshooting video
    parts TEXT,                              -- "Water Fill Tubes, Water Inlet Valve, Ice Maker"
    symptom_url TEXT,                        -- PartSelect repair page
    difficulty TEXT,                         -- "EASY", "MODERATE", "DIFFICULT"
    UNIQUE (appliance_type, symptom)
);

CREATE TABLE repair_instructions (
    id SERIAL PRIMARY KEY,
    appliance_type TEXT NOT NULL,
    symptom TEXT NOT NULL,                   -- Links to repair_symptoms
    part_type TEXT NOT NULL,                 -- "Water Inlet Valve"
    instructions TEXT,                       -- Step-by-step diagnostic guide
    part_category_url TEXT,                  -- URL with anchor to section
    UNIQUE (appliance_type, symptom, part_type)
);
```

**The Workflow These Enable**

When a user says "my ice maker isn't working", here's the query flow:

1. Match symptom: `SELECT * FROM repair_symptoms WHERE symptom ILIKE '%ice%maker%'`
2. Get the parts to check: `Water Fill Tubes, Water Inlet Valve, Ice Maker Assembly`
3. For each part type, get instructions: `SELECT * FROM repair_instructions WHERE symptom = 'Ice maker not making ice' AND part_type = 'Water Inlet Valve'`

**Why Separate Tables?**

We could embed instructions directly in `repair_symptoms` as a JSON array. We didn't because:

1. Instructions can be long (multiple paragraphs per part type)
2. Separate tables allow independent updates
3. SQL querying is cleaner for filtering by part_type

---

### 4. Vector Tables: `qna_embeddings`, `repair_stories_embeddings`, `reviews_embeddings`

These three tables share a similar structure and purpose: enabling semantic search over unstructured customer content.

```sql
CREATE TABLE qna_embeddings (
    id SERIAL PRIMARY KEY,
    ps_number TEXT REFERENCES parts(ps_number),
    question_id TEXT NOT NULL,
    question TEXT,
    answer TEXT,
    asker TEXT,
    date TEXT,
    model_number TEXT,                       -- If customer specified their model
    helpful_count INTEGER DEFAULT 0,
    embedding_text TEXT,                     -- What was embedded
    embedding vector(384),
    UNIQUE (ps_number, question_id)
);

CREATE TABLE repair_stories_embeddings (
    id SERIAL PRIMARY KEY,
    ps_number TEXT REFERENCES parts(ps_number),
    story_id TEXT NOT NULL,
    title TEXT,                              -- "Fixed my ice maker clicking"
    instruction TEXT,                        -- How they fixed it
    author TEXT,
    difficulty TEXT,                         -- "Really Easy"
    repair_time TEXT,                        -- "Less than 15 mins"
    helpful_count INTEGER DEFAULT 0,
    embedding vector(384),
    UNIQUE (ps_number, story_id)
);

CREATE TABLE reviews_embeddings (
    id SERIAL PRIMARY KEY,
    ps_number TEXT REFERENCES parts(ps_number),
    review_id TEXT NOT NULL,
    rating INTEGER,                          -- 1-5 stars
    title TEXT,
    content TEXT,
    author TEXT,
    date TEXT,
    verified_purchase BOOLEAN,
    embedding vector(384),
    UNIQUE (ps_number, review_id)
);
```

**Why Vector Search for These?**

Traditional SQL search wouldn't work for this type of natural language query. If a user asks "is this part easy to install?", we need to find Q&A entries, repair stories, and reviews that discuss installation difficulty - even if they don't use the exact word "install". Another good semantic search is askign for installation tips or instructions from this content. They might say:

- "Popped right in, took 5 minutes"
- "Even my wife could replace this"
- "No tools needed, just pushed it into place"


**The `embedding_text` Column**

We store what text was embedded (typically `title + content` or `question + answer`). This enables:

1. Debugging - we can see exactly what was embedded
2. Re-embedding - if we switch embedding models, we can regenerate without re-scraping

---

## Embedding Model Choice

We use **all-MiniLM-L6-v2**, a 384-dimensional sentence-transformer.

### Why not OpenAI embeddings?
- **Cost at scale** – We have thousands of Q&A entries, reviews, and stories. Embedding and re-embedding them via an API adds up.
- **No external dependency** – Local embeddings work offline and avoid network latency.
- **Good-enough quality** – For our use case (finding similar customer experiences), MiniLM performs well in practice.

### Trade-off: lower dimensionality
384 dimensions capture less semantic nuance than 1536+ dimensional models, which can reduce recall in edge cases. In practice, our queries are specific enough that this hasn’t been too much of an issue.

### Why this is acceptable
This is a **case study / MVP**, so we optimized for speed, cost, and simplicity over absolute embedding quality.

If higher quality became necessary, we could:
1. Switch to OpenAI embeddings
2. Update `vector(384)` → `vector(1536)`
3. Re-generate embeddings from stored `embedding_text`


---

## How Data Gets Into the Database

### The Scraping Layer

We built two Selenium-based scrapers that collect data from PartSelect:

1. **Part Scraper** (`scrapers/part_scraper.py`)
   - Navigates PartSelect's category pages
   - Extracts part details, pricing, specs
   - Expands infinite-scroll compatibility tables
   - Collects Q&A, repair stories, and reviews from each part page

2. **Repair Scraper** (`scrapers/repair_scraper.py`)
   - Crawls PartSelect's repair help section
   - Extracts symptoms, percentages, video links
   - Gets step-by-step instructions for each part type

Both scrapers write incrementally to CSV files in the `data/` directory. This means:
- Progress is saved continuously (no losing hours of work to a crash)
- We can resume scraping if interrupted
- Data can be inspected before loading to the database

### The Loading Pipeline

`database/load_data.py` handles loading CSVs into Supabase:

```python
def load_parts(supabase, embedding_model, batch_size=50):
    rows = read_csv("parts.csv")

    for row in rows:
        # Build embedding text
        embedding_text = f"{row['part_name']} {row['part_type']} {row['part_description']}"

        data = {
            "ps_number": row["ps_number"],
            "part_name": row["part_name"],
            # ... other fields ...
            "embedding": embedding_model.encode(embedding_text).tolist()
        }

        # Upsert with retry logic
        upsert_with_retry(supabase, "parts", data, on_conflict="ps_number")
```

**Key Design Decision: Idempotent Upserts**

Every table has a natural key for upserts:

| Table | Upsert Key |
|-------|------------|
| parts | `ps_number` |
| model_compatibility | `(part_id, model_number)` |
| repair_symptoms | `(appliance_type, symptom)` |
| repair_instructions | `(appliance_type, symptom, part_type)` |
| qna_embeddings | `(ps_number, question_id)` |
| repair_stories_embeddings | `(ps_number, story_id)` |
| reviews_embeddings | `(ps_number, review_id)` |

This means we can run the loader multiple times without creating duplicates. Changed data gets updated, new data gets inserted, existing data stays put. This is critical for incremental updates - we don't need to wipe the database every time we scrape.

---

## Query Patterns and Indexes

### Index Strategy

We created indexes for our most common query patterns:

```sql
-- Part lookups by common filters
CREATE INDEX idx_parts_appliance_type ON parts(appliance_type);
CREATE INDEX idx_parts_part_type ON parts(part_type);
CREATE INDEX idx_parts_brand ON parts(brand);

-- Model lookups (very frequently queried)
CREATE INDEX idx_model_compat_model ON model_compatibility(model_number);

-- Symptom lookups
CREATE INDEX idx_symptoms_appliance ON repair_symptoms(appliance_type);
CREATE INDEX idx_instructions_symptom ON repair_instructions(appliance_type, symptom);

-- Vector similarity search (IVFFlat)
CREATE INDEX idx_parts_embedding ON parts
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX idx_qna_embedding ON qna_embeddings
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX idx_stories_embedding ON repair_stories_embeddings
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX idx_reviews_embedding ON reviews_embeddings
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
```

**Why IVFFlat for Vectors?**

PostgreSQL's pgvector offers two index types:
- **IVFFlat**: Faster but approximate (may miss some matches)
- **HNSW**: More accurate but slower build time and more memory

We chose IVFFlat with 100 lists as a balanced choice. For our data size, it provides sub-millisecond query times with acceptable recall. If accuracy becomes an issue, switching to HNSW is a one-line change.

### Common Query Patterns

**Pattern 1: Direct Part Lookup**
```sql
SELECT * FROM parts WHERE ps_number = 'PS11752778'
```

**Pattern 2: Compatibility Check**
```sql
SELECT * FROM model_compatibility
WHERE part_id = 'PS11752778' AND model_number = 'WDT780SAEM1'
```

**Pattern 3: Semantic Search (via RPC functions)**
```sql
SELECT * FROM search_qna(
    query_embedding := $1,        -- User query embedding
    match_threshold := 0.5,
    match_count := 5,
    filter_ps_number := 'PS11752778'
)
```

We use PostgreSQL functions for vector search to encapsulate the similarity calculation:

```sql
CREATE FUNCTION search_qna(
    query_embedding vector(384),
    match_threshold FLOAT DEFAULT 0.7,
    match_count INT DEFAULT 5,
    filter_ps_number TEXT DEFAULT NULL
) RETURNS TABLE (...) AS $$
BEGIN
    RETURN QUERY
    SELECT
        qna_embeddings.id,
        qna_embeddings.question,
        qna_embeddings.answer,
        1 - (qna_embeddings.embedding <=> query_embedding) AS similarity
    FROM qna_embeddings
    WHERE 1 - (qna_embeddings.embedding <=> query_embedding) > match_threshold
      AND (filter_ps_number IS NULL OR qna_embeddings.ps_number = filter_ps_number)
    ORDER BY qna_embeddings.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;
```

---

## Data Integrity and Foreign Keys

### Referential Integrity

All vector tables have foreign keys to `parts`:

```sql
ps_number TEXT REFERENCES parts(ps_number) ON DELETE CASCADE
```

The `ON DELETE CASCADE` means if a part is removed, all its associated Q&A, stories, and reviews are automatically deleted. This prevents orphaned records.

### Validation During Load

Before loading vector table data, we validate foreign keys exist:

```python
def validate_foreign_keys(supabase, rows, foreign_key_field):
    # Fetch all existing parts
    existing_parts = set()
    response = supabase.table("parts").select("ps_number").execute()
    for row in response.data:
        existing_parts.add(row["ps_number"])

    # Filter rows to only those with valid FK references
    valid_rows = [r for r in rows if r[foreign_key_field] in existing_parts]

    if len(valid_rows) < len(rows):
        print(f"Skipping {len(rows) - len(valid_rows)} records with missing parts")

    return valid_rows
```

This prevents foreign key violations when reviews or Q&A reference parts that weren't scraped.

---

## Scalability Considerations

### Current Scale

| Table | Row Count |
|-------|-----------|
| parts | 6,000 |
| model_compatibility | 1,800,000 |
| repair_symptoms | 21 |
| repair_instructions | 95 |
| qna_embeddings | 10,000 |
| repair_stories_embeddings | 8,500 |
| reviews_embeddings | 11,500 |

**Total vector embeddings:** ~36,000 (parts + qna + stories + reviews)

The compatibility table is **300x larger** than the parts table - a ratio that becomes more extreme as we add appliance types. This is the dominant factor in our storage and query planning.

### Bottlenecks We'd Hit First

1. **model_compatibility** - Already the largest table by far. At 1.8M rows, queries are still fast with proper indexing. But adding more appliance types could push this to 5-10M rows, where we'd need partitioning or more sophisticated strategies.

2. **Vector search** - IVFFlat with 100 lists works well for ~36K vectors. At 100K+ vectors, we'd want to tune the `lists` parameter or consider HNSW indexes.

3. **Supabase limits** - Free tier has storage limits. At 1.8M compatibility rows (~180 MB) plus vectors, we're consuming meaningful storage. Paid tiers have rate limits to consider at production scale.

### Scaling Strategies

**Short-term (fits in Supabase):**
- Increase IVFFlat lists parameter as vectors grow
- Add composite indexes for common filter combinations
- Consider partitioning model_compatibility by brand

**Medium-term (hitting limits):**
- Migrate vector tables to Pinecone or Weaviate
- Keep SQL tables in Supabase
- Update tool functions to query both stores

**Long-term (production scale):**
- Self-hosted PostgreSQL for full control
- Dedicated vector database cluster
- Read replicas for query scaling
- Caching layer for common queries

---

## The Compatibility Scale Problem

This deserves its own section because it's the elephant in the room. The `model_compatibility` table is **300x larger** than the parts table, and this ratio will only grow as we add appliance types. Let's dig into why this happens, what problems it causes, and what we'd do differently at scale.

### The Numbers

Here's the actual data distribution:

| Table | Row Count | Avg Rows per Part | Storage |
|-------|-----------|-------------------|---------|
| parts | 6,000 | 1 | ~15 MB |
| model_compatibility | 1,800,000 | ~300 | ~180 MB |
| qna_embeddings | 10,000 | ~1.7 | ~15 MB |
| repair_stories_embeddings | 8,500 | ~1.4 | ~13 MB |
| reviews_embeddings | 11,500 | ~1.9 | ~17 MB |

The compatibility table dominates everything - it's roughly **7x the storage of all other tables combined**. And it gets worse:

**Extreme cases we discovered:**
- PS12728638: **6,120 compatible models**
- PS11752778: **2,220 compatible models**
- Average part: ~300 compatible models
- Some parts: Only 5-10 models

This is a classic **data explosion problem**. One part → thousands of model relationships.

### Why This Happens

Appliance parts are designed to work across product lines. A water filter doesn't just fit one refrigerator - it fits every refrigerator in that product family, across multiple brands (Whirlpool, KitchenAid, Maytag, Jenn-Air are all owned by the same parent company and share parts).

A single ice maker assembly might fit:
- 500 Kenmore models (Sears' house brand, actually manufactured by others)
- 400 Whirlpool models
- 300 KitchenAid models
- 200 Maytag models
- Various other brands

That's 1,400+ compatibility entries for one part.

### Problems This Causes

**1. Scraping Time**

Each part page has an infinite-scroll compatibility table. To get all 6,000 models for one part:
- Scroll, wait for load
- Scroll again, wait
- Repeat 100+ times
- Parse each row

A single part can take 2-3 minutes just for compatibility data. At 6,000 parts, that's **200+ hours of scraping** just for compatibility.

**2. Storage Growth**

If we add a new appliance type with 5,000 parts averaging 300 models each, that's 1.5 million new compatibility rows. The table grows linearly with parts × average_models.

Projection at scale (based on our observed ~300 models/part average):
| Parts | Avg Models | Compatibility Rows | Estimated Storage |
|-------|------------|-------------------|-------------------|
| 6,000 | 300 | 1,800,000 | 180 MB |
| 15,000 | 300 | 4,500,000 | 450 MB |
| 30,000 | 300 | 9,000,000 | 900 MB |
| 100,000 | 300 | 30,000,000 | 3 GB |

At 30M rows, we're still within PostgreSQL's comfort zone, but indexes become critical and queries need optimization.

**3. Query Performance**

Two common query patterns:

```sql
-- "What models does this part fit?" (expensive for popular parts)
SELECT * FROM model_compatibility WHERE part_id = 'PS12728638'
-- Returns 6,120 rows

-- "What parts fit my model?" (cheap, well-indexed)
SELECT * FROM model_compatibility WHERE model_number = 'WDT780SAEM1'
-- Returns 10-50 rows
```

The first query is problematic. Returning 6,000 rows to the agent is wasteful - no user wants to see all of them. We have to summarize, which means we fetch data we throw away.

**5. Agent Response Quality**

When a user asks "what models does PS11752778 fit?", we can't dump 2,220 model numbers into the response. We had to add summarization logic:

```python
# Guideline in synthesizer prompt
"If 50+ models, group by brand and show count"
# Result: "Fits 2,220 models including Kenmore (2,004), Whirlpool (215), Maytag (1)"
```

But this means we fetch all 2,220 rows just to count them by brand which is wasteful.

---

### Why We Chose the Simple Flat Table

Given these problems, why did we go with a simple `(part_id, model_number)` table?

**1. Correctness over cleverness**

The flat table is guaranteed correct. Each row is an explicit compatibility relationship. There's no inference, no indirection, no possibility of false positives.

**2. Query simplicity**

```sql
-- Is this part compatible with my model?
SELECT 1 FROM model_compatibility
WHERE part_id = 'PS11752778' AND model_number = 'WDT780SAEM1'
```

One query, indexed on the primary key, microsecond response. This is our most important query.

**3. Time constraints**

Building a sophisticated model hierarchy would require:
- Understanding appliance manufacturer product lines
- Grouping models into families
- Handling edge cases where family membership is imperfect
- Testing extensively

For a case study with limited time, the flat table was the pragmatic choice.

**4. It works at current MVP scale**

Currently PostgreSQL handles this scale fine. Queries are fast, storage is trivial, and the scraping time is acceptable for an initial data load. 

---

### What Would Work Better at Scale

If we were building this for production with millions of parts, here's what we'd consider:

#### Option 1: Model Families / Product Lines

Instead of storing every `(part, model)` pair, identify model families:

```sql
CREATE TABLE model_families (
    family_id TEXT PRIMARY KEY,
    brand TEXT,
    family_name TEXT,  -- e.g., "Whirlpool Side-by-Side 2018-2022"
    pattern TEXT       -- regex or prefix for model matching
);

CREATE TABLE part_family_compatibility (
    part_id TEXT REFERENCES parts(ps_number),
    family_id TEXT REFERENCES model_families(family_id),
    PRIMARY KEY (part_id, family_id)
);

CREATE TABLE models (
    model_number TEXT PRIMARY KEY,
    family_id TEXT REFERENCES model_families(family_id),
    brand TEXT,
    description TEXT
);
```

**How it works:**
- Part PS11752778 is compatible with family "whirlpool-sbs-2018"
- Model WDT780SAEM1 belongs to family "whirlpool-sbs-2018"
- Compatibility is inferred through family membership

**Compression ratio:** If a part fits 2,000 models across 5 families, we store 5 rows instead of 2,000. That's 400x compression.

**Trade-offs:**
- Requires understanding manufacturer product line structure
- Some models might not fit neatly into families
- Need fallback for edge cases
- More complex queries

```sql
-- Check compatibility via family membership
SELECT 1 FROM part_family_compatibility pfc
JOIN models m ON m.family_id = pfc.family_id
WHERE pfc.part_id = 'PS11752778' AND m.model_number = 'WDT780SAEM1'
```

#### Option 2: Materialized Aggregates

Keep the flat table but pre-compute common queries:

```sql
CREATE MATERIALIZED VIEW part_compatibility_summary AS
SELECT
    part_id,
    COUNT(*) as model_count,
    jsonb_object_agg(brand, brand_count) as models_by_brand
FROM (
    SELECT part_id, brand, COUNT(*) as brand_count
    FROM model_compatibility
    GROUP BY part_id, brand
) sub
GROUP BY part_id;

-- Refresh periodically
REFRESH MATERIALIZED VIEW part_compatibility_summary;
```

**How it works:**
- The agent queries the materialized view for summaries
- Only hits the full table when listing specific models
- View is refreshed after data loads

**Trade-offs:**
- Adds storage for the view
- Need to manage refresh
- Still have the large base table
- Good balance of simplicity and performance

#### Option 4: Hybrid with Caching

Keep the flat table but add Redis/Memcached:

```python
def get_compatible_models_summary(ps_number: str):
    # Check cache first
    cached = redis.get(f"compat_summary:{ps_number}")
    if cached:
        return json.loads(cached)

    # Query and summarize
    all_models = db.get_compatible_models(ps_number)
    summary = {
        "total": len(all_models),
        "by_brand": group_by_brand(all_models),
        "sample_models": all_models[:10]
    }

    # Cache for 24 hours
    redis.setex(f"compat_summary:{ps_number}", 86400, json.dumps(summary))
    return summary
```

**Trade-offs:**
- Adds infrastructure (Redis)
- Cache invalidation complexity
- Still slow on cache miss
- Good for read-heavy workloads

---

### What We'd Actually Recommend at Scale

For a production system with 100K+ parts:

**1. Keep the flat table as source of truth**

Don't try to be clever with the primary data model. The flat `(part_id, model_number)` table is simple, correct, and PostgreSQL handles it fine even at 30M rows.

**2. Add materialized views for summaries**

Pre-compute the "2,220 models by brand" summaries. The agent almost never needs the full list.

```sql
CREATE MATERIALIZED VIEW part_model_summary AS
SELECT
    part_id,
    COUNT(*) as total_models,
    array_agg(DISTINCT brand) as brands,
    jsonb_object_agg(brand, brand_count) as count_by_brand
FROM (
    SELECT part_id, brand, COUNT(*) as brand_count
    FROM model_compatibility
    GROUP BY part_id, brand
) sub
GROUP BY part_id;
```

**3. Cache aggressively**

Compatibility data changes rarely. Cache summaries for 24 hours minimum.

**4. Paginate by default**

Never return more than 50 models in one query. If users need to see all 6,000, make them paginate.

**5. Index strategically**

```sql
-- Already have this (from composite PK)
CREATE INDEX ON model_compatibility(part_id);

-- Add for "parts for my model" queries
CREATE INDEX ON model_compatibility(model_number);

-- Add for brand filtering
CREATE INDEX ON model_compatibility(part_id, brand);
```

**6. Consider partitioning at 10M+ rows**

```sql
-- Partition by brand prefix
CREATE TABLE model_compatibility (
    part_id TEXT,
    model_number TEXT,
    brand TEXT,
    description TEXT
) PARTITION BY LIST (brand);

CREATE TABLE model_compatibility_whirlpool PARTITION OF model_compatibility
    FOR VALUES IN ('Whirlpool');
CREATE TABLE model_compatibility_kenmore PARTITION OF model_compatibility
    FOR VALUES IN ('Kenmore');
-- etc.
```

This helps if queries frequently filter by brand.


## Extensibility

### Adding New Appliance Types

The database already supports multiple appliance types. To add microwaves:

1. Update scraper config:
```python
APPLIANCE_CONFIGS = {
    "refrigerator": {...},
    "dishwasher": {...},
    "microwave": {
        "base_url": "https://www.partselect.com/Microwave-Parts.htm",
        "related_section_pattern": "Microwave Parts"
    }
}
```

2. Run scrapers - data goes into the same tables with `appliance_type = 'microwave'`

3. Update scope check in the agent - currently hardcoded to refrigerator/dishwasher

No schema changes needed. The `appliance_type` column already handles this.

### Adding New Data Sources

Say we want to add blog posts for semantic search:

1. Create new table:
```sql
CREATE TABLE blog_embeddings (
    id SERIAL PRIMARY KEY,
    blog_id TEXT UNIQUE,
    title TEXT,
    content TEXT,
    category TEXT,
    embedding vector(384)
);
```

2. Create scraper for blog content

3. Add search function:
```sql
CREATE FUNCTION search_blogs(query_embedding vector(384), ...)
RETURNS TABLE (...) AS $$ ... $$;
```

4. Add tool in the agent:
```python
@tool
def search_blogs(query: str) -> list[dict]:
    """Search blog posts for repair guides and tips."""
    embedding = generate_embedding(query)
    return db.search_blogs(embedding)
```

The planner automatically considers new tools - no workflow changes needed.

---

## Data Freshness: Scraping, Loading, and Updates

This section addresses one of the hardest problems in any data-driven system: keeping the database in sync with the source of truth. PartSelect's website changes constantly - prices fluctuate, new reviews come in, compatibility lists get updated, parts go out of stock. How do we handle this?

### Current Approach: Full Scrape + Upsert

Our current pipeline looks like this:

```
┌─────────────────┐     ┌──────────────┐     ┌─────────────────┐
│  Selenium       │ --> │  CSV Files   │ --> │  Supabase       │
│  Scrapers       │     │  (data/)     │     │  (upsert)       │
└─────────────────┘     └──────────────┘     └─────────────────┘
```

**Step 1: Scraping**
- Navigate PartSelect category pages
- Visit each part page, extract all data
- Write incrementally to CSV files
- Optional `--resume` flag to skip already-scraped PS numbers

**Step 2: Loading**
- Read CSV files
- Generate embeddings for vector tables
- Upsert to Supabase (insert or update based on natural key)

This works, but has significant limitations.

### The `--resume` Flag Problem

The `--resume` flag was designed for crash recovery during long scrape jobs. Here's what it does:

```python
def get_scraped_part_ids(filename):
    """Read existing CSV and return set of already-scraped ps_number values."""
    scraped_ids = set()
    with open(filepath, 'r') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            ps_number = row.get('ps_number', '').strip()
            if ps_number:
                scraped_ids.add(ps_number)
    return scraped_ids
```

And in the scraper:

```python
if ps_from_url and ps_from_url in scraped_ids:
    print(f"  [SKIP] Already scraped: {ps_from_url}")
    continue  # <-- COMPLETELY SKIPS THIS PART
```

**The Problem:** If a PS number exists in the CSV, we skip it entirely. This means these changes wouldnt be captured:

- Price changes
- New reviews
- Compatibility updates
- Stock status changes

The `--resume` flag treats "existence" as "completeness", which is only true for crash recovery, not incremental updates.

### What Happens at Load Time

The data loader uses upserts, which actually do handle updates correctly:

```python
supabase.table("parts").upsert(data, on_conflict="ps_number").execute()
```

If we re-scraped a part and got new data, the upsert would update it. **The problem is we never re-scrape** because the scraper skips existing parts.

The loader itself is idempotent and update-friendly. The scraper is not.

---

### A Better Scraping Strategy

Here's how we'd redesign the scraping pipeline for production:

#### 1. Use the Sitemap

PartSelect publishes a sitemap at `https://www.partselect.com/sitemap.xml`. This is goldmine:

```xml
<urlset>
  <url>
    <loc>https://www.partselect.com/PS11752778-Whirlpool-...</loc>
    <lastmod>2024-12-20</lastmod>
    <changefreq>weekly</changefreq>
  </url>
  ...
</urlset>
```

**Key insight:** The `<lastmod>` tag tells us when a page was last modified. We should:

1. Parse the sitemap
2. Compare `lastmod` against our `last_scraped_at` timestamp
3. Only scrape pages that changed since our last run

```python
# Proposed sitemap-based scraping
def get_parts_to_scrape(sitemap_url, db):
    """Compare sitemap lastmod against our records, return URLs needing refresh."""
    sitemap = parse_sitemap(sitemap_url)

    # Get our last-scraped timestamps
    our_timestamps = db.get_all_part_timestamps()  # {ps_number: last_scraped_at}

    to_scrape = []
    for url_entry in sitemap:
        ps_number = extract_ps_from_url(url_entry.loc)
        site_modified = url_entry.lastmod
        our_timestamp = our_timestamps.get(ps_number)

        if our_timestamp is None:
            to_scrape.append(url_entry.loc)  # New part
        elif site_modified > our_timestamp:
            to_scrape.append(url_entry.loc)  # Updated part

    return to_scrape
```

**Benefits:**
- Only scrape what changed
- Faster runs (minutes instead of hours)
- Respect the site's bandwidth
- Automatic detection of new parts

**What we'd need to add:**
- `last_scraped_at` column in `parts` table
- Sitemap parser
- Changed comparison logic

#### 2. Separate Update Frequencies by Data Type

Not all data changes at the same rate:

| Data Type | Change Frequency | Recommended Refresh |
|-----------|------------------|---------------------|
| Part core info (name, description, type) | Rarely | Monthly |
| Prices | Frequently | Daily |
| Availability/stock status | Very frequently | Every few hours |
| Reviews | Moderately | Weekly |
| Q&A | Moderately | Weekly |
| Compatibility list | Rarely | Monthly |
| Repair symptoms/instructions | Very rarely | Monthly |

**Proposed tiered scraping:**

```
Daily cron job:
  - Scrape price + availability only (fast, targeted)

Weekly cron job:
  - Scrape reviews, Q&A, repair stories
  - Use sitemap lastmod to filter

Monthly cron job:
  - Full re-scrape of part details
  - Re-scrape compatibility tables
  - Re-scrape repair help pages
```

#### 3. Track What We've Scraped

Add metadata to track scraping state:

```sql
ALTER TABLE parts ADD COLUMN last_scraped_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE parts ADD COLUMN scrape_version INTEGER DEFAULT 1;

CREATE TABLE scrape_runs (
    id SERIAL PRIMARY KEY,
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    scrape_type TEXT,  -- 'full', 'prices', 'reviews', etc.
    parts_scraped INTEGER,
    parts_updated INTEGER,
    parts_added INTEGER,
    errors INTEGER
);
```

This lets us:
- Know when each part was last refreshed
- Audit scraping history
- Identify parts that haven't been updated in a while
- Track error rates and data quality

---

### Handling the Compatibility Table

The `model_compatibility` table has unique challenges:

**Problem 1: Detecting Removals**

If a model is removed from a part's compatibility list, we have no way to know. Our upsert only adds/updates rows, never deletes.

**Current situation:**
```
Part PS11752778 compatibility in January: [Model A, Model B, Model C]
Part PS11752778 compatibility in February: [Model A, Model C]  (B removed)

Our database after February scrape: [Model A, Model B, Model C]  (B still there!)
```

**Solution: Atomic replacement**

For compatibility data, don't upsert - replace entirely:

```python
def update_part_compatibility(db, ps_number, new_compatibility_list):
    """Atomically replace all compatibility entries for a part."""
    with db.transaction():
        # Delete existing
        db.table("model_compatibility").delete().eq("part_id", ps_number).execute()

        # Insert new
        if new_compatibility_list:
            db.table("model_compatibility").insert(new_compatibility_list).execute()
```

**Trade-off:** This is slower and uses more write operations, but guarantees correctness.

**Problem 2: Scale**

Some parts have 6,000+ compatible models. Scraping all of them requires scrolling through infinite-scroll containers, which is slow and fragile.

**Potential optimization:** Only re-scrape compatibility when the part's page `lastmod` indicates changes. Most parts don't get new model compatibility very often.

---

### Handling Reviews, Q&A, and Repair Stories

These vector tables have similar update challenges:

**New content:**
- New reviews, Q&A entries, stories get added → Our upsert handles this correctly

**Updated content:**
- `helpful_count` changes → Upsert handles this if we re-scrape

**Deleted content:**
- A Q&A entry gets removed → We'd keep it forever (stale data)

**Practical approach for vector tables:**

Since Q&A, reviews, and stories rarely get deleted (more commonly they just accumulate), the stale data problem is less severe. We could:

1. Add a `last_seen_at` timestamp column
2. Update it every time we scrape that part
3. Periodically purge entries not seen in 90+ days

```sql
-- Soft detection of potentially deleted content
SELECT * FROM qna_embeddings
WHERE last_seen_at < NOW() - INTERVAL '90 days';
```

---

### Adding a New Appliance Type

Let's walk through adding microwaves end-to-end:

**Step 1: Update scraper config**
```python
# scrapers/config.py
APPLIANCE_CONFIGS = {
    "refrigerator": {...},
    "dishwasher": {...},
    "microwave": {
        "base_url": "https://www.partselect.com/Microwave-Parts.htm",
        "related_section_pattern": "Microwave Parts"
    }
}

REPAIR_APPLIANCE_CONFIGS = {
    "refrigerator": {...},
    "dishwasher": {...},
    "microwave": {
        "repair_url": "https://www.partselect.com/Repair/Microwave/",
        "output_prefix": "microwave"
    }
}
```

**Step 2: Run scrapers**
```bash
# Scrape microwave parts
python -m scrapers.run_scraper microwave

# Scrape microwave repair help
python -m scrapers.repair_scraper microwave
```

Data goes into the same CSV files with `appliance_type = 'microwave'`.

**Step 3: Load to database**
```bash
python -m database.load_data
```

No schema changes - the `appliance_type` column handles it.

**Step 4: Update agent scope check**

Currently hardcoded in `backend/agent_v2/nodes/scope_check.py`:

```python
VALID_APPLIANCE_TYPES = {"refrigerator", "dishwasher"}  # Add "microwave"
```

Better approach: Make this configurable or query from database:

```python
def get_supported_appliance_types(db):
    result = db.table("parts").select("appliance_type").execute()
    return set(row["appliance_type"] for row in result.data)
```

**Step 5: Update agent prompts**

The system prompt mentions "refrigerators and dishwashers". Update to include microwaves.

**What doesn't need to change:**
- Database schema
- SQL tools
- Vector search functions
- Data loading scripts
- Most agent logic

This is the payoff of using `appliance_type` as a data attribute rather than hardcoding it into the schema.

---

### Rescraping Frequency Recommendations

Based on e-commerce data patterns:

| Environment | Full Rescrape | Price/Stock Update | Reviews/Q&A |
|-------------|---------------|-------------------|-------------|
| Development | As needed | N/A | N/A |
| Staging | Weekly | Daily | Weekly |
| Production | Monthly | Every 4-6 hours | Daily |

**Why not real-time?**

1. PartSelect's robots.txt and terms of service likely prohibit aggressive scraping
2. Selenium scraping is slow and resource-intensive
3. Most data doesn't change that fast
4. Users can tolerate slightly stale prices (especially with disclaimers)

**Alternative: API integration**

If PartSelect offered an API (they might for partners), that would be vastly better:
- Real-time data
- No scraping infrastructure
- Officially supported
- Probably faster and more reliable

Worth investigating before building elaborate scraping pipelines.

---

### The Ideal Data Pipeline (If We Had More Time)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         PROPOSED ARCHITECTURE                                │
└─────────────────────────────────────────────────────────────────────────────┘

                    ┌──────────────────┐
                    │   Sitemap Feed   │
                    │  (hourly check)  │
                    └────────┬─────────┘
                             │
                             ▼
                    ┌──────────────────┐
                    │  Change Detector │
                    │  (compare lastmod│
                    │   vs our records)│
                    └────────┬─────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
     ┌─────────────┐  ┌───────────┐  ┌─────────────┐
     │Price/Stock  │  │  Reviews  │  │    Full     │
     │  Scraper    │  │  Scraper  │  │   Scraper   │
     │ (frequent)  │  │ (weekly)  │  │ (monthly)   │
     └──────┬──────┘  └─────┬─────┘  └──────┬──────┘
            │               │               │
            ▼               ▼               ▼
     ┌─────────────────────────────────────────────┐
     │           Message Queue (optional)          │
     │  (buffer writes, handle backpressure)       │
     └───────────────────┬─────────────────────────┘
                         │
                         ▼
     ┌─────────────────────────────────────────────┐
     │              Data Loader                    │
     │  - Validate foreign keys                    │
     │  - Generate embeddings                      │
     │  - Upsert to Supabase                       │
     │  - Update last_scraped_at                   │
     └───────────────────┬─────────────────────────┘
                         │
                         ▼
     ┌─────────────────────────────────────────────┐
     │              Supabase                       │
     │  - SQL tables (parts, compatibility, etc.)  │
     │  - Vector tables (qna, reviews, stories)    │
     └─────────────────────────────────────────────┘
```

**Key improvements:**
1. Sitemap-driven change detection
2. Tiered scraping by data volatility
3. Timestamp tracking for all records
4. Optional message queue for scale
5. Atomic compatibility updates

---

### Where Our Current System Excels

**1. Initial data population**

For getting data into the database the first time, our approach works great. The scraper is thorough, the loader handles deduplication, and we end up with clean data.

**2. Development iteration**

Being able to re-run the loader idempotently made development much faster. Schema change? Just reload. Bad data? Fix scraper, reload. No manual cleanup needed.

**3. Query performance**

Once the data is loaded, queries are fast. The hybrid SQL + vector approach handles all our query patterns well.

**4. Handling complex pages**

The scraper correctly handles PartSelect's complex pages - infinite scroll, dynamic pricing, nested sections. This required significant effort but works reliably.

**5. Crash recovery**

The `--resume` flag does work well for its intended purpose: recovering from interrupted scrape jobs.

### Where Our Current System Fails

**1. Incremental updates**

As discussed, we can't update existing data without removing the `--resume` flag and doing a full re-scrape. This is the biggest gap.

**2. Detecting deletions**

We have no mechanism to know when content is removed from PartSelect. Our database only grows, never shrinks.

**3. Data staleness**

No timestamps mean we can't tell how old our data is. A price might be from yesterday or from three months ago - we have no way to know.

**4. Compatibility table scale**

Scraping 6,000+ models via infinite scroll is slow and fragile. One timeout and we have incomplete data with no way to know it's incomplete.

**5. Rate limiting and bans**

We've had issues with PartSelect's anti-bot measures. The scraper includes delays, but there's no sophisticated handling of rate limits or CAPTCHAs. This was a huge bottlneck into the speed I could scrape at. 

**6. No monitoring**

We don't track scraping metrics - success rates, failure patterns, data quality over time. In production, this would be essential.

**7. Review/Q&A pagination**

We don't paginate through reviews and Q&A - we only get what's visible on first load. Popular parts might have hundreds of reviews, but we only capture the first page.

**8. Embedding model lock-in**

If we wanted to switch embedding models, we'd need to re-embed everything. The `embedding_text` column helps, but it's still a significant migration.

---

### Quick Wins We Could Implement

If we had another week, here's what we'd prioritize:

1. **Add `last_scraped_at` to parts table** - One ALTER TABLE, then update the loader to set it

2. **Sitemap-based change detection** - Parse sitemap, compare to our timestamps, only scrape changed pages

3. **Atomic compatibility updates** - Delete-then-insert instead of upsert for the compatibility table

4. **Scrape run logging** - Track what we scraped, when, and any errors

5. **Stale data detection** - Query to find parts not updated in N days

These don't require major architectural changes but would significantly improve data freshness.

---

## What We'd Do Differently

### Things That Worked Well

1. **Hybrid SQL + Vector approach** - Having both exact lookups and semantic search is the right architecture for this domain.

2. **Idempotent upserts** - Being able to re-run loaders without fear saved us many times during development.

3. **Unified platform** - Single Supabase instance for everything simplified development significantly.

4. **384-dim local embeddings** - No API costs, no rate limits, and quality is sufficient.

### Things We'd Reconsider

1. **Storing `parts` list as comma-separated text in `repair_symptoms`** - A proper junction table would be cleaner and enable better queries.

2. **Review IDs as hashes** - We generate `review_id` from author+date+title, which could collide. UUID would be safer.

3. **Date fields as TEXT** - We store dates as strings ("December 25, 2024"). Proper DATE types would enable time-based queries.

---

## Summary

The database design reflects a pragmatic approach to building a conversational AI for appliance parts:

- **PostgreSQL + pgvector in Supabase** gives us SQL reliability with vector search capability in one platform
- **Hybrid storage** separates structured facts (SQL) from unstructured knowledge (vectors)
- **384-dimensional local embeddings** balance quality with cost and latency
- **Idempotent loading** enables safe incremental updates
- **Extensible schema** supports adding appliance types and data sources without restructuring

The architecture is deliberately straightforward. We avoided premature optimization, chose boring technology where it makes sense, and designed for the scale we have rather than the scale we might eventually need. If this system needed to handle 100x the data, and we had longer than 48 hours, we'd have clear migration paths. But for now, simplicity serves us well.
