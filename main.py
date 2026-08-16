import asyncio
import datetime
import os
import uuid
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

import chapters
import interventions
import storage
import simulation
from config import CFG
from models import Character, SimObject

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = FastAPI()
RUNNING = {"on": True}


@app.on_event("startup")
async def startup():
    storage.init_db()
    asyncio.create_task(_loop())
    asyncio.create_task(_daily_chapter_loop())


async def _loop():
    while True:
        if RUNNING["on"]:
            try:
                simulation.tick()
            except Exception as e:  # keep the loop alive across API hiccups etc.
                storage.add_event("system", f"[error during tick: {e}]")
        await asyncio.sleep(CFG.tick_seconds)


async def _daily_chapter_loop():
    """Checks hourly for a day that's ended but has no chapter yet, and writes one."""
    while True:
        try:
            yesterday = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
            if not storage.has_chapter_for_date(yesterday) and storage.has_events_for_date(yesterday):
                chapters.generate_and_store_chapter(yesterday)
        except Exception as e:
            storage.add_event("system", f"[daily chapter generation failed: {e}]")
        await asyncio.sleep(3600)


# ---------------- read state ----------------

@app.get("/api/state")
def api_state():
    chars = storage.list_characters()
    objs = storage.list_objects()
    events = storage.get_recent_events(200)
    return {
        "running": RUNNING["on"],
        "characters": [c.__dict__ for c in chars],
        "objects": [o.__dict__ for o in objs],
        "events": [e.__dict__ for e in events],
        "room_focus": storage.get_room_focus("main_room"),
    }


# ---------------- pacing controls ----------------

@app.post("/api/pause")
def api_pause():
    RUNNING["on"] = False
    return {"running": False}


@app.post("/api/resume")
def api_resume():
    RUNNING["on"] = True
    return {"running": True}


@app.post("/api/tick")
def api_tick():
    simulation.tick()
    return {"ok": True}


# ---------------- characters ----------------

class NewCharacter(BaseModel):
    name: str
    persona: str
    confirm: bool = False  # set true to add anyway despite an alive name collision


@app.post("/api/characters")
def api_add_character(body: NewCharacter):
    existing = storage.find_alive_by_name(body.name)
    if existing and not body.confirm:
        return {
            "ok": False, "duplicate": True,
            "reason": f'"{body.name}" is already alive in the room. Add another one with the same name anyway?',
        }
    c = Character(id=str(uuid.uuid4())[:8], name=body.name, persona=body.persona)
    storage.add_character(c)
    storage.add_event("system", f"{c.name} enters the room.")
    return {"ok": True, "character": c.__dict__}


@app.post("/api/characters/{char_id}/kill")
def api_kill(char_id: str):
    c = storage.get_character(char_id)
    if c and c.alive:
        storage.kill_character(char_id)
        storage.add_event("death", f"{c.name} has died.", character_id=char_id, character_name=c.name)
    return {"ok": True}


@app.post("/api/characters/{char_id}/delete")
def api_delete_character(char_id: str):
    """Permanently removes a character and scrubs them from the shared event
    log — for fixing mistakes (accidental duplicates etc.), not an in-story
    death. Doesn't retroactively fix other characters' memory_summary — pair
    with the memory-edit endpoint below if the mistake already got baked in."""
    c = storage.get_character(char_id)
    storage.delete_character_hard(char_id)
    return {"ok": True, "removed": c.name if c else char_id}


class MemoryEdit(BaseModel):
    summary: str


@app.post("/api/characters/{char_id}/memory")
def api_edit_memory(char_id: str, body: MemoryEdit):
    c = storage.get_character(char_id)
    if not c:
        return {"ok": False, "reason": "not found"}
    c.memory_summary = body.summary.strip()
    storage.update_character(c)
    return {"ok": True}


class ReplaceCharacter(BaseModel):
    name: str
    persona: str


@app.post("/api/characters/{char_id}/replace")
def api_replace(char_id: str, body: ReplaceCharacter):
    old = storage.get_character(char_id)
    who = "the empty spot"
    location = "main_room"
    if old:
        old.alive = False
        old.replaced = True
        old.health = 0
        storage.update_character(old)
        who = old.name
        location = old.location

    new = Character(id=str(uuid.uuid4())[:8], name=body.name, persona=body.persona, location=location)
    storage.add_character(new)
    storage.add_event("system", f"{new.name} arrives, taking {who}'s place.")
    return new.__dict__


# ---------------- objects ----------------

class NewObject(BaseModel):
    name: str
    description: str
    location: str = "main_room"


@app.post("/api/objects")
def api_add_object(body: NewObject):
    o = SimObject(id=str(uuid.uuid4())[:8], name=body.name, description=body.description, location=body.location)
    storage.add_object(o)
    storage.add_event("system", f"A {body.name} appears in the room.")
    return o.__dict__


# ---------------- interventions ----------------

class Intervene(BaseModel):
    type: str  # zap | insert_thought | disturb | push | custom
    text: Optional[str] = None
    intensity: Optional[int] = None


@app.post("/api/characters/{char_id}/intervene")
def api_intervene(char_id: str, body: Intervene):
    if body.type == "zap":
        interventions.zap(char_id, body.intensity or 15)
    elif body.type == "insert_thought":
        interventions.insert_thought(char_id, body.text or "...")
    elif body.type == "disturb":
        interventions.disturb(char_id, body.intensity or 10)
    elif body.type == "push":
        interventions.push(char_id, body.intensity or 5)
    elif body.type == "custom":
        interventions.custom(char_id, body.text or "Something happens.")
    return {"ok": True}


# ---------------- chapters ----------------

class GenerateChapter(BaseModel):
    date: Optional[str] = None  # "YYYY-MM-DD"; defaults to today (so-far)


@app.post("/api/chapters/generate")
def api_generate_chapter(body: GenerateChapter):
    date_str = body.date or datetime.datetime.now().strftime("%Y-%m-%d")
    result = chapters.generate_and_store_chapter(date_str)
    if result is None:
        return {"ok": False, "reason": f"No events recorded for {date_str} yet."}
    return {"ok": True, "chapter": result}


@app.get("/api/chapters")
def api_list_chapters():
    return storage.list_chapters()


@app.get("/api/chapters/{date_str}")
def api_get_chapter(date_str: str):
    c = storage.get_chapter(date_str)
    return c or {"error": "not found"}


# ---------------- room focus ----------------

class RoomFocus(BaseModel):
    focus: str
    location: str = "main_room"


@app.post("/api/room/focus")
def api_set_room_focus(body: RoomFocus):
    storage.set_room_focus(body.location, body.focus.strip())
    if body.focus.strip():
        storage.add_event("system", f"The room's focus shifts: {body.focus.strip()}")
    else:
        storage.add_event("system", "The room's focus is cleared.")
    return {"ok": True}


# ---------------- pages ----------------

@app.get("/", response_class=HTMLResponse)
def index():
    with open(os.path.join(BASE_DIR, "templates", "index.html")) as f:
        return f.read()


@app.get("/read", response_class=HTMLResponse)
def read_page():
    with open(os.path.join(BASE_DIR, "templates", "read.html")) as f:
        return f.read()
