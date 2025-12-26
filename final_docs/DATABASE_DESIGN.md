# Database Design Document

## Overview

This document describes the database architecture for the PartSelect Chat Agent, a conversational AI system designed to help customers find appliance parts, troubleshoot problems, and get installation guidance. The database layer is the backbone of the entire system - it stores product data, handles compatibility lookups, and enables intelligent semantic search across thousands of customer questions, repair stories, and reviews.

We chose a hybrid approach: structured SQL tables for exact lookups combined with vector embeddings for semantic search. This lets us answer questions like "Does PS11752778 fit my WDT780SAEM1?" with a precise yes/no, while also handling fuzzier queries like "my ice maker makes clicking sounds" by finding semantically similar repair experiences.

---

## Why Supabase?

We went with **Supabase** (PostgreSQL with pgvector) as our unified data platform. Here's why:

**The Pros:**
- **Single platform for everything** - Both relational data and vector embeddings live in the same database. No need to sync between a SQL database and a separate vector store like Pinecone.
- **PostgreSQL foundations** - Rock-solid relational database with decades of battle-testing. ACID compliance, proper foreign keys, complex queries - it just works.
- **pgvector is good enough** - For our scale (thousands of parts, tens of thousands of Q&A entries), pgvector performs well. We don't need a dedicated vector database yet.
- **Generous free tier** - Good for development and prototyping without cost concerns.
- **Easy to migrate later** - If we outgrow pgvector, the vector search is abstracted behind RPC functions. We could swap in Pinecone without touching the agent code.

**The Cons:**
- **Not the fastest for pure vector search** - Dedicated vector databases like Pinecone or Weaviate would be faster for large-scale similarity search.
- **Supabase has quirks** - The 1000-row limit per query caught us off guard (more on this later).
- **Less control over infrastructure** - Being a managed service means we can't tune PostgreSQL settings directly.

**Why not a separate vector database?**

We considered running PostgreSQL for structured data and Pinecone for vectors. The main argument against: synchronization complexity. When a part gets updated, we'd need to update both stores and keep them consistent. With everything in Supabase, a single upsert handles both the row data and its embedding.

For a production system at massive scale, separating concerns might make sense. But for this case study scope, simplicity wins.

---

## The Data Model

Our schema reflects a key insight about appliance parts e-commerce: there's a clear split between **structured facts** (prices, compatibility, specs) and **unstructured knowledge** (customer experiences, troubleshooting wisdom). We store the former in regular SQL tables and the latter in vector-enabled tables for semantic search.

### Schema Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              SQL TABLES                                      │
│                         (Exact Lookups)                                      │
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
│  │ embedding (384)     │           └─────────────────────────────┘          │
│  │ ...                 │                                                     │
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
│                            VECTOR TABLES                                     │
│                        (Semantic Search)                                     │
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

This is the heart of the database. Every part we know about lives here.

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

PartSelect assigns every part a unique PS number (like PS11752778). This is stable, unique, and what customers often reference. We considered using an auto-increment ID, but PS numbers are already perfect identifiers that users actually use.

**The Embedding Column**

The `embedding` column stores a 384-dimensional vector generated from `part_name + part_type + part_description`. This enables semantic search - when a user asks for "refrigerator bins", we can find parts with `part_type` "Drawer or Glides" even though the word "bins" never appears in our data.

We chose 384 dimensions (using the all-MiniLM-L6-v2 model) as a pragmatic trade-off between search quality and storage/compute cost. It's a small, fast model that runs locally without any API calls.

**Trade-off: Denormalization**

You'll notice `brand` appears both here and in `model_compatibility`. This is intentional denormalization. We could normalize by creating a `brands` table, but it would add join complexity for minimal benefit. Part lookups are our most frequent query, so we optimize for that path.

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
3. Semantically represents what the table actually is: a relationship

**The Scale Problem We Discovered**

During development, we hit Supabase's 1000-row-per-query limit. When a user asked "what models does PS11752778 fit?", we'd only return 1000 of the 2,200+ compatible models. The fix was adding pagination to our client code:

```python
def get_compatible_models(self, ps_number: str, limit: int = 5000):
    all_results = []
    offset = 0
    batch_size = 1000  # Supabase max

    while len(all_results) < limit:
        result = self.client.table("model_compatibility")
            .select("model_number, brand, description")
            .eq("part_id", ps_number)
            .range(offset, offset + batch_size - 1)
            .execute()

        if not result.data:
            break
        all_results.extend(result.data)
        offset += batch_size

    return all_results
```

This is a good example of why understanding your database's limitations matters. The 1000-row limit isn't documented prominently, and it caused us to miss 95%+ of compatibility data initially.

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

Traditional SQL search fails for natural language queries. If a user asks "is this part easy to install?", we need to find Q&A entries and reviews that discuss installation difficulty - even if they don't use the exact word "install". They might say:

- "Popped right in, took 5 minutes"
- "Even my wife could replace this"
- "No tools needed, just pushed it into place"

Vector embeddings capture semantic meaning. The query "is this part easy to install?" gets embedded, and we find entries with similar meaning through cosine similarity, regardless of exact word matches.

**The `embedding_text` Column**

We store what text was embedded (typically `title + content` or `question + answer`). This serves two purposes:

1. Debugging - we can see exactly what was embedded
2. Re-embedding - if we switch embedding models, we can regenerate without re-scraping

---

## The Embedding Model Choice

We use **all-MiniLM-L6-v2**, a 384-dimensional sentence transformer. Here's the reasoning:

| Option | Dimensions | API Cost | Quality | Speed |
|--------|------------|----------|---------|-------|
| OpenAI text-embedding-3-large | 3072 | ~$0.13/1M tokens | Excellent | Fast (API) |
| OpenAI text-embedding-3-small | 1536 | ~$0.02/1M tokens | Very Good | Fast (API) |
| **all-MiniLM-L6-v2** | 384 | Free (local) | Good | Very Fast |

**Why not OpenAI embeddings?**

1. **Cost at scale** - We have thousands of Q&A entries, reviews, and stories. Embedding them all costs money, and re-embedding when data changes adds up.
2. **API dependency** - Local models work offline and don't add latency from network calls.
3. **Good enough quality** - For our use case (finding similar customer experiences), MiniLM's quality is sufficient.

**Trade-off: Lower dimensionality = less precision**

384 dimensions captures less semantic nuance than 1536 or 3072. For some queries, we might miss relevant results that OpenAI embeddings would find. In practice, we haven't found this to be a problem - our queries are specific enough that 384 dimensions work well.

If we needed higher quality, we'd:
1. Switch to OpenAI embeddings
2. Change the `vector(384)` columns to `vector(1536)`
3. Regenerate all embeddings using the stored `embedding_text`

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

| Table | Approximate Row Count |
|-------|----------------------|
| parts | ~2,000 |
| model_compatibility | ~500,000 |
| repair_symptoms | ~30 |
| repair_instructions | ~100 |
| qna_embeddings | ~10,000 |
| repair_stories_embeddings | ~5,000 |
| reviews_embeddings | ~15,000 |

### Bottlenecks We'd Hit First

1. **model_compatibility** - Already the largest table. At 500K rows, queries are fast. At 5M rows, we might need more sophisticated indexing.

2. **Vector search** - IVFFlat performance degrades as data grows. At 100K+ vectors, we'd want to tune the `lists` parameter or consider HNSW.

3. **Supabase limits** - Free tier has storage limits. Paid tiers have rate limits. At production scale, we'd need to evaluate pricing.

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

## What We'd Do Differently

### Things That Worked Well

1. **Hybrid SQL + Vector approach** - Having both exact lookups and semantic search is the right architecture for this domain.

2. **Idempotent upserts** - Being able to re-run loaders without fear saved us many times during development.

3. **Unified platform** - Single Supabase instance for everything simplified development significantly.

4. **384-dim local embeddings** - No API costs, no rate limits, and quality is sufficient.

### Things We'd Reconsider

1. **Storing `parts` list as comma-separated text in `repair_symptoms`** - A proper junction table would be cleaner and enable better queries.

2. **No pagination in our initial queries** - We should have assumed large result sets from the start.

3. **Review IDs as hashes** - We generate `review_id` from author+date+title, which could collide. UUID would be safer.

4. **Date fields as TEXT** - We store dates as strings ("December 25, 2024"). Proper DATE types would enable time-based queries.

---

## Summary

The database design reflects a pragmatic approach to building a conversational AI for appliance parts:

- **PostgreSQL + pgvector in Supabase** gives us SQL reliability with vector search capability in one platform
- **Hybrid storage** separates structured facts (SQL) from unstructured knowledge (vectors)
- **384-dimensional local embeddings** balance quality with cost and latency
- **Idempotent loading** enables safe incremental updates
- **Extensible schema** supports adding appliance types and data sources without restructuring

The architecture is deliberately straightforward. We avoided premature optimization, chose boring technology where it makes sense, and designed for the scale we have rather than the scale we might eventually need. If this system needed to handle 100x the data, we have clear migration paths. But for now, simplicity serves us well.
