import storage
from config import CFG
from llm import call_llm


def build_prompt_context(char, all_chars, objects, recent_events):
    others = [c for c in all_chars if c.id != char.id and c.alive]
    others_desc = "\n".join(
        f"- {c.name}: health {c.health}/100, stability {c.stability}/100"
        + (f", status: {', '.join(c.status_effects)}" if c.status_effects else "")
        for c in others
    ) or "(no one else is here)"

    obj_desc = "\n".join(f"- {o.name}: {o.description}" for o in objects) or "(the room is empty of objects)"

    log_desc = "\n".join(
        (f"(SYSTEM: {e.content})" if e.kind in ("system", "death") else f"[{e.character_name or 'SYSTEM'}] {e.content}")
        for e in recent_events
    ) or "(nothing has happened yet)"

    return others_desc, obj_desc, log_desc


def maybe_summarize(char):
    events = storage.get_character_events_since(char.id, char.last_summary_event_id)
    if len(events) < CFG.summarize_every:
        return

    transcript = "\n".join(f"[{e.character_name or 'SYSTEM'}] {e.content}" for e in events)
    system = (
        "You compress a character's recent experiences into a short first-person memory "
        "summary (5-8 sentences). Keep concrete facts: who they interacted with, what "
        "happened to them, unresolved feelings or grudges. Be concise and specific."
    )
    user = (
        f"Existing memory summary:\n{char.memory_summary or '(none yet)'}\n\n"
        f"New events since then:\n{transcript}\n\n"
        "Write the updated memory summary, from this character's point of view."
    )
    new_summary = call_llm(system, user, max_tokens=300)
    char.memory_summary = new_summary.strip()
    char.last_summary_event_id = events[-1].id
    storage.update_character(char)
