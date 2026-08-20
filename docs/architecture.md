# Architecture

The README explains what this project does and how to run it. This doc is
the other half: how the code is actually put together, for anyone about to
read or modify it. It assumes you've read the README first.

## Process model

One process, no background workers or queues:

- `main.py` boots a FastAPI app and two `asyncio` background tasks on
  startup (`_loop()` and `_daily_chapter_loop()`), then serves the dashboard
  (`/`), the reading page (`/read`), and a JSON control API (`/api/...`).
- `_loop()` is the heartbeat: every `CFG.tick_seconds` (default 15-20s),
  if `RUNNING["on"]`, it calls `simulation.tick()`. That's the entire
  autoplay mechanism — no cron, no Celery, just a `while True: await
  asyncio.sleep(...)` loop.
- `_daily_chapter_loop()` wakes up once an hour and checks whether
  yesterday ended without a chapter; if so it calls into `chapters.py`.
- Everything shares one SQLite connection via `storage.py`. There's no
  concurrency to speak of beyond the two async loops and incoming HTTP
  requests, which is why storage doesn't need connection pooling or
  transaction juggling — see `storage.py`'s module docstring/comments for
  the specific SQLite settings this relies on.

## The tick: one round, not one character

`simulation.tick()` is called once per interval. Internally it doesn't run
one character — it runs **everyone alive and active, once each**
(`_ordered_alive_characters()`), in an order where anyone who was just
addressed (messaged, spoken to, targeted) goes first so replies land in the
same round instead of a full round later. Each character's turn is
`_take_turn(char)`, which re-checks `alive` before running (someone earlier
in the same round may have killed them).

`_take_turn` is the core function of the whole project. Per character, per
turn, it:

1. **Applies pending interventions** (`interventions.py` — zap, insert
   thought, disturb, push, forced weirdness) and lets aggression drift
   based on what just happened to them.
2. **Builds the prompt** — gathers everything the character can currently
   perceive or recall (see next section) and formats it into
   `CHARACTER_SYSTEM_TEMPLATE`.
3. **Calls the model** (`llm.call_llm_json`) and parses a JSON response:
   `thought`, `dialogue`, `action`, plus optional fields for every
   mechanic the character can invoke that turn — `target`, `move_to`,
   `message_*`, `investigate`, `explore`, `new_object_*`, `gather_item`,
   `give_*`, `current_goal`, `mood`, `lie_to`, `relationship_shift`. If
   dialogue and action both come back empty, it retries once with a
   blunter instruction (smaller/local models default to "just thinking"
   too often otherwise).
4. **Applies consequences in order**: logs thought/dialogue/action as
   events; runs harm adjudication if an action targeted someone
   (`_adjudicate_harm`, a second, cheaper LLM call that scores 0+ damage);
   handles death if health hit 0; delivers a message if one was sent
   (which also writes a `CharacterKnowledge` row for the recipient —
   messaging is the game's knowledge-sharing mechanism, true or false);
   handles investigate/explore/create-object/gather/give; decays needs;
   applies movement last (so earlier events log at the location the
   character was actually in).
5. **Persists** the character with one consolidated `storage.update_character`
   call at the end.

Everything runs synchronously inside one tick — there's no separate queue
for consequences. A character can die, move, or spawn a message mid-round,
and the very next character's turn in that same round already sees the
updated state, because `_take_turn` re-fetches from `storage` rather than
working off a stale snapshot.

## Prompt construction

A character's system prompt is assembled from many small optional "blocks"
(`_take_turn` builds ~15 of them: time, elsewhere, locations, world facts,
status effects, stimuli, focus, setting, directive, interests, dislikes,
dialect, guidelines, mood, needs, knowledge, inventory, self-goal,
scenario) and formatted into `CHARACTER_SYSTEM_TEMPLATE`
(`simulation.py`). Each block is only included if it has content — an
otherwise-plain character with no directive/mood/interests/etc. gets a
short, plain prompt; a character accumulating history gets a much longer
one. `_response_schema(locked)` and `_life_bullets(locked)` change what
the model is told it's allowed to do depending on whether the scenario is
`locked` (pure single-room drama, no travel/messaging/investigating/lying).

`memory.py::build_prompt_context()` builds the parts that require
cross-referencing other characters and the event log:
- **Who's here / who's elsewhere**, each annotated with a one-line
  relationship note (`_relationship_note`: affinity → loves/likes/neutral/
  dislikes/hates, trust note, plus the last couple of
  `RelationshipEvent`s — this is what makes a rivalry feel earned instead
  of a static label).
- **The script log window** (`CFG.memory_window` recent events), each line
  labeled `[YOU as X]` for the character's own past lines vs.
  `[OtherName]` for everyone else's — deliberately explicit rather than
  left for the model to infer from name-matching, because local models
  especially tend to blur into speaking as each other otherwise (see
  `docs/model-choice.md`).
- **World facts**, the shared discoveries from `investigate`/`explore`.

`memory.py::maybe_summarize()` is the other half of memory: once a
character has accumulated `CFG.summarize_every` new events since their
last summary, their whole recent history is compressed by one more LLM
call into a short first-person paragraph (`char.memory_summary`), which is
what actually goes in the prompt going forward instead of the raw log —
this is what keeps prompt size (and cost/latency) bounded no matter how
long a room has been running.

## Data model

`models.py` defines plain dataclasses; `storage.py` is the only place that
knows about SQLite (it hand-rolls schema + queries, no ORM). Key entities:

- **Character** — the mutable, evolving unit: health/stability/aggression/
  mood/needs (hunger/boredom/social/safety), inventory, location,
  memory_summary, and admin-settable knobs (directive, interests,
  dislikes, guidelines, dialect, weirdness_chance).
- **Event** — the append-only script log. Every dialogue/action/thought/
  system/intervention/death/message line is one row, tagged with `kind`,
  optional `character_id`/`target_id`, `location` (`None` = visible
  everywhere), and for messages a `channel`. This table is the source of
  truth for "what happened" — the dashboard, `/read` chapters, and prompt
  context are all just different views/filters over it.
- **Relationship** (directional, `char_a` about `char_b`) + **
  RelationshipEvent** (a short note like "insulted me in front of
  everyone") — together these are what `_relationship_note` renders into
  the prompt. Directional on purpose: A can trust B while B distrusts A.
- **CharacterKnowledge** — facts a specific character personally knows,
  with `is_true` (they can be lied to without knowing it) and `source`.
  Distinct from `WorldFact`, which is shared/public once discovered.
- **SimObject**, **Location**, **WorldFact** — world state, each taggable
  with who created/discovered it (`None` = admin-added).

## Module map (what to touch for what)

| Module | Responsibility |
|---|---|
| `main.py` | FastAPI app, HTTP endpoints, the two background loops |
| `simulation.py` | the turn loop, prompt template, harm adjudication, movement/messaging/investigating/gathering |
| `memory.py` | prompt context assembly (who/what/log/relationships) + memory compression |
| `storage.py` | all SQLite reads/writes; the only module that knows the schema |
| `models.py` | dataclasses for every persisted entity |
| `llm.py` | backend-agnostic wrapper (`call_llm`, `call_llm_json`) over Anthropic or Ollama |
| `interventions.py` | admin-triggered stimuli (zap/insert_thought/disturb/push) queued for the character's next turn |
| `watchdog.py` | repetition/stall detection + session tick cap — see `docs/watchdog.md` |
| `chapters.py` | turns a day's raw event log into third-person prose for `/read` |
| `config.py` | all env-var-driven settings (`CFG`), read once at startup |
| `seed.py` | optional starter cast/objects for a fresh database |

## Where to look for a given kind of change

- **New per-turn mechanic** (character can now do X): add the field to the
  JSON schema in `_response_schema()`, parse it in `_take_turn`, act on it
  alongside the existing gather/give/investigate handling.
- **New thing that shapes the prompt**: add a block in `_take_turn` (or a
  helper like `_needs_block`/`_mood_block`) and wire it into
  `CHARACTER_SYSTEM_TEMPLATE`.
- **New persisted field**: add it to the relevant dataclass in `models.py`,
  then to the schema/read/write functions in `storage.py` — there's no
  migration system, so a schema change usually means either wiping
  `simulation.db` or hand-writing an `ALTER TABLE` alongside the change.
- **New admin control**: add a Pydantic body model + endpoint in `main.py`,
  wire it into `templates/index.html`.
