"""
FastAPI application with SSE streaming for the PartSelect chat agent.

Endpoints:
- POST /chat - Non-streaming chat endpoint
- POST /chat/stream - SSE streaming endpoint
- GET /health - Health check
"""
import json
import uuid
from typing import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from backend.config import get_settings

# V2 Agent - simplified architecture
from backend.agent_v2 import run_agent, run_agent_streaming, SessionState, Message

# To switch back to V1, comment above and uncomment below:
# from backend.agent import run_agent, run_agent_streaming, SessionState
# from backend.agent.state import Message



# Request/Response models
class ChatRequest(BaseModel):
    """Chat request body."""
    message: str = Field(..., min_length=1, max_length=2000)
    session_id: str | None = None
    session_state: dict | None = None


class PartCard(BaseModel):
    """Part card data for visual display."""
    ps_number: str
    part_name: str
    manufacturer_part_number: str | None = None
    part_price: float
    average_rating: float | None = None
    num_reviews: int | None = None
    brand: str
    availability: str
    part_url: str
    image_url: str | None = None


class ChatResponse(BaseModel):
    """Chat response body."""
    message: str
    session_id: str
    session_state: dict
    parts: list[PartCard] = []


# In-memory session storage (replace with Redis/DB for production)
sessions: dict[str, SessionState] = {}


def get_or_create_session(session_id: str | None, session_state: dict | None) -> tuple[str, SessionState]:
    """Get existing session or create new one."""
    if session_id and session_id in sessions:
        return session_id, sessions[session_id]

    if session_state:
        # Restore from provided state
        try:
            session = SessionState(**session_state)
            new_id = session_id or str(uuid.uuid4())
            sessions[new_id] = session
            return new_id, session
        except Exception:
            pass

    # Create new session
    new_id = session_id or str(uuid.uuid4())
    sessions[new_id] = SessionState()
    return new_id, sessions[new_id]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Startup
    settings = get_settings()
    missing = settings.validate()
    if missing:
        print(f"WARNING: Missing environment variables: {missing}")
        print("The agent will not function properly without these.")
    else:
        print("Configuration validated successfully")

    yield

    # Shutdown
    sessions.clear()


# Create FastAPI app
app = FastAPI(
    title="PartSelect Chat Agent",
    description="Multi-agent chat system for refrigerator and dishwasher parts",
    version="1.0.0",
    lifespan=lifespan,
)

# Configure CORS - allow all origins in development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,  # Must be False when using allow_origins=["*"]
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"],
)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    settings = get_settings()
    missing = settings.validate()

    return {
        "status": "healthy" if not missing else "degraded",
        "missing_config": missing,
    }


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    Non-streaming chat endpoint.

    Returns complete response after all processing is done.
    """
    try:
        session_id, session = get_or_create_session(
            request.session_id,
            request.session_state
        )

        response, updated_session, parts = await run_agent(
            query=request.message,
            session=session
        )

        # Accumulate conversation history
        updated_session.conversation_history.append(
            Message(role="user", content=request.message)
        )
        updated_session.conversation_history.append(
            Message(role="assistant", content=response)
        )
        # Keep last 10 messages (5 exchanges)
        updated_session.conversation_history = updated_session.conversation_history[-10:]

        # Update stored session
        sessions[session_id] = updated_session

        # Convert parts dicts to PartCard models
        part_cards = [PartCard(**p) for p in parts] if parts else []

        return ChatResponse(
            message=response,
            session_id=session_id,
            session_state=updated_session.model_dump(),
            parts=part_cards
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def generate_sse_events(
    query: str,
    session: SessionState,
    session_id: str
) -> AsyncGenerator[dict, None]:
    """Generate SSE events for streaming response."""
    try:
        full_response = ""
        # Container to receive updated session from streaming
        session_container = {}

        async for token in run_agent_streaming(
            query=query,
            session=session,
            session_container=session_container
        ):
            full_response += token
            yield {
                "event": "token",
                "data": json.dumps({"token": token})
            }

        # Get updated session (or fall back to original)
        updated_session = session_container.get("session", session)
        parts = session_container.get("parts", [])

        # Accumulate conversation history
        updated_session.conversation_history.append(
            Message(role="user", content=query)
        )
        updated_session.conversation_history.append(
            Message(role="assistant", content=full_response)
        )
        # Keep last 10 messages (5 exchanges)
        updated_session.conversation_history = updated_session.conversation_history[-10:]

        # Update stored session
        sessions[session_id] = updated_session

        # Send completion event with full response and updated session
        yield {
            "event": "done",
            "data": json.dumps({
                "message": full_response,
                "session_id": session_id,
                "session_state": updated_session.model_dump(),
                "parts": parts
            })
        }

    except Exception as e:
        yield {
            "event": "error",
            "data": json.dumps({"error": str(e)})
        }


@app.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    """
    SSE streaming chat endpoint.

    Streams tokens as they're generated, then sends completion event.

    Events:
    - token: {"token": "..."}
    - done: {"message": "...", "session_id": "...", "session_state": {...}}
    - error: {"error": "..."}
    """
    session_id, session = get_or_create_session(
        request.session_id,
        request.session_state
    )

    return EventSourceResponse(
        generate_sse_events(request.message, session, session_id)
    )


# Alternative streaming endpoint using regular StreamingResponse
# (for clients that don't support SSE)
@app.post("/chat/stream-simple")
async def chat_stream_simple(request: ChatRequest):
    """
    Simple streaming endpoint using chunked transfer.

    Streams tokens as plain text, separated by newlines.
    Final line is JSON with session info.
    """
    session_id, session = get_or_create_session(
        request.session_id,
        request.session_state
    )

    async def generate():
        full_response = ""
        session_container = {}
        try:
            async for token in run_agent_streaming(
                query=request.message,
                session=session,
                session_container=session_container
            ):
                full_response += token
                yield token

            # Get updated session
            updated_session = session_container.get("session", session)

            # Accumulate conversation history
            updated_session.conversation_history.append(
                Message(role="user", content=request.message)
            )
            updated_session.conversation_history.append(
                Message(role="assistant", content=full_response)
            )
            # Keep last 10 messages (5 exchanges)
            updated_session.conversation_history = updated_session.conversation_history[-10:]

            sessions[session_id] = updated_session

            # Final metadata as JSON on last line
            yield f"\n\n---METADATA---\n{json.dumps({'session_id': session_id, 'session_state': updated_session.model_dump()})}"

        except Exception as e:
            yield f"\n\n---ERROR---\n{str(e)}"

    return StreamingResponse(
        generate(),
        media_type="text/plain",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "backend.main:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        reload=True,
    )
