"""Offline tests: a fake stdio MCP server and a stubbed Claude client. Run: python -m unittest discover tests"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

import intake  # noqa: E402
from mcp_stdio import StdioMCPClient  # noqa: E402

FAKE_SERVER = [sys.executable, str(APP_DIR / "tests" / "fake_mcp_server.py")]


def block(**kw):
    return SimpleNamespace(**kw)


class StubClaude:
    """Asks for one MCP tool call, then answers using its result."""

    def __init__(self):
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.requests.append(kwargs)
        last = kwargs["messages"][-1]["content"]
        if isinstance(last, list) and last and isinstance(last[0], dict) and last[0].get("type") == "tool_result":
            return block(stop_reason="end_turn",
                         content=[block(type="text", text=f"Relevant: {last[0]['content']}")])
        return block(stop_reason="tool_use",
                     content=[block(type="tool_use", id="t1", name="get-services-list", input={})])


class StdioClientTest(unittest.TestCase):
    def test_handshake_list_and_call(self):
        with StdioMCPClient(FAKE_SERVER, timeout=10) as mcp:
            self.assertEqual(mcp.server_info["name"], "fake-mastercard")
            self.assertEqual([t["name"] for t in mcp.list_tools()], ["get-services-list"])
            self.assertEqual(mcp.call_tool("get-services-list"),
                             ("Mastercard Payment Gateway; MDES Tokenization", False))
            self.assertTrue(mcp.call_tool("nope")[1])


class RunPromptTest(unittest.TestCase):
    def test_tool_loop_and_rendering(self):
        cfg = intake.load_config(APP_DIR / "config.yaml")
        product = intake.load_product(APP_DIR / "examples" / "acme-paylink")
        self.assertEqual(sorted(n for n, _ in product["texts"]), ["docs/overview.md", "openapi.yaml"])
        prompt = intake.load_prompts(APP_DIR / "prompts")[0]
        claude = StubClaude()
        with StdioMCPClient(FAKE_SERVER, timeout=10) as mcp:
            tools = [{"name": t["name"], "description": t["description"], "input_schema": t["inputSchema"]}
                     for t in mcp.list_tools()]
            text, trace, status = intake.run_prompt(claude, mcp, tools, product, prompt, cfg)
        self.assertEqual(status, "ok")
        self.assertIn("MDES Tokenization", text)
        self.assertEqual(trace[0]["tool"], "get-services-list")
        first_user = claude.requests[0]["messages"][0]["content"]
        self.assertIn("Acme Fintech", first_user[-1]["text"])
        self.assertNotIn("$company", first_user[-1]["text"])
        self.assertIn("<product_documents>", first_user[0]["text"])


if __name__ == "__main__":
    unittest.main()
