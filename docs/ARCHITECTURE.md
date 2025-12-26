# PartSelect Chat Agent - High Level Design

## Context

**Evaluation Criteria:**
- Agentic architecture
- Extensibility + scalability
- Answering accurately and effectively

**Scope:**
- Refrigerators and dishwashers only (extensible to more later)
- Product info, troubleshooting, compatibility, installation
- Order support deferred (but architecture supports adding it)

---

## Architecture Overview

**Tool-Centric Design** - Instead of rigid intent routing, an LLM Planner sees all available tools and decides which to call.

```
Query → Scope Check → Entity Extraction → Planner (LLM) → Executor → Synthesizer (LLM) → Response
```

**Why tool-centric?**
- Adding new capability = adding a new tool (not rewiring intents)
- Planner automatically considers new tools
- More "agentic" - LLM makes meaningful decisions

---

## Flow

```
┌────────────────────────────────────────────────────────────┐
│  USER QUERY                                                │
└────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────────┐
│  1. SCOPE CHECK                                            │
│     Is this about refrigerators or dishwashers?            │
│     NO → Polite rejection                                  │
│     YES → Continue                                         │
└────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────────┐
│  2. ENTITY EXTRACTION                                      │
│     Pull from query + session history:                     │
│     - part_number, model_number, brand                     │
│     - symptom, appliance_type, part_type                   │
└────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────────┐
│  3. PLANNER (LLM)                                          │
│     Sees: query, entities, session state, ALL tools        │
│     Decides: which tools to call OR ask clarifying question│
└────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────────┐
│  4. EXECUTOR                                               │
│     Runs tool calls (parallel when independent)            │
│     Handles fallbacks (scrape if data missing)             │
└────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────────┐
│  5. SYNTHESIZER (LLM)                                      │
│     Combines tool results into coherent response           │
│     Updates session state                                  │
└────────────────────────────────────────────────────────────┘
```

---

## Data Layer

### SQL Database (Structured/Exact Data)

**Parts Table:**
```
ps_number (PK)
part_name
part_type                    -- "Ice Maker Assembly", "Water Inlet Valve"
manufacturer_part_number
part_manufacturer
part_price
part_description
install_difficulty
install_time
install_video_url
part_url
average_rating
num_reviews
appliance_type               -- refrigerator, dishwasher
brand
availability
replaces_parts               -- list of other part numbers this replaces
```

**Model Compatibility Table:**
```
part_id (FK → parts)
model_number
brand
description
```
(Separate table because one part fits many models)

### Vector Database (Semantic/Language Data)

**Troubleshooting Guides:**
- Symptom descriptions
- Diagnostic steps for each part type
- Inspection instructions
- Video links

**Repair Stories:**
- Customer narratives of how they fixed things
- Linked to part types and symptoms

**Q&A Content:**
- Questions and answers from part pages

**Blog Posts / Tutorials:**
- General repair guides and tips

---

## Tools

Tools are self-describing. Planner sees all tools and picks which to use.

### SQL Tools
| Tool | What it does |
|------|--------------|
| `get_part_by_number` | Get part details by PS number |
| `search_parts` | Search by keywords + filters (type, model, brand) |
| `check_compatibility` | Check if part fits model |
| `get_parts_for_model` | All parts compatible with a model |
| `get_parts_by_type_and_model` | Get specific part type for a model |

### Vector Tools
| Tool | What it does |
|------|--------------|
| `search_troubleshooting` | Find diagnostic guides for a symptom |
| `search_install_guides` | Find installation instructions |
| `search_repair_stories` | Find customer repair experiences |
| `search_qa` | Search Q&A content |

### Scrape Tools (Fallback)
| Tool | What it does |
|------|--------------|
| `scrape_part_page` | Live scrape if part not in DB |
| `scrape_repair_help` | Live scrape repair guide |

---

## Key Insight: Symptom → Part Type → Specific Part

PartSelect gives **part types** for symptoms, not specific part numbers.

To get a specific part: **Part Type + Model Number → Specific Part(s)**

```
"My ice maker isn't working"
        │
        ▼
search_troubleshooting → Returns part TYPES:
  - Water Fill Tubes
  - Water Inlet Valve
  - Ice Maker Assembly
  (plus diagnostic steps for each)
        │
        ▼
User determines which part type they need
(via diagnostic steps or clarifying questions)
        │
        ▼
get_parts_by_type_and_model("Ice Maker Assembly", "WDT780SAEM1")
        │
        ▼
Returns specific parts:
  - PS11752778 - $89.99
  - PS11752779 - $95.99
```

---

## Session State

Maintain state across conversation:

```
{
  model_number: "WDT780SAEM1",
  appliance_type: "refrigerator",
  brand: "Whirlpool",
  symptom: "ice maker not making ice",
  parts_discussed: ["PS11752778"]
}
```

- Don't re-ask for info we already know
- Update as we learn new things

---

## Data Sourcing

1. **Pre-scrape** all refrigerator/dishwasher content from PartSelect
   - Parts pages
   - Repair help pages
   - Blog posts

2. **Fallback scrape** on-demand if data is missing

---

## Query Types We Handle

| Query Type | Example | Tools Used |
|------------|---------|------------|
| Product lookup | "Tell me about PS11752778" | `get_part_by_number` |
| Compatibility | "Does this fit my WDT780SAEM1?" | `check_compatibility` |
| Troubleshooting | "My ice maker isn't working" | `search_troubleshooting`, `get_parts_by_type_and_model` |
| Installation | "How do I install this?" | `search_install_guides`, `get_part_by_number` |
| Search | "I need a water filter" | `search_parts` |

---

## Extensibility

| To Add... | Just... |
|-----------|---------|
| New appliance type | Update scope check, scrape new data |
| Order support | Add order-related tools |
| New capability | Add a new tool |
| New data source | Add to vector DB + new tool |

**The tool-centric design means new features = new tools, not rewiring the system.**

---

## Open Questions

1. How interactive should diagnosis be?
2. Full conversation history vs just extracted entities?
3. How to rank multiple part results?
