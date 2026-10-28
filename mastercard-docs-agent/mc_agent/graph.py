"""LangGraph workflow: init MCP tools -> resolve targets -> collect docs -> extract -> report."""

from __future__ import annotations

import asyncio
import operator
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from . import collect, report
from .mcp_client import MastercardMCP


class AgentState(TypedDict, total=False):
    raw_targets: list[str]
    use_llm: bool
    max_sections: int
    max_operations: int
    tools: list[dict]
    services: list[dict]
    targets: list[dict]
    report: str
    notes: Annotated[list[str], operator.add]


def build_graph(mcp: MastercardMCP):
    """Nodes close over a live MCP session, so the graph must run inside `async with mcp`."""

    async def init_tools(state: AgentState) -> AgentState:
        tools = await mcp.list_tools()
        return {"tools": tools, "notes": [f"MCP server initialized with {len(tools)} tools"]}

    async def resolve_targets(state: AgentState) -> AgentState:
        parsed = [collect.parse_target(raw) for raw in state["raw_targets"]]
        services: list[dict] = []
        if any(not t["service_id"] for t in parsed):
            # get-services-list is backed by https://developer.mastercard.com/llms.txt
            services = collect.parse_services(await mcp.call("get-services-list"))
        targets, notes = [], []
        for t in parsed:
            t = await collect.resolve_target(mcp, t, services)
            # The platform docs describe Mastercard-wide auth; always pull both guides for it.
            t["include_auth_guides"] = t["service_id"] == "platform"
            notes.append(f"{t['label']!r} -> serviceId {t['service_id']!r}"
                         + (f" (candidates: {[c['service_id'] for c in t.get('candidates', [])]})" if t.get("candidates") else ""))
            targets.append(t)
        return {"services": services, "targets": targets, "notes": notes}

    async def collect_docs(state: AgentState) -> AgentState:
        targets = [
            await collect.collect_target(mcp, t, state.get("max_sections", 5), state.get("max_operations", 6))
            for t in state["targets"]
        ]
        notes = [f"{t['label']}: {len(t['docs'])} documents, {len(t['operations'])} operations, "
                 f"{len(t['fields'])} fields" for t in targets]
        return {"targets": targets, "notes": notes}

    async def extract_profiles(state: AgentState) -> AgentState:
        extract = report.llm_profile if state.get("use_llm") else report.heuristic_profile
        profiles = await asyncio.gather(*(asyncio.to_thread(extract, t) for t in state["targets"]))
        return {"targets": [{**t, "profile": p} for t, p in zip(state["targets"], profiles)]}

    async def write_report(state: AgentState) -> AgentState:
        targets = state["targets"]
        if state.get("use_llm"):
            comparison = await asyncio.to_thread(report.llm_comparison, targets)
        else:
            comparison = report.heuristic_comparison(targets)
        md = report.render_report(targets, comparison, state["tools"], mcp.log,
                                  "LLM" if state.get("use_llm") else "keyword")
        return {"report": md}

    def has_docs(state: AgentState) -> str:
        return "extract_profiles" if any(t.get("docs") for t in state["targets"]) else "no_docs"

    async def no_docs(state: AgentState) -> AgentState:
        failures = [c for c in mcp.log if not c.ok]
        reason = failures[0].error if failures else "no target resolved to a serviceId"
        return {"report": "", "notes": [f"No documentation retrieved; first error: {reason}"]}

    g = StateGraph(AgentState)
    g.add_node("init_tools", init_tools)
    g.add_node("resolve_targets", resolve_targets)
    g.add_node("collect_docs", collect_docs)
    g.add_node("extract_profiles", extract_profiles)
    g.add_node("write_report", write_report)
    g.add_node("no_docs", no_docs)
    g.add_edge(START, "init_tools")
    g.add_edge("init_tools", "resolve_targets")
    g.add_edge("resolve_targets", "collect_docs")
    g.add_conditional_edges("collect_docs", has_docs, ["extract_profiles", "no_docs"])
    g.add_edge("extract_profiles", "write_report")
    g.add_edge("write_report", END)
    g.add_edge("no_docs", END)
    return g.compile()
