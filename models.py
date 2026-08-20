import time
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Character:
    id: str
    name: str
    persona: str                       # personality, backstory, goals, speech style
    dialect: str = ""                  # preset key into simulation.DIALECTS, or "" for none
    health: int = 100
    stability: int = 100               # emotional/mental stability
    status_effects: List[str] = field(default_factory=list)
    location: str = "main_room"
    alive: bool = True
    active: bool = True                # false = benched for the current chapter; skipped by tick(), invisible to others
    replaced: bool = False             # true once a dead character has been replaced
    memory_summary: str = ""           # compressed long-term memory
    last_summary_event_id: int = 0     # bookkeeping for memory compression
    directive: str = ""                # admin-given goal the character actively pursues until done
    interests: List[str] = field(default_factory=list)  # standing passions they self-drive scenes toward
    dislikes: List[str] = field(default_factory=list)  # things they avoid, refuse, or react badly to
    guidelines: List[str] = field(default_factory=list)  # hard behavioral rules ("never help X"), always enforced
    aggression: int = 30               # 0-100, current — drifts automatically from what happens to them
    aggression_baseline: int = 30      # 0-100, resting point their aggression drifts back toward when idle
    weirdness_chance: int = 0          # 0-100, % chance per turn they act strangely, unprompted
    self_goal: str = ""                # self-set ongoing goal, distinct from admin-given `directive`
    mood: str = ""                     # transient emotional spike, independent of aggression; fades over time
    mood_set_at: float = 0.0           # sim-clock minutes when `mood` was last set, for decay
    needs_hunger: int = 0              # 0-100, rises over time; soft pressure only, no hard mechanical effect
    needs_boredom: int = 0             # 0-100, rises when idle/repetitive, falls on novel actions
    needs_social: int = 0              # 0-100, rises when alone, falls around others
    needs_safety: int = 100            # 0-100 (100=safe), drops when harmed, slowly recovers
    created_at: float = field(default_factory=time.time)


@dataclass
class SimObject:
    id: str
    name: str
    description: str
    location: str = "main_room"
    created_by: Optional[str] = None   # None = admin-added; else the character who brought it into the scene


@dataclass
class Location:
    name: str                          # also the key used by Character/SimObject.location
    description: str = ""
    discovered_by: Optional[str] = None  # None = admin-created; else the character who found it while exploring
    created_at: float = field(default_factory=time.time)


@dataclass
class WorldFact:
    id: Optional[int]
    topic: str
    content: str
    discovered_by: Optional[str]       # character name, for flavor/attribution
    ts: float = field(default_factory=time.time)


@dataclass
class Relationship:
    """Directional: how char_a feels about char_b. The reverse pair (b about a)
    is a separate row, so feelings need not be symmetric."""
    char_a_id: str
    char_b_id: str
    affinity: int = 0                  # -100..100, how much a likes/trusts b overall
    trust: int = 0                     # -100..100, separate from liking — can trust a rival, distrust a friend
    updated_at: float = field(default_factory=time.time)


@dataclass
class RelationshipEvent:
    id: Optional[int]
    char_a_id: str
    char_b_id: str
    description: str                   # short note, e.g. "insulted me in front of everyone"
    ts: float = field(default_factory=time.time)


@dataclass
class CharacterKnowledge:
    """A fact a specific character knows — not automatically visible to anyone
    else. Spreads only when explicitly shared (via messaging) or witnessed."""
    id: Optional[int]
    character_id: str
    content: str
    is_true: bool = True               # False = this character was lied to and doesn't know it
    source: Optional[str] = None       # e.g. "investigated", "told by X", "witnessed"
    ts: float = field(default_factory=time.time)


@dataclass
class Event:
    id: Optional[int]
    ts: float
    kind: str                          # dialogue | action | thought | system | intervention | death | message
    character_id: Optional[str]
    character_name: Optional[str]
    content: str
    target_id: Optional[str] = None
    location: Optional[str] = None     # where it happened; None = global/visible everywhere
    channel: Optional[str] = None      # for kind="message": "text" | "email" | "call"
