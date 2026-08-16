import memory
import storage
from config import CFG
from llm import call_llm_json

_turn_state = {"idx": 0}
_priority_queue = []  # character ids who were just addressed, so they respond promptly

CHARACTER_SYSTEM_TEMPLATE = """You are role-playing as {name}, one character living in an ongoing, unscripted simulation.
Persona: {persona}
Stay strictly in character. You do not know you are an AI. Respond ONLY as {name} would, given everything below.
Your current state: health {health}/100, emotional stability {stability}/100{status_line}.
Your standing memory of what's happened so far: {memory_summary}

Other people currently in the room:
{others}

Objects in the room:
{objects}
{focus_block}
Recent events (most recent last):
{log}
{stimuli_block}
This is a social scene, not a solo monologue. Most turns, you should be reacting to, addressing, or picking up \
on what someone else just said or did — look at the end of the recent events above before deciding what to do. \
If the most recent line was directed at you or clearly invites a response, respond to it now, by name, rather \
than changing the subject or narrating your own state in isolation. It's fine to act alone sometimes, but that \
should be the exception, not the default. Whenever your dialogue or action is aimed at someone, put their exact \
name in "target" — don't leave it null just because you didn't say their name out loud.

Respond with ONLY a compact JSON object, no other text, in this exact shape:
{{"thought": "a short private internal thought, not shown to others", "dialogue": "what you say out loud, or empty string if you stay silent", "action": "a short physical action you take, or empty string", "target": "name of another character your dialogue/action is directed at, or null"}}"""

ADJUDICATOR_SYSTEM = """You are a neutral narrator for a life simulation. You are given one character's action \
and/or dialogue directed at another character. Decide whether it causes the target physical harm, and if so \
roughly how much, on a 0-100 scale (0 = none, 20-30 = a solid hit/injury, 60+ = severe, 90+ = potentially lethal). \
Most ordinary actions (talking, gesturing, offering something, comforting) cause 0 harm. Only assign harm for \
clearly violent or dangerous physical actions.
Respond with ONLY JSON: {"harm": <int 0-100>, "reason": "<one short sentence>"}"""


def _next_character():
    chars = storage.list_characters(alive_only=True)
    if not chars:
        return None
    by_id = {c.id: c for c in chars}

    # Give the floor to whoever was just addressed, so replies land promptly
    # instead of waiting for a full round-robin cycle.
    while _priority_queue:
        candidate_id = _priority_queue.pop(0)
        if candidate_id in by_id:
            return by_id[candidate_id]

    ordered = sorted(chars, key=lambda c: c.id)
    idx = _turn_state["idx"] % len(ordered)
    _turn_state["idx"] += 1
    return ordered[idx]


def _apply_pending_interventions(char):
    pending = storage.pop_pending_interventions(char.id)
    stimuli_lines = []
    for row in pending:
        stimuli_lines.append(f"- {row['text']}")
        char.health = max(0, min(100, char.health + row["health_delta"]))
        char.stability = max(0, min(100, char.stability + row["stability_delta"]))
        if row["status_effect"] and row["status_effect"] not in char.status_effects:
            char.status_effects.append(row["status_effect"])
        storage.add_event("intervention", row["text"], character_id=char.id, character_name=char.name)
    if pending:
        storage.update_character(char)
    return stimuli_lines


def _adjudicate_harm(actor, target, action_text, dialogue_text):
    if not action_text and not dialogue_text:
        return 0, ""
    user = (
        f'{actor.name} does/says this, directed at {target.name}:\n'
        f'action="{action_text}"\ndialogue="{dialogue_text}"'
    )
    result = call_llm_json(ADJUDICATOR_SYSTEM, user, max_tokens=150, model=CFG.adjudicator_model)
    try:
        harm = int(result.get("harm", 0))
    except (TypeError, ValueError):
        harm = 0
    return max(0, min(100, harm)), result.get("reason", "")


def tick():
    char = _next_character()
    if char is None:
        storage.add_event("system", "The room is empty. Add a character to continue.")
        return

    stimuli_lines = _apply_pending_interventions(char)
    char = storage.get_character(char.id)  # reload after any intervention updates

    all_chars = storage.list_characters(alive_only=True)
    objects = storage.list_objects(location=char.location)
    recent_events = storage.get_recent_events(CFG.memory_window)
    others, obj_desc, log_desc = memory.build_prompt_context(char, all_chars, objects, recent_events)

    status_line = f", status effects: {', '.join(char.status_effects)}" if char.status_effects else ""
    stimuli_block = (
        "\nSomething just happened to you, physically or mentally:\n" + "\n".join(stimuli_lines) + "\n"
    ) if stimuli_lines else ""

    focus = storage.get_room_focus(char.location)
    focus_block = (
        f"\nSomething on people's minds right now, that you're aware of and can bring up or react to "
        f"(you don't have to make it the only thing you talk about): {focus}\n"
    ) if focus else ""

    system = CHARACTER_SYSTEM_TEMPLATE.format(
        name=char.name, persona=char.persona, health=char.health, stability=char.stability,
        status_line=status_line, memory_summary=char.memory_summary or "(no strong memories yet)",
        others=others, objects=obj_desc, log=log_desc, stimuli_block=stimuli_block, focus_block=focus_block,
    )
    result = call_llm_json(system, "Respond now, in character, as JSON only.")

    thought = (result.get("thought") or "").strip()
    dialogue = (result.get("dialogue") or "").strip()
    action = (result.get("action") or "").strip()
    target_name = result.get("target")
    target = next((c for c in all_chars if c.name == target_name), None)

    if target and target.id != char.id and (dialogue or action):
        _priority_queue.append(target.id)

    if thought:
        storage.add_event("thought", thought, character_id=char.id, character_name=char.name)
    if dialogue:
        storage.add_event("dialogue", dialogue, character_id=char.id, character_name=char.name,
                           target_id=target.id if target else None)
    if action:
        storage.add_event("action", action, character_id=char.id, character_name=char.name,
                           target_id=target.id if target else None)

    # Own health check (e.g. from an intervention this turn)
    if char.health <= 0 and char.alive:
        storage.kill_character(char.id)
        storage.add_event("death", f"{char.name} has died.", character_id=char.id, character_name=char.name)

    # Let a cheap model referee whether this turn's action/dialogue hurt someone else
    if target and target.alive and (action or dialogue):
        harm, reason = _adjudicate_harm(char, target, action, dialogue)
        if harm > 0:
            target.health = max(0, target.health - harm)
            storage.update_character(target)
            note = f"{target.name} is hurt" + (f" ({reason})" if reason else "") + f". Health: {target.health}/100."
            storage.add_event("system", note, character_id=target.id, character_name=target.name)
            if target.health <= 0 and target.alive:
                storage.kill_character(target.id)
                storage.add_event("death", f"{target.name} has died.",
                                   character_id=target.id, character_name=target.name)

    memory.maybe_summarize(char)
