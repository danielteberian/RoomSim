# Watchdog / circuit breaker

Implements item **§1** of [`ROBUSTNESS_TODO.md`](../ROBUSTNESS_TODO.md).

## Problem this solves

Long-running LLM-driven loops (this sim, or the Vending-Bench agent it's
modeled after) fail most often not by being "wrong" in an obviously bad way,
but by getting stuck: a character repeats the same line or beat turn after
turn, or a whole session just keeps running unattended long after anything
useful is happening. Nothing in the original code stopped either of those —
`CFG.ollama_repeat_penalty` (`llm.py`) discourages *token-level* looping
within a single generation, but does nothing about a character re-saying the
same *sentence*, reworded, three turns in a row.

## What was added

New module: **`watchdog.py`**. Three independent, always-on checks (originally
two — the stall detector was added after `dolphin3` was observed producing
completely empty turns repeatedly, which the repetition detector doesn't
catch since it resets on empty rather than counting it — see
[`model-choice.md`](model-choice.md)):

### 1. Repetition detector

- Every tick, once a character's `dialogue`/`action` for that turn is final
  (`simulation.py::tick()`, right after they're logged to `events`),
  `watchdog.check_repetition(char_id, char_name, dialogue, action)` runs.
- It normalizes `f"{dialogue} {action}"` into a lowercase word set and
  compares it to that same character's *previous* turn via a word-overlap
  (Jaccard) ratio — not exact string matching, so a slightly reworded repeat
  of the same beat still counts, not just a verbatim duplicate.
- If the overlap ratio is `>= CFG.repetition_similarity` (default **0.72**)
  for `CFG.repetition_repeat_threshold` (default **3**) consecutive turns,
  the breaker trips:
  - `interventions.force_break(char_id)` queues a stimulus telling the
    character, in-fiction, to do something different — it's woven into
    their next turn exactly like `zap`/`disturb`/etc. already are, so it
    plays out in-character instead of as an out-of-band reset.
  - A `system` event is logged (`[watchdog] <name> seemed stuck repeating
    themselves...`) so trips are visible in the script log/chapters, not
    just server logs.
  - The counter resets, and the tracked "last turn" is cleared so the
    nudge's own (different) line doesn't get compared against the loop it
    just broke.
- Comparison is **per character**, against their own history only — two
  different characters legitimately saying similar things (e.g. both
  arguing about the same object) is normal scene behavior, not a loop.

### 2. Stall detector

- The opposite failure mode from repetition: a character returns
  completely empty `thought`/`dialogue`/`action` — not once (that's normal;
  `simulation.py::tick()` already retries once with a blunter instruction
  for that), but repeatedly, turn after turn, even after the retry.
  Previously this was entirely silent — no `storage.add_event` fired for an
  empty turn, so a stalled room looked like "nothing is happening" with
  zero trace of why in the script log.
- `watchdog.check_stall(char_id, char_name, thought, dialogue, action)`
  runs right after the stall/retry logic in `tick()`. Unlike the repetition
  counter, an empty turn *increments* the streak (repetition's counter does
  the opposite — resets on empty, since silence isn't a repeat of anything).
- Every single stalled turn now also logs a `[stall]` system event
  immediately, independent of the counter — so a one-off stall is visible
  right away, not just once `repetition_repeat_threshold` consecutive
  stalls trip the auto-nudge (same `interventions.force_break` +
  `[watchdog]` event as the repetition detector, and it reuses the same
  `CFG.repetition_repeat_threshold` knob rather than adding a second one).

### 3. Session tick cap

- `watchdog.record_tick()` runs at the top of every `simulation.tick()`
  call, counting total ticks since the process started (not persisted
  across restarts).
- If the count reaches `CFG.max_ticks_per_session` (default **500**, `0`
  disables it), `main.py`'s `_loop()` sets `RUNNING["on"] = False` and logs
  a `system` event explaining why the room auto-paused. This is a blunt net
  independent of the repetition detector — it catches loops the detector
  doesn't recognize as such (e.g. slow drift, or a healthy-looking but
  directionless scene left running overnight).
- Pressing **Resume** on the dashboard calls `watchdog.reset_session_cap()`
  so the cap doesn't immediately re-trip on the very next tick.

### Cleanup

`watchdog.reset(char_id)` clears a character's repetition tracking. It's
called wherever a character stops being "live" so a *new* character never
inherits a dead one's repeat count:
- both death paths in `simulation.py::tick()` (self-inflicted and
  target-of-harm)
- the death path in `simulation.py::force_interaction()`
- `main.py`'s kill, hard-delete, and replace endpoints

## Config knobs

All in `config.py`, all overridable via env var:

| Field | Env var | Default | Meaning |
|---|---|---|---|
| `repetition_repeat_threshold` | `SIM_REPETITION_THRESHOLD` | `3` | consecutive similar turns before tripping |
| `repetition_similarity` | `SIM_REPETITION_SIMILARITY` | `0.72` | word-overlap ratio counted as "the same beat" |
| `max_ticks_per_session` | `SIM_MAX_TICKS` | `500` | ticks before auto-pause (`0` = disabled) |

Tuning notes:
- Lower `repetition_similarity` (e.g. `0.5`) makes the detector fire on
  looser paraphrases; raise it (e.g. `0.85`) to only catch near-verbatim
  repeats and tolerate more restating-with-variation.
- Smaller/local models (which this sim's default backend now is — see
  [`model-choice.md`](model-choice.md)) loop more than
  Claude did, so the defaults were picked assuming a chattier, more
  repetition-prone model rather than tuned purely for Claude.
- `max_ticks_per_session=500` at the default `SIM_TICK_SECONDS=15` is
  roughly ~2 hours of unattended runtime. Raise it (or set to `0`) if you
  plan to leave the sim running longer between check-ins.

## How to test it

1. Start the sim, let it run, and manually push a character into a loop via
   the dashboard — e.g. repeatedly hit **Insert thought** with the same
   text, or set a `directive` the current model tends to restate rather
   than act on.
2. Watch the script log for the `[watchdog] ... nudged to break the loop`
   line after `repetition_repeat_threshold` similar turns.
3. To test the session cap without waiting for real time to pass, temporarily
   set `SIM_MAX_TICKS=5` (or lower `SIM_TICK_SECONDS`) before starting the
   server, then watch the dashboard's Running indicator flip off with the
   matching system event after 5 ticks.

## What this does *not* do (yet)

- It doesn't distinguish "stuck" from "deliberately repetitive persona"
  (e.g. a character whose whole bit is repeating a catchphrase) — if that
  becomes a problem, a per-character opt-out would be the simplest fix.
- It's a nudge, not a hard reset — if a model repeatedly ignores the nudge,
  it'll just keep re-tripping every `repetition_repeat_threshold` turns.
  §7 in `ROBUSTNESS_TODO.md` (continuation/escalation limits) is the
  planned follow-up: escalate after N *trips*, not just N repeated turns.
- No benchmark harness yet to quantify how often this fires per model —
  that's `ROBUSTNESS_TODO.md` §8, and is meant to build on top of this.
