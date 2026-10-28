"""Thin async wrapper around the Mastercard Developers MCP server (stdio)."""

from __future__ import annotations

import os
from contextlib import AsyncExitStack
from dataclasses import dataclass, field

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

DEFAULT_COMMAND = "npx"
DEFAULT_ARGS = ["-y", "@mastercard/developers-mcp"]


@dataclass
class ToolCall:
    tool: str
    args: dict
    ok: bool
    chars: int
    error: str | None = None


@dataclass
class MastercardMCP:
    """Starts the MCP server, initializes a session and exposes `call()`."""

    command: str = DEFAULT_COMMAND
    args: list[str] = field(default_factory=lambda: list(DEFAULT_ARGS))
    log: list[ToolCall] = field(default_factory=list)
    _session: ClientSession | None = None
    _stack: AsyncExitStack | None = None

    async def __aenter__(self) -> "MastercardMCP":
        env = dict(os.environ)
        # The server uses node-fetch; honour HTTP(S)_PROXY when one is configured.
        if env.get("HTTPS_PROXY") or env.get("https_proxy"):
            env.setdefault("NODE_USE_ENV_PROXY", "1")
        params = StdioServerParameters(command=self.command, args=self.args, env=env)
        self._stack = AsyncExitStack()
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        self.server_info = (await self._session.initialize()).serverInfo
        return self

    async def __aexit__(self, *exc) -> None:
        if self._stack:
            await self._stack.aclose()

    async def list_tools(self) -> list[dict]:
        result = await self._session.list_tools()
        return [
            {"name": t.name, "description": (t.description or "").strip(), "input_schema": t.inputSchema}
            for t in result.tools
        ]

    async def call(self, tool: str, **args) -> str:
        """Call a tool and return its text. Errors are logged and returned as ''."""
        try:
            result = await self._session.call_tool(tool, args)
        except Exception as exc:  # transport-level failure
            self.log.append(ToolCall(tool, args, False, 0, str(exc)))
            return ""
        text = "".join(getattr(c, "text", "") for c in result.content)
        if result.isError:
            self.log.append(ToolCall(tool, args, False, 0, text[:300]))
            return ""
        self.log.append(ToolCall(tool, args, True, len(text)))
        return text
