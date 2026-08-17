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
        temperature=min(1.0, CFG.temperature),
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
        "options": {
            "num_predict": max_tokens,
            "temperature": CFG.temperature,
            # The main lever against small local models looping the same line
            # verbatim — penalizes tokens that already appeared recently.
            "repeat_penalty": CFG.ollama_repeat_penalty,
            "repeat_last_n": 256,
        },
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


# Leftover chat-template control tokens some local models leak into their output
# when they run past their intended stop point or the chat template isn't a
# perfect match for how Ollama is invoking them.
_CHAT_TEMPLATE_JUNK = (
    "<|im_end|>", "<|im_start|>", "<|endoftext|>", "<|eot_id|>", "<|end_of_text|>",
    "<|start_header_id|>", "<|end_header_id|>", "</s>", "<s>", "[INST]", "[/INST]",
    "<<SYS>>", "<</SYS>>",
)
# Meta-commentary preambles ("Here's the updated memory summary:") that leak into
# plain-text (non-JSON) calls like the memory summarizer, since there's no JSON
# boundary there to strip them at.
_PREAMBLE_RE = re.compile(
    r"^(?:sure[,!.]?\s*)?(?:here'?s|here is|okay,?|alright,?)\b[^\n]{0,120}?:\s*",
    re.IGNORECASE,
)
_CHATTY_OPENER_RE = re.compile(r"^(?:sure|okay|ok|alright)[,!.]+\s*", re.IGNORECASE)
# Stray control/replacement characters (decoding glitches, truncated multi-byte
# sequences) — not real unicode content, just noise. Newline/tab/CR excluded.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f﻿�]")


def _clean_text(text: str) -> str:
    for token in _CHAT_TEMPLATE_JUNK:
        text = text.replace(token, "")
    text = _CONTROL_CHARS_RE.sub("", text)
    text = text.strip()
    text = _PREAMBLE_RE.sub("", text, count=1)
    text = _CHATTY_OPENER_RE.sub("", text, count=1)
    return text.strip()


def as_text(value) -> str:
    """Coerce a JSON-parsed field to a plain string. Local models occasionally
    put a nested object or number where a string was asked for (e.g. a
    "thought" field that comes back as {"content": "..."} instead of a plain
    string) — callers that immediately do .strip() on a result field should
    route it through this first instead of crashing the tick."""
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return str(value)


def call_llm(system: str, user: str, max_tokens: int = 600, model: str = None) -> str:
    if CFG.backend == "ollama":
        raw = _call_ollama(system, user, max_tokens, model)
    else:
        raw = _call_anthropic(system, user, max_tokens, model)
    return _clean_text(raw)


def _extract_json(raw: str):
    """Find the first balanced {...} object in raw text and parse it, respecting
    quoted strings so braces inside dialogue/prose don't throw off the match.
    A naive greedy regex (old approach) grabs from the first '{' to the LAST '}'
    in the whole response, which breaks as soon as a model adds any trailing
    commentary or a second brace-containing sentence after the JSON."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"```\s*$", "", text).strip()

    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start:i + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    return None
    return None


def call_llm_json(system: str, user: str, max_tokens: int = 600, model: str = None, retry: bool = True) -> dict:
    """Ask the model for JSON and parse it, tolerating minor formatting slop.
    Local models are more prone to wrapping JSON in commentary or code fences
    than Claude is, so this is deliberately forgiving. Retries once with a
    sharper instruction if the first response doesn't parse, since that's
    cheaper than silently falling back to unstructured text."""
    raw = call_llm(system, user, max_tokens=max_tokens, model=model)
    parsed = _extract_json(raw)
    if parsed is not None:
        return parsed

    if retry:
        sharper_user = user + "\n\nYour previous response was not valid JSON. Respond with ONLY the JSON object — no commentary, no code fences, nothing before or after it."
        return call_llm_json(system, sharper_user, max_tokens=max_tokens, model=model, retry=False)

    return {"thought": "", "dialogue": raw.strip(), "action": "", "target": None}
