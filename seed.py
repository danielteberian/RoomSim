"""
Optional: populate the room with four example characters + a couple of objects
so you're not starting from a blank room. Run once: `python seed.py`

Edit the personas freely, or skip this entirely and add your own cast through
the web UI instead.
"""
import storage
from models import Character, SimObject

storage.init_db()

storage.add_location("main_room", "A shared living room where people tend to gather.")
storage.add_location("downtown_cafe", "A small cafe a short walk from the main room.")

characters = [
    Character(id="c1", name="Mara", persona="A retired locksmith in her 60s. Blunt, observant, quietly grieving her late husband. Speaks in short sentences."),
    Character(id="c2", name="Devon", persona="A 24-year-old barista. Anxious, eager to please, terrified of confrontation, secretly writes poetry.", location="downtown_cafe"),
    Character(id="c3", name="Priya", persona="A sharp-tongued lawyer in her 40s. Controlling, doesn't trust easily, has a soft spot for animals."),
    Character(id="c4", name="Oleg", persona="A former soldier in his 50s. Calm on the surface, struggles with intrusive memories, protective of the group."),
]
for c in characters:
    storage.add_character(c)

objects = [
    SimObject(id="o1", name="wooden table", description="A scarred oak table in the center of the room."),
    SimObject(id="o2", name="radio", description="An old radio that sometimes crackles to life on its own."),
]
for o in objects:
    storage.add_object(o)

storage.add_event("system", "The four of them find themselves in a room together for the first time.")
print("Seeded 4 characters and 2 objects. Start the server and open the dashboard.")
