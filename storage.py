import datetime
import json
import sqlite3
import time
from typing import List, Optional

from config import CFG
from models import Character, CharacterKnowledge, Event, Location, Relationship, RelationshipEvent, SimObject, WorldFact


def get_conn():
    conn = sqlite3.connect(CFG.db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS characters (
            id TEXT PRIMARY KEY,
            name TEXT,
            persona TEXT,
            health INTEGER,
            stability INTEGER,
            status_effects TEXT,
            location TEXT,
            alive INTEGER,
            replaced INTEGER,
            memory_summary TEXT,
            last_summary_event_id INTEGER,
            created_at REAL,
            directive TEXT
        );
        CREATE TABLE IF NOT EXISTS objects (
            id TEXT PRIMARY KEY,
            name TEXT,
            description TEXT,
            location TEXT
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL,
            kind TEXT,
            character_id TEXT,
            character_name TEXT,
            content TEXT,
            target_id TEXT
        );
        CREATE TABLE IF NOT EXISTS interventions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            character_id TEXT,
            text TEXT,
            health_delta INTEGER DEFAULT 0,
            stability_delta INTEGER DEFAULT 0,
            status_effect TEXT,
            consumed INTEGER DEFAULT 0,
            label TEXT
        );
        CREATE TABLE IF NOT EXISTS chapters (
            date TEXT PRIMARY KEY,
            title TEXT,
            content TEXT,
            created_at REAL
        );
        CREATE TABLE IF NOT EXISTS room_focus (
            location TEXT PRIMARY KEY,
            focus TEXT,
            updated_at REAL
        );
        CREATE TABLE IF NOT EXISTS room_setting (
            location TEXT PRIMARY KEY,
            setting TEXT,
            updated_at REAL
        );
        CREATE TABLE IF NOT EXISTS locations (
            name TEXT PRIMARY KEY,
            description TEXT,
            created_at REAL
        );
        CREATE TABLE IF NOT EXISTS world_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic TEXT,
            content TEXT,
            discovered_by TEXT,
            ts REAL
        );
        CREATE TABLE IF NOT EXISTS sim_clock (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            minutes REAL
        );
        CREATE TABLE IF NOT EXISTS relationships (
            char_a_id TEXT,
            char_b_id TEXT,
            affinity INTEGER DEFAULT 0,
            trust INTEGER DEFAULT 0,
            updated_at REAL,
            PRIMARY KEY (char_a_id, char_b_id)
        );
        CREATE TABLE IF NOT EXISTS relationship_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            char_a_id TEXT,
            char_b_id TEXT,
            description TEXT,
            ts REAL
        );
        CREATE TABLE IF NOT EXISTS character_knowledge (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            character_id TEXT,
            content TEXT,
            is_true INTEGER DEFAULT 1,
            source TEXT,
            ts REAL
        );
        """
    )
    # Migration: `directive` was added to the characters table after initial release —
    # existing databases won't have it yet, so add it if missing.
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(characters)").fetchall()]
    if "directive" not in cols:
        conn.execute("ALTER TABLE characters ADD COLUMN directive TEXT DEFAULT ''")
    if "interests" not in cols:
        conn.execute("ALTER TABLE characters ADD COLUMN interests TEXT DEFAULT '[]'")
    if "dislikes" not in cols:
        conn.execute("ALTER TABLE characters ADD COLUMN dislikes TEXT DEFAULT '[]'")
    if "aggression" not in cols:
        conn.execute("ALTER TABLE characters ADD COLUMN aggression INTEGER DEFAULT 30")
    if "aggression_baseline" not in cols:
        conn.execute("ALTER TABLE characters ADD COLUMN aggression_baseline INTEGER DEFAULT 30")
    if "weirdness_chance" not in cols:
        conn.execute("ALTER TABLE characters ADD COLUMN weirdness_chance INTEGER DEFAULT 0")
    if "guidelines" not in cols:
        conn.execute("ALTER TABLE characters ADD COLUMN guidelines TEXT DEFAULT '[]'")
    if "self_goal" not in cols:
        conn.execute("ALTER TABLE characters ADD COLUMN self_goal TEXT DEFAULT ''")
    if "mood" not in cols:
        conn.execute("ALTER TABLE characters ADD COLUMN mood TEXT DEFAULT ''")
    if "mood_set_at" not in cols:
        conn.execute("ALTER TABLE characters ADD COLUMN mood_set_at REAL DEFAULT 0")
    if "needs_hunger" not in cols:
        conn.execute("ALTER TABLE characters ADD COLUMN needs_hunger INTEGER DEFAULT 0")
    if "needs_boredom" not in cols:
        conn.execute("ALTER TABLE characters ADD COLUMN needs_boredom INTEGER DEFAULT 0")
    if "needs_social" not in cols:
        conn.execute("ALTER TABLE characters ADD COLUMN needs_social INTEGER DEFAULT 0")
    if "needs_safety" not in cols:
        conn.execute("ALTER TABLE characters ADD COLUMN needs_safety INTEGER DEFAULT 100")
    if "dialect" not in cols:
        conn.execute("ALTER TABLE characters ADD COLUMN dialect TEXT DEFAULT ''")

    # Migration: `location`/`channel` were added to events for multi-location
    # living + email/text/call messages between characters who aren't in the
    # same place.
    event_cols = [r["name"] for r in conn.execute("PRAGMA table_info(events)").fetchall()]
    if "location" not in event_cols:
        conn.execute("ALTER TABLE events ADD COLUMN location TEXT")
    if "channel" not in event_cols:
        conn.execute("ALTER TABLE events ADD COLUMN channel TEXT")

    loc_cols = [r["name"] for r in conn.execute("PRAGMA table_info(locations)").fetchall()]
    if "discovered_by" not in loc_cols:
        conn.execute("ALTER TABLE locations ADD COLUMN discovered_by TEXT")

    obj_cols = [r["name"] for r in conn.execute("PRAGMA table_info(objects)").fetchall()]
    if "created_by" not in obj_cols:
        conn.execute("ALTER TABLE objects ADD COLUMN created_by TEXT")

    iv_cols = [r["name"] for r in conn.execute("PRAGMA table_info(interventions)").fetchall()]
    if "label" not in iv_cols:
        conn.execute("ALTER TABLE interventions ADD COLUMN label TEXT")

    # Make sure the default location always exists so pre-existing rooms/characters
    # (which all default to location="main_room") still resolve to a real location.
    conn.execute(
        "INSERT OR IGNORE INTO locations (name, description, discovered_by, created_at) VALUES (?,?,?,?)",
        ("main_room", "", None, time.time()),
    )
    conn.execute("INSERT OR IGNORE INTO sim_clock (id, minutes) VALUES (1, 480)")  # start at 8:00 AM, day 1
    conn.commit()
    conn.close()


# ---------------- characters ----------------

def _row_to_char(row) -> Character:
    return Character(
        id=row["id"], name=row["name"], persona=row["persona"],
        health=row["health"], stability=row["stability"],
        status_effects=json.loads(row["status_effects"] or "[]"),
        location=row["location"], alive=bool(row["alive"]), replaced=bool(row["replaced"]),
        memory_summary=row["memory_summary"] or "",
        last_summary_event_id=row["last_summary_event_id"] or 0,
        created_at=row["created_at"],
        directive=row["directive"] or "",
        interests=json.loads(row["interests"] or "[]"),
        dislikes=json.loads(row["dislikes"] or "[]"),
        guidelines=json.loads(row["guidelines"] or "[]"),
        aggression=row["aggression"] if row["aggression"] is not None else 30,
        aggression_baseline=row["aggression_baseline"] if row["aggression_baseline"] is not None else 30,
        weirdness_chance=row["weirdness_chance"] if row["weirdness_chance"] is not None else 0,
        self_goal=row["self_goal"] or "",
        mood=row["mood"] or "",
        mood_set_at=row["mood_set_at"] if row["mood_set_at"] is not None else 0.0,
        needs_hunger=row["needs_hunger"] if row["needs_hunger"] is not None else 0,
        needs_boredom=row["needs_boredom"] if row["needs_boredom"] is not None else 0,
        needs_social=row["needs_social"] if row["needs_social"] is not None else 0,
        needs_safety=row["needs_safety"] if row["needs_safety"] is not None else 100,
        dialect=row["dialect"] or "",
    )


def add_character(c: Character):
    conn = get_conn()
    conn.execute(
        # Explicit column list (not positional VALUES) is deliberate: columns added
        # via ALTER TABLE (everything past `directive`) land at whatever physical
        # position they happened to be appended at, which depends on migration
        # history and can differ between a fresh DB and an already-migrated one.
        # Positional VALUES silently writes into the wrong columns if that order
        # doesn't match this tuple's order — naming columns makes it order-proof.
        """
        INSERT OR REPLACE INTO characters
            (id, name, persona, health, stability, status_effects, location, alive, replaced,
             memory_summary, last_summary_event_id, created_at, directive, interests, dislikes,
             guidelines, aggression, aggression_baseline, weirdness_chance, self_goal, mood,
             mood_set_at, needs_hunger, needs_boredom, needs_social, needs_safety, dialect)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            c.id, c.name, c.persona, c.health, c.stability,
            json.dumps(c.status_effects), c.location, int(c.alive), int(c.replaced),
            c.memory_summary, c.last_summary_event_id, c.created_at, c.directive,
            json.dumps(c.interests), json.dumps(c.dislikes), json.dumps(c.guidelines),
            c.aggression, c.aggression_baseline, c.weirdness_chance, c.self_goal, c.mood,
            c.mood_set_at, c.needs_hunger, c.needs_boredom, c.needs_social, c.needs_safety,
            c.dialect,
        ),
    )
    conn.commit()
    conn.close()


def update_character(c: Character):
    add_character(c)  # INSERT OR REPLACE doubles as update


def remove_status_effect(char_id: str, effect: str) -> bool:
    c = get_character(char_id)
    if not c or effect not in c.status_effects:
        return False
    c.status_effects.remove(effect)
    update_character(c)
    return True


def get_character(char_id: str) -> Optional[Character]:
    conn = get_conn()
    row = conn.execute("SELECT * FROM characters WHERE id=?", (char_id,)).fetchone()
    conn.close()
    return _row_to_char(row) if row else None


def list_characters(alive_only: bool = False) -> List[Character]:
    conn = get_conn()
    q = "SELECT * FROM characters" + (" WHERE alive=1" if alive_only else "")
    rows = conn.execute(q).fetchall()
    conn.close()
    return [_row_to_char(r) for r in rows]


def kill_character(char_id: str):
    c = get_character(char_id)
    if c:
        c.alive = False
        c.health = 0
        update_character(c)


def delete_character_hard(char_id: str):
    """Permanently erases a character AND scrubs their lines from the shared
    event log (both as speaker and as target). Use this to correct a mistake
    (e.g. an accidental duplicate) — unlike kill_character, this leaves no
    in-story trace, so other characters won't 'remember' them at all going
    forward. Note: it does NOT retroactively fix anything already baked into
    another character's memory_summary — use the memory-edit endpoint for that."""
    conn = get_conn()
    conn.execute("DELETE FROM characters WHERE id=?", (char_id,))
    conn.execute("DELETE FROM events WHERE character_id=? OR target_id=?", (char_id, char_id))
    conn.execute("DELETE FROM interventions WHERE character_id=?", (char_id,))
    conn.execute("DELETE FROM relationships WHERE char_a_id=? OR char_b_id=?", (char_id, char_id))
    conn.execute("DELETE FROM relationship_events WHERE char_a_id=? OR char_b_id=?", (char_id, char_id))
    conn.execute("DELETE FROM character_knowledge WHERE character_id=?", (char_id,))
    conn.commit()
    conn.close()


def find_alive_by_name(name: str) -> Optional[Character]:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM characters WHERE alive=1 AND lower(name)=lower(?) LIMIT 1", (name,)
    ).fetchone()
    conn.close()
    return _row_to_char(row) if row else None


# ---------------- objects ----------------

def _row_to_object(r) -> SimObject:
    return SimObject(id=r["id"], name=r["name"], description=r["description"], location=r["location"],
                      created_by=r["created_by"])


def add_object(o: SimObject):
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO objects (id, name, description, location, created_by) VALUES (?,?,?,?,?)",
        (o.id, o.name, o.description, o.location, o.created_by),
    )
    conn.commit()
    conn.close()


def list_objects(location: Optional[str] = None) -> List[SimObject]:
    conn = get_conn()
    if location:
        rows = conn.execute("SELECT * FROM objects WHERE location=?", (location,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM objects").fetchall()
    conn.close()
    return [_row_to_object(r) for r in rows]


def get_object(obj_id: str) -> Optional[SimObject]:
    conn = get_conn()
    row = conn.execute("SELECT * FROM objects WHERE id=?", (obj_id,)).fetchone()
    conn.close()
    return _row_to_object(row) if row else None


def update_object(o: SimObject):
    add_object(o)  # INSERT OR REPLACE doubles as update


def delete_object(obj_id: str):
    conn = get_conn()
    conn.execute("DELETE FROM objects WHERE id=?", (obj_id,))
    conn.commit()
    conn.close()


# ---------------- events (the script log) ----------------

def _row_to_event(r) -> Event:
    return Event(
        id=r["id"], ts=r["ts"], kind=r["kind"], character_id=r["character_id"],
        character_name=r["character_name"], content=r["content"], target_id=r["target_id"],
        location=r["location"], channel=r["channel"],
    )


def add_event(kind: str, content: str, character_id=None, character_name=None, target_id=None,
              location=None, channel=None) -> Event:
    conn = get_conn()
    ts = time.time()
    cur = conn.execute(
        "INSERT INTO events (ts, kind, character_id, character_name, content, target_id, location, channel) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (ts, kind, character_id, character_name, content, target_id, location, channel),
    )
    conn.commit()
    eid = cur.lastrowid
    conn.close()
    return Event(id=eid, ts=ts, kind=kind, character_id=character_id, character_name=character_name,
                 content=content, target_id=target_id, location=location, channel=channel)


def get_recent_events(n: int = 50) -> List[Event]:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (n,)).fetchall()
    conn.close()
    return list(reversed([_row_to_event(r) for r in rows]))


def get_recent_events_for_character(char, n: int = 50) -> List[Event]:
    """What a character can plausibly perceive right now: in-person events that
    happened in their own location, messages sent to/from them (regardless of
    location — that's the whole point of a phone/email), and global system/
    death/intervention events. Keeps someone in a different location from
    reading dialogue they weren't there for, while still letting a text or
    email reach them across the world."""
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT * FROM events
        WHERE (location = ? OR location IS NULL)
           OR (kind = 'message' AND (character_id = ? OR target_id = ?))
        ORDER BY id DESC LIMIT ?
        """,
        (char.location, char.id, char.id, n),
    ).fetchall()
    conn.close()
    return list(reversed([_row_to_event(r) for r in rows]))


def get_character_events_since(char_id: str, since_id: int) -> List[Event]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM events WHERE (character_id=? OR target_id=?) AND id>? ORDER BY id ASC",
        (char_id, char_id, since_id),
    ).fetchall()
    conn.close()
    return [_row_to_event(r) for r in rows]


def _date_bounds(date_str: str):
    """Local-time midnight-to-midnight bounds for a 'YYYY-MM-DD' string.
    Uses the Pi's system timezone — set it correctly (`sudo raspi-config` or
    `timedatectl set-timezone`) so days line up with your actual day."""
    start = datetime.datetime.strptime(date_str, "%Y-%m-%d")
    end = start + datetime.timedelta(days=1)
    return start.timestamp(), end.timestamp()


def get_events_for_date(date_str: str) -> List[Event]:
    start_ts, end_ts = _date_bounds(date_str)
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM events WHERE ts>=? AND ts<? ORDER BY id ASC", (start_ts, end_ts)
    ).fetchall()
    conn.close()
    return [_row_to_event(r) for r in rows]


def has_events_for_date(date_str: str) -> bool:
    start_ts, end_ts = _date_bounds(date_str)
    conn = get_conn()
    row = conn.execute(
        "SELECT 1 FROM events WHERE ts>=? AND ts<? LIMIT 1", (start_ts, end_ts)
    ).fetchone()
    conn.close()
    return row is not None


# ---------------- chapters (daily narrative recaps) ----------------

def add_chapter(date_str: str, title: str, content: str):
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO chapters VALUES (?,?,?,?)",
        (date_str, title, content, time.time()),
    )
    conn.commit()
    conn.close()


def has_chapter_for_date(date_str: str) -> bool:
    conn = get_conn()
    row = conn.execute("SELECT 1 FROM chapters WHERE date=?", (date_str,)).fetchone()
    conn.close()
    return row is not None


def get_chapter(date_str: str) -> Optional[dict]:
    conn = get_conn()
    row = conn.execute("SELECT * FROM chapters WHERE date=?", (date_str,)).fetchone()
    conn.close()
    if not row:
        return None
    return {"date": row["date"], "title": row["title"], "content": row["content"], "created_at": row["created_at"]}


def list_chapters() -> List[dict]:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM chapters ORDER BY date DESC").fetchall()
    conn.close()
    return [
        {"date": r["date"], "title": r["title"], "content": r["content"], "created_at": r["created_at"]}
        for r in rows
    ]


# ---------------- room focus (a subject/topic to nudge the scene toward) ----------------

def set_room_focus(location: str, focus: str):
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO room_focus VALUES (?,?,?)",
        (location, focus, time.time()),
    )
    conn.commit()
    conn.close()


def get_room_focus(location: str) -> str:
    conn = get_conn()
    row = conn.execute("SELECT focus FROM room_focus WHERE location=?", (location,)).fetchone()
    conn.close()
    return row["focus"] if row and row["focus"] else ""


# ---------------- room setting (a standing description of the physical space) ----------------

def set_room_setting(location: str, setting: str):
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO room_setting VALUES (?,?,?)",
        (location, setting, time.time()),
    )
    conn.commit()
    conn.close()


def get_room_setting(location: str) -> str:
    conn = get_conn()
    row = conn.execute("SELECT setting FROM room_setting WHERE location=?", (location,)).fetchone()
    conn.close()
    return row["setting"] if row and row["setting"] else ""


# ---------------- locations (the wider world) ----------------

def add_location(name: str, description: str = "", discovered_by: Optional[str] = None):
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO locations (name, description, discovered_by, created_at) VALUES (?,?,?,?)",
        (name, description, discovered_by, time.time()),
    )
    conn.commit()
    conn.close()


def _row_to_location(r) -> Location:
    return Location(name=r["name"], description=r["description"] or "", discovered_by=r["discovered_by"],
                     created_at=r["created_at"])


def list_locations() -> List[Location]:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM locations ORDER BY name").fetchall()
    conn.close()
    return [_row_to_location(r) for r in rows]


def get_location(name: str) -> Optional[Location]:
    conn = get_conn()
    row = conn.execute("SELECT * FROM locations WHERE name=?", (name,)).fetchone()
    conn.close()
    return _row_to_location(row) if row else None


def delete_location(name: str):
    conn = get_conn()
    conn.execute("DELETE FROM locations WHERE name=?", (name,))
    conn.commit()
    conn.close()


# ---------------- world facts (what characters have learned about their world) ----------------

def add_world_fact(topic: str, content: str, discovered_by: Optional[str] = None) -> WorldFact:
    conn = get_conn()
    ts = time.time()
    cur = conn.execute(
        "INSERT INTO world_facts (topic, content, discovered_by, ts) VALUES (?,?,?,?)",
        (topic, content, discovered_by, ts),
    )
    conn.commit()
    fid = cur.lastrowid
    conn.close()
    return WorldFact(id=fid, topic=topic, content=content, discovered_by=discovered_by, ts=ts)


def list_world_facts(limit: int = 30) -> List[WorldFact]:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM world_facts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [
        WorldFact(id=r["id"], topic=r["topic"], content=r["content"], discovered_by=r["discovered_by"], ts=r["ts"])
        for r in reversed(rows)
    ]


def delete_world_fact(fact_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM world_facts WHERE id=?", (fact_id,))
    conn.commit()
    conn.close()


# ---------------- relationships (dyadic — how a feels about b) ----------------

def get_relationship(a_id: str, b_id: str) -> Relationship:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM relationships WHERE char_a_id=? AND char_b_id=?", (a_id, b_id)
    ).fetchone()
    conn.close()
    if not row:
        return Relationship(char_a_id=a_id, char_b_id=b_id, affinity=0, trust=0, updated_at=0)
    return Relationship(char_a_id=a_id, char_b_id=b_id, affinity=row["affinity"], trust=row["trust"],
                         updated_at=row["updated_at"])


def adjust_relationship(a_id: str, b_id: str, affinity_delta: int = 0, trust_delta: int = 0) -> Relationship:
    """How a_id's feelings about b_id change — directional, not symmetric.
    Clamped to -100..100 and upserted (starts at 0/0 if this pair has no row yet)."""
    current = get_relationship(a_id, b_id)
    affinity = max(-100, min(100, current.affinity + affinity_delta))
    trust = max(-100, min(100, current.trust + trust_delta))
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO relationships (char_a_id, char_b_id, affinity, trust, updated_at) "
        "VALUES (?,?,?,?,?)",
        (a_id, b_id, affinity, trust, time.time()),
    )
    conn.commit()
    conn.close()
    return Relationship(char_a_id=a_id, char_b_id=b_id, affinity=affinity, trust=trust, updated_at=time.time())


def set_relationship(a_id: str, b_id: str, affinity: int, trust: int):
    """Admin override — sets absolute values rather than adjusting by a delta."""
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO relationships (char_a_id, char_b_id, affinity, trust, updated_at) "
        "VALUES (?,?,?,?,?)",
        (a_id, b_id, max(-100, min(100, affinity)), max(-100, min(100, trust)), time.time()),
    )
    conn.commit()
    conn.close()


def list_relationships_from(char_id: str) -> List[Relationship]:
    """Everyone char_id has a nonzero-or-recorded opinion of."""
    conn = get_conn()
    rows = conn.execute("SELECT * FROM relationships WHERE char_a_id=?", (char_id,)).fetchall()
    conn.close()
    return [Relationship(char_a_id=r["char_a_id"], char_b_id=r["char_b_id"], affinity=r["affinity"],
                          trust=r["trust"], updated_at=r["updated_at"]) for r in rows]


def list_all_relationships() -> List[Relationship]:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM relationships").fetchall()
    conn.close()
    return [Relationship(char_a_id=r["char_a_id"], char_b_id=r["char_b_id"], affinity=r["affinity"],
                          trust=r["trust"], updated_at=r["updated_at"]) for r in rows]


def add_relationship_event(a_id: str, b_id: str, description: str):
    """Appends a short 'significant thing that happened between us' note, then
    trims to CFG.relationship_event_cap (oldest first) so a long-running pair
    doesn't accumulate an unbounded history in the prompt."""
    conn = get_conn()
    conn.execute(
        "INSERT INTO relationship_events (char_a_id, char_b_id, description, ts) VALUES (?,?,?,?)",
        (a_id, b_id, description, time.time()),
    )
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM relationship_events WHERE char_a_id=? AND char_b_id=? ORDER BY id DESC",
        (a_id, b_id),
    ).fetchall()]
    stale = ids[CFG.relationship_event_cap:]
    if stale:
        conn.executemany("DELETE FROM relationship_events WHERE id=?", [(i,) for i in stale])
    conn.commit()
    conn.close()


def list_relationship_events(a_id: str, b_id: str, limit: int = 8) -> List[RelationshipEvent]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM relationship_events WHERE char_a_id=? AND char_b_id=? ORDER BY id DESC LIMIT ?",
        (a_id, b_id, limit),
    ).fetchall()
    conn.close()
    return list(reversed([
        RelationshipEvent(id=r["id"], char_a_id=r["char_a_id"], char_b_id=r["char_b_id"],
                           description=r["description"], ts=r["ts"])
        for r in rows
    ]))


# ---------------- character knowledge (per-character facts/secrets) ----------------

def add_character_knowledge(character_id: str, content: str, is_true: bool = True,
                             source: Optional[str] = None) -> CharacterKnowledge:
    conn = get_conn()
    ts = time.time()
    cur = conn.execute(
        "INSERT INTO character_knowledge (character_id, content, is_true, source, ts) VALUES (?,?,?,?,?)",
        (character_id, content, int(is_true), source, ts),
    )
    conn.commit()
    kid = cur.lastrowid
    conn.close()
    return CharacterKnowledge(id=kid, character_id=character_id, content=content, is_true=is_true,
                               source=source, ts=ts)


def list_character_knowledge(character_id: str, limit: int = 8) -> List[CharacterKnowledge]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM character_knowledge WHERE character_id=? ORDER BY id DESC LIMIT ?",
        (character_id, limit),
    ).fetchall()
    conn.close()
    return list(reversed([
        CharacterKnowledge(id=r["id"], character_id=r["character_id"], content=r["content"],
                            is_true=bool(r["is_true"]), source=r["source"], ts=r["ts"])
        for r in rows
    ]))


# ---------------- sim clock (shared in-world day/night cycle) ----------------

def get_sim_minutes() -> float:
    conn = get_conn()
    row = conn.execute("SELECT minutes FROM sim_clock WHERE id=1").fetchone()
    conn.close()
    return row["minutes"] if row else 480.0


def advance_sim_minutes(delta: float) -> float:
    conn = get_conn()
    conn.execute("UPDATE sim_clock SET minutes = minutes + ? WHERE id=1", (delta,))
    row = conn.execute("SELECT minutes FROM sim_clock WHERE id=1").fetchone()
    conn.commit()
    conn.close()
    return row["minutes"]


# ---------------- new chapter (archive the log and start fresh) ----------------

def clear_events():
    """Wipes the raw script log (not chapters, characters, objects, locations,
    or world facts) so a freshly-started chapter's log begins clean. Pair with
    generating a chapter from the old events first if you don't want them lost."""
    conn = get_conn()
    conn.execute("DELETE FROM events")
    conn.commit()
    conn.close()


# ---------------- interventions queue ----------------

def queue_intervention(char_id, text, health_delta=0, stability_delta=0, status_effect=None, label=None):
    conn = get_conn()
    conn.execute(
        "INSERT INTO interventions (character_id, text, health_delta, stability_delta, status_effect, consumed, label) "
        "VALUES (?,?,?,?,?,0,?)",
        (char_id, text, health_delta, stability_delta, status_effect, label),
    )
    conn.commit()
    conn.close()


def pop_pending_interventions(char_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM interventions WHERE character_id=? AND consumed=0", (char_id,)
    ).fetchall()
    conn.execute("UPDATE interventions SET consumed=1 WHERE character_id=? AND consumed=0", (char_id,))
    conn.commit()
    conn.close()
    return rows
