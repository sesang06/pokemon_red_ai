from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pokemon_agent.memory.world_state import BattleOpponent, GameMode, GameState, ItemStack, PartyMember, Position


PLAYER_FACING_DIRECTIONS = {
    0x00: "down",
    0x04: "up",
    0x08: "left",
    0x0C: "right",
}


class MemoryView(Protocol):
    def __getitem__(self, address: int) -> int:
        """Read a byte from emulator memory."""


POKEMON_RED_MAP_NAMES: dict[int, str] = {
    0x00: "Pallet Town",
    0x01: "Viridian City",
    0x02: "Pewter City",
    0x03: "Cerulean City",
    0x04: "Lavender Town",
    0x05: "Vermilion City",
    0x06: "Celadon City",
    0x07: "Fuchsia City",
    0x08: "Cinnabar Island",
    0x09: "Indigo Plateau",
    0x0A: "Saffron City",
    0x0C: "Route 1",
    0x0D: "Route 2",
    0x0E: "Route 3",
    0x0F: "Route 4",
    0x10: "Route 5",
    0x11: "Route 6",
    0x12: "Route 7",
    0x13: "Route 8",
    0x14: "Route 9",
    0x15: "Route 10",
    0x16: "Route 11",
    0x17: "Route 12",
    0x18: "Route 13",
    0x19: "Route 14",
    0x1A: "Route 15",
    0x1B: "Route 16",
    0x1C: "Route 17",
    0x1D: "Route 18",
    0x1E: "Route 19",
    0x1F: "Route 20",
    0x20: "Route 21",
    0x21: "Route 22",
    0x22: "Route 23",
    0x23: "Route 24",
    0x24: "Route 25",
    0x25: "Player's House 1F",
    0x26: "Player's House 2F",
    0x27: "Rival's House",
    0x28: "Oak's Lab",
    0x29: "Viridian Pokemon Center",
    0x2A: "Viridian Mart",
    0x2B: "Viridian School",
    0x2C: "Viridian House",
    0x2D: "Viridian Gym",
    0x2E: "Diglett's Cave Route 2",
    0x2F: "Viridian Forest North Gate",
    0x30: "Route 2 House",
    0x31: "Route 2 Gate",
    0x32: "Viridian Forest South Gate",
    0x33: "Viridian Forest",
    0x34: "Museum 1F",
    0x35: "Museum 2F",
    0x36: "Pewter Gym",
    0x37: "Pewter House 1",
    0x38: "Pewter Mart",
    0x39: "Pewter House 2",
    0x3A: "Pewter Pokemon Center",
    0x3B: "Mt. Moon 1F",
    0x3C: "Mt. Moon B1F",
    0x3D: "Mt. Moon B2F",
    0x3E: "Cerulean Trashed House",
    0x3F: "Cerulean Trade House",
    0x40: "Cerulean Pokemon Center",
    0x41: "Cerulean Gym",
    0x42: "Bike Shop",
    0x43: "Cerulean Mart",
    0x44: "Mt. Moon Pokemon Center",
    0x51: "Rock Tunnel Pokemon Center",
    0x52: "Rock Tunnel 1F",
    0x53: "Power Plant",
    0x58: "Bill's House",
    0x59: "Vermilion Pokemon Center",
    0x5A: "Pokemon Fan Club",
    0x5B: "Vermilion Mart",
    0x5C: "Vermilion Gym",
    0x5E: "Vermilion Dock",
    0x5F: "S.S. Anne 1F",
    0x60: "S.S. Anne 2F",
    0x61: "S.S. Anne 3F",
    0x62: "S.S. Anne B1F",
    0x63: "S.S. Anne Bow",
    0x65: "S.S. Anne Captain's Room",
    0x6C: "Victory Road 1F",
    0x71: "Lance",
    0x76: "Hall Of Fame",
    0x7A: "Celadon Mart 1F",
    0x7B: "Celadon Mart 2F",
    0x7C: "Celadon Mart 3F",
    0x7D: "Celadon Mart 4F",
    0x7E: "Celadon Mart Roof",
    0x85: "Celadon Pokemon Center",
    0x86: "Celadon Gym",
    0x87: "Game Corner",
    0x89: "Game Corner Prize Room",
    0x8D: "Lavender Pokemon Center",
    0x8E: "Pokemon Tower 1F",
    0x8F: "Pokemon Tower 2F",
    0x90: "Pokemon Tower 3F",
    0x91: "Pokemon Tower 4F",
    0x92: "Pokemon Tower 5F",
    0x93: "Pokemon Tower 6F",
    0x94: "Pokemon Tower 7F",
    0x98: "Fuchsia Mart",
    0x9A: "Fuchsia Pokemon Center",
    0x9C: "Safari Zone Entrance",
    0x9D: "Fuchsia Gym",
    0xA5: "Pokemon Mansion 1F",
    0xA6: "Cinnabar Gym",
    0xAB: "Cinnabar Pokemon Center",
    0xAC: "Cinnabar Mart",
    0xAE: "Indigo Plateau Lobby",
    0xB1: "Fighting Dojo",
    0xB2: "Saffron Gym",
    0xB4: "Saffron Mart",
    0xB5: "Silph Co. 1F",
    0xB6: "Saffron Pokemon Center",
    0xC0: "Seafoam Islands 1F",
    0xC2: "Victory Road 2F",
    0xC5: "Diglett's Cave",
    0xC6: "Victory Road 3F",
    0xC7: "Rocket Hideout B1F",
    0xC8: "Rocket Hideout B2F",
    0xC9: "Rocket Hideout B3F",
    0xCA: "Rocket Hideout B4F",
    0xCF: "Silph Co. 2F",
    0xD0: "Silph Co. 3F",
    0xD1: "Silph Co. 4F",
    0xD2: "Silph Co. 5F",
    0xD3: "Silph Co. 6F",
    0xD4: "Silph Co. 7F",
    0xD5: "Silph Co. 8F",
    0xD6: "Pokemon Mansion 2F",
    0xD7: "Pokemon Mansion 3F",
    0xD8: "Pokemon Mansion B1F",
    0xD9: "Safari Zone East",
    0xDA: "Safari Zone North",
    0xDB: "Safari Zone West",
    0xDC: "Safari Zone Center",
    0xE2: "Cerulean Cave 2F",
    0xE3: "Cerulean Cave B1F",
    0xE4: "Cerulean Cave 1F",
    0xE8: "Rock Tunnel B1F",
    0xE9: "Silph Co. 9F",
    0xEA: "Silph Co. 10F",
    0xEB: "Silph Co. 11F",
    0xEC: "Silph Co. Elevator",
    0xEF: "Trade Center",
    0xF0: "Colosseum",
    0xF5: "Lorelei",
    0xF6: "Bruno",
    0xF7: "Agatha",
}

TILESET_NAMES: dict[int, str] = {
    0x00: "Overworld",
    0x01: "Red's House 1",
    0x02: "Mart",
    0x03: "Forest",
    0x04: "Red's House 2",
    0x05: "Dojo",
    0x06: "Pokemon Center",
    0x07: "Gym",
    0x08: "House",
    0x09: "Forest Gate",
    0x0A: "Museum",
    0x0B: "Underground",
    0x0C: "Gate",
    0x0D: "Ship",
    0x0E: "Ship Port",
    0x0F: "Cemetery",
    0x10: "Interior",
    0x11: "Cavern",
    0x12: "Lobby",
    0x13: "Mansion",
    0x14: "Lab",
    0x15: "Club",
    0x16: "Facility",
    0x17: "Plateau",
}

POKEMON_TYPE_NAMES: dict[int, str] = {
    0x00: "Normal",
    0x01: "Fighting",
    0x02: "Flying",
    0x03: "Poison",
    0x04: "Ground",
    0x05: "Rock",
    0x07: "Bug",
    0x08: "Ghost",
    0x14: "Fire",
    0x15: "Water",
    0x16: "Grass",
    0x17: "Electric",
    0x18: "Psychic",
    0x19: "Ice",
    0x1A: "Dragon",
}

POKEMON_SPECIES_NAMES: dict[int, str] = {
    0x01: "Rhydon",
    0x02: "Kangaskhan",
    0x03: "Nidoran M",
    0x04: "Clefairy",
    0x05: "Spearow",
    0x06: "Voltorb",
    0x07: "Nidoking",
    0x08: "Slowbro",
    0x09: "Ivysaur",
    0x0A: "Exeggutor",
    0x0B: "Lickitung",
    0x0C: "Exeggcute",
    0x0D: "Grimer",
    0x0E: "Gengar",
    0x0F: "Nidoran F",
    0x10: "Nidoqueen",
    0x11: "Cubone",
    0x12: "Rhyhorn",
    0x13: "Lapras",
    0x14: "Arcanine",
    0x15: "Mew",
    0x16: "Gyarados",
    0x17: "Shellder",
    0x18: "Tentacool",
    0x19: "Gastly",
    0x1A: "Scyther",
    0x1B: "Staryu",
    0x1C: "Blastoise",
    0x1D: "Pinsir",
    0x1E: "Tangela",
    0x21: "Growlithe",
    0x22: "Onix",
    0x23: "Fearow",
    0x24: "Pidgey",
    0x25: "Slowpoke",
    0x26: "Kadabra",
    0x27: "Graveler",
    0x28: "Chansey",
    0x29: "Machoke",
    0x2A: "Mr. Mime",
    0x2B: "Hitmonlee",
    0x2C: "Hitmonchan",
    0x2D: "Arbok",
    0x2E: "Parasect",
    0x2F: "Psyduck",
    0x30: "Drowzee",
    0x31: "Golem",
    0x33: "Magmar",
    0x35: "Electabuzz",
    0x36: "Magneton",
    0x37: "Koffing",
    0x39: "Mankey",
    0x3A: "Seel",
    0x3B: "Diglett",
    0x3C: "Tauros",
    0x40: "Farfetch'd",
    0x41: "Venonat",
    0x42: "Dragonite",
    0x46: "Doduo",
    0x47: "Poliwag",
    0x48: "Jynx",
    0x49: "Moltres",
    0x4A: "Articuno",
    0x4B: "Zapdos",
    0x4C: "Ditto",
    0x4D: "Meowth",
    0x4E: "Krabby",
    0x52: "Vulpix",
    0x53: "Ninetales",
    0x54: "Pikachu",
    0x55: "Raichu",
    0x58: "Dratini",
    0x59: "Dragonair",
    0x5A: "Kabuto",
    0x5B: "Kabutops",
    0x5C: "Horsea",
    0x5D: "Seadra",
    0x60: "Sandshrew",
    0x61: "Sandslash",
    0x62: "Omanyte",
    0x63: "Omastar",
    0x64: "Jigglypuff",
    0x65: "Wigglytuff",
    0x66: "Eevee",
    0x67: "Flareon",
    0x68: "Jolteon",
    0x69: "Vaporeon",
    0x6A: "Machop",
    0x6B: "Zubat",
    0x6C: "Ekans",
    0x6D: "Paras",
    0x6E: "Poliwhirl",
    0x6F: "Poliwrath",
    0x70: "Weedle",
    0x71: "Kakuna",
    0x72: "Beedrill",
    0x74: "Dodrio",
    0x75: "Primeape",
    0x76: "Dugtrio",
    0x77: "Venomoth",
    0x78: "Dewgong",
    0x7B: "Caterpie",
    0x7C: "Metapod",
    0x7D: "Butterfree",
    0x7E: "Machamp",
    0x80: "Golduck",
    0x81: "Hypno",
    0x82: "Golbat",
    0x83: "Mewtwo",
    0x84: "Snorlax",
    0x85: "Magikarp",
    0x88: "Muk",
    0x8A: "Kingler",
    0x8B: "Cloyster",
    0x8D: "Electrode",
    0x8E: "Clefable",
    0x8F: "Weezing",
    0x90: "Persian",
    0x91: "Marowak",
    0x93: "Haunter",
    0x94: "Abra",
    0x95: "Alakazam",
    0x96: "Pidgeotto",
    0x97: "Pidgeot",
    0x98: "Starmie",
    0x99: "Bulbasaur",
    0x9A: "Venusaur",
    0x9B: "Tentacruel",
    0x9D: "Goldeen",
    0x9E: "Seaking",
    0xA3: "Ponyta",
    0xA4: "Rapidash",
    0xA5: "Rattata",
    0xA6: "Raticate",
    0xA7: "Nidorino",
    0xA8: "Nidorina",
    0xA9: "Geodude",
    0xAA: "Porygon",
    0xAB: "Aerodactyl",
    0xAD: "Magnemite",
    0xB0: "Charmander",
    0xB1: "Squirtle",
    0xB2: "Charmeleon",
    0xB3: "Wartortle",
    0xB4: "Charizard",
    0xB6: "Fossil Kabutops",
    0xB7: "Fossil Aerodactyl",
    0xB8: "Ghost",
    0xB9: "Oddish",
    0xBA: "Gloom",
    0xBB: "Vileplume",
    0xBC: "Bellsprout",
    0xBD: "Weepinbell",
    0xBE: "Victreebel",
}

# Pokémon Red stores party species in an internal index order. PartyMember.species_id
# is the National Pokédex number consumed by external data sources such as PokéAPI.
# Source: pret/pokered data/pokemon/dex_order.asm.
GEN1_POKEDEX_SPECIES: tuple[str, ...] = (
    "Bulbasaur", "Ivysaur", "Venusaur", "Charmander", "Charmeleon", "Charizard",
    "Squirtle", "Wartortle", "Blastoise", "Caterpie", "Metapod", "Butterfree",
    "Weedle", "Kakuna", "Beedrill", "Pidgey", "Pidgeotto", "Pidgeot",
    "Rattata", "Raticate", "Spearow", "Fearow", "Ekans", "Arbok", "Pikachu",
    "Raichu", "Sandshrew", "Sandslash", "Nidoran F", "Nidorina", "Nidoqueen",
    "Nidoran M", "Nidorino", "Nidoking", "Clefairy", "Clefable", "Vulpix",
    "Ninetales", "Jigglypuff", "Wigglytuff", "Zubat", "Golbat", "Oddish", "Gloom",
    "Vileplume", "Paras", "Parasect", "Venonat", "Venomoth", "Diglett", "Dugtrio",
    "Meowth", "Persian", "Psyduck", "Golduck", "Mankey", "Primeape", "Growlithe",
    "Arcanine", "Poliwag", "Poliwhirl", "Poliwrath", "Abra", "Kadabra", "Alakazam",
    "Machop", "Machoke", "Machamp", "Bellsprout", "Weepinbell", "Victreebel",
    "Tentacool", "Tentacruel", "Geodude", "Graveler", "Golem", "Ponyta", "Rapidash",
    "Slowpoke", "Slowbro", "Magnemite", "Magneton", "Farfetch'd", "Doduo", "Dodrio",
    "Seel", "Dewgong", "Grimer", "Muk", "Shellder", "Cloyster", "Gastly", "Haunter",
    "Gengar", "Onix", "Drowzee", "Hypno", "Krabby", "Kingler", "Voltorb", "Electrode",
    "Exeggcute", "Exeggutor", "Cubone", "Marowak", "Hitmonlee", "Hitmonchan",
    "Lickitung", "Koffing", "Weezing", "Rhyhorn", "Rhydon", "Chansey", "Tangela",
    "Kangaskhan", "Horsea", "Seadra", "Goldeen", "Seaking", "Staryu", "Starmie",
    "Mr. Mime", "Scyther", "Jynx", "Electabuzz", "Magmar", "Pinsir", "Tauros",
    "Magikarp", "Gyarados", "Lapras", "Ditto", "Eevee", "Vaporeon", "Jolteon",
    "Flareon", "Porygon", "Omanyte", "Omastar", "Kabuto", "Kabutops", "Aerodactyl",
    "Snorlax", "Articuno", "Zapdos", "Moltres", "Dratini", "Dragonair", "Dragonite",
    "Mewtwo", "Mew",
)
GEN1_POKEDEX_NUMBER_BY_NAME = {
    species: number for number, species in enumerate(GEN1_POKEDEX_SPECIES, start=1)
}

MOVE_NAMES: dict[int, str] = {
    0x01: "Pound",
    0x02: "Karate Chop",
    0x03: "DoubleSlap",
    0x04: "Comet Punch",
    0x05: "Mega Punch",
    0x0A: "Scratch",
    0x0F: "Cut",
    0x10: "Gust",
    0x16: "Vine Whip",
    0x21: "Tackle",
    0x22: "Body Slam",
    0x27: "Tail Whip",
    0x2D: "Growl",
    0x34: "Ember",
    0x35: "Flamethrower",
    0x37: "Water Gun",
    0x39: "Surf",
    0x3A: "Ice Beam",
    0x3B: "Blizzard",
    0x3C: "Psybeam",
    0x3D: "BubbleBeam",
    0x4B: "Razor Leaf",
    0x4C: "SolarBeam",
    0x54: "ThunderShock",
    0x55: "Thunderbolt",
    0x57: "Thunder",
    0x58: "Rock Throw",
    0x59: "Earthquake",
    0x5B: "Dig",
    0x5C: "Toxic",
    0x5D: "Confusion",
    0x5E: "Psychic",
    0x5F: "Hypnosis",
    0x62: "Quick Attack",
    0x64: "Teleport",
    0x69: "Recover",
    0x70: "Barrier",
    0x73: "Reflect",
    0x7E: "Fire Blast",
    0x7F: "Waterfall",
    0x81: "Swift",
    0x88: "Hi Jump Kick",
    0x89: "Glare",
    0x8A: "Dream Eater",
    0x93: "Spore",
    0x94: "Flash",
    0x96: "Splash",
    0x99: "Explosion",
    0x9C: "Rest",
    0x9D: "Rock Slide",
    0xA4: "Substitute",
    0xA5: "Struggle",
}

ITEM_NAMES: dict[int, str] = {
    0x01: "Master Ball",
    0x02: "Ultra Ball",
    0x03: "Great Ball",
    0x04: "Poke Ball",
    0x05: "Town Map",
    0x06: "Bicycle",
    0x08: "Safari Ball",
    0x09: "Pokedex",
    0x0A: "Moon Stone",
    0x0B: "Antidote",
    0x0C: "Burn Heal",
    0x0D: "Ice Heal",
    0x0E: "Awakening",
    0x0F: "Parlyz Heal",
    0x10: "Full Restore",
    0x11: "Max Potion",
    0x12: "Hyper Potion",
    0x13: "Super Potion",
    0x14: "Potion",
    0x1D: "Escape Rope",
    0x1E: "Repel",
    0x1F: "Old Amber",
    0x20: "Fire Stone",
    0x21: "Thunder Stone",
    0x22: "Water Stone",
    0x23: "HP Up",
    0x24: "Protein",
    0x25: "Iron",
    0x26: "Carbos",
    0x27: "Calcium",
    0x28: "Rare Candy",
    0x29: "Dome Fossil",
    0x2A: "Helix Fossil",
    0x2B: "Secret Key",
    0x2D: "Bike Voucher",
    0x2E: "X Accuracy",
    0x2F: "Leaf Stone",
    0x30: "Card Key",
    0x31: "Nugget",
    0x32: "PP Up",
    0x33: "Poke Doll",
    0x34: "Full Heal",
    0x35: "Revive",
    0x36: "Max Revive",
    0x37: "Guard Spec.",
    0x38: "Super Repel",
    0x39: "Max Repel",
    0x3A: "Dire Hit",
    0x3B: "Coin",
    0x3C: "Fresh Water",
    0x3D: "Soda Pop",
    0x3E: "Lemonade",
    0x3F: "S.S. Ticket",
    0x40: "Gold Teeth",
    0x41: "X Attack",
    0x42: "X Defend",
    0x43: "X Speed",
    0x44: "X Special",
    0x45: "Coin Case",
    0x46: "Oak's Parcel",
    0x47: "Itemfinder",
    0x48: "Silph Scope",
    0x49: "Poke Flute",
    0x4A: "Lift Key",
    0x4B: "Exp. All",
    0x4C: "Old Rod",
    0x4D: "Good Rod",
    0x4E: "Super Rod",
    0x50: "Ether",
    0x51: "Max Ether",
    0x52: "Elixer",
    0x53: "Max Elixer",
}

BADGES: tuple[tuple[str, int], ...] = (
    ("Boulder", 1 << 0),
    ("Cascade", 1 << 1),
    ("Thunder", 1 << 2),
    ("Rainbow", 1 << 3),
    ("Soul", 1 << 4),
    ("Marsh", 1 << 5),
    ("Volcano", 1 << 6),
    ("Earth", 1 << 7),
)

PARTY_DATA_BASES = (0xD16B, 0xD197, 0xD1C3, 0xD1EF, 0xD21B, 0xD247)
PARTY_NICKNAME_BASES = (0xD2B5, 0xD2C0, 0xD2CB, 0xD2D6, 0xD2E1, 0xD2EC)


@dataclass(frozen=True)
class PokemonRedRamMap:
    player_facing: int = 0xC109
    current_map: int = 0xD35E
    player_y: int = 0xD361
    player_x: int = 0xD362
    player_y_block: int = 0xD363
    player_x_block: int = 0xD364
    last_map: int = 0xD365
    tileset: int = 0xD367
    map_height: int = 0xD368
    map_width: int = 0xD369
    collision_ptr_lo: int = 0xD530
    collision_ptr_hi: int = 0xD531
    grass_tile: int = 0xD535
    tileset_type: int = 0xFFD7
    battle_type: int = 0xD057
    battle_kind: int = 0xD05A
    battle_turns: int = 0xCCD5
    enemy_species: int = 0xCFE5
    enemy_hp: int = 0xCFE6
    enemy_status: int = 0xCFE9
    enemy_type_1: int = 0xCFEA
    enemy_type_2: int = 0xCFEB
    enemy_level: int = 0xCFF3
    enemy_max_hp: int = 0xCFF4
    menu_selection: int = 0xCC26
    start_menu_cursor: int = 0xCC2D
    money: int = 0xD347
    player_name: int = 0xD158
    rival_name: int = 0xD34A
    badges: int = 0xD356
    player_id: int = 0xD359
    party_count: int = 0xD163
    party_species: int = 0xD164
    item_count: int = 0xD31D
    item_list: int = 0xD31E
    coins: int = 0xD5A4
    game_hours: int = 0xDA40
    game_minutes: int = 0xDA42
    game_seconds: int = 0xDA44
    warp_count: int = 0xD3AE
    warp_list: int = 0xD3AF
    tilemap_start: int = 0xC3A0
    tilemap_end: int = 0xC507
    pokedex_owned_start: int = 0xD2F7
    pokedex_owned_end: int = 0xD30A
    joy_ignore: int = 0xCD6B
    status_flags_5: int = 0xD730


class PokemonRedMemoryReader:
    def __init__(self, ram_map: PokemonRedRamMap | None = None):
        self.ram_map = ram_map or PokemonRedRamMap()

    def read(self, memory: MemoryView) -> GameState:
        raw = self.read_raw(memory)
        map_id = raw["current_map"]
        position = Position(x=raw["player_x"], y=raw["player_y"])
        in_battle = raw["battle_type"] != 0
        dialog_text = self.read_dialog(memory)
        dialog_open = bool(dialog_text and raw["dialog_box_detected"])
        mode = GameMode.BATTLE if in_battle else GameMode.TALK if dialog_open else GameMode.EXPLORE

        return GameState(
            map_id=map_id,
            map_name=POKEMON_RED_MAP_NAMES.get(map_id, f"Map {map_id:#04x}"),
            position=position,
            facing=PLAYER_FACING_DIRECTIONS.get(int(raw["player_facing"])),
            mode=mode,
            in_battle=in_battle,
            battle_opponent=self.read_battle_opponent(memory) if in_battle else None,
            dialog_open=dialog_open,
            player_name=self.read_player_name(memory),
            rival_name=self.read_rival_name(memory),
            money=self.read_money(memory),
            coins=self.read_coins(memory),
            game_time=self.read_game_time(memory),
            tileset=self.read_tileset(memory),
            pokedex_caught=self.read_pokedex_caught_count(memory),
            badges=self.read_badges(memory),
            party=self.read_party_pokemon(memory),
            items=self.read_items(memory),
            warps=self.read_warps(memory),
            dialog_text=dialog_text or None,
            flags={
                "has_badges": bool(raw["badge_bits"]),
                "has_dialog_text": bool(dialog_text),
                "has_warps": raw["warp_count"] > 0,
            },
            raw=raw,
        )

    def read_raw(self, memory: MemoryView) -> dict[str, object]:
        addresses = self.ram_map
        collision_ptr = self._read_u16_le(memory, addresses.collision_ptr_lo)
        dialog_text, dialog_box_detected = self._read_dialog(memory)
        return {
            "player_facing": self._read_u8(memory, addresses.player_facing),
            "current_map": self._read_u8(memory, addresses.current_map),
            "player_y": self._read_u8(memory, addresses.player_y),
            "player_x": self._read_u8(memory, addresses.player_x),
            "player_y_block": self._read_u8(memory, addresses.player_y_block),
            "player_x_block": self._read_u8(memory, addresses.player_x_block),
            "last_map": self._read_u8(memory, addresses.last_map),
            "collision_ptr": collision_ptr,
            "grass_tile": self._read_u8(memory, addresses.grass_tile),
            "tileset_type": self._read_u8(memory, addresses.tileset_type),
            "tileset": self._read_u8(memory, addresses.tileset),
            "map_height": self._read_u8(memory, addresses.map_height),
            "map_width": self._read_u8(memory, addresses.map_width),
            "battle_type": self._read_u8(memory, addresses.battle_type),
            "battle_kind": self._read_u8(memory, addresses.battle_kind),
            "battle_turns": self._read_u8(memory, addresses.battle_turns),
            "menu_selection": self._read_u8(memory, addresses.menu_selection),
            "start_menu_cursor": self._read_u8(memory, addresses.start_menu_cursor),
            "player_id": self._read_u16_be(memory, addresses.player_id),
            "badge_bits": self._read_u8(memory, addresses.badges),
            "party_count": self.read_party_size(memory),
            "item_count": self.read_item_count(memory),
            "money": self.read_money(memory),
            "coins": self.read_coins(memory),
            "game_time": self.read_game_time(memory),
            "warp_count": self._bounded_count(self._read_u8(memory, addresses.warp_count), maximum=16),
            "dialog_text": dialog_text,
            "dialog_box_detected": dialog_box_detected,
            "pokedex_caught": self.read_pokedex_caught_count(memory),
            "joy_ignore": self._read_u8(memory, addresses.joy_ignore),
            "status_flags_5": self._read_u8(memory, addresses.status_flags_5),
            "controls_locked": bool(
                self._read_u8(memory, addresses.joy_ignore)
                or self._read_u8(memory, addresses.status_flags_5) & 0xA1
            ),
        }

    def read_player_name(self, memory: MemoryView) -> str:
        return self._convert_text(self._read_bytes(memory, self.ram_map.player_name, 11))

    def read_rival_name(self, memory: MemoryView) -> str:
        return self._convert_text(self._read_bytes(memory, self.ram_map.rival_name, 11))

    def read_badges(self, memory: MemoryView) -> list[str]:
        bits = self._read_u8(memory, self.ram_map.badges)
        return [name for name, mask in BADGES if bits & mask]

    def read_party_size(self, memory: MemoryView) -> int:
        return self._bounded_count(self._read_u8(memory, self.ram_map.party_count), maximum=6)

    def read_battle_opponent(self, memory: MemoryView) -> BattleOpponent | None:
        addresses = self.ram_map
        species_id = self._read_u8(memory, addresses.enemy_species)
        if species_id in {0x00, 0xFF}:
            return None

        type_1 = self._read_u8(memory, addresses.enemy_type_1)
        type_2 = self._read_u8(memory, addresses.enemy_type_2)
        types = [_type_name(type_1)]
        if type_2 != type_1:
            types.append(_type_name(type_2))

        return BattleOpponent(
            species=_species_name(species_id),
            level=self._read_u8(memory, addresses.enemy_level),
            hp=self._read_u16_be(memory, addresses.enemy_hp),
            max_hp=self._read_u16_be(memory, addresses.enemy_max_hp),
            status=_status_name(self._read_u8(memory, addresses.enemy_status)),
            types=types,
        )

    def read_party_pokemon(self, memory: MemoryView) -> list[PartyMember]:
        party: list[PartyMember] = []
        party_size = self.read_party_size(memory)

        for index in range(party_size):
            base = PARTY_DATA_BASES[index]
            species_id = self._read_u8(memory, base)
            if species_id == 0:
                species_id = self._read_u8(memory, self.ram_map.party_species + index)
            if species_id in {0x00, 0xFF}:
                continue

            type1_id = self._read_u8(memory, base + 5)
            type2_id = self._read_u8(memory, base + 6)
            types = [_type_name(type1_id)]
            if type2_id != type1_id:
                types.append(_type_name(type2_id))

            moves: list[str] = []
            move_pp: list[int] = []
            for move_index in range(4):
                move_id = self._read_u8(memory, base + 8 + move_index)
                if move_id == 0:
                    continue
                moves.append(_move_name(move_id))
                move_pp.append(self._read_u8(memory, base + 0x1D + move_index))

            species_name = _species_name(species_id)
            party.append(
                PartyMember(
                    species=species_name,
                    species_id=GEN1_POKEDEX_NUMBER_BY_NAME.get(species_name),
                    internal_species_id=species_id,
                    nickname=self._convert_text(self._read_bytes(memory, PARTY_NICKNAME_BASES[index], 11)) or None,
                    level=self._read_u8(memory, base + 0x21),
                    hp=self._read_u16_be(memory, base + 1),
                    max_hp=self._read_u16_be(memory, base + 0x22),
                    status=_status_name(self._read_u8(memory, base + 4)),
                    types=types,
                    moves=moves,
                    move_pp=move_pp,
                    trainer_id=self._read_u16_be(memory, base + 12),
                    experience=self._read_u24_be(memory, base + 0x1A),
                )
            )

        return party

    def read_game_time(self, memory: MemoryView) -> str:
        hours = self._read_u16_be(memory, self.ram_map.game_hours)
        minutes = self._read_u8(memory, self.ram_map.game_minutes)
        seconds = self._read_u8(memory, self.ram_map.game_seconds)
        return f"{hours}:{minutes:02d}:{seconds:02d}"

    def read_tileset(self, memory: MemoryView) -> str:
        value = self._read_u8(memory, self.ram_map.tileset)
        return TILESET_NAMES.get(value, f"Tileset 0x{value:02X}")

    def read_coordinates(self, memory: MemoryView) -> tuple[int, int]:
        return (self._read_u8(memory, self.ram_map.player_x), self._read_u8(memory, self.ram_map.player_y))

    def read_money(self, memory: MemoryView) -> int:
        return _bcd_to_int(self._read_bytes(memory, self.ram_map.money, 3))

    def read_coins(self, memory: MemoryView) -> int:
        return self._read_u16_be(memory, self.ram_map.coins)

    def read_item_count(self, memory: MemoryView) -> int:
        return self._bounded_count(self._read_u8(memory, self.ram_map.item_count), maximum=20)

    def read_items(self, memory: MemoryView) -> list[ItemStack]:
        items: list[ItemStack] = []
        for index in range(self.read_item_count(memory)):
            item_id = self._read_u8(memory, self.ram_map.item_list + index * 2)
            if item_id in {0x00, 0xFF}:
                continue
            quantity = self._read_u8(memory, self.ram_map.item_list + index * 2 + 1)
            items.append(ItemStack(name=_item_name(item_id), quantity=quantity, item_id=item_id))
        return items

    def read_warps(self, memory: MemoryView) -> list[Position]:
        warps: list[Position] = []
        for index in range(self._bounded_count(self._read_u8(memory, self.ram_map.warp_count), maximum=16)):
            base = self.ram_map.warp_list + index * 4
            row = self._read_u8(memory, base)
            col = self._read_u8(memory, base + 1)
            warps.append(Position(x=col, y=row))
        return warps

    def read_dialog(self, memory: MemoryView) -> str:
        text, _has_box = self._read_dialog(memory)
        return text

    def read_pokedex_caught_count(self, memory: MemoryView) -> int:
        caught_count = 0
        for address in range(self.ram_map.pokedex_owned_start, self.ram_map.pokedex_owned_end):
            caught_count += self._read_u8(memory, address).bit_count()
        return caught_count

    def _read_dialog(self, memory: MemoryView) -> tuple[str, bool]:
        text_lines: list[str] = []
        current_line: list[int] = []
        space_count = 0
        has_box_border = False

        for address in range(self.ram_map.tilemap_start, self.ram_map.tilemap_end):
            value = self._read_u8(memory, address)
            if value in {0x79, 0x7A, 0x7B, 0x7C, 0x7D, 0x7E}:
                has_box_border = True
                self._flush_text_line(current_line, text_lines)
                space_count = 0
                continue

            if value == 0x7F:
                current_line.append(value)
                space_count += 1
            elif _is_text_tile(value):
                current_line.append(value)
                space_count = 0

            if space_count > 10:
                self._flush_text_line(current_line, text_lines)
                space_count = 0

        self._flush_text_line(current_line, text_lines)
        return "\n".join(line for line in text_lines if line), has_box_border

    def _flush_text_line(self, current_line: list[int], text_lines: list[str]) -> None:
        if not current_line:
            return
        text = self._convert_text(current_line).strip()
        if text:
            text_lines.append(text)
        current_line.clear()

    def _convert_text(self, bytes_data: list[int]) -> str:
        result = ""
        for value in bytes_data:
            if value == 0x00:
                continue
            if value == 0x50:
                break
            if value == 0x4E:
                result += "\n"
            elif value == 0x7F:
                result += " "
            elif 0x80 <= value <= 0x99:
                result += chr(value - 0x80 + ord("A"))
            elif 0xA0 <= value <= 0xB9:
                result += chr(value - 0xA0 + ord("a"))
            elif 0xF6 <= value <= 0xFF:
                result += str(value - 0xF6)
            elif value in TEXT_OVERRIDES:
                result += TEXT_OVERRIDES[value]
            else:
                result += f"[{value:02X}]"
        return " ".join(result.strip().split())

    @staticmethod
    def _read_u8(memory: MemoryView, address: int) -> int:
        return int(memory[address]) & 0xFF

    @classmethod
    def _read_u16_be(cls, memory: MemoryView, address: int) -> int:
        return (cls._read_u8(memory, address) << 8) | cls._read_u8(memory, address + 1)

    @classmethod
    def _read_u16_le(cls, memory: MemoryView, address: int) -> int:
        return cls._read_u8(memory, address) | (cls._read_u8(memory, address + 1) << 8)

    @classmethod
    def _read_u24_be(cls, memory: MemoryView, address: int) -> int:
        return (cls._read_u8(memory, address) << 16) | (cls._read_u8(memory, address + 1) << 8) | cls._read_u8(memory, address + 2)

    @classmethod
    def _read_bytes(cls, memory: MemoryView, address: int, length: int) -> list[int]:
        return [cls._read_u8(memory, address + offset) for offset in range(length)]

    @staticmethod
    def _bounded_count(value: int, *, maximum: int) -> int:
        return max(0, min(int(value), maximum))


TEXT_OVERRIDES: dict[int, str] = {
    0x54: "POKE",
    0x6D: ":",
    0x9A: "(",
    0x9B: ")",
    0x9C: ":",
    0x9D: ";",
    0x9E: "[",
    0x9F: "]",
    0xBA: "e",
    0xBB: "'d",
    0xBC: "'l",
    0xBD: "'s",
    0xBE: "'t",
    0xBF: "'v",
    0xE0: "'",
    0xE1: "PK",
    0xE2: "MN",
    0xE3: "-",
    0xE4: "'r",
    0xE5: "'m",
    0xE6: "?",
    0xE7: "!",
    0xE8: ".",
    0xE9: ".",
    0xEC: ">",
    0xED: ">",
    0xEE: "v",
    0xEF: "M",
    0xF0: "END",
    0xF1: "x",
    0xF2: ".",
    0xF3: "/",
    0xF4: ",",
    0xF5: "F",
}


def _species_name(species_id: int) -> str:
    return POKEMON_SPECIES_NAMES.get(species_id, f"UNKNOWN_{species_id:02X}")


def _move_name(move_id: int) -> str:
    return MOVE_NAMES.get(move_id, f"UNKNOWN_{move_id:02X}")


def _type_name(type_id: int) -> str:
    return POKEMON_TYPE_NAMES.get(type_id, f"TYPE_{type_id:02X}")


def _item_name(item_id: int) -> str:
    if 0xC4 <= item_id <= 0xC8:
        return f"HM{item_id - 0xC3:02d}"
    if 0xC9 <= item_id <= 0xFE:
        return f"TM{item_id - 0xC8:02d}"
    return ITEM_NAMES.get(item_id, f"UNKNOWN_{item_id:02X}")


def _status_name(status_value: int) -> str:
    if status_value & 0b111:
        return "Sleep"
    if status_value & 0b0100_0000:
        return "Paralysis"
    if status_value & 0b0010_0000:
        return "Freeze"
    if status_value & 0b0001_0000:
        return "Burn"
    if status_value & 0b0000_1000:
        return "Poison"
    return "OK"


def _bcd_to_int(values: list[int]) -> int:
    digits: list[str] = []
    for value in values:
        for nibble in ((value >> 4) & 0x0F, value & 0x0F):
            if nibble > 9:
                return 0
            digits.append(str(nibble))
    return int("".join(digits) or "0")


def _is_text_tile(value: int) -> bool:
    return (
        value == 0x4E
        or value == 0x54
        or value == 0x6D
        or 0x80 <= value <= 0x99
        or 0x9A <= value <= 0x9F
        or 0xA0 <= value <= 0xBF
        or 0xE0 <= value <= 0xFF
    )
