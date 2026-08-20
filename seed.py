"""
Optional: populate the room with a marooned cast competing to build the biggest
lemonade stand business on an island they can't leave. Run once: `python seed.py`
(this overwrites any characters with ids c1-c4 and the island locations/scenario —
skip it if you already have a different cast you want to keep).

Edit the personas freely, or skip this entirely and add your own cast through
the web UI instead.
"""
import storage
from models import Character, SimObject

storage.init_db()

storage.add_location("the beach", "Where the wreck washed everyone up. Driftwood, shells, tide pools, an old crate or two.")
storage.add_location("the lemon grove", "A stand of wild, scraggly lemon trees a short walk inland.")
storage.add_location("the spring", "A small freshwater spring bubbling up between rocks — the only fresh water anyone's found.")
storage.add_location("the market clearing", "A flat, sandy clearing where people have started leaving things out to trade.")

characters = [
    Character(id="c1", name="Mara", persona="A retired locksmith in her 60s. Blunt, observant, doesn't waste effort. "
              "Naturally suspicious of anyone offering a 'good deal.' Good with her hands — if it can be built or "
              "fixed from scraps, she can probably do it.", location="the beach"),
    Character(id="c2", name="Devon", persona="A 24-year-old former barista. Anxious, eager to please, terrified of "
              "confrontation — but he knows lemonade, ratios, and presentation better than anyone else here, and "
              "that's the one thing he's quietly confident about.", location="the lemon grove"),
    Character(id="c3", name="Priya", persona="A sharp-tongued lawyer in her 40s. Controlling, doesn't trust easily, "
              "thinks in terms of leverage and deals. Sees an unregulated island economy as an opportunity, not a "
              "hardship.", location="the market clearing"),
    Character(id="c4", name="Oleg", persona="A former soldier in his 50s. Calm, practical, protective of the group "
              "but not naive about self-interest. Values fairness in a trade and remembers exactly who's cheated "
              "him.", location="the spring"),
]
for c in characters:
    storage.add_character(c)

objects = [
    SimObject(id="o1", name="crate of odds and ends", description="Washed-up cargo — cups, a bit of rope, some "
              "cloth, nothing anyone's fully sorted through yet.", location="the beach"),
    SimObject(id="o2", name="flat rock", description="Wide and flat enough to serve as a table or a stall front.",
              location="the market clearing"),
]
for o in objects:
    storage.add_object(o)

storage.set_scenario(
    premise=(
        "You were all on the same boat when it went down. You washed up here, on a small island, with nothing "
        "but what you're wearing. There is no radio, no signal, no rescue on the way, and no way off — swimming "
        "out means drowning, and nobody's found a way to build anything seaworthy. This is where you live now, "
        "for as long as it takes. There's no money here, no shops, no outside supply — anything you want, you "
        "find it, make it, grow it, or get it off one of the other people stuck here with you, by whatever means "
        "you can talk, trade, or bully your way into. Somewhere along the way, lemonade — the one thing this "
        "island can actually produce, between the lemon grove and the spring — became the thing everyone's "
        "circling: ingredients, cups, a stand, customers, reputation, whatever 'money' ends up meaning here. "
        "Whoever builds the biggest lemonade stand business, by whatever the island decides that means, wins."
    ),
    goal=(
        "Build the biggest, most successful lemonade stand business on the island — through scavenging, growing, "
        "crafting, bartering, undercutting, or outright hustling the other three."
    ),
    locked=True,
)

storage.add_event("system", "Four survivors wash up on the same stretch of an island with no way off it. "
                             "Somewhere in the days ahead, one of them is going to end up running the biggest "
                             "lemonade stand this island has ever seen.")
print("Seeded 4 marooned characters, 4 island locations, and the lemonade-stand scenario (locked). "
      "Start the server and open the dashboard.")
