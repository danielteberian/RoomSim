import re

import memory
import storage
import watchdog
from config import CFG
from llm import call_llm_json, as_text

_turn_state = {"idx": 0}
_priority_queue = []  # character ids who were just addressed, so they respond promptly

CHARACTER_SYSTEM_TEMPLATE = """You are role-playing as {name}, one character living in an ongoing, unscripted simulation.
Persona: {persona}
Stay strictly in character. You do not know you are an AI. Respond ONLY as {name} would, given everything below.
Your current state: health {health}/100, emotional stability {stability}/100{status_line}.
Your standing memory of what's happened so far: {memory_summary}
{status_effect_block}{setting_block}
Other people currently in the room:
{others}

Objects in the room:
{objects}
{focus_block}{directive_block}
Recent events (most recent last):
{log}
{stimuli_block}
If the most recent line was directed at you or clearly invites a response, respond to it now, by name, rather \
than ignoring it. Otherwise, drive the scene yourself: pursue what your persona actually wants, bring up a topic \
you care about, start something, react to an object, needle someone, follow a grudge or an interest — don't just \
wait around for other people to hand you something to respond to. A room where everyone only reacts to everyone \
else goes nowhere; be a source of new material as often as you are a responder. The objects listed above are not \
set dressing — pick them up, use them, fight over them, hide them, break them, when it fits your persona and the \
moment; a scene where nobody ever touches anything in the room is a bug. Do not repeat a line or action you or \
someone else already did in the recent events above — if the scene has stalled on the same beat (same insult, \
same gesture) for more than a turn or two, escalate it, change tactics, or move on to something new. Whenever \
your dialogue or action is aimed at someone, put their exact name in "target" — don't leave it null just because \
you didn't say their name out loud. If you have an objective above, work out a concrete way to pursue it this \
turn rather than just thinking about it — actually say or do something that moves it forward. If it has now been \
achieved, or has clearly become impossible, include the literal text DIRECTIVE COMPLETE somewhere in your thought.
Wanting something is not the same as doing something about it — a thought alone changes nothing in the room. \
"dialogue" and "action" may NOT both be empty. Every turn, you must actually say something out loud or physically \
do something (ideally both) — ask for the thing, reach for it, block the person in your way, make your move. \
Total silence and stillness (both fields empty) is only for genuinely extreme cases like being unconscious or \
bound and gagged — not a default or a safe choice.

Respond with ONLY a compact JSON object, no other text, in this exact shape:
{{"thought": "a short private internal thought, not shown to others", "dialogue": "what you say out loud — should almost never be empty", "action": "a short physical action you take — should almost never be empty", "target": "name of another character your dialogue/action is directed at, or null"}}"""

ADJUDICATOR_SYSTEM = """You are a neutral narrator for a life simulation. You are given one character's action \
and/or dialogue directed at another character. Decide whether it causes the target physical harm, and if so \
roughly how much, on a 0-100 scale (0 = none, 20-30 = a solid hit/injury, 60+ = severe, 90+ = potentially lethal). \
Harm requires actual physical contact or a genuinely dangerous physical act (a punch that lands, a thrown object \
that hits, a weapon used, a fall, etc). Posturing, threats, insults, aggressive body language, clenched fists, \
raised voices, or approaching someone are NOT harm by themselves — score those 0 even if they're intimidating. \
If your reason describes no physical contact and no direct danger, harm MUST be 0.
Respond with ONLY JSON: {"harm": <int 0-100>, "reason": "<one short sentence>"}"""

_NO_CONTACT_PHRASES = ("no physical contact", "no direct contact", "no contact", "no direct threat",
                       "no direct physical", "without contact", "not physical")


def prioritize(char_id):
    """Force char_id to take the very next turn (e.g. right after an admin
    intervention, so its effect is visible immediately instead of waiting for
    round-robin order to reach them)."""
    _priority_queue.insert(0, char_id)


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
    reason = as_text(result.get("reason", ""))
    # Safety net: models occasionally contradict their own reasoning (say "no
    # contact" but still return nonzero harm). Trust the stated reason over the number.
    if harm > 0 and any(p in reason.lower() for p in _NO_CONTACT_PHRASES):
        harm = 0
    return max(0, min(100, harm)), reason


def force_interaction(actor_id, target_id, action_text="", dialogue_text=""):
    """Admin-authored 'X does this to Y' — puppet-master one character directly,
    instead of waiting for the model to decide to interact. Logged exactly like a
    real turn (same harm adjudication, same reaction priority) so it plays out
    with real consequences instead of being a scripted aside."""
    actor = storage.get_character(actor_id)
    target = storage.get_character(target_id)
    if not actor or not target:
        return {"ok": False, "reason": "character not found"}
    if not actor.alive or not target.alive:
        return {"ok": False, "reason": "both characters must be alive"}

    action_text = (action_text or "").strip()
    dialogue_text = (dialogue_text or "").strip()
    if not action_text and not dialogue_text:
        return {"ok": False, "reason": "need an action or dialogue"}

    # If the target's name isn't already in the action text, append it so the
    # log reads naturally ("shoves" -> "shoves Devon").
    display_action = action_text
    if action_text and target.name.lower() not in action_text.lower():
        display_action = f"{action_text} {target.name}"

    if dialogue_text:
        storage.add_event("dialogue", dialogue_text, character_id=actor.id, character_name=actor.name,
                           target_id=target.id)
    if display_action:
        storage.add_event("action", display_action, character_id=actor.id, character_name=actor.name,
                           target_id=target.id)

    harm, reason = _adjudicate_harm(actor, target, action_text, dialogue_text)
    if harm > 0:
        target.health = max(0, target.health - harm)
        storage.update_character(target)
        note = f"{target.name} is hurt" + (f" ({reason})" if reason else "") + f". Health: {target.health}/100."
        storage.add_event("system", note, character_id=target.id, character_name=target.name)
        if target.health <= 0 and target.alive:
            storage.kill_character(target.id)
            storage.add_event("death", f"{target.name} has died.",
                               character_id=target.id, character_name=target.name)
            watchdog.reset(target.id)

    prioritize(target.id)  # target reacts next turn, in character
    return {"ok": True, "harm": harm, "reason": reason}


def tick():
    watchdog.record_tick()
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
    status_effect_block = (
        f"\nOngoing conditions currently affecting you, until further notice: {', '.join(char.status_effects)}. "
        f"These aren't just labels — let them actually color how you speak, move, and think this turn (e.g. "
        f"drunk should slur and misjudge things, wounded should wince and favor the injury, cursed should have "
        f"visible small consequences), not just be mentioned once and forgotten.\n"
    ) if char.status_effects else ""
    stimuli_block = (
        "\nSomething just happened to you, physically or mentally:\n" + "\n".join(stimuli_lines) + "\n"
    ) if stimuli_lines else ""

    focus = storage.get_room_focus(char.location)
    focus_block = (
        f"\nSomething on people's minds right now, that you're aware of and can bring up or react to "
        f"(you don't have to make it the only thing you talk about): {focus}\n"
    ) if focus else ""

    setting = storage.get_room_setting(char.location)
    setting_block = f"\nThe setting: {setting}\n" if setting else ""

    directive_block = (
        f"\nYour current objective, given to you from outside and now something you genuinely want: {char.directive}\n"
        f"Figure out how to actually accomplish this yourself — through what you say and do, over however many "
        f"turns it takes — don't just wait for it to happen.\n"
    ) if char.directive else ""

    system = CHARACTER_SYSTEM_TEMPLATE.format(
        name=char.name, persona=char.persona, health=char.health, stability=char.stability,
        status_line=status_line, memory_summary=char.memory_summary or "(no strong memories yet)",
        others=others, objects=obj_desc, log=log_desc, stimuli_block=stimuli_block, focus_block=focus_block,
        setting_block=setting_block, directive_block=directive_block, status_effect_block=status_effect_block,
    )
    result = call_llm_json(system, "Respond now, in character, as JSON only.")

    thought = as_text(result.get("thought")).strip()
    dialogue = as_text(result.get("dialogue")).strip()
    action = as_text(result.get("action")).strip()
    target_name = result.get("target")
    if not isinstance(target_name, str):
        target_name = as_text(target_name).strip() or None

    # Small/local models default to "just thinking" far too often — it's the path
    # of least resistance. One retry with a blunter instruction is cheap insurance
    # against a room full of characters who only ever want things and never do them.
    if not dialogue and not action:
        retry_result = call_llm_json(
            system,
            "Respond now, in character, as JSON only. Your last instinct was to leave dialogue and action both "
            "empty — that's not allowed. Say something out loud, or physically do something, right now.",
        )
        retry_dialogue = as_text(retry_result.get("dialogue")).strip()
        retry_action = as_text(retry_result.get("action")).strip()
        if retry_dialogue or retry_action:
            thought = as_text(retry_result.get("thought")).strip() or thought
            dialogue, action = retry_dialogue, retry_action
            retry_target = retry_result.get("target")
            if isinstance(retry_target, str) and retry_target:
                target_name = retry_target
    target = next((c for c in all_chars if c.name == target_name), None)

    if target and target.id != char.id and (dialogue or action):
        _priority_queue.append(target.id)

    if char.directive and "DIRECTIVE COMPLETE" in thought.upper():
        thought = re.sub(r"DIRECTIVE COMPLETE\.?", "", thought, flags=re.IGNORECASE).strip()
        storage.add_event("system", f"{char.name}'s objective is resolved: {char.directive}",
                           character_id=char.id, character_name=char.name)
        char.directive = ""
        storage.update_character(char)

    if thought:
        storage.add_event("thought", thought, character_id=char.id, character_name=char.name)
    if dialogue:
        storage.add_event("dialogue", dialogue, character_id=char.id, character_name=char.name,
                           target_id=target.id if target else None)
    if action:
        storage.add_event("action", action, character_id=char.id, character_name=char.name,
                           target_id=target.id if target else None)

    watchdog.check_repetition(char.id, char.name, dialogue, action)

    # Own health check (e.g. from an intervention this turn)
    if char.health <= 0 and char.alive:
        storage.kill_character(char.id)
        storage.add_event("death", f"{char.name} has died.", character_id=char.id, character_name=char.name)
        watchdog.reset(char.id)

    # Let a cheap model referee whether this turn's action hurt someone else. Gated on
    # an actual physical action (not mere targeted dialogue) so ordinary conversation
    # never risks a stray nonzero harm score from the adjudicator model.
    if target and target.alive and action:
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
                watchdog.reset(target.id)

    memory.maybe_summarize(char)
