import time
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Character:
    id: str
    name: str
    persona: str                       # personality, backstory, goals, speech style
    health: int = 100
    stability: int = 100               # emotional/mental stability
    status_effects: List[str] = field(default_factory=list)
    location: str = "main_room"
    alive: bool = True
    replaced: bool = False             # true once a dead character has been replaced
    memory_summary: str = ""           # compressed long-term memory
    last_summary_event_id: int = 0     # bookkeeping for memory compression
    created_at: float = field(default_factory=time.time)


@dataclass
class SimObject:
    id: str
    name: str
    description: str
    location: str = "main_room"


@dataclass
class Event:
    id: Optional[int]
    ts: float
    kind: str                          # dialogue | action | thought | system | intervention | death
    character_id: Optional[str]
    character_name: Optional[str]
    content: str
    target_id: Optional[str] = None
