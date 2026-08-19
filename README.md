# Room Simulation

A small framework for running an AI-driven "room" of characters on a Raspberry Pi:
they talk, act, have health and mental stability, can get hurt or die, and can be
replaced. You control the room from a web dashboard — add objects, add/replace
characters, and reach in and mess with someone's head (zap, insert a thought,
disturb, push).

## How it works

- **The Pi runs everything except the "thinking."** State (characters, objects,
  the full script log) lives in a local SQLite file. A FastAPI server serves a
  dashboard and ticks the simulation forward on a timer.
- **Each character's turn** = one call to whichever model backend you've
  configured — the Anthropic API, or Ollama running on your desktop over your
  LAN. The character gets: their persona, current health/stability/status
  effects, a compressed long-term memory summary, who else is in the room,
  what objects are around, and the last N lines of the script. It replies in
  character with a private thought, spoken dialogue, and a physical action.
- **Consequences**: if a turn's action/dialogue is aimed at another character, a
  second, cheaper model call referees whether it caused harm and how much. Health
  hits 0 → the character dies and is logged. You can also directly wound/kill
  characters yourself via the dashboard.
- **Memory**: raw events accumulate in the log; periodically each character's
  history is compressed into a short first-person summary so the prompt doesn't
  grow forever (this also keeps token/cost usage bounded on a Pi's modest network
  connection).

Why call the cloud API instead of running a model locally? A Pi 4 doesn't have
the horsepower to run a model good enough to carry four distinct personalities
in a believable, non-repetitive way at a decent speed. This design puts almost
all the compute in the API call and keeps the Pi's job to orchestration, storage,
and the UI — which it's plenty capable of.

## Setup

```bash
cd room_sim
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Pick a backend (see below), then:

```bash
python3 seed.py           # optional: creates 4 example characters + 2 objects
uvicorn main:app --host 0.0.0.0 --port 8000
```

Then open `http://<your-pi-ip>:8000` from any device on your network.

### Backend A: Anthropic API (cloud, costs money per call)

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export SIM_TICK_SECONDS=20        # seconds between automatic turns (cost/pace knob)
export SIM_MODEL=claude-sonnet-5
export SIM_ADJUDICATOR_MODEL=claude-haiku-4-5-20251001
```

### Backend B: Ollama on your desktop PC (free, runs on your own hardware)

This runs the model on your desktop's GPU/CPU and has the Pi call it over your
LAN — the Pi never sends anything to Anthropic in this mode.

**On the desktop:**

1. Install Ollama: https://ollama.com
2. By default Ollama only listens on `localhost`, so the Pi can't reach it.
   Expose it on your network:
   - **Windows**: Settings → System → About → Advanced system settings →
     Environment Variables → add `OLLAMA_HOST` = `0.0.0.0:11434` (system
     variable), then restart Ollama (quit it in the system tray and reopen).
   - **Mac**: `launchctl setenv OLLAMA_HOST "0.0.0.0:11434"` then restart the
     Ollama app.
   - **Linux**: edit the systemd unit (`sudo systemctl edit ollama`) and add
     `Environment="OLLAMA_HOST=0.0.0.0:11434"`, then
     `sudo systemctl restart ollama`.
   - Make sure your desktop's firewall allows inbound TCP on port 11434 from
     your LAN.
3. Pull a model. Pick based on your GPU's VRAM (rough guide — exact numbers
   depend on quantization):
   - No GPU / CPU only: something small like `qwen2.5:3b` or `phi3:mini` —
     usable but noticeably less coherent for four distinct personalities.
   - 8GB VRAM: `llama3.1:8b`, `qwen2.5:7b`, or `mistral:7b` — the sweet spot
     for most people, good enough voice/personality separation.
   - 12–16GB VRAM: `qwen2.5:14b` or a 14B-class model — noticeably better
     writing and character consistency.
   - 24GB+ VRAM: `llama3.1:70b` (heavily quantized, e.g. Q4) or `qwen2.5:32b` —
     much better at holding four voices without them blurring together.

   ```bash
   ollama pull llama3.1:8b
   ```

4. Find your desktop's LAN IP (`ipconfig` on Windows, `ip addr` on Linux/Mac).

**On the Pi**, test connectivity first, then configure:

```bash
curl http://<desktop-ip>:11434/api/tags     # should list your pulled models

export SIM_BACKEND=ollama
export SIM_OLLAMA_HOST=http://<desktop-ip>:11434
export SIM_MODEL=llama3.1:8b
export SIM_ADJUDICATOR_MODEL=llama3.1:8b    # can be a smaller/faster model if you like
```

No `ANTHROPIC_API_KEY` needed in this mode.

**Current defaults**: `SIM_BACKEND=ollama` and `SIM_MODEL`/
`SIM_ADJUDICATOR_MODEL=hermes3` are now the built-in defaults in
`config.py` (no env vars required to get that setup) — see
[`docs/model-choice.md`](docs/model-choice.md) for the history of what was
tried before this and why. Set the env vars above to override with a
different model/host.

**Windows convenience**: `start_windows.bat` sets the four env vars above (for
running on the same PC as Ollama) and starts the server in one step — after
the one-time venv setup, just double-click it.

**On model choice / content restrictions**: Ollama's library (and Hugging Face
more broadly) includes both standard instruction-tuned chat models and various
community-published variants with different levels of built-in content
filtering — searching Ollama's library or Hugging Face for terms like
"uncensored" will surface some. Since this runs entirely on your own hardware,
what a given model will or won't produce is between you and that model, not
something this framework restricts. Quality and personality-consistency vary a
lot between models, more than "censorship level" does, so it's worth testing a
couple against your actual characters before committing.

**Local-model quirks to expect**: smaller local models are chattier and less
reliable about the JSON-only output format than Claude is — `llm.py` already
does some cleanup for this (stripping stray commentary around the JSON), but
if a character's dialogue starts looking garbled, it's usually the model
losing the format under load; a bigger model or a lower `SIM_TICK_SECONDS`
(giving it less to hold in context) usually helps.

### Switching to a different model (e.g. an uncensored one)

The model is just a name in an env var — swapping it doesn't touch any code.

1. **Pull it on the machine running Ollama** (your desktop, not the Pi):
   ```bash
   ollama pull qwen2.5:7b-instruct
   ```
   Confirm it's there with `ollama list`.

2. **Point the sim at it.** Two ways, depending on how you start the server:

   - **`start_windows.bat`**: open it in a text editor and change these two
     lines (both — the sim uses a second, usually-cheaper model just to referee
     harm between characters, so if you want the uncensored model driving that
     too, set both to the same name):
     ```bat
     set SIM_MODEL=qwen2.5:7b-instruct
     set SIM_ADJUDICATOR_MODEL=qwen2.5:7b-instruct
     ```
     Save the file and double-click it as usual — no other changes needed.

   - **Running `uvicorn` by hand / via a shell**: set the env vars before
     starting the server instead:
     ```bash
     export SIM_MODEL=qwen2.5:7b-instruct
     export SIM_ADJUDICATOR_MODEL=qwen2.5:7b-instruct
     uvicorn main:app --host 0.0.0.0 --port 8000
     ```

3. **Restart the server.** The model is only read once at startup
   (`config.py`), so a running server won't pick up the change — stop it
   (Ctrl+C) and start it again.

4. **Sanity-check it's actually being used**: watch the dashboard's script log
   for the next couple of turns after restart. If dialogue style changes and
   there are no `[error during tick: ...]` system lines, it's working. An
   error there almost always means the model name doesn't exactly match what
   `ollama list` shows (tags matter — `qwen2.5:7b-instruct` and plain
   `qwen2.5:7b` are different pulls).

You can point `SIM_MODEL` and `SIM_ADJUDICATOR_MODEL` at two *different*
models too — e.g. a bigger, better one for character voice and a smaller/
faster one just for harm adjudication, same as the Claude setup does with
Sonnet + Haiku.

## Run it as a service (so it survives reboots/SSH disconnects)

Create `/etc/systemd/system/roomsim.service`:

```ini
[Unit]
Description=Room Simulation
After=network.target

[Service]
User=pi
WorkingDirectory=/home/pi/room_sim
Environment=ANTHROPIC_API_KEY=sk-ant-...
Environment=SIM_TICK_SECONDS=20
ExecStart=/home/pi/room_sim/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now roomsim
journalctl -u roomsim -f     # watch logs
```

## Using the dashboard

- **Script log** (center): every line — dialogue in quotes, actions with `*`,
  private thoughts in italics/parentheses, system events and interventions in
  color. This is your "see every line" view.
- **Characters** (left): health/stability bars, status effects, and buttons:
  - **Zap** — a sudden jolt; damages health and stability.
  - **Insert thought** — you type a thought; it surfaces in their mind next turn
    and they react to it in character.
  - **Disturb** — vague unease, dents stability only.
  - **Push** — physical shove, small health hit.
  - **Kill** — instant death, logged.
  - Once dead, a **Replace** button lets you type in a new name + persona to
    bring in a new character.
- **Objects**: add anything with a name + description; it becomes part of every
  character's prompt from then on ("a rusted key on the windowsill", "a locked
  door", whatever you want).
- **Locations**: add anything with a name + description ("downtown_cafe", "a
  small cafe a short walk from the main room"). Every character always exists
  at exactly one location — new characters default to `main_room` unless you
  pick a different one when adding them.
- **Pause / Resume / Force next turn**: pause the autoplay loop and step through
  turns manually when you want tighter control over pacing while writing.

## Living apart: locations, messaging, and world knowledge

Characters aren't confined to one room. Each character has a `location`, and
on their turn the model can choose to:

- **Move** — travel to any location you've added, ending their in-person scene
  with whoever they left behind and starting one with whoever's at the new
  spot next turn.
- **Message** someone who isn't with them — a text, email, or call. This is
  logged as its own line in the script log (`[text] Devon → Priya: ...`) and
  reaches its recipient regardless of location, prompting them to react on
  their next turn, same as being spoken to in person. Dialogue/actions still
  only reach people actually in the same location — a character can't overhear
  or physically interact with someone somewhere else.
- **Investigate** a topic — a short "what does {name} find out" model call
  invents one concrete fact about the wider world, which gets stored in a
  shared world-knowledge base (visible in the sidebar) and folded into every
  character's prompt going forward, so discoveries can actually shape the
  story instead of staying private to whoever found them out.

None of this is forced — a character who never has anyone to message and
never wanders off will just keep behaving like it's a single-room sim, same as
before this feature existed.

## Cost and pacing

Each automatic turn is 1 API call (character) + up to 1 more (harm adjudication,
only when an action targets someone). At `SIM_TICK_SECONDS=20` that's roughly
150-200 calls/hour if the room is active. Raise the tick interval, or keep the
sim paused and use "Force next turn" while you're actively writing, to control
cost.

## Extending it

This is intentionally a minimal skeleton. Natural next steps:

- **Relationships**: add a `relationships` table/JSON blob so characters
  remember how they feel about each specific other character, not just a
  general memory summary.
- **Richer status effects**: status effects currently are just strings shown to
  the character. You could give them mechanical effects (e.g. "wounded" reduces
  max possible actions) by branching on them in `simulation.py`.
- **Configurable chapter cadence/style**: `chapters.py` has one fixed prompt;
  you could add a tone knob (comedic, bleak, literary) or generate on a
  different schedule than daily.
- **A "narrator" pass**: run one more LLM call per few turns that writes a short
  scene-setting line, for richer material when a chapter gets generated.

## Reading it as a story (daily chapters)

Every hour, the Pi checks whether the previous day has ended without a chapter
yet. If so, it sends that day's full raw event log to whichever backend you've
configured (your desktop's Ollama, or Claude) with instructions to write it up
as a short third-person narrative chapter — not the script, actual prose. The
chapter gets stored in the same SQLite database and served at:

```
http://<your-pi-ip>:8000/read
```

That's a separate, phone-friendly page from the admin dashboard — no buttons,
just a list of chapters you can tap into, serif type, and a light/dark toggle
that remembers your preference. Open it in Safari on your iPhone and use
"Add to Home Screen" if you want it to feel like its own app.

You don't have to wait for the hourly check: the admin dashboard has a
**"Write today's chapter"** button that generates one immediately from
whatever's happened so far (running it again later regenerates and replaces
that day's chapter with an updated version covering more of the day).

Note: day boundaries are based on the Pi's system clock/timezone, so make sure
that's set correctly (`sudo raspi-config` → Localisation Options, or
`timedatectl set-timezone <Region/City>`).

## Files

```
config.py           settings (env vars)
models.py            Character / SimObject / Location / WorldFact / Event dataclasses
storage.py            SQLite layer
llm.py                 Anthropic / Ollama backend wrapper
memory.py               prompt context building + memory compression
interventions.py         zap / insert_thought / disturb / push / custom
watchdog.py                repetition detector + session tick cap (docs/watchdog.md)
simulation.py                the turn loop + harm adjudication + movement/messaging/investigating
chapters.py                    daily raw log → narrative chapter
main.py                          FastAPI app: dashboard, reading page, control API
templates/index.html              the admin dashboard UI
templates/read.html                the phone-friendly reading UI
seed.py                             optional starter cast
docs/                                 design notes for specific subsystems
```

See `ROBUSTNESS_TODO.md` for the running list of robustness work (memory,
watchdog, tool calling, etc.) and `docs/` for design writeups of what's
already landed.
