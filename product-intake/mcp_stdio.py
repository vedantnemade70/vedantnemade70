"""Minimal MCP client that talks to a server over stdio.

No MCP SDK is used. The server is started as a child process and we exchange
JSON-RPC 2.0 messages with it, one JSON object per line, on its stdin/stdout:

    -> {"jsonrpc":"2.0","id":1,"method":"initialize","params":{...}}
    <- {"jsonrpc":"2.0","id":1,"result":{...}}
    -> {"jsonrpc":"2.0","method":"notifications/initialized"}
    -> {"jsonrpc":"2.0","id":2,"method":"tools/list"}
    -> {"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":...,"arguments":{...}}}

The server's stderr is passed through so its log lines stay visible.
"""

import json
import queue
import subprocess
import sys
import threading

PROTOCOL_VERSION = "2025-06-18"


class MCPError(Exception):
    pass


class StdioMCPClient:
    def __init__(self, command, env=None, timeout=120):
        self.command = command
        self.env = env
        self.timeout = timeout
        self.proc = None
        self._next_id = 0
        self._lock = threading.Lock()
        self._pending = {}  # request id -> Queue that receives the response
        self.server_info = {}

    # -- lifecycle ---------------------------------------------------------

    def start(self):
        self.proc = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=sys.stderr,
            env=self.env,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        threading.Thread(target=self._read_loop, daemon=True).start()

        result = self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "product-intake", "version": "1.0.0"},
        })
        self.server_info = result.get("serverInfo", {})
        self.notify("notifications/initialized")
        return result

    def close(self):
        if not self.proc:
            return
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()
            self.proc.wait()
        self.proc.stdout.close()
        self.proc = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.close()

    # -- JSON-RPC ----------------------------------------------------------

    def _send(self, message):
        if not self.proc or self.proc.poll() is not None:
            raise MCPError("MCP server process is not running")
        line = json.dumps(message, ensure_ascii=False)
        with self._lock:
            self.proc.stdin.write(line + "\n")
            self.proc.stdin.flush()

    def _read_loop(self):
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                print(f"[mcp] ignoring non-JSON output: {line[:200]}", file=sys.stderr)
                continue
            msg_id = message.get("id")
            if "method" in message:
                # A request or notification from the server. We advertise no
                # client capabilities, so answer requests with "not found".
                if msg_id is not None:
                    self._send({"jsonrpc": "2.0", "id": msg_id,
                                "error": {"code": -32601, "message": "Method not found"}})
                continue
            waiter = self._pending.pop(msg_id, None)
            if waiter:
                waiter.put(message)
        # stdout closed: wake everyone still waiting.
        for waiter in list(self._pending.values()):
            waiter.put({"error": {"code": -1, "message": "MCP server exited"}})
        self._pending.clear()

    def request(self, method, params=None):
        with self._lock:
            self._next_id += 1
            msg_id = self._next_id
        waiter = queue.Queue(maxsize=1)
        self._pending[msg_id] = waiter
        message = {"jsonrpc": "2.0", "id": msg_id, "method": method}
        if params is not None:
            message["params"] = params
        self._send(message)
        try:
            response = waiter.get(timeout=self.timeout)
        except queue.Empty:
            self._pending.pop(msg_id, None)
            raise MCPError(f"Timed out after {self.timeout}s waiting for {method}")
        if "error" in response:
            err = response["error"]
            raise MCPError(f"{method} failed: {err.get('message')} (code {err.get('code')})")
        return response.get("result", {})

    def notify(self, method, params=None):
        message = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self._send(message)

    # -- MCP methods -------------------------------------------------------

    def list_tools(self):
        tools, cursor = [], None
        while True:
            result = self.request("tools/list", {"cursor": cursor} if cursor else {})
            tools.extend(result.get("tools", []))
            cursor = result.get("nextCursor")
            if not cursor:
                return tools

    def call_tool(self, name, arguments=None):
        """Call a tool and return (text, is_error)."""
        result = self.request("tools/call", {"name": name, "arguments": arguments or {}})
        parts = []
        for item in result.get("content", []):
            if item.get("type") == "text":
                parts.append(item["text"])
            elif item.get("type") == "resource":
                parts.append(item.get("resource", {}).get("text", ""))
            else:
                parts.append(json.dumps(item))
        if not parts and "structuredContent" in result:
            parts.append(json.dumps(result["structuredContent"]))
        return "\n".join(parts), bool(result.get("isError"))
