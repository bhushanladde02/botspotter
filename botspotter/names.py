"""Word lists for deterministic bot names: "<Adjective> <Animal> #<n>".

Rules for editing: NEVER remove or reorder an entry. The index into these
lists is derived from a hash; reordering silently renames every existing
bot. Append only, and bump NAME_SCHEME_VERSION if you must break names.
"""

NAME_SCHEME_VERSION = 1

ADJECTIVES = [
    "Amber", "Arctic", "Azure", "Bold", "Brass", "Bright", "Bronze", "Cobalt",
    "Copper", "Coral", "Crimson", "Crystal", "Dusk", "Ember", "Frost", "Gilded",
    "Golden", "Granite", "Hazel", "Indigo", "Iron", "Ivory", "Jade", "Jet",
    "Lunar", "Marble", "Midnight", "Misty", "Neon", "Nimble", "Obsidian", "Onyx",
    "Opal", "Pale", "Pearl", "Plum", "Quartz", "Quiet", "Rapid", "Ruby",
    "Rusty", "Sable", "Saffron", "Scarlet", "Shadow", "Silent", "Silver", "Slate",
    "Smoky", "Solar", "Steel", "Storm", "Swift", "Teal", "Tidal", "Topaz",
    "Velvet", "Violet", "Wild", "Zinc", "Electric", "Cosmic", "Pixel", "Turbo",
]

ANIMALS = [
    "Falcon", "Heron", "Otter", "Lynx", "Fox", "Owl", "Raven", "Wolf",
    "Panda", "Badger", "Moth", "Crane", "Ibis", "Jackal", "Koala", "Lemur",
    "Marten", "Newt", "Osprey", "Puffin", "Quokka", "Robin", "Stoat", "Tapir",
    "Urchin", "Viper", "Walrus", "Yak", "Zebra", "Beetle", "Cobra", "Dingo",
    "Eagle", "Ferret", "Gecko", "Hornet", "Iguana", "Jaguar", "Kestrel", "Lark",
    "Mongoose", "Narwhal", "Ocelot", "Pelican", "Quail", "Rhino", "Shark", "Toucan",
    "Vole", "Wombat", "Axolotl", "Bison", "Cheetah", "Dolphin", "Elk", "Finch",
    "Gibbon", "Hyena", "Impala", "Jay", "Kiwi", "Llama", "Macaw", "Orca",
]
