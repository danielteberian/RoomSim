import json
import re

import requests

from config import CFG

# Anthropic client is created lazily/optionally — not needed at all if you're
# running purely on the ollama backend.
_anthropic_client = None
if CFG.backend == "anthropic" and CFG.anthropic_api_key:
    import anthropic
    _anthropic_client = anthropic.Anthropic(api_key=CFG.anthropic_api_key)


def _call_anthropic(system: str, user: str, max_tokens: int, model: str) -> str:
    if _anthropic_client is None:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Export it, or set SIM_BACKEND=ollama to use a local model instead."
        )
    resp = _anthropic_client.messages.create(
        model=model or CFG.model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(block.text for block in resp.content if block.type == "text")


def _call_ollama(system: str, user: str, max_tokens: int, model: str) -> str:
    url = f"{CFG.ollama_host.rstrip('/')}/api/chat"
    payload = {
        "model": model or CFG.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "options": {"num_predict": max_tokens},
    }
    try:
        resp = requests.post(url, json=payload, timeout=180)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise RuntimeError(
            f"Couldn't reach Ollama at {CFG.ollama_host} ({e}). "
            "Check that Ollama is running on the host machine, that it's bound to "
            "0.0.0.0 (not just localhost), and that SIM_OLLAMA_HOST points at the "
            "right LAN IP and port."
        ) from e
    data = resp.json()
    return data.get("message", {}).get("content", "")


def call_llm(system: str, user: str, max_tokens: int = 600, model: str = None) -> str:
    if CFG.backend == "ollama":
        return _call_ollama(system, user, max_tokens, model)
    return _call_anthropic(system, user, max_tokens, model)


def call_llm_json(system: str, user: str, max_tokens: int = 600, model: str = None) -> dict:
    """Ask the model for JSON and parse it, tolerating minor formatting slop.
    Local models are more prone to wrapping JSON in commentary or code fences
    than Claude is, so this is deliberately forgiving."""
    raw = call_llm(system, user, max_tokens=max_tokens, model=model)
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return {"thought": "", "dialogue": raw.strip(), "action": "", "target": None}
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return {"thought": "", "dialogue": raw.strip(), "action": "", "target": None}
