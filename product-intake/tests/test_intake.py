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


class ServerApiTest(unittest.TestCase):
    def setUp(self):
        import json
        import shutil
        import tempfile
        import threading
        import urllib.request
        from http.server import ThreadingHTTPServer

        import server

        self.json, self.urllib = json, urllib.request
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copytree(APP_DIR / "prompts", self.tmp / "prompts")
        self.cfg = intake.load_config(APP_DIR / "config.yaml")
        for key in ("inbox", "outbox", "processed", "prompts"):
            self.cfg["paths"][key] = self.tmp / key
            self.cfg["paths"][key].mkdir(exist_ok=True)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(self.cfg))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"
        self.addCleanup(shutil.rmtree, self.tmp)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def call(self, method, path, body=None):
        data = self.json.dumps(body).encode() if body is not None else None
        req = self.urllib.Request(self.base + path, data=data, method=method,
                                  headers={"Content-Type": "application/json"})
        try:
            with self.urllib.urlopen(req) as res:
                return res.status, self.json.loads(res.read())
        except self.urllib.HTTPError as err:
            return err.code, self.json.loads(err.read())

    def test_submit_product_and_list(self):
        code, res = self.call("POST", "/api/products", {
            "company": "Acme Fintech", "name": "PayLink",
            "files": [{"name": "../../evil.yaml", "content": "openapi: 3.0.3"}]})
        self.assertEqual((code, res["id"]), (202, "acme-fintech__paylink"))
        folder = self.cfg["paths"]["inbox"] / "acme-fintech__paylink"
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["evil.yaml", "product.yaml"])
        code, products = self.call("GET", "/api/products")
        self.assertEqual(products[0]["status"], "queued")
        self.assertEqual(self.call("GET", "/api/products/acme-fintech__paylink")[1]["status"], "queued")
        self.assertEqual(self.call("POST", "/api/products", {"company": "x"})[0], 400)

    def test_prompt_crud(self):
        code, created = self.call("POST", "/api/prompts", {"name": "Pricing check", "prompt": "Cost of $product_name?"})
        self.assertEqual((code, created["file"]), (201, "05_pricing_check.yaml"))
        code, _ = self.call("PUT", f"/api/prompts/{created['file']}", {"name": "Pricing", "prompt": "Updated"})
        self.assertEqual(code, 200)
        prompts = self.call("GET", "/api/prompts")[1]
        self.assertEqual(prompts[-1]["prompt"].strip(), "Updated")
        self.assertEqual(self.call("PUT", "/api/prompts/..%2Fconfig.yaml", {"name": "a", "prompt": "b"})[0], 404)
        self.assertEqual(self.call("DELETE", f"/api/prompts/{created['file']}")[0], 200)
        self.assertEqual(len(self.call("GET", "/api/prompts")[1]), 4)


if __name__ == "__main__":
    unittest.main()
