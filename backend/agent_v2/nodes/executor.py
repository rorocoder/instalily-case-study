"""
Executor Node for agent_v2.

Single ReAct executor that handles all query types.
The LLM decides which tools to call and in what order.
"""
import json
from langchain_anthropic import ChatAnthropic
from langgraph.prebuilt import create_react_agent
from backend.config import get_settings
from backend.agent_v2.tools import get_all_tools
from backend.agent_v2.prompts import format_executor_prompt
from backend.agent_v2.state import AgentState
from backend.agent_v2.session import update_session_from_tool_results


def _log_tool_calls(messages: list) -> None:
    """Log detailed tool call information."""
    # Build a map of tool_call_id -> (tool_name, args)
    tool_call_map = {}
    for msg in messages:
        if hasattr(msg, 'tool_calls') and msg.tool_calls:
            for tc in msg.tool_calls:
                tool_call_map[tc.get('id', '')] = {
                    'name': tc.get('name', ''),
                    'args': tc.get('args', {})
                }

    # Count and log tool calls
    tool_count = 0
    for msg in messages:
        if hasattr(msg, 'type') and msg.type == 'tool':
            tool_count += 1
            tool_name = getattr(msg, 'name', 'unknown')
            tool_call_id = getattr(msg, 'tool_call_id', '')

            # Get the args from our map
            call_info = tool_call_map.get(tool_call_id, {})
            args = call_info.get('args', {})

            # Format args for display
            args_str = ', '.join(f"{k}={repr(v)[:50]}" for k, v in args.items())

            # Parse and summarize result
            content = msg.content
            if isinstance(content, str):
                try:
                    parsed = json.loads(content)
                    if isinstance(parsed, dict):
                        if parsed.get('error'):
                            result_summary = f"ERROR: {parsed['error'][:80]}"
                        elif parsed.get('resolved'):
                            result_summary = f"resolved -> {parsed.get('ps_number', 'N/A')}"
                        elif parsed.get('compatible') is not None:
                            result_summary = f"compatible={parsed['compatible']}"
                        elif parsed.get('ps_number'):
                            result_summary = f"part: {parsed.get('part_name', '')[:40]}"
                        else:
                            keys = list(parsed.keys())[:4]
                            result_summary = f"dict with keys: {keys}"
                    elif isinstance(parsed, list):
                        result_summary = f"list of {len(parsed)} items"
                    else:
                        result_summary = str(parsed)[:60]
                except json.JSONDecodeError:
                    result_summary = content[:60]
            else:
                result_summary = str(content)[:60]

            print(f"  [TOOL {tool_count}] {tool_name}({args_str})")
            print(f"           → {result_summary}")

    print(f"  [EXECUTOR] Made {tool_count} tool calls")


def format_session_context(state: AgentState) -> str:
    """Format session state for the executor prompt."""
    session = state.session
    parts = []

    # Include conversation history for context (CRITICAL for follow-up questions)
    if state.conversation_history:
        parts.append("## Recent Conversation")
        for msg in state.conversation_history[-6:]:  # Last 3 exchanges
            role = "User" if msg.role == "user" else "Assistant"
            content = msg.content[:300] + "..." if len(msg.content) > 300 else msg.content
            parts.append(f"**{role}:** {content}")

    # Recently discussed parts (for "this part" references)
    if session.all_discussed_parts:
        parts.append(f"\n## Recently Discussed Parts")
        parts.append(f"PS numbers: {', '.join(session.all_discussed_parts[-5:])}")
        parts.append(f"(Use these when user says 'this part', 'the first one', etc.)")

    return "\n".join(parts) if parts else "No prior context."


async def executor_node(state: AgentState) -> dict:
    """
    Single ReAct executor - handles all query types.

    The LLM decides which tools to call and in what order based on the
    workflow patterns in the prompt.
    """
    settings = get_settings()

    print(f"  [EXECUTOR] Query: {state.user_query[:50]}...")

    llm = ChatAnthropic(
        model=settings.HAIKU_MODEL,
        api_key=settings.ANTHROPIC_API_KEY,
        max_tokens=1024,
    )

    tools = get_all_tools()
    print(f"  [EXECUTOR] Loaded {len(tools)} tools")

    # Create the ReAct agent
    agent = create_react_agent(llm, tools)

    # Format the prompt with session context
    session_context = format_session_context(state)
    prompt = format_executor_prompt(state.user_query, session_context)

    # Run the agent
    result = await agent.ainvoke({"messages": [{"role": "user", "content": prompt}]})

    # Extract tool results and update session
    messages = result.get("messages", [])

    # Log detailed tool calls
    _log_tool_calls(messages)

    # === AUTOMATIC SCRAPE FALLBACK ===
    # When get_part returns "not found", auto-trigger scrape_part_live
    from langchain_core.messages import ToolMessage

    # Check if scrape was already called by the agent
    scrape_already_called = any(
        hasattr(msg, 'name') and msg.name == 'scrape_part_live'
        for msg in messages
        if hasattr(msg, 'type') and msg.type == 'tool'
    )

    if not scrape_already_called:
        for msg in messages:
            if hasattr(msg, 'type') and msg.type == 'tool':
                tool_name = getattr(msg, 'name', '')

                # Only trigger fallback for get_part errors
                if tool_name == 'get_part':
                    # Parse content (could be string or dict)
                    content = msg.content
                    if isinstance(content, str):
                        try:
                            data = json.loads(content)
                        except json.JSONDecodeError:
                            continue
                    else:
                        data = content

                    # Check for "not found" error
                    if isinstance(data, dict) and data.get('error'):
                        error_msg = data.get('error', '').lower()
                        ps_number = data.get('ps_number', '')

                        if 'not found' in error_msg and ps_number:
                            print(f"  [EXECUTOR] Part {ps_number} not in DB → triggering live scrape...")

                            # Get the scrape tool from tool map and invoke it
                            scrape_tool = None
                            for tool in tools:
                                if tool.name == 'scrape_part_live':
                                    scrape_tool = tool
                                    break

                            if scrape_tool:
                                # Execute live scrape using tool's invoke method
                                scraped_data = scrape_tool.invoke({"ps_number": ps_number})

                                if scraped_data.get('error'):
                                    print(f"  [EXECUTOR] Live scrape failed: {scraped_data['error']}")
                                else:
                                    print(f"  [EXECUTOR] Live scrape successful!")

                                    # Inject scraped data into messages for synthesizer
                                    scrape_msg = ToolMessage(
                                        content=json.dumps(scraped_data),
                                        tool_call_id=f"scrape_{ps_number}",
                                        name="scrape_part_live"
                                    )
                                    messages.append(scrape_msg)

                                    # Update result with new messages
                                    result["messages"] = messages
                                    print(f"  [EXECUTOR] Added scraped part to results")
                            else:
                                print(f"  [EXECUTOR] Warning: scrape_part_live tool not found")
    # === END FALLBACK ===

    # Update session from tool results
    updated_session = update_session_from_tool_results(state.session, messages)
    print(f"  [EXECUTOR] Discussed parts: {updated_session.all_discussed_parts}")

    return {
        "executor_result": result,
        "session": updated_session,
    }
