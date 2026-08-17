# Robustness Roadmap (Vending-Bench-inspired)

Context: the Vending-Bench paper found that long-horizon LLM agents degrade not
from lack of intelligence but from context/memory failures — they lose track
of ground truth, spiral into repetition, and can't recover without external
scaffolding. This sim already does some of this right (fresh DB read every
`tick()`, rolling `memory_window` + `memory_summary`, a repeat-penalty at the
sampler level). The gaps below are the ones actually worth closing, ordered
by cost/benefit, with pointers into the current code so each item is a real
patch, not a rewrite.

Check items off as you land them. Each section is independent — no need to
do them in order except where noted.

---

## 1. Watchdog / circuit breaker — do this first ✅ DONE

Cheapest fix for the worst failure mode (a character stuck repeating the same
beat forever, silently burning your whole session).

Implemented in `watchdog.py`, wired into `simulation.py` and `main.py`.
Full design writeup: [`docs/watchdog.md`](docs/watchdog.md).

- [x] **Repetition detector.** Word-overlap comparison against each
      character's own previous turn, tracked in-memory in `watchdog.py`
      (`_repeat_counts`, `_last_turn_words`), checked in
      `simulation.py::tick()` right after that turn's dialogue/action are
      logged.
- [x] **Auto-intervention on trip.** New `interventions.force_break()`,
      queued through the existing `storage.queue_intervention` /
      `_apply_pending_interventions` stimuli channel — no new channel built.
- [x] **Hard turn cap.** `CFG.max_ticks_per_session` (`config.py`,
      `SIM_MAX_TICKS`, default 500), tracked by `watchdog.record_tick()`/
      `session_cap_reached()`, checked in `main.py::_loop()` which pauses
      (`RUNNING["on"] = False`) and logs why.
- [x] Every trip and auto-pause logs a `storage.add_event("system", ...)`
      line (prefixed `[watchdog]`) so it's visible in the script log/
      chapters, ready for §8's harness to consume later.

## 2. State-grounding — mostly already true, close the remaining gaps

`tick()` already reloads `char` fresh from `storage.get_character(char.id)`
after interventions (line 174) and rebuilds `others`/`objects`/`log` from DB
every turn (memory.py:6-21) — this is the single biggest thing the paper says
kills agents, and you're already doing it. Remaining gaps:

- [ ] `all_chars` (line 176) is fetched once per tick and reused for target
      resolution (line 239) — fine within a tick, but double check nothing
      caches it *across* ticks.
- [ ] `memory_summary` is model-written prose (memory.py:40) — it's a
      compression, not ground truth, and models can drift/hallucinate into
      it over many compressions. Consider keeping a small structured
      "facts" side-channel (see §3.2) for things that must stay correct
      (who's alive, current location, active status effects) rather than
      trusting the prose summary for those — health/stability/status_effects
      are already read fresh from DB into the prompt (simulation.py:181-187),
      which is correct; just don't let future features accidentally start
      trusting `memory_summary` for anything that already has a DB column.

## 3. External memory beyond the context window

Right now "memory" = `memory_summary` (one blob per character,
memory.py:24-43) + last `CFG.memory_window` events. That's the sliding
window; it's missing the two things Vending-Bench's agent used to survive
long horizons.

- [ ] **3.1 Scratchpad.** Add a `scratchpad` table (or reuse
      `char.memory_summary` as-is and add a *second*, model-editable field)
      that the character can write short freeform notes to on its own turn —
      e.g. add an optional `"note"` field to the JSON schema in
      `CHARACTER_SYSTEM_TEMPLATE` (simulation.py:46), persist it via a new
      `storage.append_scratchpad(char_id, text)` call. Surface the last few
      scratchpad entries back into the prompt the same way `others`/`log`
      are injected.
- [ ] **3.2 Key-value store.** A `facts` table (`character_id, key, value`)
      for structured, non-prose state that doesn't need re-deriving from the
      event log each turn — grudges, promises, relationship scores, "owes X
      a favor." Give the model a lightweight way to set these (could piggyback
      on the sub-agent split in §6: one call decides the action, a second
      call extracts any fact updates). Read relevant facts into the prompt
      alongside `memory_summary`.
- [ ] **3.3 Vector store for semantic recall.** Only worth it once event
      history is long enough that `CFG.memory_window` truncation is
      actually losing relevant material. Use a local embedding model via
      Ollama (`nomic-embed-text` or `mxbai-embed-large`) rather than
      OpenAI's — embed each `Event` on insert (`storage.add_event`), store
      vectors in a sidecar table or sqlite-vec/FTS5, and pull top-k relevant
      past events into the prompt instead of (or in addition to) the
      strict recency window. Lowest priority of the three — do 3.1/3.2
      first since they're cheap and this is the bigger lift.

## 4. Native tool calling

`llm.py` currently does prompt-engineered JSON end to end
(`call_llm_json` + `_extract_json`, llm.py:115-172) with a hand-rolled
brace-matcher and a retry-with-sharper-prompt fallback. This works but is
exactly the kind of thing Ollama's native tools API removes the need for.

- [ ] Add an `_call_ollama_tools()` path in `llm.py` that passes a JSON
      schema (matching the shape already documented in
      `CHARACTER_SYSTEM_TEMPLATE`'s trailing JSON block, simulation.py:46,
      and `ADJUDICATOR_SYSTEM`, simulation.py:55) via Ollama's `tools`
      parameter instead of asking for JSON in prose.
- [ ] Gate it behind `CFG.model` / a capability flag — test against
      `qwen3:8b` specifically, since that's the model most likely to support
      it well among your candidates (§8).
- [ ] Keep `call_llm_json` exactly as-is as the fallback path for
      models/backends without solid native tool support (it already has
      the retry-once-then-degrade behavior you'd want as a fallback).

## 5. Sliding context management (mostly formalize what exists)

`CFG.memory_window` (config.py) + `memory.maybe_summarize` already implement
"last N turns + external memory summary." Nothing structurally missing here,
just tighten it up:

- [ ] Confirm `CFG.memory_window` and `CFG.summarize_every` are tuned by
      testing, not guessing — the paper's finding that bigger context
      budgets performed *worse* means don't reflexively raise
      `memory_window` if a character seems to be forgetting something;
      that's what §3.2's fact store is for instead.
- [ ] Once §3.1/3.2 land, make sure the prompt assembly in
      `simulation.py::tick()` (the `CHARACTER_SYSTEM_TEMPLATE.format(...)`
      call, line 207) treats scratchpad/facts as the durable layer and the
      event log window as the recent-detail layer — don't let both balloon
      the prompt independently.

## 6. Sub-agent / role separation

Right now one call does everything: decide + generate dialogue + generate
action + pick a target, all in a single `call_llm_json` (simulation.py:213).
The empty-response retry (line 225-238) is a partial patch for this same
class of problem.

- [ ] Split into two calls: a cheap "decide" call (what does this character
      want to do this turn — target + intent, low `max_tokens`) followed by
      an "execute" call that generates the actual dialogue/action text given
      that decision. Mirrors the paper's main-agent/sub-agent split and
      should reduce cases where a single confused generation garbles both
      the decision and the phrasing together.
- [ ] This maps naturally onto `interventions.py`'s existing queue pattern —
      treat the "decide" output as something that could also be
      admin-overridden/inspected before the "execute" call runs, the same
      way `force_interaction()` already puppet-masters a character
      end-to-end.
- [ ] Lower priority than §2/§3 — bigger refactor, moderate payoff. Do it
      after the watchdog and memory work are in, since it'll change the
      call sites those depend on.

## 7. Continuation & escalation limits

- [ ] Cap how many consecutive turns a character can go with only
      `thought` filled and no real state change (ties directly into the
      repetition detector in §1 — reuse the same counter/table rather than
      building a second one).
- [ ] The empty-dialogue-and-action retry (simulation.py:225-238) already
      logs nothing when it fails twice — add a `storage.add_event("system",
      ...)` note when a character "stalls" (retry also comes back empty),
      so stall patterns show up in the script log/chapters instead of
      silently no-op'ing.
- [ ] Once tripped repeatedly for one character, escalate to a forced
      checkpoint: run `memory.maybe_summarize()` early (don't wait for
      `CFG.summarize_every`) and drop a stronger nudge via
      `interventions.custom()`.

## 8. Benchmark harness

- [ ] You already have durable, queryable history (`events` table,
      `chapters` table) — add a small script (`bench.py`?) that runs a fixed
      seeded scenario (reuse `seed.py`) for N ticks against a given
      `CFG.model` and reports: turns until first repetition-detector trip
      (§1), turns until "no meaningful state change" (health/stability/
      location all static for M turns), and stall count (§7).
- [ ] Run the same scenario across `llama3.1:8b`, `qwen2.5:7b`, `qwen3:8b` to
      pick a default `CFG.model` with data instead of guessing — this also
      doubles as your test bed for validating §1 and §7 actually work
      before trusting them unattended.
- [ ] Do this *after* §1 (watchdog) exists — otherwise there's nothing for
      the harness to measure turns-until-trip against.

---

## Suggested order

1. §1 Watchdog (cheap, fixes the worst failure mode)
2. §2 State-grounding gaps (small, mostly already done — just close them)
3. §3.1 + §3.2 Scratchpad + fact store (cheap external memory wins)
4. §7 Continuation/escalation limits (builds directly on §1)
5. §8 Benchmark harness (needs §1 to have something to measure)
6. §4 Native tool calling (bigger lift, test against qwen3:8b)
7. §6 Sub-agent split (bigger refactor, do after the above stabilizes)
8. §3.3 Vector store (biggest lift, only once event history is long enough
   to need it)
