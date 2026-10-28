# Mastercard Docs Agent (LangGraph + MCP)

A small LangGraph agent that compares Mastercard products using the official
[Mastercard Developers MCP server](https://www.npmjs.com/package/@mastercard/developers-mcp).
You give it product names or Mastercard developer URLs. It starts the MCP server, lists the server's tools,
pulls each product's docs (llms.txt index, documentation sections, OpenAPI operations, auth guides) and writes
a comparison report with four sections:

1. **Terminology**
2. **API Security**
3. **Authentication Methods**
4. **Fields** (request/response fields from the OpenAPI spec)

The default run compares **Card Switcher** with the **Mastercard platform** (Mastercard-wide docs:
authentication, security, OAuth guides).

## How it works

```
START → init_tools → resolve_targets → collect_docs ─┬─ extract_profiles → write_report → END
                                                     └─ no_docs → END   (nothing could be fetched)
```

| Node | MCP tools used | What it does |
|---|---|---|
| `init_tools` | `tools/list` | Initializes the MCP session and records the server's tools |
| `resolve_targets` | `get-services-list` (the site's root `llms.txt`) | Gets a `serviceId` from each URL, or matches a product name against the service list |
| `collect_docs` | `get-documentation` (service `llms.txt`), `get-documentation-section-content`, `get-api-operation-list`, `get-api-operation-details`, `get-oauth10a-integration-guide`, `get-oauth20-integration-guide` | Reads priority sections (API reference, security, glossary…), finds OpenAPI spec paths, inspects operations and parses their fields |
| `extract_profiles` | none | Claude extracts a structured profile per product (or keyword rules with `--no-llm`) |
| `write_report` | none | Builds the Markdown comparison report and logs every MCP call |

## Setup

Needs Python 3.10+ and Node.js 18+ (the MCP server runs through `npx`).

```bash
cd mastercard-docs-agent
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...        # optional, see --no-llm
```

## Usage

```bash
python main.py --list-tools                       # start the MCP server and print its tools
python main.py                                    # Card Switcher vs Mastercard platform
python main.py "Card Switcher" "https://developer.mastercard.com/platform/documentation/"
python main.py "Card Switcher=https://developer.mastercard.com/<service-id>/documentation/"
python main.py "Spec=https://static.developer.mastercard.com/content/<service-id>/swagger/<file>.yaml"
python main.py --no-llm                           # keyword extraction, no API key needed
```

Each target is a product name, a docs URL or an OpenAPI spec URL, optionally prefixed with `Label=`.
If a name does not match a service, the report says so and lists the closest services. Pass the product's
docs URL to pin the exact `serviceId`.

Output goes to `output/<timestamp>/`: `report.md`, plus `raw/`, which holds every document fetched through MCP
and the extracted profile JSON, so you can check each claim in the report.

Options: `--max-sections` (default 5), `--max-operations` (default 6), `--out`, `--mcp-command` / `--mcp-args`
(e.g. to pin `@mastercard/developers-mcp@0.1.7`), and `MC_AGENT_MODEL` to change the Claude model.

## Network

The MCP server fetches from `developer.mastercard.com` (and GitHub for the OAuth guides). Behind a proxy, the
client passes your environment to the server and sets `NODE_USE_ENV_PROXY=1` so Node uses `HTTPS_PROXY`.

## Tests

```bash
pip install pytest && python -m pytest -q
```

The tests run offline against a fake MCP server, so they cover parsing and graph wiring, not live documentation.
