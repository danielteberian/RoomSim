"""
Turns a day's raw event log into a short narrative chapter, using whichever
backend is configured in llm.py (Anthropic or your desktop's Ollama). This is
what powers the /read page.
"""

from typing import Optional

import storage
from llm import call_llm_json, as_text

CHAPTER_SYSTEM = """You are a skilled novelist adapting a raw event log from a life simulation into a short, \
well-written narrative chapter. Turn the dialogue, actions, and internal thoughts into flowing third-person \
prose — a real chapter, not a script. Preserve what actually happened accurately: who said and did what, any \
injuries, deaths, or new arrivals, and roughly the order things occurred in. You may add light scene-setting \
and interiority to connect the events, but do not invent events that aren't in the log. Separate paragraphs \
with a blank line. Aim for roughly 500-1000 words.

Respond with ONLY JSON in this exact shape:
{"title": "a short, evocative chapter title", "content": "the full chapter text, with blank lines between paragraphs"}"""


def _format_log(events) -> str:
    lines = []
    for e in events:
        who = e.character_name or "—"
        if e.kind == "dialogue":
            lines.append(f'{who} said: "{e.content}"')
        elif e.kind == "action":
            lines.append(f"*{who} {e.content}*")
        elif e.kind == "thought":
            lines.append(f"({who} privately thought: {e.content})")
        elif e.kind == "message":
            lines.append(f'{who} sent a {e.channel or "message"}: "{e.content}"')
        elif e.kind in ("system", "death", "intervention"):
            lines.append(f"[{e.content}]")
    return "\n".join(lines)


def generate_and_store_chapter(date_str: str) -> Optional[dict]:
    events = storage.get_events_for_date(date_str)
    if not events:
        return None

    transcript = _format_log(events)
    user = f"Date: {date_str}\n\nRaw event log:\n{transcript}\n\nWrite the chapter now."
    result = call_llm_json(CHAPTER_SYSTEM, user, max_tokens=1800)

    title = as_text(result.get("title")).strip()
    content = as_text(result.get("content")).strip()
    if not content:
        # The model didn't return usable prose even after call_llm_json's retry —
        # fall back to the raw log rather than losing the day, but at least break
        # it into readable paragraphs (one per speaker turn) instead of one wall
        # of unbroken text, and say plainly that this isn't the real chapter.
        title = f"Chapter — {date_str} (raw log, narration failed)"
        content = "\n\n".join(_format_log(events).split("\n"))
    elif not title:
        title = f"Chapter — {date_str}"

    storage.add_chapter(date_str, title, content)
    return {"date": date_str, "title": title, "content": content}
