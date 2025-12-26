# Database Plan - Supabase

## Overview

Using **Supabase** (PostgreSQL + pgvector) as a unified platform for both structured data and vector embeddings.

---

## SQL Tables (Exact Lookups)

These tables store structured, queryable data with definite answers.

### 1. `parts`
Product catalog with specs, pricing, installation info. Includes embedding for semantic search.

| Column | Type | Description |
|--------|------|-------------|
| ps_number | TEXT (PK) | PartSelect number (e.g., "PS11752778") |
| part_name | TEXT | Display name |
| part_type | TEXT | Category (e.g., "Ice Maker Assembly") |
| manufacturer_part_number | TEXT | OEM part number |
| part_manufacturer | TEXT | Who makes it |
| part_price | DECIMAL | Current price |
| part_description | TEXT | Full description |
| install_difficulty | TEXT | "Easy", "Moderate", etc. |
| install_time | TEXT | "15-30 minutes" |
| install_video_url | TEXT | YouTube link |
| part_url | TEXT | PartSelect product page |
| average_rating | DECIMAL | Star rating |
| num_reviews | INTEGER | Review count |
| appliance_type | TEXT | "refrigerator", "dishwasher" |
| brand | TEXT | e.g., "Whirlpool" |
| manufactured_for | TEXT | Brands this part works with |
| availability | TEXT | In stock status |
| replaces_parts | TEXT[] | Array of part numbers this replaces |
| created_at | TIMESTAMP | When scraped |
| embedding | VECTOR(384) | Semantic embedding of name + type + description |

**Use cases:**
- "Tell me about PS11752778" → Direct lookup by PS number
- "What's the price of this part?" → Direct lookup
- "Find water filters under $50" → Filtered SQL query (ILIKE)
- "Find refrigerator bins" → Semantic search (matches "Drawer or Glides", "Tray or Shelf")

---

### 2. `model_compatibility`
Maps parts to compatible appliance models. This is a relationship table, not an entity.

| Column | Type | Description |
|--------|------|-------------|
| part_id | TEXT | References parts.ps_number |
| model_number | TEXT | Appliance model (e.g., "WDT780SAEM1") |
| brand | TEXT | Model brand |
| description | TEXT | Model description |

**Primary Key:** `(part_id, model_number)` - composite key

**Why composite PK?**
- Compatibility is inherently a relationship, not an entity
- Prevents duplicates by construction
- Makes "is this compatible?" query canonical

**Use cases:**
- "Does PS11752778 fit my WDT780SAEM1?" → Direct lookup on PK
- "What parts fit my model?" → Filter by model_number

---

### 3. `repair_symptoms`
Common problems and which part types to check.

| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL (PK) | Auto-increment ID |
| appliance_type | TEXT | "refrigerator", "dishwasher" |
| symptom | TEXT | "Noisy", "Leaking", "Ice maker not making ice" |
| symptom_description | TEXT | Detailed description |
| percentage | DECIMAL | 29.0 - how common (enables sorting/filtering) |
| video_url | TEXT | YouTube troubleshooting video |
| parts | TEXT | Comma-separated part types to check |
| symptom_url | TEXT | PartSelect repair page |
| difficulty | TEXT | "EASY", "MODERATE", "DIFFICULT" |

**Use cases:**
- "My fridge is leaking" → Match symptom → Get part types
- "What causes a noisy dishwasher?" → Symptom lookup
- "What are the most common fridge problems?" → ORDER BY percentage DESC

---

### 4. `repair_instructions`
Step-by-step diagnostic instructions per part type.

| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL (PK) | Auto-increment ID |
| appliance_type | TEXT | "refrigerator", "dishwasher" |
| symptom | TEXT | Links to repair_symptoms.symptom |
| part_type | TEXT | "Water Inlet Valve", "Door Gasket" |
| instructions | TEXT | Full step-by-step guide |
| part_category_url | TEXT | Link with anchor to specific section |

**Use cases:**
- User identified symptom → Show instructions for each part type
- "How do I check if my water inlet valve is bad?" → Match part_type

---

## Vector Tables (Semantic Search)

These tables use pgvector for similarity search on unstructured text. The goal is to find relevant context, not exact answers.

### 5. `qna_embeddings`
Questions and answers from part pages - searchable by semantic similarity.

| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL (PK) | Auto-increment ID |
| ps_number | TEXT (FK) | References parts.ps_number |
| question_id | TEXT | Unique Q&A identifier |
| question | TEXT | Customer question |
| answer | TEXT | PartSelect response |
| asker | TEXT | Who asked |
| date | TEXT | When asked |
| model_number | TEXT | If customer specified their model |
| helpful_count | INTEGER | Upvotes |
| embedding | VECTOR(1536) | OpenAI embedding vector |

**Use cases:**
- "Will this part fix my ice dispenser?" → Find similar questions
- "Is installation difficult?" → Find relevant Q&As about installation

---

### 6. `repair_stories_embeddings`
Customer repair narratives - searchable by semantic similarity.

| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL (PK) | Auto-increment ID |
| ps_number | TEXT (FK) | References parts.ps_number |
| story_id | TEXT | Unique story identifier |
| title | TEXT | Problem description |
| instruction | TEXT | How they fixed it |
| author | TEXT | Who wrote it |
| difficulty | TEXT | "Really Easy", "A bit difficult" |
| repair_time | TEXT | "Less than 15 mins" |
| helpful_count | INTEGER | Upvotes |
| vote_count | INTEGER | Total votes |
| embedding | VECTOR(1536) | OpenAI embedding vector |

**Use cases:**
- "My ice maker makes clicking noises" → Find similar repair stories
- "Has anyone replaced this part themselves?" → Find DIY experiences

---

### 7. `reviews_embeddings`
Customer product reviews - searchable by semantic similarity.

| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL (PK) | Auto-increment ID |
| ps_number | TEXT (FK) | References parts.ps_number |
| review_id | TEXT | Unique review identifier (hash of author+date+title) |
| rating | INTEGER | Star rating (1-5) |
| title | TEXT | Review title/headline |
| content | TEXT | Full review text |
| author | TEXT | Reviewer name |
| date | TEXT | Review date |
| verified_purchase | BOOLEAN | Whether purchase was verified |
| embedding | VECTOR(1536) | OpenAI embedding vector |

**Use cases:**
- "Is this part easy to install?" → Find reviews mentioning installation experience
- "Does this filter really work?" → Find reviews about product effectiveness
- "Any issues with this part?" → Find negative reviews or complaints

---

## Query Flow Examples

### Example 1: "Is PS11752778 compatible with my WDT780SAEM1?"
```
1. SQL: SELECT * FROM model_compatibility
        WHERE part_id = 'PS11752778' AND model_number = 'WDT780SAEM1'
2. Return: Yes/No with part details
```

### Example 2: "My ice maker isn't making ice"
```
1. SQL: SELECT * FROM repair_symptoms
        WHERE appliance_type = 'refrigerator'
        AND symptom ILIKE '%ice%maker%'
   → Returns part types: Water Fill Tubes, Water Inlet Valve, Ice Maker Assembly

2. SQL: SELECT * FROM repair_instructions
        WHERE symptom = 'Ice maker not making ice'
   → Returns diagnostic steps for each part type

3. Vector: Search repair_stories_embeddings for similar experiences
   → Returns: "I had same issue, replaced the water inlet valve..."

4. Synthesize response with all sources
```

### Example 3: "How do I install PS11752778?"
```
1. SQL: SELECT install_difficulty, install_time, install_video_url
        FROM parts WHERE ps_number = 'PS11752778'
   → Returns: "Easy", "15-30 mins", YouTube link

2. Vector: Search qna_embeddings for installation questions about this part
   → Returns relevant Q&As about installation tips

3. Vector: Search reviews_embeddings for installation experiences
   → Returns: "Easy to install", "Part arrived quickly and was easy to install"

4. Synthesize response with all sources
```

### Example 4: "Is PS11752778 any good? Should I buy it?"
```
1. SQL: SELECT average_rating, num_reviews FROM parts
        WHERE ps_number = 'PS11752778'
   → Returns: 4.8 stars, 10 reviews

2. Vector: Search reviews_embeddings for this part
   → Returns mix of positive/negative reviews with context

3. Synthesize response: "This part has 4.8/5 stars from 10 reviews.
   Customers say it's easy to install and works great..."
```

### Example 5: "Find refrigerator bins" (Semantic Part Search)
```
1. Vector: search_parts_semantic("refrigerator bins", appliance_type="refrigerator")
   → Embeds query and finds similar parts by embedding distance
   → Returns parts with part_type "Drawer or Glides", "Tray or Shelf"

   Why this works: "bins" semantically matches "drawer", "crisper", "shelf"
   even though ILIKE '%bins%' would find nothing.

2. Return list of matching parts with details
```

---

## Data Loading Strategy

### CSV → Supabase Flow

```
data/
├── refrigerator_parts.csv        → parts table
├── refrigerator_model_compatibility.csv → model_compatibility table
├── repair_symptoms.csv           → repair_symptoms table
├── repair_instructions.csv       → repair_instructions table
├── qna.csv                       → qna_embeddings (+ generate embeddings)
├── repair_stories.csv            → repair_stories_embeddings (+ generate embeddings)
└── reviews.csv                   → reviews_embeddings (+ generate embeddings)
```

### Loader Script Tasks
1. Create tables with proper schemas
2. Load CSV data into SQL tables
3. Generate embeddings for parts (name + type + description) using OpenAI API
4. Generate embeddings for Q&A and repair stories using OpenAI API
5. Insert into vector tables with embeddings

---

## Supabase Setup Instructions

### 1. Create Supabase Project
1. Go to [supabase.com](https://supabase.com) and create a new project
2. Note your project URL and anon key from Settings > API

### 2. Configure Environment
```bash
# Copy the example env file
cp .env.example .env

# Edit .env with your credentials
SUPABASE_URL=https://your-project-id.supabase.co
SUPABASE_KEY=your-anon-key
OPENAI_API_KEY=your-openai-key
```

### 3. Create Database Schema
1. Open Supabase SQL Editor (Database > SQL Editor)
2. Copy and paste the contents of `database/schema.sql`
3. Run the SQL to create all tables and functions

### 4. Load Data
```bash
# Install dependencies
pip install -r requirements.txt

# Load all data (includes embedding generation)
python -m database.load_data

# Or load without embeddings (faster, for testing)
python -m database.load_data --no-embeddings

# Or load SQL tables only (skip vector tables)
python -m database.load_data --sql-only
```

### 5. Verify Data
Run these queries in Supabase SQL Editor:
```sql
-- Check row counts
SELECT 'parts' as table_name, COUNT(*) FROM parts
UNION ALL SELECT 'model_compatibility', COUNT(*) FROM model_compatibility
UNION ALL SELECT 'repair_symptoms', COUNT(*) FROM repair_symptoms
UNION ALL SELECT 'repair_instructions', COUNT(*) FROM repair_instructions
UNION ALL SELECT 'qna_embeddings', COUNT(*) FROM qna_embeddings
UNION ALL SELECT 'repair_stories', COUNT(*) FROM repair_stories_embeddings
UNION ALL SELECT 'reviews', COUNT(*) FROM reviews_embeddings;

-- Test semantic search (requires embeddings)
-- SELECT * FROM search_qna(query_embedding, 0.7, 5);
-- SELECT * FROM search_reviews(query_embedding, 0.7, 5);
```

---

## Why This Split?

| Data Type | Storage | Reason |
|-----------|---------|--------|
| Parts catalog | SQL + Vector | Exact lookups (PS#, filters) + semantic search ("bins" → "drawers") |
| Model compatibility | SQL | Exact match needed for compatibility |
| Symptoms/Instructions | SQL | Structured troubleshooting flow |
| Q&A content | Vector | Semantic search for similar questions |
| Repair stories | Vector | Semantic search for similar experiences |
| Reviews | Vector | Semantic search for product feedback |

**Key insight:** SQL for "ground truth" answers, Vector for "find me something relevant."

**Parts hybrid approach:** Use SQL (`search_parts`) for exact/filtered queries, Vector (`search_parts_semantic`) for natural language queries. Agent chooses based on query type.

---

## Data Integrity

### Idempotent Upserts

All data loading uses upserts keyed on natural identifiers to ensure re-runs don't create duplicates:

| Table | Upsert Key |
|-------|------------|
| parts | `ps_number` |
| model_compatibility | `(part_id, model_number)` |
| repair_symptoms | `(appliance_type, symptom)` |
| repair_instructions | `(appliance_type, symptom, part_type)` |
| qna_embeddings | `(ps_number, question_id)` |
| repair_stories_embeddings | `(ps_number, story_id)` |
| reviews_embeddings | `(ps_number, review_id)` |

This allows safe incremental updates and re-scraping without data corruption.

---

## Future Evolution

As the catalog grows, this architecture scales cleanly:

- **SQL tables remain stable** - schema changes are rare, indexes handle query load
- **Vector indexes can be regenerated** - if embedding models improve, rebuild without touching SQL
- **Migration to dedicated vector store** - if pgvector hits limits, move to Pinecone/Weaviate without changing agent logic (retrieval is abstracted behind tools)
- **Adding appliance types** - just scrape new data, same schema
- **Adding data sources** - new vector table + new tool, planner automatically considers it

The tool-centric agent design means database changes don't require rewiring the conversational flow.
