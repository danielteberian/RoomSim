"""
Circuit breaker for the simulation loop (ROBUSTNESS_TODO.md §1, §7).

Three independent safety nets:
  - repetition detector: flags a character stuck repeating the same
    dialogue/action turn after turn, and auto-nudges them out of it.
  - stall detector: flags a character producing nothing at all (empty
    thought/dialogue/action, even after simulation.py's built-in retry)
    turn after turn, and auto-nudges them too.
  - session tick cap: a blunt whole-room limit so a room stuck in any kind
    of loop can't run unattended forever.

See docs/watchdog.md for the design rationale and how to tune it.
"""
import re

import interventions
import storage
from config import CFG

_WORD_RE = re.compile(r"[a-z0-9']+")

# char_id -> consecutive near-duplicate turns seen so far
_repeat_counts = {}
# char_id -> normalized word set from that character's last turn
_last_turn_words = {}
# char_id -> consecutive completely-empty turns seen so far
_stall_counts = {}
# total ticks this process has run (resets on restart, not persisted)
_session_ticks = {"count": 0}


def _normalize(text: str) -> frozenset:
    return frozenset(_WORD_RE.findall(text.lower()))


def _similarity(a: frozenset, b: frozenset) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def check_repetition(char_id: str, char_name: str, dialogue: str, action: str) -> bool:
    """Call once per turn, after a character's dialogue/action for that turn
    is finalized. Compares against their own last turn only (not other
    characters' lines) via a word-overlap ratio, since exact-string matching
    misses near-duplicates like a re-worded version of the same beat.
    Returns True if the breaker tripped this turn (a nudge was queued)."""
    combined = f"{dialogue} {action}".strip()
    words = _normalize(combined)
    prev = _last_turn_words.get(char_id)
    _last_turn_words[char_id] = words

    if not combined:
        _repeat_counts[char_id] = 0
        return False

    if prev is not None and _similarity(words, prev) >= CFG.repetition_similarity:
        _repeat_counts[char_id] = _repeat_counts.get(char_id, 0) + 1
    else:
        _repeat_counts[char_id] = 0

    if _repeat_counts[char_id] >= CFG.repetition_repeat_threshold:
        _repeat_counts[char_id] = 0
        # Don't let the nudge's own (different) line immediately compare
        # against a stale "prev" from the loop we just broke.
        _last_turn_words[char_id] = None
        interventions.force_break(char_id)
        storage.add_event(
            "system",
            f"[watchdog] {char_name} seemed stuck repeating themselves — nudged to break the loop.",
            character_id=char_id, character_name=char_name,
        )
        return True
    return False


def check_stall(char_id: str, char_name: str, thought: str, dialogue: str, action: str) -> bool:
    """Call once per turn. A 'stall' is thought/dialogue/action ALL empty —
    simulation.py::tick() already retries once with a blunter instruction
    before calling this, so by the time this sees an empty turn, the model
    has already ignored two direct instructions to do something. Unlike
    check_repetition, an empty turn here counts *toward* the streak instead
    of resetting it — that's the point, it's the opposite failure mode
    (producing nothing instead of producing the same thing).
    Returns True if the breaker tripped this turn (a nudge was queued)."""
    if thought or dialogue or action:
        _stall_counts[char_id] = 0
        return False

    _stall_counts[char_id] = _stall_counts.get(char_id, 0) + 1
    if _stall_counts[char_id] >= CFG.repetition_repeat_threshold:
        _stall_counts[char_id] = 0
        interventions.force_break(char_id)
        storage.add_event(
            "system",
            f"[watchdog] {char_name} has gone silent for several turns in a row — nudged to act.",
            character_id=char_id, character_name=char_name,
        )
        return True
    return False


def reset(char_id: str):
    """Clear a character's repetition/stall tracking — call when they die,
    are replaced, or are hard-deleted, so a fresh character never inherits a
    dead one's counts."""
    _repeat_counts.pop(char_id, None)
    _last_turn_words.pop(char_id, None)
    _stall_counts.pop(char_id, None)


def record_tick():
    """Call once per simulation.tick() call, regardless of outcome."""
    _session_ticks["count"] += 1


def session_tick_count() -> int:
    return _session_ticks["count"]


def session_cap_reached() -> bool:
    return CFG.max_ticks_per_session > 0 and _session_ticks["count"] >= CFG.max_ticks_per_session


def reset_session_cap():
    """Call when the admin manually resumes after an auto-pause, so the cap
    doesn't immediately re-trip on the very next tick."""
    _session_ticks["count"] = 0
