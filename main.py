import asyncio
import datetime
import os
import uuid
from typing import List, Optional

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

import chapters
import interventions
import storage
import simulation
import watchdog
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
                # tick() makes blocking HTTP calls to the model backend (and can now
                # chain a few of them via retries) — run it in a worker thread so a
                # slow/stuck model call doesn't freeze the whole server (dashboard
                # polling, button clicks, etc) for the duration.
                await asyncio.to_thread(simulation.tick)
                if watchdog.session_cap_reached():
                    RUNNING["on"] = False
                    storage.add_event(
                        "system",
                        f"[watchdog] hit the {CFG.max_ticks_per_session}-tick session cap — "
                        f"auto-pausing. Press Resume to keep going.",
                    )
            except Exception as e:  # keep the loop alive across API hiccups etc.
                storage.add_event("system", f"[error during tick: {e}]")
        await asyncio.sleep(CFG.tick_seconds)


async def _daily_chapter_loop():
    """Checks hourly for a day that's ended but has no chapter yet, and writes one."""
    while True:
        try:
            yesterday = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
            if not storage.has_chapter_for_date(yesterday) and storage.has_events_for_date(yesterday):
                await asyncio.to_thread(chapters.generate_and_store_chapter, yesterday)
        except Exception as e:
            storage.add_event("system", f"[daily chapter generation failed: {e}]")
        await asyncio.sleep(3600)


# ---------------- read state ----------------

@app.get("/api/state")
def api_state():
    chars = storage.list_characters()
    objs = storage.list_objects()
    events = storage.get_recent_events(200)
    locations = storage.list_locations()
    world_facts = storage.list_world_facts(30)
    sim_minutes = storage.get_sim_minutes()
    relationships = storage.list_all_relationships()
    name_by_id = {c.id: c.name for c in chars}
    return {
        "running": RUNNING["on"],
        "characters": [c.__dict__ for c in chars],
        "objects": [o.__dict__ for o in objs],
        "events": [e.__dict__ for e in events],
        "locations": [l.__dict__ for l in locations],
        "world_facts": [f.__dict__ for f in world_facts],
        "sim_time": simulation.format_sim_time(sim_minutes),
        "room_focus": storage.get_room_focus("main_room"),
        "room_setting": storage.get_room_setting("main_room"),
        "relationships": [
            {**r.__dict__, "char_a_name": name_by_id.get(r.char_a_id, "?"),
             "char_b_name": name_by_id.get(r.char_b_id, "?")}
            for r in relationships
        ],
    }


# ---------------- pacing controls ----------------

@app.post("/api/pause")
def api_pause():
    RUNNING["on"] = False
    return {"running": False}


@app.post("/api/resume")
def api_resume():
    RUNNING["on"] = True
    watchdog.reset_session_cap()  # so a watchdog auto-pause doesn't immediately re-trip
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
    location: str = "main_room"
    interests: List[str] = []
    dislikes: List[str] = []
    guidelines: List[str] = []
    aggression: int = 30
    weirdness_chance: int = 0
    dialect: str = ""  # one of simulation.DIALECTS' keys, or "" for none


@app.post("/api/characters")
def api_add_character(body: NewCharacter):
    existing = storage.find_alive_by_name(body.name)
    if existing and not body.confirm:
        return {
            "ok": False, "duplicate": True,
            "reason": f'"{body.name}" is already alive in the room. Add another one with the same name anyway?',
        }
    location = body.location.strip() or "main_room"
    if not storage.get_location(location):
        storage.add_location(location)
    aggression = max(0, min(100, body.aggression))
    c = Character(
        id=str(uuid.uuid4())[:8], name=body.name, persona=body.persona, location=location,
        interests=[i.strip() for i in body.interests if i.strip()],
        dislikes=[i.strip() for i in body.dislikes if i.strip()],
        guidelines=[g.strip() for g in body.guidelines if g.strip()],
        aggression=aggression, aggression_baseline=aggression,
        weirdness_chance=max(0, min(100, body.weirdness_chance)),
        dialect=body.dialect if body.dialect in simulation.DIALECTS else "",
    )
    storage.add_character(c)
    storage.add_event("system", f"{c.name} enters the scene, at {location}.", location=location)
    return {"ok": True, "character": c.__dict__}


@app.post("/api/characters/{char_id}/kill")
def api_kill(char_id: str):
    c = storage.get_character(char_id)
    if c and c.alive:
        storage.kill_character(char_id)
        storage.add_event("death", f"{c.name} has died.", character_id=char_id, character_name=c.name)
        watchdog.reset(char_id)
    return {"ok": True}


@app.post("/api/characters/{char_id}/delete")
def api_delete_character(char_id: str):
    """Permanently removes a character and scrubs them from the shared event
    log — for fixing mistakes (accidental duplicates etc.), not an in-story
    death. Doesn't retroactively fix other characters' memory_summary — pair
    with the memory-edit endpoint below if the mistake already got baked in."""
    c = storage.get_character(char_id)
    storage.delete_character_hard(char_id)
    watchdog.reset(char_id)
    return {"ok": True, "removed": c.name if c else char_id}


class Directive(BaseModel):
    text: str  # empty string clears the current directive


@app.post("/api/characters/{char_id}/directive")
def api_set_directive(char_id: str, body: Directive):
    c = storage.get_character(char_id)
    if not c:
        return {"ok": False, "reason": "not found"}
    c.directive = body.text.strip()
    storage.update_character(c)
    if c.directive:
        storage.add_event("system", f"{c.name} is given a new objective.", character_id=char_id, character_name=c.name)
        simulation.prioritize(char_id)
    return {"ok": True}


class PersonaEdit(BaseModel):
    persona: str


@app.post("/api/characters/{char_id}/persona")
def api_edit_persona(char_id: str, body: PersonaEdit):
    c = storage.get_character(char_id)
    if not c:
        return {"ok": False, "reason": "not found"}
    persona = body.persona.strip()
    if not persona:
        return {"ok": False, "reason": "persona cannot be empty"}
    c.persona = persona
    storage.update_character(c)
    return {"ok": True}


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


class Interests(BaseModel):
    interests: List[str]


@app.post("/api/characters/{char_id}/interests")
def api_set_interests(char_id: str, body: Interests):
    c = storage.get_character(char_id)
    if not c:
        return {"ok": False, "reason": "not found"}
    c.interests = [i.strip() for i in body.interests if i.strip()]
    storage.update_character(c)
    return {"ok": True}


class Dislikes(BaseModel):
    dislikes: List[str]


@app.post("/api/characters/{char_id}/dislikes")
def api_set_dislikes(char_id: str, body: Dislikes):
    c = storage.get_character(char_id)
    if not c:
        return {"ok": False, "reason": "not found"}
    c.dislikes = [i.strip() for i in body.dislikes if i.strip()]
    storage.update_character(c)
    return {"ok": True}


class Guidelines(BaseModel):
    guidelines: List[str]  # hard behavioral rules, e.g. "never help Priya"


@app.post("/api/characters/{char_id}/guidelines")
def api_set_guidelines(char_id: str, body: Guidelines):
    c = storage.get_character(char_id)
    if not c:
        return {"ok": False, "reason": "not found"}
    c.guidelines = [g.strip() for g in body.guidelines if g.strip()]
    storage.update_character(c)
    return {"ok": True}


@app.get("/api/dialects")
def api_list_dialects():
    """Presets for the dashboard's dialect dropdown — key is what gets stored,
    value is a human-readable label (kept short; the actual instruction text
    used in the prompt lives in simulation.DIALECTS)."""
    labels = {
        "patois": "Jamaican Patois", "us_english": "US English", "philadelphia": "Philadelphia",
        "aave": "AAVE",
    }
    return [{"key": k, "label": labels.get(k, k)} for k in simulation.DIALECTS]


class DialectEdit(BaseModel):
    dialect: str  # one of simulation.DIALECTS' keys, or "" to clear


@app.post("/api/characters/{char_id}/dialect")
def api_set_dialect(char_id: str, body: DialectEdit):
    c = storage.get_character(char_id)
    if not c:
        return {"ok": False, "reason": "not found"}
    if body.dialect and body.dialect not in simulation.DIALECTS:
        return {"ok": False, "reason": "unknown dialect key"}
    c.dialect = body.dialect
    storage.update_character(c)
    return {"ok": True}


@app.get("/api/characters/{char_id}/knowledge")
def api_get_knowledge(char_id: str):
    return [k.__dict__ for k in storage.list_character_knowledge(char_id, limit=50)]


class RelationshipEdit(BaseModel):
    target_id: str
    affinity: int
    trust: int


@app.post("/api/characters/{char_id}/relationship")
def api_set_relationship(char_id: str, body: RelationshipEdit):
    c = storage.get_character(char_id)
    target = storage.get_character(body.target_id)
    if not c or not target:
        return {"ok": False, "reason": "not found"}
    storage.set_relationship(char_id, body.target_id, body.affinity, body.trust)
    return {"ok": True}


class Aggression(BaseModel):
    aggression: int  # sets both current aggression AND the baseline it drifts back toward


@app.post("/api/characters/{char_id}/aggression")
def api_set_aggression(char_id: str, body: Aggression):
    c = storage.get_character(char_id)
    if not c:
        return {"ok": False, "reason": "not found"}
    value = max(0, min(100, body.aggression))
    c.aggression = value
    c.aggression_baseline = value
    storage.update_character(c)
    return {"ok": True}


class Weirdness(BaseModel):
    chance: int  # 0-100, % chance per turn they auto-act weird


@app.post("/api/characters/{char_id}/weirdness")
def api_set_weirdness(char_id: str, body: Weirdness):
    c = storage.get_character(char_id)
    if not c:
        return {"ok": False, "reason": "not found"}
    c.weirdness_chance = max(0, min(100, body.chance))
    storage.update_character(c)
    return {"ok": True}


@app.post("/api/characters/{char_id}/weird")
def api_make_weird(char_id: str):
    """On-demand version of the automatic weirdness_chance roll — makes them
    act strangely on their very next turn."""
    c = storage.get_character(char_id)
    if not c:
        return {"ok": False, "reason": "not found"}
    interventions.make_weird(char_id)
    simulation.prioritize(char_id)
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
        watchdog.reset(old.id)
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


class EditObject(BaseModel):
    name: str
    description: str
    location: Optional[str] = None


@app.post("/api/objects/{obj_id}/edit")
def api_edit_object(obj_id: str, body: EditObject):
    o = storage.get_object(obj_id)
    if not o:
        return {"ok": False, "reason": "not found"}
    o.name = body.name
    o.description = body.description
    if body.location:
        o.location = body.location
    storage.update_object(o)
    return {"ok": True, "object": o.__dict__}


@app.post("/api/objects/{obj_id}/delete")
def api_delete_object(obj_id: str):
    o = storage.get_object(obj_id)
    if o:
        storage.delete_object(obj_id)
        storage.add_event("system", f"The {o.name} is removed from the room.")
    return {"ok": True}


# ---------------- locations ----------------

class NewLocation(BaseModel):
    name: str
    description: str = ""


@app.post("/api/locations")
def api_add_location(body: NewLocation):
    name = body.name.strip()
    if not name:
        return {"ok": False, "reason": "name required"}
    storage.add_location(name, body.description.strip())
    return {"ok": True}


@app.post("/api/locations/{name}/delete")
def api_delete_location(name: str):
    if name == "main_room":
        return {"ok": False, "reason": "can't delete the default location"}
    storage.delete_location(name)
    return {"ok": True}


# ---------------- world facts ----------------

class NewWorldFact(BaseModel):
    topic: str
    content: str


@app.post("/api/world_facts")
def api_add_world_fact(body: NewWorldFact):
    content = body.content.strip()
    if not content:
        return {"ok": False, "reason": "content required"}
    fact = storage.add_world_fact(body.topic.strip() or "note", content, discovered_by=None)
    return {"ok": True, "fact": fact.__dict__}


@app.post("/api/world_facts/{fact_id}/delete")
def api_delete_world_fact(fact_id: int):
    storage.delete_world_fact(fact_id)
    return {"ok": True}


# ---------------- interventions ----------------

class Intervene(BaseModel):
    type: str  # zap | insert_thought | disturb | push | heal | calm | custom
    text: Optional[str] = None
    intensity: Optional[int] = None
    health_delta: Optional[int] = None
    stability_delta: Optional[int] = None


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
    elif body.type == "heal":
        interventions.heal(char_id, body.intensity or 25)
    elif body.type == "calm":
        interventions.calm(char_id, body.intensity or 20)
    elif body.type == "custom":
        interventions.custom(char_id, body.text or "Something happens.",
                              health_delta=body.health_delta or 0, stability_delta=body.stability_delta or 0)
    simulation.prioritize(char_id)
    return {"ok": True}


class StatusEffect(BaseModel):
    effect: str
    text: Optional[str] = None  # flavor text for how it's introduced; defaults to a generic line


@app.post("/api/characters/{char_id}/status_effect/add")
def api_add_status_effect(char_id: str, body: StatusEffect):
    effect = body.effect.strip()
    if not effect:
        return {"ok": False, "reason": "effect name required"}
    interventions.add_status_effect(char_id, effect, body.text)
    simulation.prioritize(char_id)
    return {"ok": True}


@app.post("/api/characters/{char_id}/status_effect/remove")
def api_remove_status_effect(char_id: str, body: StatusEffect):
    c = storage.get_character(char_id)
    removed = storage.remove_status_effect(char_id, body.effect.strip())
    if removed and c:
        storage.add_event("system", f"{c.name} is no longer {body.effect.strip()}.", character_id=char_id, character_name=c.name)
    return {"ok": removed}


class Interact(BaseModel):
    actor_id: str
    target_id: str
    action: Optional[str] = None
    dialogue: Optional[str] = None


@app.post("/api/interact")
def api_interact(body: Interact):
    return simulation.force_interaction(body.actor_id, body.target_id, body.action or "", body.dialogue or "")


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


@app.post("/api/new_chapter")
def api_new_chapter():
    """Wraps up whatever's happened so far into a written chapter (so nothing's
    lost), pauses the sim, and clears the live script log — leaving characters,
    objects, and locations in place for you to freely edit before pressing
    Resume to start the new chapter. Chapters already written are untouched."""
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    chapter = None
    if storage.has_events_for_date(today):
        chapter = chapters.generate_and_store_chapter(today)
    RUNNING["on"] = False
    storage.clear_events()
    storage.add_event("system", "A new chapter begins.")
    return {"ok": True, "chapter": chapter}


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


class RoomSetting(BaseModel):
    setting: str
    location: str = "main_room"


@app.post("/api/room/setting")
def api_set_room_setting(body: RoomSetting):
    storage.set_room_setting(body.location, body.setting.strip())
    if body.setting.strip():
        storage.add_event("system", f"The setting shifts: {body.setting.strip()}")
    return {"ok": True}


# ---------------- pages ----------------

@app.get("/", response_class=HTMLResponse)
def index():
    # Explicit encoding matters here: without it, open() falls back to the
    # platform locale encoding (cp1252 on Windows), which mangles the non-ASCII
    # characters used in the templates (e.g. "→") into mojibake once served.
    with open(os.path.join(BASE_DIR, "templates", "index.html"), encoding="utf-8") as f:
        return f.read()


@app.get("/read", response_class=HTMLResponse)
def read_page():
    with open(os.path.join(BASE_DIR, "templates", "read.html"), encoding="utf-8") as f:
        return f.read()
