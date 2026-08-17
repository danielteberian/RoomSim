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


def zap(char_id, intensity=15):
    storage.queue_intervention(char_id, ZAP_TEXT, health_delta=-intensity, stability_delta=-intensity)


def heal(char_id, amount=25):
    storage.queue_intervention(char_id, HEAL_TEXT, health_delta=amount)


def calm(char_id, amount=20):
    storage.queue_intervention(char_id, CALM_TEXT, stability_delta=amount)


def insert_thought(char_id, thought_text):
    storage.queue_intervention(
        char_id,
        f'An intrusive thought surfaces, unbidden: "{thought_text}"',
        stability_delta=-5,
    )


def disturb(char_id, intensity=10):
    storage.queue_intervention(char_id, DISTURB_TEXT, stability_delta=-intensity)


def push(char_id, intensity=5):
    storage.queue_intervention(char_id, PUSH_TEXT, health_delta=-intensity)


def custom(char_id, text, health_delta=0, stability_delta=0, status_effect=None):
    """Escape hatch for anything not covered above — write your own stimulus text."""
    storage.queue_intervention(char_id, text, health_delta, stability_delta, status_effect)


def force_break(char_id):
    """Watchdog-only (see watchdog.py): nudges a character out of a detected
    repetition loop. Not exposed on the dashboard — this fires automatically."""
    storage.queue_intervention(char_id, BREAK_TEXT, stability_delta=-5)


def add_status_effect(char_id, effect, flavor_text=None):
    """A named condition (e.g. 'drunk', 'cursed', 'wounded') that sticks to the
    character and keeps showing up in their prompt every turn until removed —
    unlike the other interventions above, which are one-time stimuli."""
    text = flavor_text or f"Something changes in you, lasting: you are now {effect}."
    storage.queue_intervention(char_id, text, status_effect=effect)
