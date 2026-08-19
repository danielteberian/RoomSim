import random
import re
import uuid

import interventions
import memory
import storage
import watchdog
from config import CFG
from llm import call_llm_json, as_text
from models import SimObject

_priority_queue = []  # character ids who were just addressed, so they respond promptly (and go first in the round)

# Local models frequently emit the literal string "null" (or similar) instead of
# an empty string for an unused optional field — as_text() alone doesn't catch
# this since it's already valid string content, just not what it means.
_NULLISH = {"null", "none", "n/a", "na", "nil", "undefined", "-"}


def _clean_opt(value) -> str:
    s = as_text(value).strip()
    return "" if s.lower() in _NULLISH else s


def _strip_self_name(text, name):
    """Local models often write actions/dialogue as a full third-person sentence
    that already includes the character's own name ("Vlad flexes his muscles"),
    which then gets a second, redundant name prepended when the log renders it
    ("Vlad Vlad flexes..."). Strip a leading self-name if the model included one."""
    if not text:
        return text
    stripped = re.sub(rf"^{re.escape(name)}(?:'s)?\b[\s:,-]*", "", text, count=1, flags=re.IGNORECASE).strip()
    return stripped or text


_AGGRESSION_LABELS = (
    (20, "placid, slow to anger"), (40, "even-tempered"), (60, "on edge, short-fused"),
    (80, "hostile, quick to confront"), (101, "volatile — barely holding it together"),
)


def _aggression_label(value):
    for threshold, label in _AGGRESSION_LABELS:
        if value < threshold:
            return label
    return _AGGRESSION_LABELS[-1][1]


# Preset speech-style keys selectable per character (see main.py's dropdown).
# Keys are what gets stored on Character.dialect; values are the instruction
# folded into that character's prompt every turn.
DIALECTS = {
    "patois": "Speak in heavy Jamaican Patois — dialect, slang, and grammar, not just an accent spelled out "
              "phonetically. Every line, no exceptions.",
    "us_english": "Speak in standard, general American English — no strong regional dialect.",
    "philadelphia": "Speak with a distinct Philadelphia dialect and accent — local slang and phrasing (e.g. "
                    "\"jawn,\" \"wooder,\" \"yous\"), flattened vowels, blunt/direct delivery.",
    "aave": "Speak in African American Vernacular English (AAVE) — its own grammar, rhythm, and vocabulary, not "
            "just slang words dropped into standard English.",
}


def _dialect_block(char):
    instruction = DIALECTS.get(char.dialect)
    if not instruction:
        return ""
    return f"\nSpeech style, always in effect: {instruction}\n"


def format_sim_time(total_minutes):
    day = int(total_minutes // 1440) + 1
    minute_of_day = int(total_minutes % 1440)
    hour24, minute = divmod(minute_of_day, 60)
    period = (
        "the dead of night" if hour24 < 5 else "early morning" if hour24 < 8 else
        "morning" if hour24 < 12 else "afternoon" if hour24 < 17 else
        "evening" if hour24 < 21 else "night"
    )
    hour12 = hour24 % 12 or 12
    ampm = "AM" if hour24 < 12 else "PM"
    return f"Day {day}, {hour12}:{minute:02d} {ampm} ({period})"


CHARACTER_SYSTEM_TEMPLATE = """You are role-playing as {name}, one character living in an ongoing, unscripted simulation.
Persona: {persona}
{dialect_block}{guidelines_block}Stay strictly in character. You do not know you are an AI. Respond ONLY as {name} would, given everything below.
Your current state: health {health}/100, emotional stability {stability}/100, aggression {aggression}/100 \
({aggression_label}){status_line}.
You are currently at: {location}
{time_block}Your standing memory of what's happened so far: {memory_summary}
{status_effect_block}{mood_block}{needs_block}{setting_block}
Other people here with you right now (this is a closed-world fictional scene — these \
are the only people who exist here; do not invent, address, mention, or compare anyone to a real-world celebrity \
or public figure by name, even in passing — if you want to reference someone else, do it without naming a real \
person). You are {name} and ONLY {name} — never speak, act, or think as if you were one of the people listed \
below, even briefly, even to voice their side of things; that's their line to deliver on their own turn, not yours \
to write for them. Never invent a brand-new named person who isn't listed here or in the "elsewhere" list below \
either — if your persona would bring up someone else (family, a stranger, a coworker), refer to them descriptively \
instead of giving them a proper name ("my sister", "some guy from the bar"), since a full name implies a specific \
person who doesn't actually exist in this world. This cuts both ways: don't dodge that rule by manufacturing a vague, \
unnamed threat or rival to talk about fighting instead ("these guys", "these punks", "troublemakers in this town") \
— if there's no actual person, here or listed below, behind a conflict, it isn't real, so drop it and engage with \
something that actually exists right now: the people actually present, an object, your directive, or your own \
interests. Vague tough talk about an enemy that doesn't exist is empty filler, not drama:
{others}
{elsewhere_block}
Objects currently in your location (this list is authoritative and up to date — if \
an object you remember from earlier isn't listed here, it's gone; don't act as if it's still present):
{objects}
{locations_block}{world_block}{knowledge_block}{focus_block}{directive_block}{self_goal_block}{interests_block}{dislikes_block}
Recent events (most recent last — lines marked "YOU as {name}" are what you yourself already said or did; \
every other name is someone else's line, not yours):
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
bound and gagged — not a default or a safe choice. Let your current aggression level genuinely color your tone \
and choices this turn — higher means a shorter fuse and blunter, more confrontational word/action choices; lower \
means more patience and conflict-avoidance — not just something you mention once and ignore.

You also have a life beyond this one conversation. Every turn, on top of dialogue/action, you may optionally:
- Move: set "move_to" to the exact name of a listed location to go there instead of staying put (leave it \
empty to stay). You arrive there next turn and will no longer see or be seen by people in your old location \
in person.
- Message someone who isn't with you: fill "message_channel" (exactly "text", "email", or "call"), "message_to" \
(the exact name of someone listed above, whether they're here or elsewhere), and "message_content" with what you \
say. Leave all three empty if you're not messaging anyone this turn. This works even for people in another \
location — that's the point of a phone/email — but use it for people who AREN'T in the room with you; if they're \
right here, just talk to them instead.
- Investigate/research something: set "investigate" to a short, specific topic you're actively looking into or \
asking around about this turn (leave it empty most turns — only use it when your persona would genuinely be \
digging for information, not as a substitute for talking or acting).
- Explore: set "explore" to a short idea of a new kind of place you'd like to go check out that isn't in any list \
above (leave it empty almost always — only when your persona would genuinely wander off looking for somewhere new, \
within reason: a shop, a park, someone's apartment, not something wildly out of place).
- Bring something new into the scene: if you'd genuinely pull out, grab, or otherwise produce an object that isn't \
already listed above, set "new_object_name" (a short name) and "new_object_description" (one sentence) and it \
becomes real, staying in this location for others to use too. Leave both empty almost always — only when your \
action already implies producing something specific (pulling a knife, opening a bag of tools), not for anything \
you're just talking about.
- Set or update your own current goal: "current_goal" is something YOU decide you want, separate from any objective \
given to you — a short phrase. Leave it empty to keep pursuing whatever you last set; change it when it's been \
achieved, abandoned, or a new one genuinely takes over. This should persist across turns, not change every time.
- Report your mood, if it's shifted: "mood" is a short word or phrase (e.g. "afraid", "elated", "humiliated") for a \
transient emotional spike distinct from your baseline temperament — set it when something just happened that would \
genuinely shift how you feel right now. Leave it empty most turns to let your last mood naturally fade over time \
rather than resetting it constantly.
- Update how you feel about whoever "target" is: "relationship_shift" is a small integer from -15 to 15 — positive \
if this turn made you like/trust them more, negative if less, 0 or omitted if unchanged. This only applies when \
"target" is set to someone.
- Lie: if "message_to" is set and what you're telling them in "message_content" is a deliberate lie rather than the \
truth, set "lie_to" to that same person's exact name. They won't know it's false. Leave empty for anything truthful \
or for in-person dialogue (lying is only tracked through messages).

Respond with ONLY a compact JSON object, no other text, in this exact shape:
{{"thought": "a short private internal thought, not shown to others", "dialogue": "what you say out loud — should almost never be empty", "action": "a short physical action you take — should almost never be empty", "target": "name of another character your dialogue/action is directed at, or null", "move_to": "exact name of a location to travel to, or empty string", "message_channel": "text, email, or call, or empty string", "message_to": "exact name of who you're messaging, or empty string", "message_content": "the message, or empty string", "investigate": "a short topic you're looking into right now, or empty string", "explore": "a short idea of somewhere new to check out, or empty string", "new_object_name": "a short name for something new you're bringing into the scene, or empty string", "new_object_description": "one sentence describing it, or empty string", "current_goal": "your own current goal, or empty string to leave unchanged", "mood": "a short word/phrase for a transient mood shift, or empty string", "relationship_shift": 0, "lie_to": "exact name of who you just lied to via message, or empty string"}}"""

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
# Positive backstop: local models don't reliably follow ADJUDICATOR_SYSTEM's
# "raised voices/posturing are NOT harm" instruction just because we ask nicely
# (seen in practice: pure yelling scored as an injury). Rather than trust the
# model to police its own reasoning, require the action text or its own stated
# reason to actually name a contact-implying verb before any nonzero harm is
# accepted at all — absence of one of these means it's not physical, full stop.
_CONTACT_KEYWORDS = (
    "hit", "punch", "kick", "stab", "throw", "shove", "grab", "slam", "cut", "strike",
    "hurl", "wound", "contact", "slap", "push", "tackle", "strangl", "chok", "burn",
    "bite", "claw", "smash", "crush", "trip", "knock", "swing", "kicked", "hurled",
)


def prioritize(char_id):
    """Force char_id to take the very next turn (e.g. right after an admin
    intervention, so its effect is visible immediately instead of waiting for
    round-robin order to reach them)."""
    _priority_queue.insert(0, char_id)




def _apply_pending_interventions(char):
    pending = storage.pop_pending_interventions(char.id)
    stimuli_lines = []
    net_health, net_stability = 0, 0
    for row in pending:
        stimuli_lines.append(f"- {row['text']}")
        char.health = max(0, min(100, char.health + row["health_delta"]))
        char.stability = max(0, min(100, char.stability + row["stability_delta"]))
        net_health += row["health_delta"]
        net_stability += row["stability_delta"]
        if row["status_effect"] and row["status_effect"] not in char.status_effects:
            char.status_effects.append(row["status_effect"])
        # Admin-facing log line uses the short label when one was provided
        # (see interventions.py) instead of the character's full private
        # flavor text — the flavor text still reaches the character via
        # stimuli_lines above, just not the shared script log.
        log_content = row["label"] if row["label"] else row["text"]
        storage.add_event("intervention", log_content, character_id=char.id, character_name=char.name)
    return stimuli_lines, net_health, net_stability


def _drift_aggression(char, net_health=0, net_stability=0):
    """Aggression isn't admin-only — it nudges itself based on what actually
    happens to a character. Getting hurt or shaken raises it; being healed/
    calmed lowers it; with no stimulus either way this turn, it eases back
    toward the character's own baseline instead of staying wherever it last was."""
    harm = abs(min(net_health, 0)) + abs(min(net_stability, 0))
    relief = max(net_health, 0) + max(net_stability, 0)
    if harm:
        delta = max(1, harm // 4)
    elif relief:
        delta = -max(1, relief // 4)
    elif char.aggression > char.aggression_baseline:
        delta = -1
    elif char.aggression < char.aggression_baseline:
        delta = 1
    else:
        delta = 0
    char.aggression = max(0, min(100, char.aggression + delta))


def _adjudicate_harm(actor, target, action_text, dialogue_text):
    if not action_text and not dialogue_text:
        return 0, ""
    user = (
        f'{actor.name} does/says this, directed at {target.name}:\n'
        f'action="{action_text}"\ndialogue="{dialogue_text}"'
    )
    result = call_llm_json(ADJUDICATOR_SYSTEM, user, max_tokens=150, model=CFG.adjudicator_model,
                            temperature=CFG.adjudicator_temperature)
    try:
        harm = int(result.get("harm", 0))
    except (TypeError, ValueError):
        harm = 0
    reason = as_text(result.get("reason", ""))
    # Safety net 1: models occasionally contradict their own reasoning (say "no
    # contact" but still return nonzero harm). Trust the stated reason over the number.
    if harm > 0 and any(p in reason.lower() for p in _NO_CONTACT_PHRASES):
        harm = 0
    # Safety net 2: don't just trust the model to have followed "raised voices
    # aren't harm" on its own — require an actual contact-implying word in the
    # action text or its own reason before accepting any nonzero harm at all.
    if harm > 0:
        evidence = f"{action_text} {reason}".lower()
        if not any(k in evidence for k in _CONTACT_KEYWORDS):
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
    if actor.location != target.location:
        return {"ok": False, "reason": "both characters must be in the same location for an in-person interaction"}

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
                           target_id=target.id, location=actor.location)
    if display_action:
        storage.add_event("action", display_action, character_id=actor.id, character_name=actor.name,
                           target_id=target.id, location=actor.location)

    harm, reason = _adjudicate_harm(actor, target, action_text, dialogue_text)
    if harm > 0:
        target.health = max(0, target.health - harm)
        target.aggression = max(0, min(100, target.aggression + max(1, harm // 4)))
        storage.update_character(target)
        storage.adjust_relationship(target.id, actor.id, affinity_delta=-max(5, harm // 3))
        storage.add_relationship_event(target.id, actor.id, f"was hurt by {actor.name}" + (f" ({reason})" if reason else ""))
        note = f"{target.name} is hurt" + (f" ({reason})" if reason else "") + f". Health: {target.health}/100."
        storage.add_event("system", note, character_id=target.id, character_name=target.name)
        if target.health <= 0 and target.alive:
            storage.kill_character(target.id)
            storage.add_event("death", f"{target.name} has died.",
                               character_id=target.id, character_name=target.name)
            watchdog.reset(target.id)
            _apply_death_consequences(target, storage.list_characters(alive_only=True) + [target],
                                       cause_note=f"{actor.name} was involved.")

    prioritize(target.id)  # target reacts next turn, in character
    return {"ok": True, "harm": harm, "reason": reason}


def _find_char_by_name(chars, name):
    if not name:
        return None
    name = name.strip().lower()
    return next((c for c in chars if c.name.lower() == name), None)


INVESTIGATE_SYSTEM = """You invent one single, concrete, specific fact about the wider fictional world of an \
ongoing life simulation, in response to a character investigating or asking around about a topic. It should be \
grounded and mundane-plausible (not supernatural or wildly dramatic), consistent with anything already known \
about the world, and something that could plausibly color future scenes. 1-2 sentences, third person, no \
meta-commentary, no mention of the character's name. Do NOT invent any new named person (no proper names for \
people — no "Mr./Ms. Someone", no first-and-last names, nothing a character could later treat as a real named \
individual in this world) — describe roles/places/events/objects/rumors generically instead ("the store's owner", \
"a regular there", "the old manager") if a person needs to be involved at all. This world's cast is fixed; you're \
only adding color to its surroundings, not populating it with new people.
Respond with ONLY JSON: {"fact": "<the new fact, 1-2 sentences>"}"""


def _investigate(char, topic):
    existing = storage.list_world_facts(CFG.world_facts_window)
    existing_desc = "\n".join(f"- {f.content}" for f in existing) or "(nothing established yet)"
    user = (
        f"What's already known about the world:\n{existing_desc}\n\n"
        f"A character is now investigating/asking around about: {topic}\n\n"
        "Invent the fact they turn up."
    )
    result = call_llm_json(INVESTIGATE_SYSTEM, user, max_tokens=150, model=CFG.adjudicator_model,
                            temperature=CFG.adjudicator_temperature + 0.3)
    fact = as_text(result.get("fact")).strip()
    if not fact:
        return
    storage.add_world_fact(topic, fact, discovered_by=char.name)
    storage.add_event("system", f"{char.name} learns something, looking into \"{topic}\": {fact}",
                       character_id=char.id, character_name=char.name, location=char.location)


EXPLORE_SYSTEM = """You invent one new, plausible, mundane place that fits naturally into the world of an ongoing \
life simulation, because a character wants to go check something out. Keep it small-scale and grounded (a shop, a \
park, someone's apartment, a diner — not a sprawling landmark or anything supernatural), consistent with anything \
already known about the world. Give it a short name using only lowercase letters, digits, and underscores (used as \
a unique key, e.g. "corner_diner") and a one-sentence description. The description must NOT invent or name any new \
person (no proper names — "the owner", "a regular", "whoever runs the place" are fine if you need to mention \
someone at all) — this world's cast of people is fixed; you're only adding a place, not a person.
Respond with ONLY JSON: {"name": "<short_key_name>", "description": "<one sentence>"}"""


def _explore(char, idea):
    """Lets a character discover a brand-new location on their own, capped by
    CFG.max_discovered_locations so the world can't sprawl indefinitely beyond
    what you've deliberately added. Returns the Location they should head to,
    or None if nothing was discovered (cap hit, or a malformed model response)."""
    existing = storage.list_locations()
    if len([l for l in existing if l.discovered_by]) >= CFG.max_discovered_locations:
        return None
    existing_desc = "\n".join(f"- {l.name}: {l.description}" for l in existing) or "(nowhere established yet)"
    user = (
        f"Known places so far:\n{existing_desc}\n\n"
        f"{char.name} wants to go check out/explore: {idea}\n\nInvent the place they find."
    )
    result = call_llm_json(EXPLORE_SYSTEM, user, max_tokens=150, model=CFG.adjudicator_model,
                            temperature=CFG.adjudicator_temperature + 0.3)
    name = re.sub(r"[^a-z0-9_]", "", as_text(result.get("name")).strip().lower().replace(" ", "_"))
    description = as_text(result.get("description")).strip()
    if not name or not description:
        return None
    existing_loc = storage.get_location(name)
    if existing_loc:
        return existing_loc
    storage.add_location(name, description, discovered_by=char.name)
    storage.add_event("system", f"{char.name} discovers a new place while exploring: {name} — {description}",
                       character_id=char.id, character_name=char.name, location=char.location)
    return storage.get_location(name)


def _create_object(char, name, description):
    """Lets a character bring a brand-new object into their current location,
    capped by CFG.max_created_objects so a scene can't get cluttered beyond
    what you've deliberately added. No extra model call needed — the character
    already described it themselves."""
    existing = storage.list_objects()
    if len([o for o in existing if o.created_by]) >= CFG.max_created_objects:
        return
    if any(o.name.lower() == name.lower() and o.location == char.location for o in existing):
        return  # already here, don't spam a duplicate
    storage.add_object(SimObject(id=str(uuid.uuid4())[:8], name=name, description=description,
                                  location=char.location, created_by=char.name))
    storage.add_event("system", f"{char.name} brings out {name}: {description}",
                       character_id=char.id, character_name=char.name, location=char.location)


def _guidelines_block(char):
    if not char.guidelines:
        return ""
    return (
        f"\nHard behavioral rules you follow no matter what, even if a scene seems to invite breaking them — "
        f"these override your usual instincts and any topic, object, or person that shows up: \n"
        + "\n".join(f"- {g}" for g in char.guidelines) + "\n"
    )


def _mood_block(char, sim_minutes):
    """Decays an unreinforced mood back to neutral after CFG.mood_decay_minutes
    of in-world time, then returns the prompt block if a mood is still active.
    Mutates char.mood in place; caller is responsible for persisting it."""
    if char.mood and sim_minutes - char.mood_set_at > CFG.mood_decay_minutes:
        char.mood = ""
    if not char.mood:
        return ""
    return (
        f"\nYour current mood, on top of your baseline temperament, is: {char.mood}. This is transient and will "
        f"fade with time if nothing reinforces it — let it color your tone and choices this turn.\n"
    )


def _needs_block(char):
    urgent = [
        (char.needs_hunger, "hungry"), (char.needs_boredom, "restless and bored"),
        (char.needs_social, "starved for company"), (100 - char.needs_safety, "on edge, feeling unsafe"),
    ]
    urgent = sorted([u for u in urgent if u[0] >= CFG.needs_pressure_threshold], key=lambda u: -u[0])[:2]
    if not urgent:
        return ""
    labels = ", ".join(label for _, label in urgent)
    return f"\nYou're increasingly {labels}, and it's starting to affect your focus and patience.\n"


def _decay_needs(char, all_chars, net_health, net_stability, did_something_novel):
    others_here = any(c.id != char.id and c.location == char.location for c in all_chars)
    char.needs_hunger = max(0, min(100, char.needs_hunger + CFG.needs_hunger_rate))
    char.needs_boredom = max(0, min(100, char.needs_boredom + (0 if did_something_novel else CFG.needs_boredom_rate)
                                     - (30 if did_something_novel else 0)))
    char.needs_social = max(0, min(100, char.needs_social + (
        -20 if others_here else CFG.needs_social_rate
    )))
    harm_taken = abs(min(net_health, 0)) + abs(min(net_stability, 0))
    char.needs_safety = max(0, min(100, char.needs_safety - harm_taken + (0 if harm_taken else 1)))


def _knowledge_block(char):
    items = storage.list_character_knowledge(char.id, CFG.knowledge_window)
    if not items:
        return ""
    return (
        f"\nThings you personally know or have been told — not necessarily known to anyone else, and not all "
        f"guaranteed true (you have no way to tell which, if any, might be a lie someone fed you):\n"
        + "\n".join(f"- {i.content}" for i in items) + "\n"
    )


def _apply_relationship_shift(char, target, shift, reason_text):
    if not target or not shift:
        return
    shift = max(-15, min(15, shift))
    storage.adjust_relationship(char.id, target.id, affinity_delta=shift)
    if reason_text:
        storage.add_relationship_event(char.id, target.id, reason_text[:140])


def _apply_death_consequences(dead_char, all_chars_at_death, cause_note=""):
    """Death should leave scars, not just remove a roster entry: survivors who
    had a real relationship with the deceased grieve or feel vindicated, and
    anyone who was actually present when it happened comes away with a
    (possibly one-sided) account of what happened."""
    witnesses = [c for c in all_chars_at_death if c.id != dead_char.id and c.alive
                 and c.location == dead_char.location]
    for c in witnesses:
        storage.add_character_knowledge(
            c.id, f"{dead_char.name} died. {cause_note}".strip(), is_true=True, source="witnessed",
        )
    for c in all_chars_at_death:
        if c.id == dead_char.id or not c.alive:
            continue
        rel = storage.get_relationship(c.id, dead_char.id)
        if rel.affinity >= 20:
            c.stability = max(0, c.stability - min(25, rel.affinity // 3))
            storage.update_character(c)
            storage.add_relationship_event(c.id, dead_char.id, f"grieved {dead_char.name}'s death")
        elif rel.affinity <= -20:
            c.aggression = max(0, c.aggression - 5)
            storage.update_character(c)
            storage.add_relationship_event(c.id, dead_char.id, f"felt vindicated by {dead_char.name}'s death")


def _ordered_alive_characters():
    """Everyone alive acts once per round. Anyone who was just addressed
    (messaged, spoken to, targeted) goes first so replies land promptly within
    the same round instead of only being read a full round later; everyone
    else follows in a stable order."""
    chars = storage.list_characters(alive_only=True)
    by_id = {c.id: c for c in chars}
    ordered = []
    seen = set()
    while _priority_queue:
        candidate_id = _priority_queue.pop(0)
        if candidate_id in by_id and candidate_id not in seen:
            ordered.append(by_id[candidate_id])
            seen.add(candidate_id)
    for c in sorted(chars, key=lambda c: c.id):
        if c.id not in seen:
            ordered.append(c)
            seen.add(c.id)
    return ordered


def tick():
    """One round: every currently-alive character takes a turn, not just one
    per call — see _ordered_alive_characters(). A character can still die or
    move mid-round from someone else's action, so each turn re-checks the
    character is still alive before processing."""
    ordered = _ordered_alive_characters()
    if not ordered:
        storage.add_event("system", "The room is empty. Add a character to continue.")
        return
    for char_stub in ordered:
        current = storage.get_character(char_stub.id)
        if current and current.alive:
            _take_turn(current)


def _take_turn(char):
    watchdog.record_tick()
    sim_minutes = storage.advance_sim_minutes(CFG.minutes_per_tick)
    time_block = (
        f"The current time is: {format_sim_time(sim_minutes)}. Let this genuinely shape what you're doing — "
        f"most people sleep at night, eat around typical mealtimes, and have some kind of daily rhythm, even if "
        f"yours is unusual; don't act like time doesn't exist or hold the exact same energy at 3 AM as at noon.\n"
    )

    # Auto-rolled weirdness (per-character weirdness_chance) queues the same
    # one-time stimulus an admin-triggered "Act weird" button would, so it
    # flows through the normal intervention pipeline below like anything else.
    if char.weirdness_chance > 0 and random.randint(1, 100) <= char.weirdness_chance:
        interventions.make_weird(char.id)

    stimuli_lines, net_health, net_stability = _apply_pending_interventions(char)
    _drift_aggression(char, net_health, net_stability)
    storage.update_character(char)
    char = storage.get_character(char.id)  # reload after any intervention/aggression updates

    all_chars = storage.list_characters(alive_only=True)
    objects = storage.list_objects(location=char.location)
    recent_events = storage.get_recent_events_for_character(char, CFG.memory_window)
    others, elsewhere, obj_desc, log_desc, world_desc = memory.build_prompt_context(char, all_chars, objects, recent_events)

    elsewhere_block = (
        f"\nPeople you know who are elsewhere right now (not in the room with you — you can't talk to them in "
        f"person, but you can reach them by text/email/call using \"message_to\"):\n{elsewhere}\n"
    ) if elsewhere else ""

    all_locations = [l for l in storage.list_locations() if l.name != char.location]
    locations_block = (
        "\nOther places that exist, that you could travel to (set \"move_to\" to the exact name to go there):\n"
        + "\n".join(f"- {l.name}" + (f": {l.description}" if l.description else "") for l in all_locations) + "\n"
    ) if all_locations else ""

    world_block = (
        f"\nWhat's been learned about the wider world so far (things you or others have found out or been told):\n{world_desc}\n"
    ) if world_desc else ""

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

    interests_block = (
        f"\nThings you're genuinely into and tend to steer conversation/action toward when nothing more urgent is "
        f"happening — standing passions, not one-off tasks: {', '.join(char.interests)}\n"
    ) if char.interests else ""

    dislikes_block = (
        f"\nThings you genuinely dislike, avoid, refuse, or react badly to — let these actually shape your choices "
        f"(turning things down, pushing back, visible discomfort), not just get mentioned once: {', '.join(char.dislikes)}\n"
    ) if char.dislikes else ""

    dialect_block = _dialect_block(char)
    guidelines_block = _guidelines_block(char)
    mood_block = _mood_block(char, sim_minutes)  # may clear char.mood if it's decayed past CFG.mood_decay_minutes
    needs_block = _needs_block(char)
    knowledge_block = _knowledge_block(char)
    self_goal_block = (
        f"\nYour own current goal, something you decided you want yourself, separate from any objective given to "
        f"you: {char.self_goal}\n"
    ) if char.self_goal else ""

    system = CHARACTER_SYSTEM_TEMPLATE.format(
        name=char.name, persona=char.persona, health=char.health, stability=char.stability,
        aggression=char.aggression, aggression_label=_aggression_label(char.aggression),
        status_line=status_line, memory_summary=char.memory_summary or "(no strong memories yet)",
        location=char.location, time_block=time_block, others=others, elsewhere_block=elsewhere_block,
        objects=obj_desc, locations_block=locations_block, world_block=world_block, log=log_desc,
        stimuli_block=stimuli_block, focus_block=focus_block, setting_block=setting_block,
        directive_block=directive_block, interests_block=interests_block, dislikes_block=dislikes_block,
        status_effect_block=status_effect_block, guidelines_block=guidelines_block, mood_block=mood_block,
        needs_block=needs_block, knowledge_block=knowledge_block, self_goal_block=self_goal_block,
        dialect_block=dialect_block,
    )
    result = call_llm_json(system, f"Respond now, in character as {char.name} — and only as {char.name}, "
                                    f"never as anyone else in the room — in JSON only.")

    thought = as_text(result.get("thought")).strip()
    dialogue = _strip_self_name(as_text(result.get("dialogue")).strip(), char.name)
    action = _strip_self_name(as_text(result.get("action")).strip(), char.name)
    target_name = _clean_opt(result.get("target")) or None

    # These are independent of dialogue/action, so parsed once from the original
    # attempt only — no need to re-derive them from the empty-dialogue retry below.
    # _clean_opt filters out literal "null"/"none"/etc, which local models return
    # more often than an actual empty string for an unused optional field.
    move_to = _clean_opt(result.get("move_to"))
    message_channel = _clean_opt(result.get("message_channel")).lower()
    message_to = _clean_opt(result.get("message_to"))
    message_content = _clean_opt(result.get("message_content"))
    investigate_topic = _clean_opt(result.get("investigate"))
    explore_idea = _clean_opt(result.get("explore"))
    new_object_name = _clean_opt(result.get("new_object_name"))
    new_object_description = _clean_opt(result.get("new_object_description"))
    current_goal = _clean_opt(result.get("current_goal"))
    new_mood = _clean_opt(result.get("mood"))
    lie_to = _clean_opt(result.get("lie_to"))
    try:
        relationship_shift = int(result.get("relationship_shift") or 0)
    except (TypeError, ValueError):
        relationship_shift = 0

    # Small/local models default to "just thinking" far too often — it's the path
    # of least resistance. One retry with a blunter instruction is cheap insurance
    # against a room full of characters who only ever want things and never do them.
    if not dialogue and not action:
        retry_result = call_llm_json(
            system,
            f"Respond now, in character as {char.name} — and only as {char.name} — in JSON only. Your last "
            f"instinct was to leave dialogue and action both empty — that's not allowed. Say something out loud, "
            f"or physically do something, right now.",
        )
        retry_dialogue = _strip_self_name(as_text(retry_result.get("dialogue")).strip(), char.name)
        retry_action = _strip_self_name(as_text(retry_result.get("action")).strip(), char.name)
        if retry_dialogue or retry_action:
            thought = as_text(retry_result.get("thought")).strip() or thought
            dialogue, action = retry_dialogue, retry_action
            retry_target = _clean_opt(retry_result.get("target"))
            if retry_target:
                target_name = retry_target
    target = next((c for c in all_chars if c.name == target_name), None)

    if current_goal:
        char.self_goal = current_goal
    if new_mood:
        char.mood = new_mood
        char.mood_set_at = sim_minutes
    _apply_relationship_shift(char, target, relationship_shift, dialogue or action)

    # Both the original attempt AND the blunt retry can still come back with
    # dialogue, action, AND thought all empty. Previously this logged nothing
    # at all, so a stalled character was invisible in the script log (looked
    # like "nothing ever happens" with no trace of why). watchdog.check_stall
    # tracks consecutive stalls per character and auto-nudges after repeated
    # ones; log every occurrence too so a single stall is visible immediately,
    # not just once the streak trips the nudge.
    if not thought and not dialogue and not action:
        storage.add_event(
            "system", f"[stall] {char.name} produced no dialogue, action, or thought this turn, "
                      f"even after being told to.",
            character_id=char.id, character_name=char.name,
        )
    watchdog.check_stall(char.id, char.name, thought, dialogue, action)

    if target and target.id != char.id and (dialogue or action):
        _priority_queue.append(target.id)

    if char.directive and "DIRECTIVE COMPLETE" in thought.upper():
        thought = re.sub(r"DIRECTIVE COMPLETE\.?", "", thought, flags=re.IGNORECASE).strip()
        storage.add_event("system", f"{char.name}'s objective is resolved: {char.directive}",
                           character_id=char.id, character_name=char.name)
        char.directive = ""
        storage.update_character(char)

    if thought:
        storage.add_event("thought", thought, character_id=char.id, character_name=char.name, location=char.location)
    if dialogue:
        storage.add_event("dialogue", dialogue, character_id=char.id, character_name=char.name,
                           target_id=target.id if target else None, location=char.location)
    if action:
        storage.add_event("action", action, character_id=char.id, character_name=char.name,
                           target_id=target.id if target else None, location=char.location)

    watchdog.check_repetition(char.id, char.name, dialogue, action)

    # Own health check (e.g. from an intervention this turn)
    if char.health <= 0 and char.alive:
        storage.kill_character(char.id)
        # kill_character() writes alive=False/health=0 to the DB, but this
        # function's local `char` object doesn't know that yet — without
        # updating it here too, the consolidated storage.update_character(char)
        # call at the end of this function would silently overwrite that and
        # resurrect them with stale in-memory state.
        char.alive = False
        char.health = 0
        storage.add_event("death", f"{char.name} has died.", character_id=char.id, character_name=char.name)
        watchdog.reset(char.id)
        _apply_death_consequences(char, all_chars)

    # Let a cheap model referee whether this turn's action hurt someone else. Gated on
    # an actual physical action (not mere targeted dialogue) so ordinary conversation
    # never risks a stray nonzero harm score from the adjudicator model.
    if target and target.alive and action:
        harm, reason = _adjudicate_harm(char, target, action, dialogue)
        if harm > 0:
            target.health = max(0, target.health - harm)
            target.aggression = max(0, min(100, target.aggression + max(1, harm // 4)))
            storage.update_character(target)
            storage.adjust_relationship(target.id, char.id, affinity_delta=-max(5, harm // 3))
            storage.add_relationship_event(target.id, char.id, f"was hurt by {char.name}" + (f" ({reason})" if reason else ""))
            note = f"{target.name} is hurt" + (f" ({reason})" if reason else "") + f". Health: {target.health}/100."
            storage.add_event("system", note, character_id=target.id, character_name=target.name)
            if target.health <= 0 and target.alive:
                storage.kill_character(target.id)
                storage.add_event("death", f"{target.name} has died.",
                                   character_id=target.id, character_name=target.name)
                watchdog.reset(target.id)
                _apply_death_consequences(target, all_chars, cause_note=f"{char.name} was involved.")

    # A message reaches its recipient regardless of location — that's the whole
    # point of a phone/email — so it's not gated on location like dialogue/action.
    # A message is also this sim's knowledge-sharing mechanism: whatever gets
    # said becomes something the recipient now personally knows, true or not.
    did_message = False
    if message_channel in ("text", "email", "call") and message_to and message_content:
        recipient = _find_char_by_name(all_chars, message_to)
        if recipient and recipient.id != char.id:
            did_message = True
            storage.add_event("message", message_content, character_id=char.id, character_name=char.name,
                               target_id=recipient.id, channel=message_channel)
            prioritize(recipient.id)  # they react to it promptly, like a phone buzzing
            is_lie = lie_to and lie_to.strip().lower() == recipient.name.lower()
            storage.add_character_knowledge(
                recipient.id, message_content, is_true=not is_lie,
                source=f"told by {char.name}" + (" (unverified)" if is_lie else ""),
            )

    if investigate_topic:
        _investigate(char, investigate_topic)
        did_something_novel = True
    else:
        did_something_novel = False

    if explore_idea and not move_to:
        discovered = _explore(char, explore_idea)
        if discovered:
            move_to = discovered.name
        did_something_novel = True

    if new_object_name and new_object_description:
        _create_object(char, new_object_name, new_object_description)
        did_something_novel = True

    _decay_needs(char, all_chars, net_health, net_stability, did_something_novel or did_message)

    # Applied last so the events above (dialogue/action/message) are logged at
    # the location the character was actually in during this turn, not the one
    # they're heading to.
    if move_to:
        dest = next((l for l in storage.list_locations() if l.name.lower() == move_to.lower()), None)
        if dest and dest.name != char.location:
            storage.add_event("system", f"{char.name} heads to {dest.name}.",
                               character_id=char.id, character_name=char.name)
            char.location = dest.name

    # Consolidated final save: catches self_goal/mood/needs and any other
    # mutation above (aggression drift, directive completion, move) that
    # hasn't already been persisted.
    storage.update_character(char)

    memory.maybe_summarize(char)
