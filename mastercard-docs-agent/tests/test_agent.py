"""Offline tests: parsing helpers and the full graph against a fake MCP server.

The canned responses below only mimic the *shape* of the real llms.txt / OpenAPI output;
they are test fixtures, not Mastercard documentation.
"""

import asyncio

from mc_agent import collect, report
from mc_agent.graph import build_graph
from mc_agent.mcp_client import ToolCall

SERVICES = """# Mastercard Developers
## Services
- [Demo Switch](/demo-switch/documentation/): Move a cardholder's card to merchants
- [Demo Payments](/demo-pay/documentation/): Pay things
"""
INDEX = """# Demo Switch
- [Overview](/demo-switch/documentation/overview/index.md): what it is
- [Glossary](/demo-switch/documentation/glossary/index.md): terms
- [API Reference](/demo-switch/documentation/api-reference/index.md): see /demo-switch/swagger/switch.yaml
"""
GLOSSARY = """| Term | Definition |
|---|---|
| PAN | Primary account number of the card |
| Merchant | The site where the card gets switched |

**Session**: A short-lived switch session created by the partner.
Requests use OAuth 1.0a signing and the payload is a JWE over TLS 1.2.
"""
OPS = """- POST /sessions: Create session
- GET /sessions/{id}: Read session
"""
DETAIL = """
method: POST
path: /sessions
parameters:
  - name: X-Request-Id
    in: header
    required: true
    schema: {type: string}
requestBody:
  content:
    application/json:
      schema:
        properties:
          cardholderId: {type: string, description: Partner user id}
          merchantIds: {type: array}
"""


class FakeMCP:
    def __init__(self):
        self.log = []

    async def list_tools(self):
        return [{"name": n, "description": "", "input_schema": {}} for n in
                ("get-services-list", "get-documentation", "get-documentation-section-content",
                 "get-api-operation-list", "get-api-operation-details", "get-oauth10a-integration-guide")]

    async def call(self, tool, **args):
        text = {
            "get-services-list": SERVICES,
            "get-documentation": INDEX if args.get("serviceId") == "demo-switch" else "",
            "get-documentation-section-content": GLOSSARY if args.get("sectionId") == "glossary" else "x",
            "get-api-operation-list": OPS,
            "get-api-operation-details": DETAIL,
            "get-oauth10a-integration-guide": "OAuth 1.0a guide with oauth_body_hash",
        }.get(tool, "")
        self.log.append(ToolCall(tool, args, bool(text), len(text)))
        return text


def test_parse_target_urls_and_names():
    t = collect.parse_target("https://developer.mastercard.com/platform/documentation/")
    assert t["service_id"] == "platform" and t["label"] == "platform"
    t = collect.parse_target("Spec=https://static.developer.mastercard.com/content/demo-pay/swagger/a/b.yaml")
    assert t["service_id"] == "demo-pay" and t["spec_paths"] == ["/demo-pay/swagger/a/b.yaml"]
    t = collect.parse_target("Card Switcher")
    assert t["service_id"] is None and t["label"] == "Card Switcher"


def test_service_matching_and_sections():
    services = collect.parse_services(SERVICES)
    assert [s["service_id"] for s in services] == ["demo-switch", "demo-pay"]
    assert collect.match_service("card switcher", services)[0]["service_id"] == "demo-switch"
    sections = collect.parse_sections("demo-switch", INDEX)
    assert collect.pick_sections(sections, 2) == ["api-reference", "glossary"]
    assert collect.find_spec_paths(INDEX) == ["/demo-switch/swagger/switch.yaml"]


def test_operations_fields_and_terms():
    assert collect.parse_operations(OPS) == [("POST", "/sessions"), ("GET", "/sessions/{id}")]
    names = {f["name"]: f for f in collect.parse_fields(DETAIL, "POST /sessions")}
    assert names["X-Request-Id"]["location"] == "header" and names["X-Request-Id"]["required"]
    assert {"cardholderId", "merchantIds"} <= names.keys()
    terms = {t["term"] for t in report.extract_terms(GLOSSARY)}
    assert terms == {"PAN", "Merchant", "Session"}


def test_graph_end_to_end_keyword_mode():
    mcp = FakeMCP()
    state = asyncio.run(build_graph(mcp).ainvoke({
        "raw_targets": ["Card Switcher", "Platform=https://developer.mastercard.com/platform/documentation/"],
        "use_llm": False, "notes": [],
    }))
    switch, platform = state["targets"]
    assert switch["service_id"] == "demo-switch" and switch["operations"]
    assert any("OAuth 1.0a" in a for a in switch["profile"]["authentication_methods"])
    assert any("JWE" in s for s in switch["profile"]["api_security"])
    md = state["report"]
    for heading in ("## 1. Terminology", "## 2. API Security", "## 3. Authentication Methods", "## 4. Fields"):
        assert heading in md
    assert "`cardholderId`" in md
    # platform: index is empty in the fake, but the platform auth guide is always fetched
    assert "guide: OAuth 1.0a" in platform["docs"]
