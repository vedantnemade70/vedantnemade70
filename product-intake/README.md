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
   free model on OpenRouter + your prompts/*.yaml
                   │
                   ▼
       outbox/<company>__<product>/*.md + summary.json
```

No MCP SDK is used. `mcp_stdio.py` starts the server as a subprocess and speaks MCP
directly: newline-delimited JSON-RPC 2.0 (`initialize` → `notifications/initialized`
→ `tools/list` → `tools/call`) over the process's stdin/stdout.

## Setup
1. Python 3.10+ and Node.js 18+ (the Mastercard server runs with `npx`).
2. `pip install -r requirements.txt` (only PyYAML; everything else is the standard library)
3. Put your OpenRouter key in an environment variable:
   - macOS / Linux: `export OPENROUTER_API_KEY=sk-or-...`
   - Windows (PowerShell): `$env:OPENROUTER_API_KEY="sk-or-..."`
4. Check the connections:
   - `python intake.py tools` lists the Mastercard MCP tools.
   - `python intake.py models` lists OpenRouter's free models that support tool calling.

## Model (OpenRouter, free)
The prompts are answered by a free OpenRouter model that supports tool calling, so it can
call the Mastercard tools. Defaults in `config.yaml`:

```yaml
llm:
  model: openai/gpt-oss-120b:free
  fallback_models:            # used when the main model is down or rate limited
    - qwen/qwen3-next-80b-a3b-instruct:free
    - google/gemma-4-31b-it:free
```
OpenRouter's free lineup changes often. If a model stops working, run `python intake.py models`
and put one of the listed IDs in `config.yaml`. Free models are rate limited (OpenRouter allows a
limited number of free requests per day; buying a small amount of credit raises it), and each
product uses several requests per prompt, so the app waits and retries when it hits a limit.
To use a paid model instead (better answers), set `model` to any model ID from openrouter.ai/models.
To run fully offline, point `base_url` at Ollama (`http://localhost:11434/v1`, `api_key_env: ""`).

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

## Web front end
```bash
python intake.py serve
```
Then open **http://localhost:8080**. From the page you can:
- **New product**: enter company, product name and description, drop in docs and API specs, and send.
- **Products**: see every product as Queued → Processing (with progress) → Done / Failed, and read
  each prompt's response (tables and lists rendered) plus the Mastercard MCP tool calls it made.
- **Prompts**: add, edit and delete your predefined prompts.

The header shows the MCP server, the model, and whether `OPENROUTER_API_KEY` is set.
The server listens on 127.0.0.1 only; change `server.host` in `config.yaml` to expose it,
but note the page has no login.

## Command line
| Command | What it does |
|---|---|
| `python intake.py serve` | web front end + process products as they arrive |
| `python intake.py run examples/acme-paylink` | process one product now |
| `python intake.py watch` | process every product folder dropped into `inbox/`, then move it to `processed/` |

Other systems can submit products to the same server over HTTP:
```bash
curl -X POST localhost:8080/api/products -H 'Content-Type: application/json' -d '{
  "company": "Acme Fintech", "name": "PayLink", "description": "Payment links",
  "files": [{"name": "openapi.yaml", "content": "openapi: 3.0.3 ..."},
            {"name": "brochure.pdf", "content_base64": "JVBERi0..."}]}'
# -> {"id": "acme-fintech__paylink", "status": "queued"}
curl localhost:8080/api/products/acme-fintech__paylink   # responses once finished
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
- `llm`: endpoint, API key variable, model and fallbacks, token and tool-call limits, retries.
- `paths`, `watch.poll_seconds`, `server.host/port`.

## Tests
`python -m unittest discover tests` runs offline against a fake stdio MCP server
a stubbed model and a fake OpenRouter server, and checks the web API.
