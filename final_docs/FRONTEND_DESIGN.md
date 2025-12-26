# PartSelect Chat Agent - Frontend Design Document

## Overview

This document covers the frontend implementation of the PartSelect chat agent - a React-based chat interface that helps customers find information about refrigerator and dishwasher parts. The frontend is intentionally minimal, prioritizing a clean user experience over feature complexity.

The goal was straightforward: build something that looks like it belongs on PartSelect's website, gets out of the user's way, and makes it easy to discover and purchase parts.

---

## The Problem We're Solving

Customers come to PartSelect with a broken appliance and usually need to:
1. Figure out which part is broken
2. Find that part in the catalog
3. Make sure it fits their specific model
4. Get enough confidence to buy it (installation difficulty, reviews, etc.)

A traditional e-commerce browse/search experience works, but it puts the burden on the customer to know what they're looking for. A chat interface flips this - the customer describes their problem, and the agent helps them navigate to the right part.

The frontend's job is to make this conversation feel natural while keeping the customer connected to the actual PartSelect catalog.

---

## Why the Frontend is Intentionally Simple

Let's be direct: the frontend is not where the complexity of this project lives.

The case study evaluation criteria mentions "design of your interface" alongside "agentic architecture, extensibility and scalability, and ability to answer user queries accurately." The first is table stakes; the latter three are where the real engineering challenges are.

Consider what makes this project hard:
- **Agent architecture**: Designing a ReAct agent that can figure out which tools to call, handle multi-step queries, and gracefully fall back to live scraping when data isn't in the database
- **Data layer**: Building a hybrid SQL + vector database that handles exact lookups ("does this part fit my model?") and semantic search ("what do people say about installation?")
- **Query accuracy**: Handling the variety of ways customers ask questions - PS numbers, manufacturer numbers, URLs, symptoms, model numbers with typos
- **Scope enforcement**: Rejecting off-topic queries without being overly restrictive, including a secondary scope check that catches out-of-scope parts discovered mid-execution

None of that is frontend work. The frontend's job is to display what the agent returns and make it easy to act on.

This is why we didn't reach for Next.js, didn't set up a design system, didn't add state management libraries. Those tools solve problems we don't have. A chat interface needs:
1. A message list that scrolls
2. An input field that sends
3. Cards that display product info
4. A loading state while the backend thinks

React with plain CSS handles all of that cleanly. Adding more would be complexity theater - looking sophisticated without solving real problems.

The skills the frontend demonstrates are straightforward:
- Clean component structure (three components, clear responsibilities)
- Proper async handling (loading states, error handling)
- Responsive design (three breakpoints, works on mobile)
- Brand alignment (PartSelect colors, professional appearance)
- Good UX instincts (thinking timer, part cards, welcome message)

That's it. The frontend is a competent presentation layer for an interesting backend. The interesting part is the agent.

---

## Technology Choices

### React (Create React App)

We went with React via Create React App. It's not the most exciting choice, but for a chat interface that's primarily displaying text and cards, it's more than sufficient. The built-in tooling (hot reload, build pipeline, test setup) just works without configuration overhead.

**Why not Next.js?** The case study mentioned Next.js as an option, but we didn't need server-side rendering or file-based routing. This is a single-page application with one main view. Adding Next.js would have been complexity without benefit.

**Why not a more minimal approach?** We considered plain HTML/JS, but React's component model makes it easy to encapsulate the PartCard rendering logic, manage conversation state, and handle the async API flow cleanly.

### Plain CSS (No Framework)

We chose plain CSS over frameworks like Tailwind or styled-components. The styling needs were modest - a fixed header, a message list, an input area, and some cards. Component-scoped CSS files (ChatWindow.css, PartCard.css) keep things organized without the cognitive overhead of a CSS-in-JS solution.

The tradeoff is obvious: no utility classes, more verbose selectors, manual responsive breakpoints. For a project this size, we felt the simplicity was worth it.

### Chrome Extension Manifest (Side Panel)

An interesting deployment decision: the frontend is packaged as a Chrome extension that appears in the browser's side panel. The `manifest.json` in `/public` configures this.

**Why a side panel?** The idea was that customers could browse PartSelect.com in the main browser window while chatting with the assistant in the side panel. They wouldn't need to switch tabs or lose context. The assistant could theoretically even be aware of what page they're on (though we didn't implement that).

This isn't the only way to deploy it - the same React app works fine as a standalone webpage - but the side panel concept fits the "assistant while you shop" use case nicely.

---

## Component Architecture

The frontend has exactly three React components. This wasn't minimalism for minimalism's sake - it's all that's needed.

```
src/
├── App.js              # Header + container
├── components/
│   ├── ChatWindow.js   # The entire chat experience
│   └── PartCard.js     # Individual part display
└── api/
    └── api.js          # Backend communication
```

### App.js - The Shell

`App` renders a fixed header and the `ChatWindow`. That's it.

The header matches PartSelect's branding:
- Full logo on the left (links to PartSelect.com)
- Mobile logo + "Assistant" text in the center (orange `#ec951a` accent)
- Phone number and hours on the right

The header stays fixed at the top (100px height), giving consistent branding without eating into chat space on scroll. The contact info is a real link, so if someone wants human support, they can click through.

### ChatWindow.js - Where Everything Happens

This is the meat of the frontend. It handles:

**Message Display**
Messages are stored in a `messages` array state. Each message has a `role` (user or assistant) and `content` (the text). Assistant messages can also include `partCards` - structured product data the backend extracts from tool results.

The welcome message explains capabilities upfront. We experimented with showing an empty chat, but users didn't know what to ask. The welcome message sets expectations: "I can help with finding parts, compatibility checks, troubleshooting..."

**Markdown Rendering**
Assistant responses come as markdown (headings, bold, lists, links). We use the `marked` library to convert this to HTML. One custom tweak: all links open in new tabs. This prevents users from accidentally leaving the chat when clicking a PartSelect link.

```javascript
const renderer = new marked.Renderer();
renderer.link = (href, title, text) => {
  const html = originalLinkRenderer(href, title, text);
  return html.replace(/^<a /', '<a target="_blank" rel="noopener noreferrer" ');
};
```

**Thinking Indicator**
When the user sends a message, we show a "Thinking..." indicator with an elapsed time counter. This is important because backend responses can take 2-30 seconds (especially if live scraping kicks in). Without the timer, users would wonder if the app froze.

The timer starts at 0 and ticks every second. It's implemented with `useEffect` and `setInterval`, cleaned up properly when thinking completes.

**Auto-scroll**
New messages automatically scroll into view. We use a ref at the bottom of the message list and call `scrollIntoView({ behavior: "smooth" })` whenever `messages` or `isThinking` change.

**Input Handling**
Enter sends, Shift+Enter doesn't (for potential multiline, though we use a single-line input). The "New Chat" button resets both the UI state and the backend session.

### PartCard.js - Product Display

When the backend returns part data, we render it as clickable cards. Each card shows:
- Product image (more on this below)
- Part name and price
- Brand and part numbers (PS number + manufacturer number)
- Star rating with review count
- Stock status badge (green for "In Stock", red for "Out of Stock")

The entire card is a link to the PartSelect product page, so users can click through to buy.

**The Image Situation**
We don't have actual product images from PartSelect - that would require storing/serving thousands of photos or hot-linking (which has legal/ethical issues). Instead, we use five sample appliance part images and deterministically select one based on the PS number.

```javascript
const getRandomImage = () => {
  const hash = part.ps_number?.split('').reduce((acc, char) => acc + char.charCodeAt(0), 0) || 0;
  return samplePhotos[hash % samplePhotos.length];
};
```

The hash ensures the same part always shows the same image, preventing jarring changes if the same part appears multiple times in a conversation. It's a compromise - not ideal, but honest. A production system would integrate with PartSelect's actual product images.

---

## Styling Approach

### Color Palette

The color choices align with PartSelect's branding:

| Color | Hex | Usage |
|-------|-----|-------|
| Teal | `#337778` | User message bubbles, buttons, links, prices |
| Orange | `#ec951a` | "Assistant" header text, brand accent |
| Dark Gray | `#121212` | Header text |
| Light Gray | `#888` | Secondary text, timestamps |
| White | `#ffffff` | Background, assistant messages |

The teal provides visual cohesion with PartSelect's site while distinguishing user messages from assistant responses.

### Responsive Design

Three breakpoints handle different screen sizes:

```css
/* Desktop (default): 60% width, centered */
.messages-container { max-width: 60%; margin: 0 auto; }

/* Tablet: 80% width */
@media (max-width: 1024px) {
  .messages-container { max-width: 80%; }
}

/* Mobile: full width */
@media (max-width: 768px) {
  .messages-container { max-width: 100%; }
}
```

The same breakpoints apply to the input area and AI disclaimer. The result is a comfortable reading width on large screens without wasted space on smaller devices.

### Message Styling

User messages appear on the right with a teal background - the "speech bubble coming from you" convention. They have rounded corners except the top-right (indicating the bubble "points" toward the user label).

Assistant messages are left-aligned, transparent background, no bubble styling. This makes them feel less like "robot responses" and more like readable content. Lists, headings, and links render naturally without fighting with bubble constraints.

### Fixed Elements

Two elements are fixed-position:
1. **Header** - Always visible at top (100px)
2. **Input area** - Always visible at bottom (~50px)

The message container sits between them with appropriate margins (100px top, 90px bottom). This ensures the user can always see where to type and always has access to branding/contact info.

A subtle "AI responses may contain errors" disclaimer sits just above the input area. It links to PartSelect.com for verification - important for legal/trust reasons when AI is generating product advice.

---

## API Communication Layer

The `api.js` module handles all backend communication. It's intentionally simple - no Redux, no global state management, just module-level variables and exported functions.

### Session Management

```javascript
let currentSessionId = null;
let currentSessionState = null;
```

These persist across API calls within a page session. Each response from `/chat` includes updated session info, which we save and send back with the next request. This lets the backend maintain conversation context (which parts were discussed, what appliance type was mentioned, etc.).

The `resetSession()` function clears these, used when the user clicks "New Chat".

### Two Chat Modes

**Non-streaming (`getAIMessage`):**
The simpler approach. POST to `/chat`, wait for complete response, return the message object. This is what we currently use.

**Streaming (`getAIMessageStreaming`):**
POST to `/chat/stream`, get back Server-Sent Events (SSE) with tokens as they generate. The function takes callbacks for `onToken`, `onComplete`, and `onError`.

```javascript
// SSE parsing
if (line.startsWith("data:")) {
  const parsed = JSON.parse(data);
  if (parsed.token) {
    fullMessage += parsed.token;
    onToken(parsed.token);
  }
}
```

We implemented streaming support in the API layer but currently use non-streaming in the UI. The infrastructure is there for a future enhancement where responses "type out" in real-time.

### Error Handling

Errors return a graceful message rather than crashing:

```javascript
catch (error) {
  return {
    role: "assistant",
    content: `Sorry, I encountered an error: ${error.message}. Please try again.`,
  };
}
```

The user sees an assistant message explaining something went wrong, not a blank screen or JavaScript error.

### Health Check

`checkHealth()` pings `/health` to verify the backend is reachable and properly configured. Not currently used in the UI but useful for debugging.

---

## User Experience Decisions

### Welcome Message Over Empty State

The first thing users see is a welcome message listing what the assistant can do. This was a deliberate choice over showing an empty chat or a generic "How can I help?" prompt.

Reasoning: most users don't know what an AI assistant for appliance parts can actually do. Can it check orders? Can it help with returns? By being explicit upfront ("I can help with finding parts, compatibility checks, troubleshooting, installation guidance, reviews"), we set accurate expectations and give users concrete ideas of what to ask.

### Thinking Indicator with Timer

When the backend is processing, we show "Thinking..." with animated dots and an elapsed time counter. This serves several purposes:

1. **Acknowledgment** - The user knows their message was received
2. **Progress indication** - The timer shows something is happening, not frozen
3. **Expectation setting** - If they see 15s, 20s, 25s ticking up, they know this might take a while (especially for live scraping)

We considered a progress bar but couldn't accurately estimate completion time. A simple timer is honest about uncertainty.

### Part Cards, Not Just Text

When parts are mentioned, we render rich cards with price, rating, availability, and a direct link to purchase. This transforms the assistant from "information provider" to "shopping companion."

The cards appear below the relevant message text. If the assistant says "Here are three water filters that fit your model...", the three cards appear right after that paragraph. Users can scan the visual info and click through without parsing text for product names and prices.

### AI Disclaimer

"AI responses may contain errors. Always verify information on PartSelect.com"

This is fixed above the input area, always visible. It's small (11px) and gray, not intrusive, but present. For any product recommendations or compatibility claims, we want users to verify before purchasing. The link goes directly to PartSelect.com.

### New Chat Button

Prominent, always available. Some chat interfaces bury session reset in a menu. We made it a primary action next to Send because:
1. Users might want to start fresh if the conversation went off track
2. It's clearer that context doesn't persist across page reloads (there's no "continue previous conversation" feature)

---

## Tradeoffs and Design Decisions

### Why Not Streaming in the UI?

We built streaming support in the API layer but don't use it in `ChatWindow.js`. The current implementation waits for the full response, then renders it all at once.

**Why?** Part card extraction happens on the backend after the full response is generated. With streaming, we'd either need to:
1. Show cards only after streaming completes (awkward - text finishes, then cards pop in)
2. Have the backend stream card data separately (more complex protocol)
3. Extract cards on the frontend from partial text (fragile)

For now, non-streaming is simpler and the UX is fine. Average response time is 3-5 seconds for most queries. The thinking indicator bridges the wait.

**Future consideration:** For long responses (complex troubleshooting guides), streaming would improve perceived performance.

### Why Sample Images Instead of No Images?

We could have rendered cards without images - just text and metadata. We chose to include sample images because:
1. Visual scanning is faster - users can tell "this is a part card" at a glance
2. The cards feel more like product listings, matching what they'd see on PartSelect.com
3. It demonstrates where product images would go in a production integration

The tradeoff is that the images are generic and potentially misleading (a water filter might show a motor part photo). We mitigate this by making the image small and the text content prominent. In production, this would integrate with actual product imagery.

### Why Plain CSS Over Tailwind/Styled-Components?

For a project this size, plain CSS was faster to write and easier to reason about. We're styling maybe 15-20 elements across 3 components. The overhead of setting up Tailwind (config, purging, learning utility classes) or styled-components (runtime, template literals everywhere) didn't pay off.

The downside: no design system, no enforced consistency, manual media queries. If this scaled to 20+ components, we'd reconsider.

### Why No Chat History Persistence?

Refreshing the page loses the conversation. We could persist to localStorage or sync to the backend, but:
1. Sessions with the backend are tied to specific conversation context - resuming stale context could confuse the agent
2. Users coming to troubleshoot typically have a single session's worth of interaction
3. Adds complexity (what if localStorage has 50 old conversations?)

For a demo/case study, ephemeral sessions are fine. A production system might persist recent sessions with clear "continue this conversation" UX.

### Why No Message Editing/Deletion?

Once sent, messages can't be edited or deleted. Standard for chat UIs, but worth noting. The "New Chat" button is the escape valve for wrong turns.

---

## Scalability Considerations

### Frontend Scalability

The frontend itself is stateless and trivially scalable - it's just static files. Build it, host it on a CDN, done. No frontend server to scale.

React's virtual DOM handles message list updates efficiently. We tested with 100+ messages in a conversation and saw no performance degradation. The auto-scroll does trigger re-renders, but `scrollIntoView` is cheap.

If conversations got extremely long (1000+ messages), we'd consider virtualization (only rendering visible messages). Not needed for typical use.

### API Scalability

The API layer has no connection pooling or retry logic. Each request is independent. This is fine because:
1. Chat requests are inherently sequential (user sends, waits, sends again)
2. The backend handles rate limiting if needed
3. Error handling returns graceful messages rather than breaking the UI

For high-traffic production use, we'd add:
- Request timeouts with user feedback
- Retry with exponential backoff for transient failures
- Connection health monitoring

### State Management Scalability

We use React's built-in `useState` for everything. No Redux, no Context (beyond what React provides). This works because:
1. State is localized to `ChatWindow`
2. No need to share state across distant components
3. The data flow is simple: user input → API → message list

If we added features like multi-conversation tabs, user profiles, or real-time notifications, we'd need to reconsider. Context or a lightweight state manager (Zustand, Jotai) would make sense then.

---

## Extensibility

### Adding New Message Types

Currently we have text messages and part cards. To add new types (video embeds, image carousels, order status cards):

1. Extend the message object with a `type` field
2. Add rendering logic in `ChatWindow.js`:
   ```javascript
   {message.type === 'video' && <VideoEmbed url={message.videoUrl} />}
   ```
3. Create the new component
4. Update the backend to include the new type in responses

The architecture supports this cleanly - messages are already objects with flexible structure.

### Adding Order Support

The case study mentions order support as a potential feature. Frontend changes would include:
- Order status cards (order number, status, items, tracking link)
- Return initiation UI (reason selection, confirmation)
- Authentication flow (login to view orders)

The chat interface naturally supports this - these become new message types and potentially new input modes (uploading order receipts, entering order numbers).

### Multi-Language Support

All user-facing strings are currently hardcoded. For internationalization:
1. Extract strings to a localization file
2. Detect user locale or add language selector
3. Pass locale context to components
4. Backend would need to respond in the appropriate language

The UI layout is language-agnostic (no fixed-width assumptions), so RTL languages would need CSS adjustments but no structural changes.

---

## What's Not Included (Future Work)

### Streaming Response Display
The API supports it, the UI doesn't use it yet. Would improve perceived performance for long responses.

### Actual Product Images
Currently using sample images. Production would integrate with PartSelect's product image CDN.

### Conversation History
No persistence across page reloads. Would need localStorage + backend session reconciliation.

### Rich Input Methods
No image upload (for "what's this part?"), no voice input, no file attachment. Currently text-only.

### Accessibility Audit
Haven't done comprehensive a11y testing. Would need ARIA labels, keyboard navigation review, screen reader testing.

### Analytics
No tracking of user interactions, message send rates, click-through on part cards. Would inform product decisions.

### Offline Support
No service worker, no offline capability. Requires network connection to function.

### Mobile App
This is a web app (and Chrome extension). Native iOS/Android apps would share the API but need platform-specific UI.

---

## Summary

The PartSelect chat frontend is deliberately simple: React for components, plain CSS for styling, minimal dependencies. It renders a conversation, shows product cards, and connects to a backend API. That's it.

Key design decisions:
- **Welcome message** sets expectations upfront
- **Thinking indicator with timer** handles variable response times
- **Part cards** transform information into actionable product listings
- **Fixed header/input** keeps branding and interaction always visible
- **Chrome extension packaging** enables side-panel shopping companion UX

The frontend doesn't try to be clever. It displays what the backend returns, handles errors gracefully, and stays out of the way. The intelligence lives in the agent; the frontend's job is to make that intelligence accessible and actionable for customers trying to fix their broken appliances.
