"""A small MCP client, so the assistant can use tools you already run elsewhere.

MCP servers speak JSON-RPC 2.0 over a pipe: one JSON object per line, in and out. That is
little enough to talk directly, which keeps this app dependency-free - the SDK would pull in a
stack of packages for a protocol that is three messages wide.

Servers are configured in ``<data>/agent/mcp.json``, the same shape other MCP clients use:

    {"mcpServers": {"files": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "D:/clips"]}}}

Each server is started on demand, asked what tools it has, and stopped when the app closes.
Nothing is started automatically at boot: a misbehaving server should not be able to stop
BURN-IN from starting.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("clipbot.agent.mcp")

PROTOCOL = "2024-11-05"
START_TIMEOUT_S = 20.0
CALL_TIMEOUT_S = 120.0
LINE_MAX = 1 << 20
SUMMARY_MAX = 2000     # when a server answers with something other than text
STOP_GRACE_S = 5.0     # a server gets this long to exit before it is killed
DESC_MAX = 400         # tool descriptions the model is shown


class McpError(RuntimeError):
    """The server could not be started, or refused a call."""


@dataclass
class McpServer:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    process: asyncio.subprocess.Process | None = None
    tools: list[dict] = field(default_factory=list)
    _next_id: int = 1

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.returncode is None


def load_config(data_dir: Path) -> list[McpServer]:
    """Read mcp.json. A missing or broken file means no servers, never a crash."""
    path = data_dir / "agent" / "mcp.json"
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("mcp.json could not be read: %s", exc)
        return []
    servers = raw.get("mcpServers") or raw.get("servers") or {}
    out = []
    for name, spec in servers.items():
        command = str(spec.get("command", "")).strip()
        if not command:
            logger.warning("mcp server %r has no command", name)
            continue
        out.append(McpServer(name=str(name), command=command,
                             args=[str(a) for a in spec.get("args", [])],
                             env={str(k): str(v) for k, v in (spec.get("env") or {}).items()}))
    return out


class McpClient:
    """Starts MCP servers on demand and calls their tools."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.servers: dict[str, McpServer] = {s.name: s for s in load_config(data_dir)}

    def reload(self) -> None:
        for server in list(self.servers.values()):
            if server.running:
                return                      # leave running servers alone
        self.servers = {s.name: s for s in load_config(self.data_dir)}

    async def start(self, server: McpServer) -> None:
        if server.running:
            return
        exe = shutil.which(server.command) or server.command
        try:
            server.process = await asyncio.create_subprocess_exec(
                exe, *server.args,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env={**os.environ, **server.env},
            )
        except (OSError, ValueError) as exc:
            raise McpError(f"{server.name}: could not start {server.command}: {exc}") from exc

        await self._request(server, "initialize", {
            "protocolVersion": PROTOCOL,
            "capabilities": {},
            "clientInfo": {"name": "BURN-IN", "version": "1"},
        })
        await self._notify(server, "notifications/initialized", {})
        listed = await self._request(server, "tools/list", {})
        server.tools = listed.get("tools", []) if isinstance(listed, dict) else []
        logger.info("mcp %s: %d tool(s)", server.name, len(server.tools))

    async def stop_all(self) -> None:
        for server in self.servers.values():
            if server.running and server.process:
                server.process.terminate()
                try:
                    await asyncio.wait_for(server.process.wait(), timeout=STOP_GRACE_S)
                except asyncio.TimeoutError:
                    server.process.kill()
            server.process, server.tools = None, []

    async def list_tools(self) -> list[dict]:
        """Every configured server's tools, namespaced so names cannot collide."""
        out = []
        for server in self.servers.values():
            try:
                await self.start(server)
            except McpError as exc:
                logger.warning("%s", exc)
                continue
            for tool in server.tools:
                out.append({
                    "name": f"{server.name}__{tool.get('name')}",
                    "description": (tool.get("description") or "")[:DESC_MAX],
                    "schema": tool.get("inputSchema") or {"type": "object", "properties": {}},
                })
        return out

    async def call(self, namespaced: str, arguments: dict) -> str:
        server_name, _, tool_name = namespaced.partition("__")
        server = self.servers.get(server_name)
        if server is None:
            raise McpError(f"no MCP server called {server_name}")
        await self.start(server)
        result = await self._request(server, "tools/call",
                                     {"name": tool_name, "arguments": arguments},
                                     timeout=CALL_TIMEOUT_S)
        return _flatten(result)

    # ------------------------------------------------------------------ JSON-RPC over a pipe
    async def _request(self, server: McpServer, method: str, params: dict,
                       timeout: float = START_TIMEOUT_S) -> dict:
        if not server.running or server.process is None:
            raise McpError(f"{server.name} is not running")
        message_id = server._next_id
        server._next_id += 1
        await self._send(server, {"jsonrpc": "2.0", "id": message_id,
                                  "method": method, "params": params})
        while True:
            reply = await asyncio.wait_for(self._read(server), timeout=timeout)
            if reply.get("id") != message_id:
                continue                     # a notification or another reply: keep reading
            if "error" in reply:
                raise McpError(f"{server.name}.{method}: {reply['error'].get('message')}")
            return reply.get("result") or {}

    async def _notify(self, server: McpServer, method: str, params: dict) -> None:
        await self._send(server, {"jsonrpc": "2.0", "method": method, "params": params})

    async def _send(self, server: McpServer, message: dict) -> None:
        assert server.process and server.process.stdin
        server.process.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
        await server.process.stdin.drain()

    async def _read(self, server: McpServer) -> dict:
        assert server.process and server.process.stdout
        line = await server.process.stdout.readline()
        if not line:
            raise McpError(f"{server.name} stopped responding")
        try:
            return json.loads(line[:LINE_MAX].decode("utf-8", "replace"))
        except json.JSONDecodeError as exc:
            raise McpError(f"{server.name} sent something that is not JSON: {exc}") from exc


def _flatten(result: dict) -> str:
    """MCP returns a list of content blocks; the model wants text."""
    if not isinstance(result, dict):
        return str(result)
    parts = []
    for block in result.get("content", []):
        if block.get("type") == "text":
            parts.append(block.get("text", ""))
        else:
            parts.append(f"[{block.get('type')}]")
    return "\n".join(parts).strip() or json.dumps(result)[:SUMMARY_MAX]
