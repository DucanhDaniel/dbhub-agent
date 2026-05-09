import os
import httpx
import asyncio
import json
import warnings
from typing import Any, Optional, Type
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.tools import BaseTool
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, Field
from dotenv import load_dotenv

warnings.filterwarnings("ignore")
load_dotenv()

MCP_URL = "http://localhost:5008/mcp"


def parse_sse_events(text: str) -> list[dict]:
    """
    Parse tất cả SSE events từ response.
    Mỗi event có dạng:
        event: message
        data: {...}
    Trả về list các dict đã parse.
    """
    events = []
    current_data = None

    for line in text.strip().splitlines():
        line = line.strip()
        if line.startswith("data:"):
            current_data = line[len("data:"):].strip()
        elif line == "" and current_data:
            try:
                events.append(json.loads(current_data))
            except json.JSONDecodeError:
                pass
            current_data = None

    # Flush last event nếu không có blank line cuối
    if current_data:
        try:
            events.append(json.loads(current_data))
        except json.JSONDecodeError:
            pass

    # Fallback: thử parse thẳng nếu không tìm thấy event nào
    if not events:
        try:
            events.append(json.loads(text))
        except json.JSONDecodeError:
            pass

    return events


def find_result_event(events: list[dict]) -> dict:
    """
    Lọc ra event có 'result' field (bỏ qua notifications).
    """
    for event in events:
        if "result" in event:
            return event
        # Bỏ qua notifications
        if "method" in event and "result" not in event:
            print(f"    [NOTIFICATION] {json.dumps(event)[:200]}")
    return {}


async def call_mcp(method: str, params: dict) -> dict:
    """Gọi MCP server, parse tất cả SSE events, trả về event có result."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        payload = {
            "jsonrpc": "2.0",
            "id": "1",
            "method": method,
            "params": params
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream"
        }
        response = await client.post(MCP_URL, json=payload, headers=headers)

        events = parse_sse_events(response.text)
        print(f"    [SSE] {len(events)} events received")

        result_event = find_result_event(events)
        if result_event:
            print(f"    [RESULT EVENT] {json.dumps(result_event)[:400]}")
        else:
            print(f"    [WARNING] No result event found! Raw: {response.text[:400]}")

        return result_event


def extract_text(result: dict) -> str:
    """Extract và unwrap text từ MCP result content blocks."""
    content = result.get("result", {}).get("content", [])
    all_texts = []

    for block in content:
        if not isinstance(block, dict):
            continue
        raw = block.get("text", "")
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
            all_texts.append(json.dumps(parsed, ensure_ascii=False, indent=2))
        except (json.JSONDecodeError, TypeError):
            all_texts.append(raw)

    return "\n".join(all_texts)


# ---------------------------------------------------------------------------
# LangChain Tools
# ---------------------------------------------------------------------------

class SearchToolsInput(BaseModel):
    query: str = Field(description="English keywords to search for available tools")


class CallToolInput(BaseModel):
    name: str = Field(description="Exact tool name from search_tools results")
    arguments: Optional[dict] = Field(
        default=None,
        description="Arguments for the tool. Use {} if tool takes no arguments."
    )


class SearchToolsMcpTool(BaseTool):
    name: str = "search_tools"
    description: str = (
        "Search available Superset MCP tools by natural language. "
        "Always call this FIRST to find the correct tool name."
    )
    args_schema: Type[BaseModel] = SearchToolsInput

    async def _arun(self, query: str) -> str:
        print(f"\n>>> [search_tools] query='{query}'")
        result = await call_mcp("tools/call", {
            "name": "search_tools",
            "arguments": {"query": query}
        })
        text = extract_text(result)
        print(f"<<< [search_tools] {text[:300]}")
        return text or "No tools found."

    def _run(self, query: str) -> str:
        return asyncio.run(self._arun(query))


class CallToolMcpTool(BaseTool):
    name: str = "call_tool"
    description: str = (
        "Execute a Superset tool by name. "
        "Use the exact name from search_tools. "
        "Pass arguments={} for tools with no parameters."
    )
    args_schema: Type[BaseModel] = CallToolInput

    async def _arun(self, name: str, arguments: Optional[dict] = None) -> str:
        args = arguments or {}
        print(f"\n>>> [call_tool] name='{name}', arguments={args}")
        result = await call_mcp("tools/call", {
            "name": "call_tool",
            "arguments": {"name": name, "arguments": args}
        })
        text = extract_text(result)
        print(f"<<< [call_tool] {text[:300]}")
        return text or "Tool returned empty result."

    def _run(self, name: str, arguments: Optional[dict] = None) -> str:
        return asyncio.run(self._arun(name, arguments))


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

async def run_agent(query: str):
    llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash")
    tools = [SearchToolsMcpTool(), CallToolMcpTool()]

    system_prompt = (
        "You are a Superset data analysis assistant. "
        "Mandatory workflow:\n"
        "1. Call 'search_tools' with English keywords to find the right tool.\n"
        "2. Call 'call_tool' with the exact tool name to fetch real data.\n"
        "3. Answer based ONLY on the actual data returned by the tools.\n"
        "Never fabricate answers. Always use tools first."
    )

    agent = create_react_agent(model=llm, tools=tools, prompt=system_prompt)
    result = await agent.ainvoke({"messages": [("human", query)]})
    print(f"\n{'='*50}\nKẾT QUẢ:\n{result['messages'][-1].content}")


if __name__ == "__main__":
    query = "Tôi đang có những bộ dataset nào trong Superset?"
    asyncio.run(run_agent(query))