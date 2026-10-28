"""CLI: compare Mastercard products using the Mastercard Developers MCP server.

Examples:
  python main.py                                   # Card Switcher vs Mastercard platform (defaults)
  python main.py "Card Switcher" "https://developer.mastercard.com/platform/documentation/"
  python main.py "Switch=https://developer.mastercard.com/<service-id>/documentation/" --no-llm
  python main.py --list-tools
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from mc_agent.graph import build_graph
from mc_agent.mcp_client import DEFAULT_ARGS, MastercardMCP

DEFAULT_TARGETS = [
    "Card Switcher",
    "Mastercard Platform=https://developer.mastercard.com/platform/documentation/",
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("targets", nargs="*", default=DEFAULT_TARGETS,
                   help="Product name, docs URL or OpenAPI spec URL; optionally 'Label=value'")
    p.add_argument("--no-llm", action="store_true", help="keyword-based extraction, no ANTHROPIC_API_KEY needed")
    p.add_argument("--max-sections", type=int, default=5, help="doc sections to read per product")
    p.add_argument("--max-operations", type=int, default=6, help="API operations to inspect per spec")
    p.add_argument("--out", default="output", help="directory for the report and raw docs")
    p.add_argument("--mcp-command", default="npx", help="command that starts the MCP server")
    p.add_argument("--mcp-args", nargs="*", default=DEFAULT_ARGS, help="arguments for --mcp-command")
    p.add_argument("--list-tools", action="store_true", help="only initialize the server and print its tools")
    return p.parse_args()


async def run(args: argparse.Namespace) -> int:
    use_llm = not args.no_llm
    if use_llm and not os.getenv("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set; falling back to --no-llm keyword mode.", file=sys.stderr)
        use_llm = False

    async with MastercardMCP(command=args.mcp_command, args=args.mcp_args) as mcp:
        print(f"Connected to {mcp.server_info.name} {mcp.server_info.version}")
        if args.list_tools:
            for t in await mcp.list_tools():
                print(f"- {t['name']}: {t['description'].splitlines()[0]}")
            return 0

        graph = build_graph(mcp)
        state = await graph.ainvoke({
            "raw_targets": args.targets,
            "use_llm": use_llm,
            "max_sections": args.max_sections,
            "max_operations": args.max_operations,
            "notes": [],
        })

    for note in state.get("notes", []):
        print(f"  * {note}")
    if not state.get("report"):
        print("No report produced. If the error mentions developer.mastercard.com, check network/proxy access.",
              file=sys.stderr)
        return 1

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = Path(args.out) / stamp
    (out / "raw").mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text(state["report"], encoding="utf-8")
    for t in state["targets"]:
        slug = re.sub(r"[^a-z0-9]+", "-", t["label"].lower()).strip("-")
        for name, text in t.get("docs", {}).items():
            fname = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")[:80]
            (out / "raw" / f"{slug}__{fname}.txt").write_text(text, encoding="utf-8")
        (out / "raw" / f"{slug}__profile.json").write_text(json.dumps(t.get("profile"), indent=2), encoding="utf-8")
    print(f"Report written to {out / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run(parse_args())))
