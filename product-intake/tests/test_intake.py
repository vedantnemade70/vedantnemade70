"""Offline tests: a fake stdio MCP server and a stubbed model. Run: python -m unittest discover tests"""

import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

import intake  # noqa: E402
from mcp_stdio import StdioMCPClient  # noqa: E402

FAKE_SERVER = [sys.executable, str(APP_DIR / "tests" / "fake_mcp_server.py")]


class StubLLM:
    """Asks for one MCP tool call, then answers using its result."""

    model = "stub-model"

    def __init__(self):
        self.requests = []

    def chat(self, messages, tools):
        self.requests.append(messages)
        last = messages[-1]
        if last["role"] == "tool":
            return {"role": "assistant", "content": f"Relevant: {last['content']}"}, "stop", self.model
        return ({"role": "assistant", "content": None, "tool_calls": [
            {"id": "t1", "type": "function", "function": {"name": "get-services-list", "arguments": "{}"}}]},
            "tool_calls", self.model)


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
        llm = StubLLM()
        with StdioMCPClient(FAKE_SERVER, timeout=10) as mcp:
            tools = intake.mcp_tools_for_llm(mcp.list_tools())
            text, trace, status, model = intake.run_prompt(llm, mcp, tools, product, prompt, cfg)
        self.assertEqual((status, model), ("ok", "stub-model"))
        self.assertEqual(tools[0]["function"]["name"], "get-services-list")
        self.assertIn("MDES Tokenization", text)
        self.assertEqual(trace[0]["tool"], "get-services-list")
        first_user = llm.requests[0][1]["content"]
        self.assertIn("Acme Fintech", first_user[-1]["text"])
        self.assertNotIn("$company", first_user[-1]["text"])
        self.assertIn("<product_documents>", first_user[0]["text"])


class ChatClientTest(unittest.TestCase):
    def test_payload_auth_and_retry_on_429(self):
        import json
        import os
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        from llm import ChatClient

        seen = []

        class FakeOpenRouter(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                seen.append((self.path, self.headers.get("Authorization"), body))
                if len(seen) == 1:
                    self.send_response(429)
                    self.send_header("Retry-After", "0.01")
                    self.end_headers()
                    return
                data = json.dumps({"model": body["models"][1], "choices": [
                    {"message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}]}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeOpenRouter)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        os.environ["TEST_OR_KEY"] = "sk-test"
        client = ChatClient({"base_url": f"http://127.0.0.1:{httpd.server_port}/api/v1", "api_key_env": "TEST_OR_KEY",
                             "model": "a:free", "fallback_models": ["b:free"]})
        message, finish, model = client.chat([{"role": "user", "content": "hello"}], tools=[])
        self.assertEqual((message["content"], finish, model), ("hi", "stop", "b:free"))
        self.assertEqual(len(seen), 2)
        path, auth, body = seen[1]
        self.assertEqual((path, auth), ("/api/v1/chat/completions", "Bearer sk-test"))
        self.assertEqual(body["models"], ["a:free", "b:free"])
        self.assertNotIn("tools", body)


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
