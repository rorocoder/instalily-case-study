# Scraper Design Document

## Overview

This document describes the web scraping system built to collect appliance parts data from PartSelect.com. The scraper is the foundation of our entire chat agent - without accurate, comprehensive data, the agent can't help customers find parts, check compatibility, or troubleshoot problems.

The scraping challenge was significant: PartSelect has thousands of parts across dozens of brands, each with extensive compatibility tables (some parts fit 6,000+ models), customer Q&A, reviews, and repair stories. We needed a system that could reliably collect all of this while being respectful of the website and resilient to failures.

---

## Why Build a Scraper?

Before diving into the technical details, it's worth explaining why we built a custom scraper rather than using an API or third-party data source.

**The simple answer: there is no API.** PartSelect doesn't offer a public API for accessing their product catalog. This is common in e-commerce - companies often keep their data proprietary. Our options were:

1. **Manual data collection** - Not feasible for thousands of parts
2. **Third-party data providers** - None available for this specific domain
3. **Custom web scraping** - The only viable option

**Web scraping comes with tradeoffs:**

| Approach | Pros | Cons |
|----------|------|------|
| API | Reliable, structured, fast | Doesn't exist for PartSelect |
| Third-party data | Pre-cleaned, supported | Not available for appliance parts |
| Custom scraper | Full control, complete data | Fragile to site changes, slower |

We accepted the scraper's downsides because it was the only path to getting the data we needed. The upside is that we control exactly what data we collect and how it's structured.

---

## Architecture: Two Scrapers, One Goal

We split the scraping into two distinct components that serve different purposes:

### 1. Part Scraper (`scrapers/part_scraper.py`)

This is the workhorse. It collects the core product catalog:

- **Basic part information** - Name, PS number, manufacturer part number, price, availability
- **Installation details** - Difficulty rating, estimated time, YouTube video links
- **Model compatibility** - Which appliance models this part fits (the many-to-many relationship)
- **Customer content** - Q&A entries, repair stories, and reviews

The part scraper navigates PartSelect's category structure:
```
Appliance Type (Refrigerator)
    └── Brand (Whirlpool, Samsung, LG...)
        └── Category Page (Ice Makers, Water Filters...)
            └── Individual Part Pages
```

### 2. Repair Scraper (`scrapers/repair_scraper.py`)

This scraper focuses on PartSelect's troubleshooting content - the "Repair Help" section that guides customers through diagnosing problems.

- **Symptoms** - Common problems like "Ice maker not making ice" or "Refrigerator is noisy"
- **Diagnostic flow** - Which parts to check for each symptom
- **Step-by-step instructions** - How to test each potentially faulty part
- **Video links** - YouTube tutorials for repairs

**Why separate scrapers?**

The data lives in completely different sections of the site with different page structures. More importantly, the information serves different purposes:

- **Parts data** answers: "Tell me about this specific part" or "Does X fit my model?"
- **Repair data** answers: "My ice maker isn't working - what should I check?"

Separating the scrapers keeps each one focused and maintainable. When PartSelect changes their repair page layout (which happens), we only need to update one file without touching the part scraper.

---

## How the Part Scraper Works

Let's walk through what happens when you run `python -m scrapers.run_scraper refrigerator`:

### Step 1: Get Brand Links

The scraper starts at the main appliance page (e.g., `partselect.com/Refrigerator-Parts.htm`) and collects all brand links:

```python
def get_brand_links(driver, base_url):
    brand_links = []
    driver.get(base_url)

    ul_tags = driver.find_elements(By.CLASS_NAME, "nf__links")
    for li_tag in ul_tags[0].find_elements(By.TAG_NAME, "li"):
        a_tag = li_tag.find_element(By.TAG_NAME, "a")
        link_url = a_tag.get_attribute("href")
        brand_links.append(link_url)

    return brand_links
```

This gives us links like:
- `partselect.com/Refrigerator-Parts/Whirlpool.htm`
- `partselect.com/Refrigerator-Parts/Samsung.htm`
- `partselect.com/Refrigerator-Parts/LG.htm`
- ...and so on

### Step 2: Process Each Brand

For each brand, we:
1. Visit the brand page
2. Scrape all parts on that page
3. Find "Related Parts" links (subcategories like Ice Makers, Filters, etc.)
4. Scrape each related category page

This captures parts that might only appear in subcategory pages.

### Step 3: Scrape Individual Part Pages

This is where the detailed extraction happens. For each part, we extract:

```python
# From the part page HTML
part_data = {
    "ps_number": "PS11752778",                    # Unique identifier
    "part_name": "Ice Maker Assembly",
    "part_type": "Ice Maker",                     # From breadcrumbs
    "manufacturer_part_number": "WPW10469286",
    "part_manufacturer": "Whirlpool",
    "part_price": "89.95",
    "install_difficulty": "Easy",
    "install_time": "15-30 mins",
    "install_video_url": "https://youtube.com/...",
    "average_rating": "4.7",
    "num_reviews": "127",
    # ...plus more fields
}
```

### Step 4: Handle the Compatibility Table

This is where things get interesting. PartSelect uses an infinite scroll table for model compatibility - the table only loads more rows as you scroll down. A single part can fit thousands of models, but the initial page load might only show 20.

We handle this with a scroll-and-wait loop:

```python
def scroll_infinite_container(driver, container_selector, row_selector, max_scrolls=50):
    container = driver.find_element(By.CSS_SELECTOR, container_selector)

    prev_count = 0
    for _ in range(max_scrolls):
        # Scroll to bottom
        driver.execute_script(
            "arguments[0].scrollTop = arguments[0].scrollHeight;",
            container
        )
        time.sleep(0.5)  # Wait for content to load

        rows = container.find_elements(By.CSS_SELECTOR, row_selector)
        if len(rows) == prev_count:
            break  # No new content, we're done
        prev_count = len(rows)

    return rows
```

We discovered parts like PS12728638 that fit over 6,000 models. Without infinite scroll handling, we'd have captured maybe 20 of those - completely breaking the compatibility check feature.

### Step 5: Extract Customer Content

For each part, we also scrape:

- **Q&A** - Customer questions and expert answers
- **Repair Stories** - First-hand accounts of repairs ("My ice maker was clicking...")
- **Reviews** - Star ratings and written reviews

This content feeds into the vector database for semantic search. When a customer asks "is this part easy to install?", we can find relevant reviews and Q&A entries that discuss installation.

We only grab the first page of each content type (roughly 10 items). The reasoning:

1. **Diminishing returns** - The first page is sorted by "Most Helpful", so it contains the highest-quality content
2. **Pagination overhead** - Each additional page requires another network request and DOM parsing
3. **Good enough** - 10 high-quality Q&A entries per part is plenty for semantic search

---

## Rate Limiting and Being a Good Citizen

Web scraping exists in a gray area. We're accessing public data, but we need to be respectful of the site's resources. Our approach:

### Configurable Delays

```python
SCRAPER_SETTINGS = {
    "delay_between_pages": (0.5, 1),      # Random 0.5-1s between page loads
    "delay_between_brands": (1, 2),       # Random 1-2s between brands
    "delay_before_navigate": (0.3, 0.7),  # Small delay before each navigation
    "stagger_start_delay": (1, 2),        # Stagger parallel workers
}
```

Random delays help avoid triggering rate limits and make our traffic pattern look more natural.

### User Agent Rotation

We cycle through realistic desktop user agents:

```python
desktop_agents = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) ...",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ...",
    # ...more agents
]
```

We explicitly avoid mobile user agents because PartSelect's mobile site has a different HTML structure - scraping it would break our selectors.

### Why Not Just Scrape Faster?

We initially tried aggressive parallelization (10+ workers, minimal delays). This caused problems:

1. **Rate limiting** - PartSelect started returning 403 errors
2. **Incomplete pages** - Fast requests sometimes got partially-loaded HTML
3. **IP blocking risk** - Aggressive scraping can get your IP blacklisted

The slower, respectful approach turned out to be faster overall because we didn't have to re-scrape failed pages.

---

## Resilience: When Things Go Wrong

Web scraping is inherently fragile. Sites change their HTML, networks fail, and pages don't load correctly. We built several resilience mechanisms:

### Retry Logic

Every page load has automatic retries with exponential backoff:

```python
def process_brand_with_retry(brand_url, ..., max_retries=2):
    for attempt in range(max_retries):
        try:
            driver = setup_driver()
            # ... scraping logic ...
            return results
        except Exception as e:
            print(f"Attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(5 * (2 ** attempt))  # 5s, 10s, 20s...
```

### Incremental Saves

This is crucial. We don't wait until the scraper finishes to save data - we write to CSV files after every category page:

```python
parts, compatibility, qna, stories, reviews = process_category_page(driver, url)

# Save immediately
append_parts_data(parts, output_files["parts"])
append_model_compatibility_data(compatibility, output_files["compat"])
append_qna_data(qna, output_files["qna"])
# ...
```

Why this matters: A full scrape takes hours. If the scraper crashes at hour 3, we don't lose hours 1 and 2. We can resume from where we left off.

### Resume Capability

The `--resume` flag enables continuing from existing data:

```bash
python -m scrapers.run_scraper --resume refrigerator
```

This reads the existing `parts.csv`, extracts all PS numbers already scraped, and skips those parts:

```python
def get_scraped_part_ids(filename):
    scraped_ids = set()
    with open(filepath, 'r') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            scraped_ids.add(row['ps_number'])
    return scraped_ids
```

Combined with incremental saves, this means we can stop and restart the scraper at any time without losing progress.

### Thread-Safe File Writing

When running with multiple parallel workers, we need to prevent file corruption from concurrent writes:

```python
_file_locks = {}

def _get_file_lock(filepath):
    with _lock_lock:
        if filepath not in _file_locks:
            _file_locks[filepath] = threading.Lock()
        return _file_locks[filepath]

def append_to_csv(data, filename, schema):
    file_lock = _get_file_lock(str(filepath))

    with file_lock:
        with open(filepath, 'a', newline='') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=schema)
            writer.writerows(data)
```

Each file has its own lock, so workers can write to different files simultaneously, but writes to the same file are serialized.

---

## Parallelization Strategy

Scraping thousands of parts sequentially would take forever. We use parallelization, but carefully:

### Brand-Level Parallelism

We parallelize at the brand level, not the page level:

```python
max_workers = 10  # Process 10 brands simultaneously

with ThreadPoolExecutor(max_workers=max_workers) as executor:
    futures = {
        executor.submit(process_brand_worker, (idx, url)): url
        for idx, url in enumerate(brand_links)
    }
```

**Why brand-level?**

1. **Natural isolation** - Each brand's pages are independent
2. **Manageable scope** - A single brand typically has 50-200 parts
3. **Easy load balancing** - Some brands have more parts than others, so brand-level parallelism naturally balances work

### Staggered Starts

Even with brand-level parallelism, starting 10 workers simultaneously would hammer the server. We stagger the starts:

```python
def process_brand_worker(args):
    idx, brand_url = args

    if idx > 0:
        delay = random.uniform(1, 2)  # Random 1-2 second delay
        time.sleep(delay)

    # Now proceed with scraping
```

This spreads out the initial burst of requests.

---

## The Repair Scraper: A Different Beast

The repair scraper has a simpler structure because the repair help section is organized differently:

```
Appliance Type (Refrigerator)
    └── Symptom Page (Ice maker not making ice)
        └── Part Sections (Water Inlet Valve, Ice Maker Assembly, ...)
```

### What We Extract

For each symptom:
```python
symptom = {
    "appliance_type": "refrigerator",
    "symptom": "Ice maker not making ice",
    "percentage": "29%",  # % of customers reporting this
    "video_url": "https://youtube.com/...",
    "difficulty": "MODERATE",
    "parts": "Water Fill Tubes, Water Inlet Valve, Ice Maker Assembly"
}
```

For each part mentioned in the symptom:
```python
instruction = {
    "appliance_type": "refrigerator",
    "symptom": "Ice maker not making ice",
    "part_type": "Water Inlet Valve",
    "instructions": "The water inlet valve opens to supply water to the ice maker...",
}
```

### HTML to Text Conversion

The repair instructions come as HTML with lists, paragraphs, and formatting. We convert to clean text:

```python
def _html_to_text(html):
    # Remove script and style elements
    html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)

    # Convert list items to bullet points
    html = re.sub(r'<li[^>]*>(.*?)</li>', '\n- \1', html, flags=re.DOTALL)

    # Convert breaks and paragraphs to newlines
    html = re.sub(r'<br\s*/?>', '\n', html)
    html = re.sub(r'</p>', '\n', html)

    # Remove remaining tags
    html = re.sub(r'<[^>]+>', '', html)

    # Decode HTML entities
    html = unescape(html)

    return html.strip()
```

This preserves the structure of instructions while removing HTML noise.

---

## Data Flow: Scraper to Database

The scraper outputs CSV files. A separate loader (`database/load_data.py`) handles database insertion:

```
Scraper → CSV Files → Loader → Supabase

┌─────────────────────┐     ┌─────────────────────┐     ┌─────────────────────┐
│  Part Scraper       │────▶│  data/parts.csv     │────▶│  parts table        │
│  (Selenium)         │     │  data/qna.csv       │     │  qna_embeddings     │
│                     │     │  data/reviews.csv   │     │  reviews_embeddings │
│                     │     │  data/compat.csv    │     │  model_compatibility│
└─────────────────────┘     └─────────────────────┘     └─────────────────────┘
```

**Why CSV as an intermediate format?**

1. **Inspection** - We can review scraped data before loading
2. **Debugging** - Easy to spot issues like missing fields or malformed data
3. **Decoupling** - Scraper doesn't need database credentials
4. **Reproducibility** - Same CSVs can be loaded multiple times

The loader generates vector embeddings during insertion (not during scraping). This keeps the scraper focused on data extraction and avoids embedding model dependencies in the scraper code.

---

## Configuration and Extensibility

### Adding a New Appliance Type

The config makes adding new appliance types straightforward:

```python
APPLIANCE_CONFIGS = {
    "refrigerator": {
        "base_url": "https://www.partselect.com/Refrigerator-Parts.htm",
        "related_section_pattern": "Refrigerator Parts"
    },
    "dishwasher": {
        "base_url": "https://www.partselect.com/Dishwasher-Parts.htm",
        "related_section_pattern": "Dishwasher Parts"
    },
    # To add microwave:
    # "microwave": {
    #     "base_url": "https://www.partselect.com/Microwave-Parts.htm",
    #     "related_section_pattern": "Microwave Parts"
    # }
}
```

All data flows into the same output files with an `appliance_type` column to distinguish them.

### Tuning Performance

The scraper settings are all configurable:

```python
SCRAPER_SETTINGS = {
    "max_workers": 10,              # Parallel workers
    "max_retries": 2,               # Retry attempts
    "page_load_timeout": 20,        # Selenium timeout
    "delay_between_pages": (0.5, 1),  # Random delay range
}
```

For testing or gentle scraping, you can reduce parallelism and increase delays. For faster scraping (at your own risk), you can do the opposite.

### Schema Definitions

Each data type has a defined schema:

```python
PARTS_SCHEMA = [
    "ps_number",
    "part_name",
    "part_type",
    "manufacturer_part_number",
    # ...
]

QNA_SCHEMA = [
    "ps_number",
    "question_id",
    "asker",
    "question",
    "answer",
    # ...
]
```

These schemas ensure consistent CSV output and make it easy to add new fields when needed.

---

## What We'd Do Differently

### Things That Worked Well

1. **Incremental saves + resume** - Saved us countless hours when the scraper crashed
2. **Brand-level parallelism** - Good balance of speed and stability
3. **Separate part/repair scrapers** - Kept the code maintainable
4. **Schema-driven CSV output** - Consistent data format

### Things We'd Reconsider

1. **Selenium dependency** - It's heavy and slow. For a production system, we'd explore lighter alternatives like Playwright or even direct HTTP requests with a headless approach.

2. **First-page-only content extraction** - We only grab the first page of Q&A, reviews, and repair stories. For some popular parts, there are hundreds of Q&A entries. We might be missing valuable content.

3. **No change detection** - We have no way to know when PartSelect updates a part's information. In production, we'd want differential scraping that only re-scrapes changed pages.

4. **Date parsing** - We store dates as strings ("December 25, 2024"). Proper date parsing would enable time-based analysis.

5. **Error categorization** - Currently, all errors are treated the same. Distinguishing between "page doesn't exist" and "temporary network failure" would improve retry logic.

---

## Testing and Development Tools

The `scrapers/dev/` directory contains helper tools:

- **`test_single_page.py`** - Test scraping logic on a single part page
- **`test_repair_scraper.py`** - Test repair scraper on one symptom
- **`diagnose_selectors.py`** - Debug CSS selectors when they break
- **`debug_page.py`** - Dump raw HTML for manual inspection

These tools are invaluable when PartSelect changes their HTML. Instead of running the full scraper and waiting for it to fail, you can test specific selectors quickly.

Example usage:
```bash
# Test scraping a specific part
python -m scrapers.dev.test_single_page PS11752778

# Test repair scraper on refrigerator symptoms
python -m scrapers.dev.test_repair_scraper refrigerator
```

---

## Scalability Considerations

### Current Scale

| Data Type | Approximate Count |
|-----------|------------------|
| Parts | ~2,000 |
| Compatibility records | ~500,000 |
| Q&A entries | ~10,000 |
| Repair stories | ~5,000 |
| Reviews | ~15,000 |

### Bottlenecks at Larger Scale

1. **Scraping time** - At 10 workers with ~1 second per page, 10,000 parts takes roughly 17 minutes. 100,000 parts would take nearly 3 hours.

2. **CSV file size** - Model compatibility already has 500K rows. At millions of rows, CSV handling becomes slow.

3. **Memory usage** - Selenium keeps browser instances in memory. 10 parallel workers use significant RAM.

### Scaling Strategies

**Short-term:**
- Increase parallelism during off-peak hours
- Add more aggressive caching for unchanged pages
- Compress CSV files between runs

**Medium-term:**
- Switch to a streaming database loader instead of CSV intermediate
- Implement differential scraping (only re-scrape changed content)
- Move to a lighter browser automation tool

**Long-term:**
- Distributed scraping across multiple machines
- Real-time change detection via RSS or sitemap monitoring
- Direct partnership with PartSelect for data access

---

## Legal and Ethical Considerations

Web scraping raises legal and ethical questions. Our approach:

1. **Public data only** - We only scrape publicly accessible pages, no login required
2. **Robots.txt** - We respect PartSelect's robots.txt directives
3. **Rate limiting** - Our delays prevent server overload
4. **No personal data** - We don't scrape customer personal information (names on reviews are public)
5. **Internal use** - The scraped data is for our chat agent, not for resale

For a production deployment, you'd want legal review of terms of service and potentially reach out to PartSelect about data access.

---

## Summary

The scraping system is designed around a few core principles:

1. **Resilience over speed** - Retries, incremental saves, and resume capability mean we never lose progress
2. **Respect for the source** - Rate limiting and realistic user agents keep us from being blocked
3. **Separation of concerns** - Part scraper and repair scraper are independent, and scraping is decoupled from database loading
4. **Flexibility** - Configuration-driven design makes it easy to add appliance types or tune performance

The result is a system that reliably collects comprehensive appliance parts data - the foundation that makes our chat agent useful. When a customer asks "does this ice maker fit my model?", we can answer confidently because we've scraped and stored all 6,000+ compatible models, not just the first 20 that appear on initial page load.
