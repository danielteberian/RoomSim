"""
Admin interventions: things you can do TO a character from outside the simulation.
Each one queues a "stimulus" that gets woven into that character's next turn, so
they react to it in-character rather than just having a stat silently change.
"""

import storage

ZAP_TEXT = "A sharp, sudden jolt runs through your body — like static shock, but stronger. It came from nowhere you can see."
DISTURB_TEXT = "A wave of unease washes over you. Something feels wrong, though you can't say what."
PUSH_TEXT = "You feel a sudden, forceful shove, as if unseen hands moved you."


def zap(char_id, intensity=15):
    storage.queue_intervention(char_id, ZAP_TEXT, health_delta=-intensity, stability_delta=-intensity)


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
