"""Build the comparison report: terminology, API security, authentication methods, fields."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone

from pydantic import BaseModel, Field

DEFAULT_MODEL = "claude-sonnet-5-5"
MAX_CORPUS_CHARS = 120_000

# Keyword rules for the --no-llm path. Each maps a label to patterns found in the docs.
AUTH_PATTERNS = {
    "OAuth 1.0a (RSA-SHA256 signed requests, consumer key + .p12 signing key)": r"oauth\s*1\.0a|oauth1|oauth_signature|consumer key",
    "OAuth 2.0 (client credentials / FAPI 2.0 access tokens)": r"oauth\s*2(\.0)?|client[_ ]credentials|access[_ ]token|fapi",
    "Mutual TLS (mTLS) / client certificates": r"mutual tls|\bmtls\b|client certificate",
    "DPoP-bound tokens": r"\bdpop\b",
    "API key / app token": r"api[- ]key|app[- ]token|partner[- ]id",
    "JWT / JWS signed assertions": r"\bjws\b|\bjwt\b|private_key_jwt|client assertion",
}
SECURITY_PATTERNS = {
    "TLS 1.2+ transport encryption": r"tls\s*1\.[23]|https only|transport layer security",
    "Payload encryption (JWE / Mastercard field-level encryption)": r"\bjwe\b|field[- ]level encryption|payload encryption|encrypted payload",
    "Request signing / body hash": r"oauth_body_hash|request signing|signature base string",
    "Sandbox vs. production key separation": r"sandbox key|production key|sandbox and production",
    "PCI DSS scope / card data handling": r"pci[- ]dss|\bpan\b|primary account number",
    "Tokenization of card credentials": r"tokeni[sz]ation|\bmdes\b|network token",
    "Idempotency / replay protection": r"idempotency|\bnonce\b|replay",
    "IP allow-listing": r"ip (white|allow)[- ]?list",
    "Consent / customer authorization": r"consent|customer authori[sz]ation|strong customer authentication|\bsca\b",
}
BOLD_TERM_RE = re.compile(r"^\s*(?:[-*]\s*)?\*\*([^*]{2,40}?)\*\*\s*[:\-–]\s*(.{10,240})$", re.M)
NOT_TERMS = {"important", "note", "notes", "warning", "tip", "example", "caution"}


class Term(BaseModel):
    term: str
    definition: str


class FieldInfo(BaseModel):
    name: str
    type: str = ""
    location: str = Field("", description="path, query, header, body or response")
    description: str = ""


class ProductProfile(BaseModel):
    terminology: list[Term] = Field(description="Key domain terms and acronyms with short definitions")
    api_security: list[str] = Field(description="Security controls: transport, encryption, signing, data protection")
    authentication_methods: list[str] = Field(description="How API callers authenticate, with key/credential details")
    fields: list[FieldInfo] = Field(description="Important request/response fields of the main API operations")


def corpus(target: dict) -> str:
    parts = [f"### SOURCE: {name}\n{text}" for name, text in target.get("docs", {}).items()]
    return "\n\n".join(parts)[:MAX_CORPUS_CHARS]


# ---------------------------------------------------------------- heuristic path

def extract_terms(text: str) -> list[dict]:
    """Glossary-style definitions: `**Term**: definition` lines and Term/Definition tables."""
    terms: dict[str, dict] = {}
    for term, definition in BOLD_TERM_RE.findall(text):
        if term.strip().rstrip(":").lower() not in NOT_TERMS:
            terms.setdefault(term.strip().lower(), {"term": term.strip(), "definition": definition.strip()})
    lines = text.splitlines()
    for i, line in enumerate(lines[:-1]):
        head = [c.strip().lower() for c in line.strip().strip("|").split("|")]
        if len(head) < 2 or not re.match(r"^\s*\|?\s*:?-{3}", lines[i + 1]):
            continue
        if re.search(r"term|acronym|abbreviation|name|keyword", head[0]) and \
                re.search(r"definition|description|meaning", head[1]):
            for row in lines[i + 2:]:
                cells = [c.strip() for c in row.strip().strip("|").split("|")]
                if not row.strip().startswith("|") or len(cells) < 2:
                    break
                term = cells[0].strip("*` ")
                terms.setdefault(term.lower(), {"term": term, "definition": cells[1]})
    return list(terms.values())


def heuristic_profile(target: dict) -> dict:
    text = corpus(target)
    low = text.lower()
    terms = extract_terms(text)
    return {
        "terminology": terms[:25],
        "api_security": [label for label, pat in SECURITY_PATTERNS.items() if re.search(pat, low)],
        "authentication_methods": [label for label, pat in AUTH_PATTERNS.items() if re.search(pat, low)],
        "fields": target.get("fields", [])[:40],
    }


# ---------------------------------------------------------------- LLM path

def _llm():
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(model=os.getenv("MC_AGENT_MODEL", DEFAULT_MODEL), max_tokens=8000)


def llm_profile(target: dict) -> dict:
    if not target.get("docs"):
        return heuristic_profile(target)
    prompt = (
        f"You are analysing Mastercard developer documentation for the product '{target['label']}' "
        f"(serviceId: {target.get('service_id')}). Using ONLY the sources below, extract the product's "
        "terminology, API security controls, authentication methods and the important API fields. "
        "Prefer concrete facts (algorithms, header names, key types, field names). If a topic is not "
        "covered by the sources, return an empty list for it rather than guessing.\n\n"
        f"Fields already parsed from the OpenAPI operations (verify and enrich):\n{target.get('fields', [])[:60]}\n\n"
        f"{corpus(target)}"
    )
    profile = _llm().with_structured_output(ProductProfile, method="json_schema").invoke(prompt)
    return profile.model_dump()


def llm_comparison(targets: list[dict]) -> str:
    blocks = "\n\n".join(
        f"## {t['label']} (serviceId: {t.get('service_id')})\n{t['profile']}" for t in targets
    )
    prompt = (
        "Write a concise comparison report in GitHub Markdown of the Mastercard products below, based ONLY on "
        "the extracted profiles. Use exactly these sections, each with a comparison table (one column per "
        "product) followed by 2-4 bullet takeaways:\n"
        "## 1. Terminology\n## 2. API Security\n## 3. Authentication Methods\n## 4. Fields\n"
        "Then '## Key Differences' with at most 5 bullets. Write 'Not documented' where a profile has no data. "
        "Do not add a title or an intro paragraph.\n\n" + blocks
    )
    return _llm().invoke(prompt).content


# ---------------------------------------------------------------- rendering

def _cell(items: list[str]) -> str:
    return "<br>".join(i.replace("|", "\\|") for i in items) if items else "_Not documented_"


def heuristic_comparison(targets: list[dict]) -> str:
    labels = [t["label"] for t in targets]
    head = "| | " + " | ".join(labels) + " |\n|---|" + "---|" * len(labels) + "\n"
    out = []

    out.append("## 1. Terminology\n")
    for t in targets:
        terms = t["profile"]["terminology"]
        out.append(f"\n**{t['label']}**\n\n")
        if terms:
            out.append("| Term | Definition |\n|---|---|\n" + "".join(
                f"| {x['term']} | {x['definition'].replace('|', ' ')} |\n" for x in terms))
        else:
            out.append("_No glossary-style definitions found in the retrieved sections._\n")
    shared = set.intersection(*[{x["term"].lower() for x in t["profile"]["terminology"]} for t in targets]) if targets else set()
    out.append(f"\nShared terms: {', '.join(sorted(shared)) or 'none'}\n")

    for title, key, catalogue in (("2. API Security", "api_security", SECURITY_PATTERNS),
                                  ("3. Authentication Methods", "authentication_methods", AUTH_PATTERNS)):
        out.append(f"\n## {title}\n\n" + head)
        for label in catalogue:
            marks = ["Yes" if label in t["profile"][key] else "—" for t in targets]
            out.append(f"| {label} | " + " | ".join(marks) + " |\n")

    out.append("\n## 4. Fields\n")
    for t in targets:
        fields = t["profile"]["fields"]
        out.append(f"\n**{t['label']}** — {len(t.get('operations', []))} operation(s) inspected\n\n")
        if fields:
            out.append("| Field | Type | Location | Operation | Description |\n|---|---|---|---|---|\n" + "".join(
                f"| `{f['name']}` | {f.get('type', '')} | {f.get('location', '')} | {f.get('operation', '')} | "
                f"{f.get('description', '').replace('|', ' ')} |\n" for f in fields))
        else:
            out.append("_No OpenAPI fields retrieved._\n")
    names = [{f["name"] for f in t["profile"]["fields"]} for t in targets]
    if len(names) > 1:
        out.append(f"\nFields in common: {', '.join(sorted(set.intersection(*names))) or 'none'}\n")
    return "".join(out)


def render_report(targets: list[dict], comparison: str, tools: list[dict], tool_log: list, mode: str) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# Mastercard API Comparison: {' vs '.join(t['label'] for t in targets)}\n",
        f"_Generated {now} by the LangGraph Mastercard docs agent ({mode} mode)._\n",
        "\n## Products compared\n\n| Product | Input | serviceId | Sections read | OpenAPI specs |\n|---|---|---|---|---|\n",
    ]
    for t in targets:
        read = [k.removeprefix("section: ") for k in t.get("docs", {}) if k.startswith("section: ")]
        lines.append(f"| {t['label']} | {t['input']} | `{t.get('service_id') or 'unresolved'}` | "
                     f"{', '.join(read) or '—'} | {', '.join(t.get('spec_paths', [])[:2]) or '—'} |\n")
    unresolved = [t for t in targets if not t.get("docs")]
    for t in unresolved:
        hint = ", ".join(f"`{c['service_id']}`" for c in t.get("candidates", [])) or "none"
        lines.append(f"\n> **No documentation retrieved for {t['label']}.** serviceId: "
                     f"`{t.get('service_id') or 'unresolved'}`; closest services: {hint}. "
                     "Pass its docs URL (`https://developer.mastercard.com/<service-id>/documentation/`) instead.\n")
    lines.append("\n" + comparison.strip() + "\n")
    lines.append("\n## Appendix: MCP server tools and calls\n\n")
    lines.append("Tools exposed by the server: " + ", ".join(f"`{t['name']}`" for t in tools) + "\n\n")
    ok = sum(c.ok for c in tool_log)
    lines.append(f"{len(tool_log)} tool calls, {ok} succeeded.\n\n| Tool | Arguments | Result |\n|---|---|---|\n")
    for c in tool_log:
        args = ", ".join(f"{k}={v}" for k, v in c.args.items())
        lines.append(f"| `{c.tool}` | {args} | {f'{c.chars} chars' if c.ok else 'error: ' + ' '.join((c.error or '').split())[:120].replace('|', ' ')} |\n")
    return "".join(lines)
