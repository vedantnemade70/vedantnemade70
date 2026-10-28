# Product Intake → Mastercard MCP

Whenever a new product arrives from a company (its documentation, API specs, PDFs),
this app sends it to the **Mastercard Developers MCP server** and runs your
**predefined prompts** against it. Each prompt's response is saved as Markdown.

```
new product ──► inbox/ (or POST /products)
                   │
                   ▼
           intake.py ──stdin/stdout JSON-RPC──► Mastercard MCP server (child process)
                   │                                 get-services-list, get-api-operation-list,
                   ▼                                 get-documentation, OAuth guides, ...
        Claude + your prompts/*.yaml
                   │
                   ▼
       outbox/<company>__<product>/*.md + summary.json
```

No MCP SDK is used. `mcp_stdio.py` starts the server as a subprocess and speaks MCP
directly: newline-delimited JSON-RPC 2.0 (`initialize` → `notifications/initialized`
→ `tools/list` → `tools/call`) over the process's stdin/stdout.

## Setup
1. Python 3.10+ and Node.js 18+ (the Mastercard server runs with `npx`).
2. `pip install -r requirements.txt`
3. `export ANTHROPIC_API_KEY=...`
4. Check the connection: `python intake.py tools` (lists the Mastercard tools).

## A product
A folder with a `product.yaml` plus any documents:

```
acme-paylink/
  product.yaml        company, name, description
  openapi.yaml        API spec (.yaml / .json)
  docs/overview.md    docs (.md, .txt, .json, .xml, .csv, .html ...)
  brochure.pdf        PDFs are sent as documents
```
See `examples/acme-paylink/`.

## Run it
| Command | What it does |
|---|---|
| `python intake.py run examples/acme-paylink` | process one product now |
| `python intake.py watch` | process every product folder dropped into `inbox/`, then move it to `processed/` |
| `python intake.py serve` | `watch` + an HTTP endpoint for other systems |

HTTP (with `serve`):
```bash
curl -X POST localhost:8080/products -H 'Content-Type: application/json' -d '{
  "company": "Acme Fintech", "name": "PayLink", "description": "Payment links",
  "files": [{"name": "openapi.yaml", "content": "openapi: 3.0.3 ..."},
            {"name": "brochure.pdf", "content_base64": "JVBERi0..."}]}'
# -> {"id": "acme-fintech__paylink", "status": "queued"}
curl localhost:8080/products/acme-fintech__paylink   # responses once finished
```

## Predefined prompts
Every `prompts/*.yaml` file runs for each product, in file-name order:
```yaml
name: api_mapping
description: Map the product's API endpoints to Mastercard API operations.
prompt: |
  Compare the API specification(s) of $company's product "$product_name" ...
```
Placeholders: `$company`, `$product_name`, `$product_description`. The product's
documents are attached automatically, so a prompt doesn't need to include them.
Add, edit or delete files to change what gets asked.

Included: `product_overview`, `api_mapping`, `integration_plan`, `compliance_check`.

## Configuration (`config.yaml`)
- `mcp_server.command`: how to launch the server. Default `npx -y @mastercard/developers-mcp`;
  any stdio MCP server works (for example `["node", "/path/to/dist/index.js"]`).
- `mcp_server.extra_args`: focus the server, e.g. `--service=<docs URL>` or `--api-specification=<spec URL>`.
- `claude`: model, effort, token and tool-call limits.
- `paths`, `watch.poll_seconds`, `server.host/port`.

## Tests
`python -m unittest discover tests` runs offline against a fake stdio MCP server
and a stubbed Claude client.
