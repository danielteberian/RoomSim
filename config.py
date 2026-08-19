import os
from dataclasses import dataclass


@dataclass
class Config:
    # "anthropic" (cloud API) or "ollama" (a local/network model, e.g. your desktop PC)
    backend: str = os.environ.get("SIM_BACKEND", "ollama")

    anthropic_api_key: str = os.environ.get("ANTHROPIC_API_KEY", "")

    # Base URL of the machine running Ollama. Point this at your desktop's LAN IP,
    # e.g. "http://192.168.1.50:11434". Defaults to localhost for same-machine use.
    ollama_host: str = os.environ.get("SIM_OLLAMA_HOST", "http://localhost:11434")

    # Main "voice" model for characters. For the ollama backend this must exactly
    # match a model you've pulled on the host machine (see `ollama list`).
    # Default is Hermes 3 (8B, Llama-3.1 base) — see docs/model-choice.md
    # for the history of what was tried before this (Dolphin-Mistral, then
    # Dolphin3) and why: Dolphin's uncensoring fine-tunes were trading away
    # exactly the instruction/schema-following reliability this sim depends on
    # (JSON compliance, state grounding, harm-judgment consistency). Hermes 3 was
    # trained specifically for structured-output adherence.
    model: str = os.environ.get("SIM_MODEL", "hermes3")
    # Model used only to referee whether an action causes harm to another character.
    # Cheap/fast on the Anthropic backend; can just be the same model on Ollama.
    adjudicator_model: str = os.environ.get("SIM_ADJUDICATOR_MODEL", "hermes3")
    db_path: str = os.environ.get("SIM_DB", "simulation.db")
    # Seconds between automatic turns. Also controls your API cost — raise this
    # if you want a slower, cheaper simulation.
    tick_seconds: int = int(os.environ.get("SIM_TICK_SECONDS", "15"))
    # How many recent log lines each character sees per turn
    memory_window: int = int(os.environ.get("SIM_MEMORY_WINDOW", "20"))
    # Compress a character's memory into a running summary every N events
    summarize_every: int = 20
    # Sampling temperature (0-1 on Anthropic; roughly 0-2 on Ollama, though most
    # models are tuned around 0.7-1.0). Higher = less repetitive/more varied.
    # Lowered from 1.0: local 7-8B models are much less steerable than Claude at
    # high temperature — they drift off the prompt (objects/directive/scene get
    # ignored) instead of just sounding more "creative." See
    # docs/model-choice.md.
    temperature: float = float(os.environ.get("SIM_TEMPERATURE", "0.8"))
    # Ollama-only: penalizes tokens that already appeared recently, which is the
    # main lever against small local models looping the same line verbatim.
    # Lowered from 1.3 alongside temperature — too aggressive a repeat penalty
    # pushes a less-steerable model away from natural, on-topic word choices too.
    ollama_repeat_penalty: float = float(os.environ.get("SIM_OLLAMA_REPEAT_PENALTY", "1.15"))
    # Separate, much lower temperature just for the harm-adjudicator call
    # (simulation.py::_adjudicate_harm). That call is a consistent yes/no/how-much
    # judgment, not creative writing — at the main temperature, a local model
    # adjudicating its own peers' actions was hallucinating harm from harmless
    # dialogue, producing unprovoked "fights." Low temperature makes it stick
    # much closer to the ADJUDICATOR_SYSTEM instructions instead of improvising.
    adjudicator_temperature: float = float(os.environ.get("SIM_ADJUDICATOR_TEMPERATURE", "0.2"))

    # --- watchdog / circuit breaker (see docs/watchdog.md) ---
    # Consecutive turns a character can repeat near-identical dialogue/action
    # before the watchdog steps in and nudges them out of it.
    repetition_repeat_threshold: int = int(os.environ.get("SIM_REPETITION_THRESHOLD", "3"))
    # Word-overlap ratio (0-1) above which two turns count as "the same beat."
    repetition_similarity: float = float(os.environ.get("SIM_REPETITION_SIMILARITY", "0.72"))
    # Auto-pause the whole session after this many total ticks (0 = disabled).
    # A blunt safety net against a stuck/looping room — but the whole point of
    # this sim is running unattended for a long time, so off by default; the
    # stall/repetition watchdogs (docs/watchdog.md) still catch actual loops
    # without needing a hard tick ceiling. Set SIM_MAX_TICKS to a positive
    # number if you want the ceiling back.
    max_ticks_per_session: int = int(os.environ.get("SIM_MAX_TICKS", "0"))

    # How many recent shared world-knowledge facts (from characters investigating/
    # researching things) are folded into each character's prompt.
    world_facts_window: int = int(os.environ.get("SIM_WORLD_FACTS_WINDOW", "10"))

    # Cap on locations characters can discover themselves via "explore" (on top of
    # whatever you've added by hand), so the world can't sprawl indefinitely.
    max_discovered_locations: int = int(os.environ.get("SIM_MAX_DISCOVERED_LOCATIONS", "12"))

    # Same idea, for objects characters bring into a scene themselves (on top of
    # whatever you've added by hand).
    max_created_objects: int = int(os.environ.get("SIM_MAX_CREATED_OBJECTS", "20"))

    # In-world minutes that pass per tick (i.e. per round of turns), driving
    # the shared day/night clock every character's prompt sees.
    minutes_per_tick: int = int(os.environ.get("SIM_MINUTES_PER_TICK", "20"))

    # --- relationships ---
    # Max "significant events between us" kept per relationship pair (oldest
    # trimmed first) — keeps the prompt from growing unbounded per pair.
    relationship_event_cap: int = int(os.environ.get("SIM_RELATIONSHIP_EVENT_CAP", "8"))
    # How many relationship pairs (highest/lowest affinity) get surfaced in a
    # character's prompt, so it stays readable even with a big cast.
    relationship_prompt_count: int = int(os.environ.get("SIM_RELATIONSHIP_PROMPT_COUNT", "4"))

    # --- per-character knowledge/secrets ---
    # How many of a character's own private knowledge items show up in their prompt.
    knowledge_window: int = int(os.environ.get("SIM_KNOWLEDGE_WINDOW", "8"))

    # --- needs (soft pressure, no hard mechanical effect) ---
    needs_hunger_rate: int = int(os.environ.get("SIM_NEEDS_HUNGER_RATE", "2"))    # per own turn
    needs_boredom_rate: int = int(os.environ.get("SIM_NEEDS_BOREDOM_RATE", "2"))  # per own turn, idle
    needs_social_rate: int = int(os.environ.get("SIM_NEEDS_SOCIAL_RATE", "3"))    # per own turn, alone
    needs_pressure_threshold: int = int(os.environ.get("SIM_NEEDS_PRESSURE_THRESHOLD", "70"))

    # --- mood ---
    # In-world minutes after which an unreinforced mood fades back to neutral.
    mood_decay_minutes: int = int(os.environ.get("SIM_MOOD_DECAY_MINUTES", "180"))


CFG = Config()
