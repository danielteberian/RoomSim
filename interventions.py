"""
Admin interventions: things you can do TO a character from outside the simulation.
Each one queues a "stimulus" that gets woven into that character's next turn, so
they react to it in-character rather than just having a stat silently change.
"""

import storage

ZAP_TEXT = "A sharp, sudden jolt runs through your body — like static shock, but stronger. It came from nowhere you can see."
DISTURB_TEXT = "A wave of unease washes over you. Something feels wrong, though you can't say what."
PUSH_TEXT = "You feel a sudden, forceful shove, as if unseen hands moved you."
HEAL_TEXT = "A warmth spreads through you, dulling every ache — whatever was hurt in you is mending fast, faster than it should."
CALM_TEXT = "A deep, unnatural calm settles over you, quieting whatever was spiraling in your mind a moment ago."
BREAK_TEXT = ("A jolt of clarity cuts through you mid-thought — whatever you were about to say or do again, "
              "the exact same way as before, don't. Do something genuinely different this time.")
WEIRD_TEXT = ("Something in you shifts, unbidden — for this moment, you're not quite yourself. Act strangely and "
              "unpredictably, whatever that means for you: an odd fixation, a non sequitur, a sudden mood swing, "
              "talking to something that isn't there, an inexplicable urge acted on without hesitation. Commit to "
              "it fully rather than just remarking on feeling strange.")


# Every queue_intervention() call below passes a `label` — a short, plain
# admin-facing summary ("was healed (+25 health)") shown in the script log
# instead of the character's private flavor text, which stays reserved for
# their own prompt (see simulation.py::_apply_pending_interventions). Without
# this, the log line was just the raw flavor text with no indication of who
# it applied to or what actually happened mechanically.

def zap(char_id, intensity=15):
    storage.queue_intervention(char_id, ZAP_TEXT, health_delta=-intensity, stability_delta=-intensity,
                                label=f"was zapped (-{intensity} health/stability)")


def heal(char_id, amount=25):
    storage.queue_intervention(char_id, HEAL_TEXT, health_delta=amount, label=f"was healed (+{amount} health)")


def calm(char_id, amount=20):
    storage.queue_intervention(char_id, CALM_TEXT, stability_delta=amount, label=f"was calmed (+{amount} stability)")


def insert_thought(char_id, thought_text):
    storage.queue_intervention(
        char_id,
        f'An intrusive thought surfaces, unbidden: "{thought_text}"',
        stability_delta=-5,
        label="had a thought inserted",
    )


def disturb(char_id, intensity=10):
    storage.queue_intervention(char_id, DISTURB_TEXT, stability_delta=-intensity,
                                label=f"was disturbed (-{intensity} stability)")


def push(char_id, intensity=5):
    storage.queue_intervention(char_id, PUSH_TEXT, health_delta=-intensity, label=f"was pushed (-{intensity} health)")


def custom(char_id, text, health_delta=0, stability_delta=0, status_effect=None):
    """Escape hatch for anything not covered above — write your own stimulus text.
    No label: the admin already wrote this text themselves, so it's shown as-is."""
    storage.queue_intervention(char_id, text, health_delta, stability_delta, status_effect)


def force_break(char_id):
    """Watchdog-only (see watchdog.py): nudges a character out of a detected
    repetition loop. Not exposed on the dashboard — this fires automatically."""
    storage.queue_intervention(char_id, BREAK_TEXT, stability_delta=-5, label="was nudged out of a repeated beat")


def make_weird(char_id):
    """Admin-triggered or auto-rolled (simulation.py, per-character weirdness_chance)
    one-time nudge to act erratically this turn — not a lasting status effect."""
    storage.queue_intervention(char_id, WEIRD_TEXT, stability_delta=-3, label="is acting strangely")


def add_status_effect(char_id, effect, flavor_text=None):
    """A named condition (e.g. 'drunk', 'cursed', 'wounded') that sticks to the
    character and keeps showing up in their prompt every turn until removed —
    unlike the other interventions above, which are one-time stimuli."""
    text = flavor_text or f"Something changes in you, lasting: you are now {effect}."
    storage.queue_intervention(char_id, text, status_effect=effect, label=f"is now {effect}")
