"""Turn user input (URL or product name) into a service, then pull its docs through MCP."""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from urllib.parse import urlparse

import yaml

from .mcp_client import MastercardMCP

DOCS_HOSTS = {"developer.mastercard.com", "static.developer.mastercard.com"}
SERVICE_ID_RE = re.compile(r"^[a-z]+(?:-[a-z0-9]+)*$", re.I)
SPEC_PATH_RE = re.compile(r"(/[a-z0-9-]+/swagger/[A-Za-z0-9_./-]+?\.(?:ya?ml|json))", re.I)
MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((/[^)\s]+)\)(?::\s*(.*))?")
HTTP_METHODS = "GET|POST|PUT|PATCH|DELETE"

# Sections most likely to hold terminology, security, auth and field definitions.
SECTION_PRIORITY = [
    "api-reference", "security", "authentication", "encryption", "glossary", "terminology",
    "getting-started", "quick-start", "overview", "use-cases", "tutorials", "testing",
]


def parse_target(raw: str) -> dict:
    """`"Label=value"` or `"value"`; value is a docs URL, spec URL or product name."""
    label, _, value = raw.partition("=") if "=" in raw and not raw.startswith("http") else ("", "", raw)
    value = value.strip()
    target = {"label": label.strip() or value, "input": value, "service_id": None, "spec_paths": []}
    url = urlparse(value)
    if url.scheme in ("http", "https") and url.hostname in DOCS_HOSTS:
        parts = [p for p in url.path.split("/") if p]
        if parts and parts[0] == "content":  # static.developer.mastercard.com/content/<svc>/swagger/...
            parts = parts[1:]
        if parts and SERVICE_ID_RE.match(parts[0]):
            target["service_id"] = parts[0].lower()
        spec = SPEC_PATH_RE.search("/" + "/".join(parts))
        if spec:
            target["spec_paths"].append(spec.group(1))
        if not label:
            target["label"] = target["service_id"] or value
    return target


def parse_services(llms_txt: str) -> list[dict]:
    """Services from the root llms.txt (markdown links whose path starts with /<serviceId>/)."""
    services, seen = [], set()
    for title, path, desc in MD_LINK_RE.findall(llms_txt):
        sid = path.strip("/").split("/")[0].lower()
        if SERVICE_ID_RE.match(sid) and sid not in seen and "/documentation" in path:
            seen.add(sid)
            services.append({"service_id": sid, "title": title.strip(), "description": (desc or "").strip()})
    return services


def match_service(name: str, services: list[dict]) -> list[dict]:
    """Rank services by similarity to a product name (title, id and description)."""
    needle = name.lower()
    words = [w for w in re.findall(r"[a-z0-9]+", needle) if len(w) > 2]

    def score(s: dict) -> float:
        hay = f"{s['title']} {s['service_id'].replace('-', ' ')}".lower()
        ratio = SequenceMatcher(None, needle, s["title"].lower()).ratio()
        hits = sum(w in hay for w in words) + 0.5 * sum(w in s["description"].lower() for w in words)
        return hits + ratio

    ranked = sorted(services, key=score, reverse=True)
    return [s for s in ranked if score(s) > 0.6][:5]


def parse_sections(service_id: str, llms_txt: str) -> list[dict]:
    """Section ids are the first path segment after /<svc>/documentation/ in the service llms.txt."""
    sections: dict[str, dict] = {}
    prefix = f"/{service_id}/documentation/"
    for title, path, desc in MD_LINK_RE.findall(llms_txt):
        if not path.startswith(prefix):
            continue
        rest = path[len(prefix):].strip("/").split("/")
        sid = rest[0] if rest and rest[0] and not rest[0].endswith(".md") else None
        if sid and sid not in sections:
            sections[sid] = {"section_id": sid, "title": title.strip(), "pages": []}
        if sid:
            sections[sid]["pages"].append(path)
    return list(sections.values())


def pick_sections(sections: list[dict], limit: int) -> list[str]:
    def rank(s: dict) -> int:
        key = f"{s['section_id']} {s['title']}".lower()
        for i, word in enumerate(SECTION_PRIORITY):
            if word in key or word.replace("-", " ") in key:
                return i
        return len(SECTION_PRIORITY)

    return [s["section_id"] for s in sorted(sections, key=rank)[:limit]]


def find_spec_paths(*texts: str) -> list[str]:
    found: list[str] = []
    for text in texts:
        for m in SPEC_PATH_RE.findall(text or ""):
            path = re.sub(r"^/content", "", m)
            if path not in found:
                found.append(path)
    return found


def _load_structured(text: str):
    try:
        return yaml.safe_load(text)  # also parses JSON
    except Exception:
        return None


def parse_operations(text: str) -> list[tuple[str, str]]:
    ops: list[tuple[str, str]] = []
    data = _load_structured(text)

    def walk(node):
        if isinstance(node, dict):
            method, path = node.get("method"), node.get("path")
            if isinstance(method, str) and isinstance(path, str) and path.startswith("/"):
                ops.append((method.upper(), path))
            if isinstance(node.get("paths"), dict):  # raw OpenAPI document
                for p, item in node["paths"].items():
                    for m in (item or {}):
                        if m.upper() in HTTP_METHODS.split("|"):
                            ops.append((m.upper(), p))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    if not ops:
        ops = [(m.upper(), p) for m, p in re.findall(rf"\b({HTTP_METHODS})\b[\s`|]+(/[A-Za-z0-9_{{}}/.~-]*)", text)]
    return list(dict.fromkeys(ops))


def parse_fields(text: str, operation: str) -> list[dict]:
    """Collect parameters and schema properties from an operation-details response."""
    fields: dict[str, dict] = {}
    data = _load_structured(text)

    def add(name, info, location):
        if not isinstance(name, str) or name in fields:
            return
        info = info if isinstance(info, dict) else {}
        schema = info.get("schema") if isinstance(info.get("schema"), dict) else {}
        fields[name] = {
            "name": name,
            "type": str(info.get("type") or schema.get("type") or ""),
            "location": location,
            "required": bool(info.get("required") is True),
            "description": " ".join(str(info.get("description") or "").split())[:200],
            "operation": operation,
        }

    def walk(node, location="body"):
        if isinstance(node, dict):
            for p in node.get("parameters") or []:
                if isinstance(p, dict):
                    add(p.get("name"), p, p.get("in", "parameter"))
            props = node.get("properties")
            if isinstance(props, dict):
                for name, info in props.items():
                    add(name, info, location)
            for key, v in node.items():
                walk(v, "response" if key in ("responses", "response") else location)
        elif isinstance(node, list):
            for v in node:
                walk(v, location)

    if isinstance(data, (dict, list)):
        walk(data)
    if not fields:  # plain-text/markdown fallback: `fieldName` (type)
        for name, typ in re.findall(r"`([A-Za-z_][\w.]*)`\s*\(?\s*(string|integer|number|boolean|object|array)?", text):
            add(name, {"type": typ}, "unknown")
    return list(fields.values())


async def resolve_target(mcp: MastercardMCP, target: dict, services: list[dict]) -> dict:
    if target["service_id"]:
        return target
    matches = match_service(target["input"], services)
    target["candidates"] = matches
    if matches:
        target["service_id"] = matches[0]["service_id"]
    return target


async def collect_target(mcp: MastercardMCP, target: dict, max_sections: int, max_operations: int) -> dict:
    """Pull documentation, OpenAPI operations and auth guides for one resolved target."""
    sid = target["service_id"]
    docs: dict[str, str] = {}
    if not sid:
        return {**target, "docs": docs, "fields": [], "operations": []}

    index = await mcp.call("get-documentation", serviceId=sid)
    docs["llms.txt (documentation index)"] = index
    sections = parse_sections(sid, index)
    for section_id in pick_sections(sections, max_sections):
        docs[f"section: {section_id}"] = await mcp.call(
            "get-documentation-section-content", serviceId=sid, sectionId=section_id
        )

    spec_paths = target["spec_paths"] or find_spec_paths(*docs.values())
    operations, fields = [], []
    for spec in spec_paths[:2]:
        op_list = await mcp.call("get-api-operation-list", apiSpecificationPath=spec)
        docs[f"openapi operations: {spec}"] = op_list
        for method, path in parse_operations(op_list)[:max_operations]:
            detail = await mcp.call("get-api-operation-details", apiSpecificationPath=spec, method=method, path=path)
            op = f"{method} {path}"
            operations.append({"spec": spec, "operation": op})
            docs[f"operation: {op}"] = detail
            fields.extend(parse_fields(detail, op))

    corpus = "\n".join(docs.values()).lower()
    if "oauth 1.0a" in corpus or "oauth1" in corpus or target.get("include_auth_guides"):
        docs["guide: OAuth 1.0a"] = await mcp.call("get-oauth10a-integration-guide", language="python")
    if "oauth 2" in corpus or "fapi" in corpus or target.get("include_auth_guides"):
        docs["guide: OAuth 2.0"] = await mcp.call("get-oauth20-integration-guide", language="others")

    return {
        **target,
        "spec_paths": spec_paths,
        "sections": [s["section_id"] for s in sections],
        "docs": {k: v for k, v in docs.items() if v},
        "operations": operations,
        "fields": fields,
    }


def to_json(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False)
