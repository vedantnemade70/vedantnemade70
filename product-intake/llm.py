"""Chat client for OpenRouter (or any OpenAI-compatible endpoint), using only the standard library.

OpenRouter: https://openrouter.ai/api/v1  (key in OPENROUTER_API_KEY)
Ollama:     http://localhost:11434/v1     (no key needed)
"""

import json
import os
import time
import urllib.error
import urllib.request


class LLMError(Exception):
    pass


class ChatClient:
    def __init__(self, cfg):
        self.base_url = cfg["base_url"].rstrip("/")
        self.api_key = os.environ.get(cfg.get("api_key_env") or "", "")
        self.model = cfg["model"]
        self.fallback_models = list(cfg.get("fallback_models") or [])
        self.max_tokens = cfg.get("max_tokens", 8000)
        self.timeout = cfg.get("timeout_seconds", 300)
        self.max_retries = cfg.get("max_retries", 4)

    def _post(self, path, payload):
        headers = {
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/vedantnemade70/vedantnemade70",
            "X-Title": "Product Intake",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(self.base_url + path, data=json.dumps(payload).encode(),
                                     headers=headers, method="POST")
        for attempt in range(self.max_retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as res:
                    body = json.loads(res.read())
            except urllib.error.HTTPError as err:
                detail = err.read().decode(errors="replace")[:500]
                # Free models are rate limited; 429 and 5xx are worth retrying.
                if (err.code == 429 or err.code >= 500) and attempt < self.max_retries:
                    wait = float(err.headers.get("Retry-After") or 0) or 5 * 2 ** attempt
                    print(f"    [llm] HTTP {err.code}, retrying in {wait:.0f}s")
                    time.sleep(min(wait, 120))
                    continue
                raise LLMError(f"HTTP {err.code}: {detail}") from None
            except urllib.error.URLError as err:
                if attempt < self.max_retries:
                    time.sleep(5 * 2 ** attempt)
                    continue
                raise LLMError(f"cannot reach {self.base_url}: {err.reason}") from None
            if "error" in body:  # OpenRouter can report provider errors with HTTP 200
                err = body["error"]
                if attempt < self.max_retries and err.get("code") in (429, 502, 503):
                    time.sleep(5 * 2 ** attempt)
                    continue
                raise LLMError(f"{err.get('code')}: {err.get('message')}")
            return body
        raise LLMError("gave up after retries")

    def chat(self, messages, tools):
        """One chat completion. Returns (assistant_message, finish_reason, model_used)."""
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
        }
        if tools:
            payload["tools"] = tools
        if self.fallback_models:
            payload["models"] = [self.model] + self.fallback_models  # OpenRouter tries these in order
        if any(isinstance(m.get("content"), list) and any(p.get("type") == "file" for p in m["content"])
               for m in messages):
            payload["plugins"] = [{"id": "file-parser", "pdf": {"engine": "pdf-text"}}]  # free PDF parsing
        body = self._post("/chat/completions", payload)
        if not body.get("choices"):
            raise LLMError(f"empty response: {json.dumps(body)[:300]}")
        choice = body["choices"][0]
        return choice["message"], choice.get("finish_reason"), body.get("model", self.model)


def list_free_tool_models(base_url="https://openrouter.ai/api/v1"):
    """Free OpenRouter models that accept tools, newest first."""
    with urllib.request.urlopen(base_url.rstrip("/") + "/models", timeout=60) as res:
        models = json.loads(res.read())["data"]
    free = [
        m for m in models
        if "tools" in (m.get("supported_parameters") or [])
        and float(m.get("pricing", {}).get("prompt", 1)) == 0
        and float(m.get("pricing", {}).get("completion", 1)) == 0
    ]
    return sorted(free, key=lambda m: m.get("created", 0), reverse=True)
