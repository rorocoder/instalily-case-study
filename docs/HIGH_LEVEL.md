# High Level 

Preliminary high level design for thinking aboout our overall flow

As a reminder we are hoping to keep this extensible and scalable. 

We are judged on: 
- agentic architecture
- extensibility + scalability
- answering accurately and effectively

We must stay in this scope: 

- providing product info 
- assist with customer transactions
- only working with refrigerators and dishwashers for now

## Database Table

part_name
ps_number
manufacturer_part_number
part_manufacturer
part_price
part_description
install_difficulty
install_time
symptoms
appliance_types
replace_parts
brand
availability
install_video_url
part_url
average_rating
num_reviews
model_cross_reference   a list of model numbers? 

we have a cross reference table on the page which tells us which models the part works with. should we store this as a field on this table or make a new one? each row in the table on the page contains: Brand, model number, and a description 

Keep in mind that models are separate from parts


## Vector Databases

There are semantic level things that we may want to store in a vector database. We will have to link each of these to the product ids? 

There's a troubleshooting section which has things like what products it works with (dishwasher, refridgerator, etc). also: Part# WPW10503548 replaces these:
AP6022403, W10331789, W10503548, WPW10503548VP

also: This part fixes the following symptoms:
Door won’t open or close
Ice maker won’t dispense ice
Leaking

Its also got reviews. 

Its also got customer repair stories where they explain how they fixed certain things. 

Its also got a Q&A section. 

All that was on the part page. Here's some general info we may need to store to answer questions like: 

The ice maker on my Whirlpool fridge is not working. How can I fix it?

There's a blog page with a lot of tutorials and repair guides. Its a bunch of article cards with titles and a short preview blurb under.  

Then there's a repair help page. That page asks you to select an appliance type (dishwasher, fridge, dryer, microwave, etc) and then once you do you are taken to a list of common problem descriptions (noisy, leaking, will not start, ice maker not making ice) with a percetnage for how many customers deal with that specific issue: 

REPAIR > REFRIGERATOR
How To Repair A Broken Refrigerator
Refrigerator
If you are having problems with your refrigerator and need help troubleshooting what the issue could be, you’ve come to the right spot. Below you will find all of our fridge-related troubleshooting videos, and we also list all of the common symptoms refrigerators experience, like not making ice or being too noisy. Select the symptom your fridge is having and you’ll learn about the parts that can fix this issue.
Want Mr. Appliance to fix that broken Refrigerator?
Schedule Service
Common Broken Refrigerator Symptoms
Reported By
Noisy
When your fridge is noisy, find out how to repair it by troubleshooting the location of the noise, from the evaporator fan motor in the freezer to the bottom of the fridge with the condenser fan motor.

  
29% of customers
Leaking
Diagnose the reason for your leaking fridge, from a faulty water inlet valve to a worn out door seal.

  
27% of customers
Will not start
Find out how to fix a fridge that will not start, by examining a few key parts such as the temperature control or the compressor overload relay.

  
18% of customers
Ice maker not making ice
Learn how to fix your ice maker when it's not making ice and inspect the water fill tubes, water inlet valve and the icemaker.

  
6% of customers
Fridge too warm
If your fridge is, too warm then troubleshooting common parts like the air inlet damper.

Theres a description of it too. when you click on it it takes you to a page with a youtube video. then it has parts that you can seelct to see where you need to start your repair. it gives you the difficutly of the repair. how many repair storeis there are, and how many step by step videos there are. Each of the parts sections on the page contains a step by step instructions for how to go about inspecting that part to see if it needs a replacement. 

## Flow

Query comes in: 

1. Check if in scope. If yes, proceed. If no, return back to query
2. Use a router agent to analyze the intent of the query and route to agents/specialists

Classify into one intent: 

- PRODUCT_LOOKUP (price, availability, specs, replacements)
- COMPATIBILITY (model ↔ part)
- INSTALL_GUIDE (how to install PS…)
- TROUBLESHOOT (symptom → diagnose → likely parts)
- SEARCH_RECOMMEND (find the right part)
- ORDER_SUPPORT (status/returns; can be stubbed)
- CLARIFY
- Get reviews 
- etc. (we can think of more)
- Get repair advice
- Lookup symptom repairs

these intents may have to be refined

extract entities like model number, brand, symptom

3. Plan builder (chooses tools + asks for missing info)

Planner
Given (intent, entities, session_state) decide:
What info is missing?
Which tools to call (SQL vs vector vs scrape fallback)?
Whether to ask user a clarifying question vs proceed.
Example rules:
- COMPATIBILITY needs both model_number and part_number (or a selected part). If missing → ask for it.

- INSTALL_GUIDE needs part_number → if missing, run SEARCH_RECOMMEND first.

- TROUBLESHOOT needs appliance_type and brand ideally → if missing ask 1 question, otherwise proceed.

Output: a small “plan” object:
- actions: [tool calls…]
- questions_to_user?: []


## Query Types 

Tell me details about this product?

Is my part compatible with this model? 

How can i fix my ice repair? 

## Tools

- get product details
- get compatability

## Multi-Agent System
1. **Router Agent** - Analyzes intent and routes to specialists
2. **Product Search Agent** - Finds parts using SQL queries
3. **Compatibility Agent** - Verifies model-part compatibility
4. **Troubleshooting Agent** - Diagnoses problems using vector search
5. **Installation Agent** - Provides installation guidance using vector search


