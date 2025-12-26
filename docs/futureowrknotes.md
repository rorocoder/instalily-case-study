Reviews and stoies may update far more often than anything else. think about scraping for those more often and not having to replace the whole database whne that happens. 

reeviews and blogs may not be super necessary for this but woudl like to add them

last seen timestamp. update this

Way to just add data instead of having to rescrape everything. just checking for changes? 

state to have multiple models instaed of just multiople parts

searching for models? the scope seems to suggest that we should really only be dealing with parts, so i think i shouldnt scrape for model data. but this can be worth adding since the only info we have on this is compatability 

fallback scrapes (actual product lookups)

tools for blogs. tools for reviews. 

ability to easily add new domains (eg. adding microwsaves)

scoping done automatically and not hard coded (maybe can put in our system prompt)

Second biggest improvement: add a Verifier node
You currently rely on the Synthesizer to be correct. 
AGENT_PLAN
In commerce, the two biggest “bad” failures are:
claiming compatibility without evidence
making up a part number / price / availability

what happens if tasks cant be run in paralle. we need the result of previous runs to do the next ones. 

DEF SHOULD BE ABLE TO SEARCH BY OUR MANUFACTURER ID INSTEAD OF PS ID


testing:
- find parts by different identifiers (description, manufacturer, compatability?)
- see what happens when we ask queries about non-existing parts



right now we are only scraping relevant parts, what if theres data thats not relevant, have to do more work to separate out domain concerns. 

scrape blogs
~~add review tools~~ ✅ COMPLETED (Dec 25, 2025)
- Added `search_reviews()` to EXECUTOR_PROMPT (Pattern 1b, Rule 7)
- Added review presentation guidelines to SYNTHESIZER_PROMPT (Guideline 8a, 16)
- Tool was already implemented in vector_tools.py but not exposed in prompts


issue with separating out part types in the symptoms from searching for parts themesleves. 

unclear if you wanted me to deal with models or if you wanted me to deal with carts and transaction stuff

mention not paginating through thigns like reviews, qna, and stories

~~issues with the scale of compatability~~ ✅ RESOLVED (Dec 25, 2025)
- **Problem**: PS11752778 has 2,220 compatible models (not 1,170 - that was old data)
  - Worst case: PS12728638 has 6,120 compatible models
  - Initial limits: 20 parts / 50 models (way too low, missing 95%+ of data)
  - Supabase has 1,000 row limit per query
- **Solution**: Implemented pagination in `get_compatible_models()`
  - Fetches results in batches of 1,000 using `.range(offset, offset+999)`
  - Loops until all results retrieved (up to limit of 5,000)
  - Now returns ALL brands (was only showing Kenmore, missing Whirlpool/Maytag)
- **Final limits**: 200 parts / 5,000 models (with pagination)
- **Token cost**: Acceptable - synthesizer summarizes large lists, doesn't dump all models
  - Guideline 16 instructs: "If 50+ models, group by brand and show count"
  - Example: "Fits 2,220 models including Kenmore (2,004), Whirlpool (215), Maytag (1)" 

using sitemap to scrape instead. also i think sitemap listed when things were last updated which is very useful for us. 

robots.txt

Mention the issues with scraping all data given the time frame i have. (maybe worth doing fallbacsk in that case?)

improvements to load data


out of scope AFTER searching for part (finding microwave part eg)