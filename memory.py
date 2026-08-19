import storage
from config import CFG
from llm import call_llm


def _relationship_note(char, other):
    """How char feels about other, in a few words, plus the most recent
    'significant events between us' if any exist — this is what turns a
    generic rival into an earned grudge instead of vague hostility."""
    rel = storage.get_relationship(char.id, other.id)
    if rel.affinity >= 60:
        feel = "loves"
    elif rel.affinity >= 25:
        feel = "likes"
    elif rel.affinity <= -60:
        feel = "hates"
    elif rel.affinity <= -25:
        feel = "dislikes"
    else:
        feel = "feels neutral about"
    trust_note = ""
    if rel.trust >= 50:
        trust_note = ", trusts them"
    elif rel.trust <= -50:
        trust_note = ", distrusts them"
    note = f"you {feel} {other.name}{trust_note}"
    events = storage.list_relationship_events(char.id, other.id, limit=2)
    if events:
        note += " (" + "; ".join(e.description for e in events) + ")"
    return note


def build_prompt_context(char, all_chars, objects, recent_events):
    here = [c for c in all_chars if c.id != char.id and c.alive and c.location == char.location]
    others_desc = "\n".join(
        f"- {c.name}: health {c.health}/100, stability {c.stability}/100"
        + (f", status: {', '.join(c.status_effects)}" if c.status_effects else "")
        + f" — {_relationship_note(char, c)}"
        for c in here
    ) or "(no one else is here)"

    elsewhere = [c for c in all_chars if c.id != char.id and c.alive and c.location != char.location]
    elsewhere_desc = "\n".join(
        f"- {c.name} (at {c.location}) — {_relationship_note(char, c)}" for c in elsewhere
    ) or ""

    obj_desc = "\n".join(f"- {o.name}: {o.description}" for o in objects) or "(the room is empty of objects)"

    # Mark this character's own past lines distinctly ("[YOU as X]" vs
    # "[Other]") so a less-steerable model doesn't have to infer authorship
    # from name-matching alone — that's exactly the kind of ambiguity that
    # leads to a character adopting or echoing someone else's lines as its
    # own. See docs/model-choice.md for the "confusing/pretending to be each
    # other" symptom this addresses.
    name_by_id = {c.id: c.name for c in all_chars}

    def _speaker_label(e):
        if e.character_id == char.id:
            return f"YOU as {char.name}"
        return e.character_name or "SYSTEM"

    def _format_event(e):
        if e.kind in ("system", "death"):
            return f"(SYSTEM: {e.content})"
        if e.kind == "message":
            channel = e.channel or "message"
            if e.character_id == char.id:
                to = name_by_id.get(e.target_id, "someone")
                return f"[YOU as {char.name}, {channel} to {to}] {e.content}"
            return f"[{e.character_name or 'someone'}, {channel} to you] {e.content}"
        return f"[{_speaker_label(e)}] {e.content}"

    log_desc = "\n".join(_format_event(e) for e in recent_events) or "(nothing has happened yet)"

    world_facts = storage.list_world_facts(CFG.world_facts_window)
    world_desc = "\n".join(f"- {f.content}" for f in world_facts)

    return others_desc, elsewhere_desc, obj_desc, log_desc, world_desc


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
