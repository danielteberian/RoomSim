import datetime
import json
import sqlite3
import time
from typing import List, Optional

from config import CFG
from models import Character, Event, SimObject


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
            created_at REAL
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
            consumed INTEGER DEFAULT 0
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
        """
    )
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
    )


def add_character(c: Character):
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO characters VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            c.id, c.name, c.persona, c.health, c.stability,
            json.dumps(c.status_effects), c.location, int(c.alive), int(c.replaced),
            c.memory_summary, c.last_summary_event_id, c.created_at,
        ),
    )
    conn.commit()
    conn.close()


def update_character(c: Character):
    add_character(c)  # INSERT OR REPLACE doubles as update


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

def add_object(o: SimObject):
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO objects VALUES (?,?,?,?)",
        (o.id, o.name, o.description, o.location),
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
    return [SimObject(id=r["id"], name=r["name"], description=r["description"], location=r["location"]) for r in rows]


# ---------------- events (the script log) ----------------

def _row_to_event(r) -> Event:
    return Event(
        id=r["id"], ts=r["ts"], kind=r["kind"], character_id=r["character_id"],
        character_name=r["character_name"], content=r["content"], target_id=r["target_id"],
    )


def add_event(kind: str, content: str, character_id=None, character_name=None, target_id=None) -> Event:
    conn = get_conn()
    ts = time.time()
    cur = conn.execute(
        "INSERT INTO events (ts, kind, character_id, character_name, content, target_id) VALUES (?,?,?,?,?,?)",
        (ts, kind, character_id, character_name, content, target_id),
    )
    conn.commit()
    eid = cur.lastrowid
    conn.close()
    return Event(id=eid, ts=ts, kind=kind, character_id=character_id,
                 character_name=character_name, content=content, target_id=target_id)


def get_recent_events(n: int = 50) -> List[Event]:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (n,)).fetchall()
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


# ---------------- interventions queue ----------------

def queue_intervention(char_id, text, health_delta=0, stability_delta=0, status_effect=None):
    conn = get_conn()
    conn.execute(
        "INSERT INTO interventions (character_id, text, health_delta, stability_delta, status_effect, consumed) "
        "VALUES (?,?,?,?,?,0)",
        (char_id, text, health_delta, stability_delta, status_effect),
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
