"""Web front end and JSON API for the product intake app (started by `python intake.py serve`).

    GET    /                        the web page (web/index.html)
    GET    /api/status              model, MCP command, whether an API key is set
    GET    /api/products            every product: queued, processing, done or failed
    POST   /api/products            submit a product {company, name, description, files: [...]}
    GET    /api/products/<id>       one product with its responses
    GET    /api/prompts             the predefined prompts
    POST   /api/prompts             create a prompt {name, description, prompt}
    PUT    /api/prompts/<file>      update a prompt
    DELETE /api/prompts/<file>      delete a prompt
"""

import base64
import json
import os
import re
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

import intake

WEB_DIR = Path(__file__).resolve().parent / "web"
MAX_BODY_BYTES = 50 * 1024 * 1024
PROMPT_FILE = re.compile(r"[A-Za-z0-9_-]+\.ya?ml")
PRODUCT_ID = re.compile(r"[a-z0-9-]+__[a-z0-9-]+")


class ApiError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def list_products(cfg):
    products = {}
    outbox = cfg["paths"]["outbox"]
    if outbox.exists():
        for summary_file in outbox.glob("*/summary.json"):
            summary = json.loads(summary_file.read_text())
            products[summary_file.parent.name] = {
                "id": summary_file.parent.name,
                "company": summary["company"],
                "product": summary["product"],
                "status": summary.get("status", "done"),
                "done": len(summary["results"]),
                "total": len(summary.get("prompts", summary["results"])),
                "updated": summary.get("finished_at") or summary.get("started_at"),
            }
    inbox = cfg["paths"]["inbox"]
    if inbox.exists():
        for meta_file in inbox.glob("*/product.yaml"):
            if meta_file.parent.name.startswith("."):
                continue
            meta = yaml.safe_load(meta_file.read_text()) or {}
            company = meta.get("company", "Unknown company")
            name = meta.get("name", meta_file.parent.name)
            pid = intake.product_id(company, name)
            if products.get(pid, {}).get("status") == "processing":
                continue
            products[pid] = {"id": pid, "company": company, "product": name, "status": "queued",
                             "done": 0, "total": 0, "updated": None}
    return sorted(products.values(), key=lambda p: p["updated"] or "9999", reverse=True)


def get_product(cfg, pid):
    if not PRODUCT_ID.fullmatch(pid):
        raise ApiError(404, "unknown product")
    out_dir = cfg["paths"]["outbox"] / pid
    if not (out_dir / "summary.json").exists():
        for item in list_products(cfg):
            if item["id"] == pid:
                return {**item, "results": []}
        raise ApiError(404, "unknown product")
    summary = json.loads((out_dir / "summary.json").read_text())
    for item in summary["results"]:
        item["response"] = (out_dir / item["output"]).read_text()
    return summary


def submit_product(cfg, body):
    company, name = str(body.get("company", "")).strip(), str(body.get("name", "")).strip()
    if not company or not name:
        raise ApiError(400, "company and name are required")
    pid = intake.product_id(company, name)
    inbox = cfg["paths"]["inbox"]
    staging = inbox / f".{pid}.tmp"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    for item in body.get("files", []):
        filename = Path(str(item.get("name", ""))).name
        if not filename or filename.startswith(".") or filename == "product.yaml":
            continue
        if "content_base64" in item:
            (staging / filename).write_bytes(base64.b64decode(item["content_base64"]))
        else:
            (staging / filename).write_text(str(item.get("content", "")), encoding="utf-8")
    (staging / "product.yaml").write_text(yaml.safe_dump({
        "company": company, "name": name, "description": str(body.get("description", "")),
    }))
    target = inbox / pid
    shutil.rmtree(target, ignore_errors=True)
    staging.rename(target)  # appears in the inbox all at once
    return {"id": pid, "status": "queued"}


def list_prompts(cfg):
    return [{k: p.get(k, "") for k in ("file", "name", "description", "prompt")}
            for p in intake.load_prompts(cfg["paths"]["prompts"])]


def save_prompt(cfg, body, filename=None):
    name = str(body.get("name", "")).strip()
    text = str(body.get("prompt", "")).strip()
    if not name or not text:
        raise ApiError(400, "name and prompt are required")
    prompts_dir = cfg["paths"]["prompts"]
    if filename is None:
        existing = sorted(prompts_dir.glob("*.y*ml"))
        number = len(existing) + 1
        filename = f"{number:02d}_{intake.slug(name).replace('-', '_')}.yaml"
        if (prompts_dir / filename).exists():
            raise ApiError(409, f"{filename} already exists")
    elif not PROMPT_FILE.fullmatch(filename) or not (prompts_dir / filename).exists():
        raise ApiError(404, "unknown prompt")
    data = {"name": name, "description": str(body.get("description", "")).strip(), "prompt": text + "\n"}
    (prompts_dir / filename).write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=1000))
    return {"file": filename, **data}


def delete_prompt(cfg, filename):
    path = cfg["paths"]["prompts"] / filename
    if not PROMPT_FILE.fullmatch(filename) or not path.exists():
        raise ApiError(404, "unknown prompt")
    path.unlink()
    return {"deleted": filename}


def make_handler(cfg):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            if not self.path.startswith("/api/products"):  # the page polls; keep the log quiet
                super().log_message(fmt, *args)

        def _send(self, code, data, content_type):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _json(self, code, body):
            self._send(code, json.dumps(body, indent=2).encode(), "application/json")

        def _body(self):
            length = int(self.headers.get("Content-Length", 0))
            if length > MAX_BODY_BYTES:
                raise ApiError(413, "request too large (50 MB max)")
            try:
                return json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                raise ApiError(400, "body must be JSON")

        def _route(self, method):
            path = self.path.split("?", 1)[0].rstrip("/") or "/"
            if method == "GET" and path == "/":
                return self._send(200, (WEB_DIR / "index.html").read_bytes(), "text/html; charset=utf-8")
            if method == "GET" and path == "/api/status":
                return self._json(200, {
                    "model": cfg["llm"]["model"],
                    "fallback_models": cfg["llm"].get("fallback_models") or [],
                    "mcp_command": " ".join(intake.mcp_command(cfg)),
                    "api_key_env": cfg["llm"].get("api_key_env"),
                    "api_key_set": not cfg["llm"].get("api_key_env")
                                   or bool(os.environ.get(cfg["llm"]["api_key_env"])),
                })
            if path in ("/api/products", "/products"):
                if method == "GET":
                    return self._json(200, list_products(cfg))
                if method == "POST":
                    return self._json(202, submit_product(cfg, self._body()))
            match = re.fullmatch(r"/(?:api/)?products/([^/]+)", path)
            if match and method == "GET":
                return self._json(200, get_product(cfg, match.group(1)))
            if path == "/api/prompts":
                if method == "GET":
                    return self._json(200, list_prompts(cfg))
                if method == "POST":
                    return self._json(201, save_prompt(cfg, self._body()))
            match = re.fullmatch(r"/api/prompts/([^/]+)", path)
            if match and method == "PUT":
                return self._json(200, save_prompt(cfg, self._body(), match.group(1)))
            if match and method == "DELETE":
                return self._json(200, delete_prompt(cfg, match.group(1)))
            raise ApiError(404, "not found")

        def _handle(self, method):
            try:
                self._route(method)
            except ApiError as exc:
                self._json(exc.code, {"error": str(exc)})
            except Exception as exc:
                self._json(500, {"error": str(exc)})

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

        def do_PUT(self):
            self._handle("PUT")

        def do_DELETE(self):
            self._handle("DELETE")

    return Handler


def serve(cfg):
    for key in ("inbox", "outbox", "prompts"):
        cfg["paths"][key].mkdir(parents=True, exist_ok=True)
    threading.Thread(target=intake.watch, args=(cfg,), daemon=True).start()
    host, port = cfg["server"]["host"], cfg["server"]["port"]
    print(f"[serve] open http://{host}:{port} in your browser")
    ThreadingHTTPServer((host, port), make_handler(cfg)).serve_forever()
