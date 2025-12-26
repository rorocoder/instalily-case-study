# PartSelect Chat Agent - Presentation Script

**Duration:** ~10 minutes
**Audience:** Technical evaluators familiar with the case study requirements

---

## Opening (30 seconds)

I built a conversational AI agent for PartSelect that handles part lookups, compatibility checks, and troubleshooting for refrigerators and dishwashers. Rather than walk through features, I want to focus on the **architectural decisions** that shaped this system and the **non-obvious problems** I had to solve.

The interesting parts aren't "it can answer questions" — it's *how* it figures out which questions to answer, *what* tools to call, and *why* certain approaches failed before I found ones that worked.

---

## Section 1: The Architecture Evolution (2 minutes)

### What I Started With (v1)

My first design followed a classic multi-agent pattern:

```
Query → Planner → [Simple?] → Executor → Synthesizer
                  [Complex?] → Workers (parallel) → Synthesizer
```

The **Planner** would analyze each query, classify it as "simple" or "complex," and route accordingly. Simple queries went to a single Executor. Complex queries spawned parallel Workers.

**This looked good on paper. In practice, it failed.**

### Why v1 Didn't Work

**Problem 1: The simple/complex boundary was arbitrary.**

Is "compare these two parts" complex? What about "tell me about this part and its reviews"? The Planner had to make judgment calls that were inconsistent. The same query might route differently on different runs.

**Problem 2: Pre-planning tool calls couldn't adapt.**

The Planner would decide upfront: "Call `get_part()`, then `get_compatible_models()`." But what if `get_part()` returns "not found"? Now I need to scrape instead. The pre-planned sequence couldn't handle this.

**Problem 3: Adding tools was painful.**

Every new tool required updates to:
- The tool itself
- The Planner's knowledge of when to use it
- Sometimes the routing logic

### The v2 Insight

The key realization: **modern LLMs with good prompts can figure out tool selection dynamically.**

I removed the Planner entirely. The new architecture:

```
Query → Scope Check → Executor (ReAct) → Secondary Scope Check → Synthesizer
```

The **Executor** uses a ReAct pattern — observe the query and available tools, reason about what it needs, act by calling a tool, observe the result, reason again, and repeat until it has enough information.

For "tell me about PS11752778," it calls `get_part()` once and stops.
For "compare PS11752778 and PS11752779," it calls `get_part()` twice.
For "is this compatible with my WDT780SAEM1?" it resolves "this" from session, then calls `check_compatibility()`.

**The LLM adapts. No rigid routing needed.**

### Why This Works

The ReAct pattern gives flexibility without chaos because:

1. **Workflow patterns in the prompt** guide the LLM without being rigid rules. I document patterns like "Pattern 1: Part Lookup," "Pattern 2: Symptom Query," etc. The LLM learns when to apply each.

2. **The tool docstrings act as documentation.** Each tool has a clear description of when to use it. The LLM sees all tools and picks appropriately.

3. **Session context provides continuity.** When user says "this part," the Executor sees recent parts in session state.

---

## Section 2: Two-Stage Scope Checking (2 minutes)

This is probably the most non-obvious part of the architecture.

### The Primary Scope Check

The first scope check happens before any tools run. It answers: "Is this query about refrigerators or dishwashers?"

I use a **hybrid approach**:

1. **Rule-based patterns first** — Regex matching for keywords like "refrigerator," "ice maker," "PS" numbers. Fast (~0ms) and catches obvious cases.

2. **LLM fallback for ambiguity** — If rules can't decide, ask Claude Haiku to classify. Adds ~300ms but handles edge cases like "is it easy to install?" (follow-up with no appliance keywords).

**Why hybrid?** Rules alone miss context. LLM alone is slow and expensive for every query. The hybrid gives speed for common cases and accuracy for edge cases.

### The Discovery That Required a Second Check

Here's where it gets interesting. Early in testing, I tried:

```
User: "Tell me about PS16688554"
```

Primary scope check: **PASS** — It's a PS number, clearly a part lookup.

Executor fetches the part...

Result: **"DeWALT Pole, Middle Extension"** — It's a chainsaw part!

PartSelect sells parts for many appliances. PS numbers don't encode the appliance type. The query *looked* in-scope but the *data* revealed it wasn't.

### The Secondary Scope Check

I added a second gate after the Executor runs. It examines **actual fetched data**, not just the query text.

```python
# Secondary scope check scans tool results
for result in tool_results:
    appliance_type = result.get("appliance_type")
    if appliance_type not in ["refrigerator", "dishwasher"]:
        # Reject with helpful message
        return f"I'm sorry, but {part_name} is a {appliance_type} part..."
```

**For live-scraped parts**, the appliance type might be unknown. I use Haiku to classify based on:
- Part name and description
- Compatible model names (e.g., "DCPH820BH" sounds like power tools)
- Sample review content mentioning "chainsaw" or "yard work"

### Why Two Stages?

**Stage 1 catches:** "What's the weather?", "Tell me about microwaves"
**Stage 2 catches:** "Tell me about PS16688554" → chainsaw

Neither alone is sufficient. Text analysis can't know what a PS number refers to without fetching data. But fetching data for obvious spam ("write me a poem") wastes resources.

---

## Section 3: The Data Architecture (2 minutes)

### The Hybrid SQL + Vector Approach

I use Supabase (PostgreSQL with pgvector) as a unified platform for both structured and vector data.

**SQL tables handle ground truth:**
- `parts` — Product catalog with prices, specs, ratings
- `model_compatibility` — 500K+ part-to-model relationships
- `repair_symptoms` — Common problems and what to check
- `repair_instructions` — Step-by-step diagnostics

**Vector tables handle semantic search:**
- `qna_embeddings` — Customer Q&A for "is this easy to install?" queries
- `reviews_embeddings` — Customer reviews for quality questions
- `repair_stories_embeddings` — DIY repair experiences

### Why One Platform?

I considered separating SQL (PostgreSQL) and vectors (Pinecone). The main argument against: **synchronization complexity**.

When a part gets updated, I'd need to update both stores and keep them consistent. With everything in Supabase, a single upsert handles both the row data and its embedding.

**Trade-off acknowledged:** A dedicated vector database would be faster at scale. But for thousands of parts and tens of thousands of Q&A entries, pgvector performs well. And I've abstracted vector search behind RPC functions — migrating to Pinecone later would require no agent code changes.

### Why Local Embeddings?

I use **all-MiniLM-L6-v2** (384 dimensions) instead of OpenAI embeddings.

**The reasoning:**
- **No API costs** — Embedding thousands of Q&A entries with OpenAI adds up
- **No rate limits** — Can re-embed the entire dataset without throttling
- **Fast** — Local inference, no network latency
- **Good enough** — For finding similar customer experiences, 384 dimensions works

**Trade-off:** Lower dimensionality means less semantic precision. A 1536-dimension OpenAI embedding captures more nuance. In practice, I haven't found this to be a problem for my use cases.

### The Compatibility Table Challenge

Some parts fit 6,000+ models. I discovered this when a query returned only 1,000 results — Supabase's default limit.

**The fix:** Pagination in the client code:

```python
while len(all_results) < limit:
    result = query.range(offset, offset + 999).execute()
    if not result.data:
        break
    all_results.extend(result.data)
    offset += 1000
```

This is a good example of why understanding your database's quirks matters.

---

## Section 4: The Live Scraping Fallback (1.5 minutes)

### The Problem

I scraped ~2,000 parts. PartSelect has millions. When a user asks about a part not in my database, I have two options:
1. Say "I don't know"
2. Go get it

I chose option 2.

### How It Works

When `get_part()` returns "not found," the system automatically triggers `scrape_part_live()`:

1. Opens headless Chrome
2. Navigates to PartSelect
3. Searches for the PS number
4. Extracts everything: part info, compatibility, Q&A, reviews, repair stories
5. Returns comprehensive data

**Why comprehensive?** If I scraped minimally (just basic info), every follow-up would trigger another 15-second scrape:

```
"Tell me about PS12345678" → 15s (scraping)
"What models does it fit?" → 15s (scrape again for compatibility)
"What do customers say?" → 15s (scrape again for reviews)
```

By scraping everything upfront:

```
"Tell me about PS12345678" → 15s (comprehensive scrape)
"What models does it fit?" → 2s (data already in scrape result)
"What do customers say?" → 2s (data already in scrape result)
```

### The Prompt Challenge

After a scrape, the Executor must use the scraped data, not call database tools (which would return empty).

I had to explicitly instruct in the prompt:

```
**DO NOT call additional database tools after a successful scrape:**
- ❌ get_compatible_models(ps_number) - use _compatible_models from scrape result
- ❌ search_qna(ps_number, query) - use _qna_data from scrape result
```

Without this, the LLM would happily call `get_compatible_models()` and get empty results.

---

## Section 5: Edge Cases That Shaped the Design (2 minutes)

Building an agent that handles real queries means dealing with messy input. Let me share a few edge cases that forced design changes.

### The "Washer" vs "Dishwasher" Collision

My initial regex for out-of-scope:
```python
OUT_OF_SCOPE = [r"\bwasher\b"]  # Block washing machines
```

This rejected "dishwasher" queries. The fix:
```python
r"\bwasher\b(?<!\bdish)"  # "washer" not preceded by "dish"
```

**Lesson:** Scope rules need careful ordering. Check in-scope patterns first, then out-scope.

### The Follow-Up Query Problem

```
User: "Tell me about PS11752778"
Agent: [Responds about an ice maker]
User: "Is it easy to install?"
```

The second query has no refrigerator keywords. A naive scope check rejects it.

**Solution:** Pass conversation history to the LLM scope check with context: "If this is a follow-up to a conversation about refrigerators/dishwashers, it's IN_SCOPE."

### Pattern 2a vs 2b: The Symptom Split

Users ask about symptoms in two distinct ways:

**2a (overview):** "My ice maker isn't working"
→ List all parts to check, symptom frequency, video link
→ **Do NOT** give detailed repair instructions yet

**2b (specific):** "How do I check the water inlet valve?"
→ Give step-by-step diagnostic instructions
→ **Do NOT** return part cards (this is troubleshooting, not shopping)

Early versions confused these. The Executor would dump repair instructions when users just wanted an overview, or give vague overviews when users wanted specific steps.

**Solution:** Explicit patterns in the prompt with clear differentiation.

### Session Cleanup After Rejection

If a user asks about a chainsaw part and I reject it, I also remove that PS number from session state. Otherwise:

```
User: "Tell me about PS16688554" (chainsaw)
[Rejected]
User: "How about PS11752778 instead?" (ice maker)
[Added to session: [PS16688554, PS11752778]]
User: "Compare them"
[Agent tries to compare chainsaw and ice maker!]
```

### PS Numbers in Response = Part Cards

The frontend extracts PS numbers from the response text to show part cards. If the Synthesizer describes a part without its PS number, no card appears.

I had to add explicit prompt instruction:
```
**ALWAYS include PS numbers for EVERY part you mention**
Part cards ONLY display for parts with PS numbers in your response.
No PS number = no card.
```

---

## Section 6: Model Selection and Cost Optimization (1 minute)

I use different Claude models for different tasks:

| Node | Model | Why |
|------|-------|-----|
| Scope Check (LLM fallback) | Haiku | Simple yes/no classification |
| Executor | Haiku | Tool selection doesn't need Sonnet quality |
| Appliance Classifier | Haiku | Quick classification task |
| **Synthesizer** | **Sonnet** | Customer-facing response quality matters |

**The economics:**
- Haiku: ~$0.25/1M input tokens, $1.25/1M output
- Sonnet: ~$3/1M input, $15/1M output

By using Haiku for all the "thinking" work and Sonnet only for the final polish, I get quality where customers see it while keeping costs down.

The Executor might call tools 3-4 times, each requiring LLM reasoning. That's a lot of tokens. Using Haiku there saves significant cost without sacrificing capability — Haiku is perfectly capable of deciding "I should call `get_part()` with this PS number."

---

## Section 7: What I'd Do Differently (1 minute)

### Persist Live-Scraped Parts

Currently, parts scraped in real-time aren't saved. If another user asks about the same part tomorrow, I scrape again. Adding database persistence would create a self-expanding catalog.

### Add a Verifier Node

Between Executor and Synthesizer, I'd add verification that cross-checks claims:
- "This fits your model" → Is there actual compatibility data confirming this?
- "Price is $89.99" → Does this match the tool result exactly?
- PS numbers mentioned → Do they all exist in tool results?

This prevents the worst failures: false compatibility claims, invented part numbers, wrong prices.

### Differential Scraping

Currently, updating the database means re-scraping everything. PartSelect has a sitemap with `lastmod` dates. I should:
1. Parse the sitemap
2. Compare against my `last_scraped_at` timestamps
3. Only scrape changed pages

This would reduce 17-minute full refreshes to potentially seconds.

### Better Session State

I track discussed parts, but not:
- User's model number (for follow-up compatibility checks)
- Established symptom context (for follow-up part questions)
- Comparison sets ("these parts" vs "this part")

Richer session state would improve conversation continuity.

---

## Closing (30 seconds)

The system works because of a few key principles:

1. **Let the LLM figure it out** — ReAct over rigid routing
2. **Validate at the right layer** — Text-based scope check for speed, data-based for accuracy
3. **Comprehensive data when you fetch** — One slow scrape beats three slow scrapes
4. **Honest about limitations** — When data is missing, say so and link to PartSelect

The architecture is designed to be extensible. Adding a new tool is one decorated function. Adding a new appliance type is config + scraping + scope update. Adding order support would plug into the existing tool registry.

Happy to dive deeper into any of these areas.

---

## Appendix: Potential Q&A Topics

**Q: Why not use RAG with a single vector store?**
A: Some queries need exact answers ("what's the price?", "does this fit my model?"). Vector similarity can't guarantee precision for compatibility checks. The hybrid approach uses vectors for fuzzy queries and SQL for ground truth.

**Q: Why Supabase over self-hosted PostgreSQL?**
A: Development velocity. Supabase's dashboard, built-in auth (for future features), and managed infrastructure let me focus on the agent logic. For production at scale, I'd consider self-hosted for more control.

**Q: How do you handle prompt injection?**
A: Multiple layers:
1. Scope check validates topic before Executor runs
2. Secondary scope check validates fetched data
3. Synthesizer prompt focuses on formatting, not arbitrary generation
4. Tools only expose specific database operations

**Q: What's the hardest edge case you encountered?**
A: The chainsaw part via PS number. It exposed that query-text scope checking is fundamentally insufficient — you need to validate actual data. This led to the two-stage architecture.

**Q: How would you scale this to handle more traffic?**
A:
- Move sessions to Redis (currently in-memory)
- Add read replicas for database
- Rate limit scraping triggers
- Consider dedicated vector DB if query latency becomes an issue
- The agent itself is stateless and horizontally scalable

**Q: Why not streaming in the UI?**
A: Backend supports it; frontend doesn't use it yet. Part card extraction happens after full response generation. With streaming, I'd need to either show cards only after streaming completes (awkward UX) or stream card data separately (more complex). Non-streaming with a thinking indicator works fine for 2-5 second responses.
