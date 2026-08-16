import os
from dataclasses import dataclass


@dataclass
class Config:
    # "anthropic" (cloud API) or "ollama" (a local/network model, e.g. your desktop PC)
    backend: str = os.environ.get("SIM_BACKEND", "anthropic")

    anthropic_api_key: str = os.environ.get("ANTHROPIC_API_KEY", "")

    # Base URL of the machine running Ollama. Point this at your desktop's LAN IP,
    # e.g. "http://192.168.1.50:11434". Defaults to localhost for same-machine use.
    ollama_host: str = os.environ.get("SIM_OLLAMA_HOST", "http://localhost:11434")

    # Main "voice" model for characters. For the ollama backend this must exactly
    # match a model you've pulled on the host machine (see `ollama list`).
    model: str = os.environ.get("SIM_MODEL", "claude-sonnet-5")
    # Model used only to referee whether an action causes harm to another character.
    # Cheap/fast on the Anthropic backend; can just be the same model on Ollama.
    adjudicator_model: str = os.environ.get("SIM_ADJUDICATOR_MODEL", "claude-haiku-4-5-20251001")
    db_path: str = os.environ.get("SIM_DB", "simulation.db")
    # Seconds between automatic turns. Also controls your API cost — raise this
    # if you want a slower, cheaper simulation.
    tick_seconds: int = int(os.environ.get("SIM_TICK_SECONDS", "15"))
    # How many recent log lines each character sees per turn
    memory_window: int = int(os.environ.get("SIM_MEMORY_WINDOW", "20"))
    # Compress a character's memory into a running summary every N events
    summarize_every: int = 20


CFG = Config()
