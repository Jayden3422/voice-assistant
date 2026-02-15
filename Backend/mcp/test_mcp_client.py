"""Standalone test client for the Voice-Autopilot MCP Server.

Usage:
    python Backend/test_mcp_client.py              # run all tests
    python Backend/test_mcp_client.py list_tools    # just list tools
    python Backend/test_mcp_client.py <tool_name>   # test a specific tool
"""

import asyncio
import json
import sys
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters

SERVER_SCRIPT = str(Path(__file__).resolve().parent / "mcp_server.py")
TOOL_TIMEOUT = timedelta(seconds=45)  # must be > server-side 30s timeout

# ─────────────────── helpers ───────────────────


def pretty(obj) -> str:
    """Pretty-print JSON-serializable objects."""
    if isinstance(obj, str):
        try:
            obj = json.loads(obj)
        except (json.JSONDecodeError, TypeError):
            return obj
    return json.dumps(obj, ensure_ascii=False, indent=2)


def header(text: str):
    width = 60
    print(f"\n{'=' * width}")
    print(f"  {text}")
    print(f"{'=' * width}")


def passed(name: str):
    print(f"  [PASS] {name}")


def failed(name: str, err):
    print(f"  [FAIL] {name}: {err}")


# ─────────────────── test cases ───────────────────


async def test_list_tools(session: ClientSession):
    header("List Tools")
    result = await session.list_tools()
    print(f"  Found {len(result.tools)} tools:")
    for t in result.tools:
        params = list(t.inputSchema.get("properties", {}).keys())
        print(f"    - {t.name}({', '.join(params)})")
    assert len(result.tools) == 8, f"Expected 8 tools, got {len(result.tools)}"
    passed("list_tools")
    return result.tools


async def test_list_resources(session: ClientSession):
    header("List Resources")
    result = await session.list_resources()
    print(f"  Found {len(result.resources)} resources:")
    for r in result.resources:
        print(f"    - {r.uri}  ({r.name})")
    assert len(result.resources) == 2, f"Expected 2 resources, got {len(result.resources)}"
    passed("list_resources")


async def test_read_resource_schema(session: ClientSession):
    header("Read Resource: autopilot://schema")
    result = await session.read_resource("autopilot://schema")
    text = result.contents[0].text
    schema = json.loads(text)
    assert "properties" in schema, "Schema missing 'properties' key"
    print(f"  Schema keys: {list(schema.get('properties', {}).keys())[:6]}...")
    passed("read_resource(schema)")


async def test_read_resource_kb(session: ClientSession):
    header("Read Resource: autopilot://knowledge-base")
    result = await session.read_resource("autopilot://knowledge-base")
    text = result.contents[0].text
    data = json.loads(text)
    docs = data.get("documents", [])
    print(f"  Knowledge base documents: {len(docs)}")
    for d in docs[:5]:
        print(f"    - {d['filename']} ({d['size_bytes']} bytes)")
    passed("read_resource(knowledge-base)")


async def test_analyze_transcript(session: ClientSession):
    header("Tool: analyze_transcript")
    transcript = (
        "Hi, this is John from Acme Corp. We discussed the enterprise plan pricing "
        "at $500/month. Can we schedule a demo meeting next Tuesday at 2pm? "
        "My email is john@acme.com. Also please send a summary to our Slack channel."
    )
    print(f"  Input: {transcript[:80]}...")
    result = await session.call_tool("analyze_transcript", {"transcript": transcript}, read_timeout_seconds=TOOL_TIMEOUT)
    text = result.content[0].text
    data = json.loads(text)
    assert "summary" in data, "Missing 'summary' in result"
    assert "next_best_actions" in data, "Missing 'next_best_actions'"
    print(f"  Intent: {data.get('intent')}")
    print(f"  Urgency: {data.get('urgency')}")
    print(f"  Summary: {data.get('summary', '')[:100]}...")
    print(f"  Actions: {len(data.get('next_best_actions', []))} actions extracted")
    for a in data.get("next_best_actions", []):
        print(f"    - {a.get('action_type')}: {json.dumps(a.get('payload', {}), ensure_ascii=False)[:80]}")
    passed("analyze_transcript")
    return data


async def test_list_runs(session: ClientSession):
    header("Tool: list_runs")
    result = await session.call_tool("list_runs", {"limit": 5}, read_timeout_seconds=TOOL_TIMEOUT)
    text = result.content[0].text
    runs = json.loads(text)
    print(f"  Found {len(runs)} runs")
    for r in runs[:3]:
        print(f"    - {r.get('run_id', '?')[:8]}... | {r.get('status')} | {r.get('created_at', '')[:19]}")
    passed("list_runs")


async def test_search_knowledge_base(session: ClientSession):
    header("Tool: search_knowledge_base")
    result = await session.call_tool("search_knowledge_base", {"query": "pricing plan", "top_k": 3}, read_timeout_seconds=TOOL_TIMEOUT)
    text = result.content[0].text
    data = json.loads(text)
    if isinstance(data, dict) and "error" in data:
        print(f"  Error: {data['error']}")
        passed("search_knowledge_base (returned error gracefully)")
        return
    print(f"  Found {len(data)} chunks")
    for c in data[:3]:
        print(f"    - [{c.get('doc')}#{c.get('chunk')}] score={c.get('score'):.4f}: {c.get('text', '')[:60]}...")
    passed("search_knowledge_base")


async def test_send_slack_dry(session: ClientSession):
    """Test send_slack_message -- will fail gracefully if webhook not configured."""
    header("Tool: send_slack_message (expect failure if no webhook)")
    result = await session.call_tool("send_slack_message", {
        "message": "[MCP Test] Hello from test client!",
        "channel": "#test",
    }, read_timeout_seconds=TOOL_TIMEOUT)
    text = result.content[0].text
    data = json.loads(text)
    print(f"  Result: {data}")
    passed("send_slack_message (returned result)")


async def test_send_email_dry(session: ClientSession):
    """Test send_email -- will fail gracefully if SMTP not configured."""
    header("Tool: send_email (expect failure if no SMTP)")
    result = await session.call_tool("send_email", {
        "to": "test@example.com",
        "subject": "[MCP Test] Hello",
        "body": "This is a test email from the MCP test client.",
    }, read_timeout_seconds=TOOL_TIMEOUT)
    text = result.content[0].text
    data = json.loads(text)
    print(f"  Result: {data}")
    passed("send_email (returned result)")


async def test_create_linear_dry(session: ClientSession):
    """Test create_linear_ticket -- will fail gracefully if API key not configured."""
    header("Tool: create_linear_ticket (expect failure if no API key)")
    result = await session.call_tool("create_linear_ticket", {
        "title": "[MCP Test] Test ticket",
        "description": "Created by MCP test client",
        "priority": "low",
    }, read_timeout_seconds=TOOL_TIMEOUT)
    text = result.content[0].text
    data = json.loads(text)
    print(f"  Result: {data}")
    passed("create_linear_ticket (returned result)")


# ─────────────────── runner ───────────────────


ALL_TESTS = {
    "list_tools": test_list_tools,
    "list_resources": test_list_resources,
    "read_schema": test_read_resource_schema,
    "read_kb": test_read_resource_kb,
    "analyze_transcript": test_analyze_transcript,
    "list_runs": test_list_runs,
    "search_knowledge_base": test_search_knowledge_base,
    "send_slack_message": test_send_slack_dry,
    "send_email": test_send_email_dry,
    "create_linear_ticket": test_create_linear_dry,
}


async def main() -> int:
    target = sys.argv[1] if len(sys.argv) > 1 else None

    server_params = StdioServerParameters(
        command=sys.executable,
        args=[SERVER_SCRIPT],
        env={**dict(__import__("os").environ), "PYTHONPATH": str(Path(SERVER_SCRIPT).parent)},
    )

    print(f"Connecting to MCP server: {SERVER_SCRIPT}")
    print(f"Python: {sys.executable}")
    print("  (first startup may take ~60s while faiss loads)")

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=120)) as session:
            await session.initialize()
            print("Session initialized OK")

            if target and target in ALL_TESTS:
                tests = {target: ALL_TESTS[target]}
            else:
                tests = ALL_TESTS

            results = {"pass": 0, "fail": 0}
            for name, test_fn in tests.items():
                try:
                    await test_fn(session)
                    results["pass"] += 1
                except Exception as e:
                    failed(name, e)
                    results["fail"] += 1

            header("Summary")
            total = results["pass"] + results["fail"]
            print(f"  {results['pass']}/{total} passed, {results['fail']} failed")

    return 1 if results["fail"] > 0 else 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
