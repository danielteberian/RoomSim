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
    # Default is Dolphin-Mistral (7B) — see docs/model-dolphin-mistral.md for why
    # this size was picked (8GB-VRAM AMD RX 590) and GPU-acceleration caveats.
    model: str = os.environ.get("SIM_MODEL", "dolphin-mistral")
    # Model used only to referee whether an action causes harm to another character.
    # Cheap/fast on the Anthropic backend; can just be the same model on Ollama.
    adjudicator_model: str = os.environ.get("SIM_ADJUDICATOR_MODEL", "dolphin-mistral")
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
    temperature: float = float(os.environ.get("SIM_TEMPERATURE", "1.0"))
    # Ollama-only: penalizes tokens that already appeared recently, which is the
    # main lever against small local models looping the same line verbatim.
    ollama_repeat_penalty: float = float(os.environ.get("SIM_OLLAMA_REPEAT_PENALTY", "1.3"))

    # --- watchdog / circuit breaker (see docs/watchdog.md) ---
    # Consecutive turns a character can repeat near-identical dialogue/action
    # before the watchdog steps in and nudges them out of it.
    repetition_repeat_threshold: int = int(os.environ.get("SIM_REPETITION_THRESHOLD", "3"))
    # Word-overlap ratio (0-1) above which two turns count as "the same beat."
    repetition_similarity: float = float(os.environ.get("SIM_REPETITION_SIMILARITY", "0.72"))
    # Auto-pause the whole session after this many total ticks (0 = disabled).
    # A blunt safety net so a stuck/looping room can't run unattended forever.
    max_ticks_per_session: int = int(os.environ.get("SIM_MAX_TICKS", "500"))


CFG = Config()
