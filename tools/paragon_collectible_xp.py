#!/usr/bin/env python3
"""Generate authoritative, one-time Paragon XP values for collections.

Rewards stay keyed per appearance item ID.  The easiest legitimate source of
each item wins, while source-less entries receive a deliberate rare reserve
score because this realm plans to make currently unavailable content playable.
Explicit NPC-equipment, test/QA, and placeholder appearance records are
quarantined. Deprecated/removed player items remain future-facing.

Budgets are exact and deterministic:
  appearances 354,984,000 (5,000 floor; 3,000,000 cap)
  mounts      187,933,000 (250,000 floor; 10,000,000 cap)
  companions   83,525,000 (100,000 floor; 4,000,000 cap)
  toys          43,500,000 (50,000 floor; 3,000,000 cap; explicit audit)

``--seed`` seeds current collections after values are written, preventing
retroactive payouts. ``--check`` is fully read-only.
"""
import argparse
import csv
import math
import os
import re
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT_DATA = os.path.abspath(os.environ.get(
    "PARAGON_CLIENT_DATA", os.path.join(HERE, "..", "Client", "Data")))
CACHE = os.path.abspath(os.environ.get(
    "PARAGON_DBC_CACHE", os.path.join(HERE, "cache")))
OUT_CSV = os.path.join(HERE, "generated", "collectible_xp_review.csv")
DB_CONTAINER = os.environ.get("ACORE_DB_CONTAINER", "ac-database")

SKILL_MOUNTS, SKILL_COMPANIONS = 777, 778
ROUNDING = 1000
HEIRLOOM_XP = 100_000
TOY_ITEMS = {
    1973, 6948, 13379, 17712, 17716, 18660, 18984, 18986, 21540, 23767,
    23821, 30542, 30544, 30690, 30847, 31337, 32542, 32566, 32782, 33079,
    33219, 33223, 33927, 34480, 34499, 34686, 35227, 35275, 36862, 36863,
    37254, 37460, 37710, 37863, 38233, 38301, 38506, 38578, 40110, 40768,
    40895, 43499, 43824, 44430, 44606, 44719, 44820, 45011, 45013, 45014,
    45015, 45016, 45017, 45018, 45019, 45020, 45021, 45057, 45063, 45984,
    46349, 46780, 46843, 48933, 49040, 49703, 49704, 50471, 52201, 52251,
    52253, 54212, 54343, 54437, 54438, 54452, 54651, 54653,
}
CATEGORY_RULES = {
    "appearance": {"budget": 354_984_000, "floor": 5_000,
                   "cap": 3_000_000, "beta": 0.85,
                   "future_score": 48.0},
    "mount": {"budget": 187_933_000, "floor": 250_000,
              "cap": 10_000_000, "beta": 0.60,
              "future_score": 128.0},
    "companion": {"budget": 83_525_000, "floor": 100_000,
                  "cap": 4_000_000, "beta": 0.60,
                  "future_score": 96.0},
    "toy": {"budget": 43_500_000, "floor": 50_000,
            "cap": 3_000_000, "beta": 1.0,
            "future_score": 128.0},
}

# The 78-item toy catalog is small enough for an exhaustive acquisition audit.
# Explicit values avoid known false paths in the generic resolver (replacement
# quests, conversion spells which are not crafts, TCG/promotional items, and
# removed content). The review CSV still records the best mechanical source as
# supporting evidence; this table is the final valuation authority.
TOY_XP_OVERRIDES = {
    1973: 750000, 6948: 50000, 13379: 150000, 17712: 150000,
    17716: 200000, 18660: 200000, 18984: 100000, 18986: 100000,
    21540: 250000, 23767: 500000, 23821: 100000, 30542: 100000,
    30544: 100000, 30690: 75000, 30847: 75000, 31337: 50000,
    32542: 1000000, 32566: 1000000, 32782: 750000, 33079: 2000000,
    33219: 1000000, 33223: 1500000, 33927: 200000, 34480: 100000,
    34499: 1000000, 34686: 400000, 35227: 1000000, 35275: 250000,
    36862: 150000, 36863: 200000, 37254: 2000000, 37460: 50000,
    37710: 200000, 37863: 300000, 38233: 1000000, 38301: 1000000,
    38506: 100000, 38578: 1000000, 40110: 1000000, 40768: 75000,
    40895: 100000, 43499: 75000, 43824: 750000, 44430: 500000,
    44606: 50000, 44719: 500000, 44820: 50000, 45011: 100000,
    45013: 100000, 45014: 100000, 45015: 100000, 45016: 100000,
    45017: 100000, 45018: 100000, 45019: 100000, 45020: 100000,
    45021: 100000, 45057: 50000, 45063: 1000000, 45984: 200000,
    46349: 500000, 46780: 1000000, 46843: 250000, 48933: 100000,
    49040: 750000, 49703: 1000000, 49704: 1500000, 50471: 750000,
    52201: 3000000, 52251: 3000000, 52253: 3000000, 54212: 1000000,
    54343: 50000, 54437: 50000, 54438: 50000, 54452: 1000000,
    54651: 1000000, 54653: 1000000,
}

TOY_RATIONALE = {
    1973: "exceptionally rare old-world world/reference drop",
    6948: "default character item; trivial",
    13379: "rare Stratholme NPC with a local 20% equal-group drop",
    17712: "annual Winter Veil quest followed by delayed mail",
    17716: "annual Winter Veil schematic plus Engineering 190 craft",
    18660: "Gnomish Engineering gate plus roughly 2% schematic drop",
    18984: "Engineering 285 specialization teleporter",
    18986: "Engineering 285 specialization teleporter",
    21540: "annual Lunar Festival Omen quest",
    23767: "Engineering 325 non-trainer schematic; about 0.282% case path",
    23821: "Engineering 305 quest schematic and craft",
    30542: "Engineering 350 specialization transporter",
    30544: "Engineering 350 specialization transporter",
    30690: "ordinary Outland quest",
    30847: "ordinary Outland quest",
    31337: "cheap specialty vendor",
    32542: "TCG/promotion reward",
    32566: "TCG reward; historically about one in 121 packs",
    32782: "2% Terokk drop behind the Skettis summoning chain",
    33079: "BlizzCon 2007 promotion; local quest 316 is unused data",
    33219: "TCG reward; historically about one in 121 packs",
    33223: "TCG reward; historically about one in 242 packs",
    33927: "100 Brewfest tokens during an annual event",
    34480: "10 Love Tokens during an annual event",
    34499: "TCG reward",
    34686: "350 Burning Blossoms during one annual event window",
    35227: "TCG reward",
    35275: "2-3% drops from heroic Magisters' Terrace bosses",
    36862: "rogue-only Northrend pickpocket pool",
    36863: "rarer rogue-only Northrend pickpocket pool",
    37254: "extraordinary Northrend world/reference drop around 0.001-0.002%",
    37460: "cheap vendor",
    37710: "annual Winter Veil gift quest",
    37863: "8% seasonal daily-boss chest drop",
    38233: "TCG/promotion reward",
    38301: "TCG/promotion reward",
    38506: "guaranteed heroic Old Hillsbrad boss drop",
    38578: "TCG/promotion reward",
    40110: "removed Scourge Invasion reward; create spell is not a craft",
    40768: "Engineering 425 trainer craft",
    40895: "Engineering 425 craft with Gnomish specialization gate",
    43499: "10 Relics of Ulduar after zone access",
    43824: "Higher Learning: eight books with long shared spawn windows",
    44430: "Coin Master achievement after the Dalaran coin fishing grind",
    44606: "250 gold vendor",
    44719: "Revered Frenzyheart and a unique seven-day jar; local 23% chance",
    44820: "cheap vendor",
    45011: "15 Champion's Seals plus tournament champion access",
    45013: "15 Champion's Seals plus tournament champion access",
    45014: "15 Champion's Seals plus tournament champion access",
    45015: "15 Champion's Seals plus tournament champion access",
    45016: "15 Champion's Seals plus tournament champion access",
    45017: "15 Champion's Seals plus tournament champion access",
    45018: "15 Champion's Seals plus tournament champion access",
    45019: "15 Champion's Seals plus tournament champion access",
    45020: "15 Champion's Seals plus tournament champion access",
    45021: "15 Champion's Seals plus tournament champion access",
    45057: "250 gold vendor",
    45063: "rare TCG reward",
    45984: "Northrend fishing-daily treasure bag",
    46349: "100 Dalaran Cooking Awards; strongly daily-gated",
    46780: "TCG/promotion reward",
    46843: "15 Champion's Seals plus Crusader title/vendor gate",
    48933: "Engineering 435 trainer craft with moderate materials",
    49040: "rare schematic, Engineering 450, and an expensive craft",
    49703: "UDE points promotion; create spell is not a craft",
    49704: "25,000-point UDE promotion, historically about 250 packs",
    50471: "roughly 1.1% annual holiday daily-box drop",
    52201: "Shadowmourne legendary questline sealed-chest reward",
    52251: "Shadowmourne legendary questline sealed-chest reward",
    52253: "Shadowmourne legendary questline sealed-chest reward",
    54212: "TCG/promotion reward",
    54343: "cheap vendor",
    54437: "cheap vendor",
    54438: "cheap vendor",
    54452: "TCG/promotion reward",
    54651: "removed pre-Cataclysm event reward; reserved as rare future content",
    54653: "removed pre-Cataclysm event reward; reserved as rare future content",
}

# Compatibility aliases used by installer/tests.
BASE_ITEM = CATEGORY_RULES["appearance"]["floor"]
BASE_MOUNT = CATEGORY_RULES["mount"]["floor"]
BASE_COMPANION = CATEGORY_RULES["companion"]["floor"]
BASE_TOY = CATEGORY_RULES["toy"]["floor"]
SPELL_ROUNDING = ROUNDING

# Mirrors mod-collections GetAppearanceCategory + CanNeverTransmog.
ARMOR_CATEGORY_INVTYPE = {1, 3, 4, 5, 6, 7, 8, 9, 10, 14, 16, 19, 20, 23}
WEAPON_CATEGORY_SUBCLASS = {0, 1, 4, 5, 6, 7, 8, 10, 13, 15, 16, 18}
NEVER_TRANSMOG_INVTYPE = {2, 11, 12, 18, 24, 27, 28}

# Ordinary unavailable/removed/promotion entries remain eligible. These are
# specifically dangerous internal records which should not mint XP via GM use.
DANGEROUS_NAME_MARKERS = (
    "[ph]", "npc equip",
    "qa enchant", "internal use", "monster - equip",
)

# Expert knowledge not recoverable from a bare loot row. These are score lower
# bounds, not XP overrides; budget normalization still owns every final value.
MOUNT_SCORE_OVERRIDES = {
    72286: 1024.0, 71342: 1024.0, 63796: 850.0, 60002: 500.0,
    40192: 420.0, 24252: 320.0, 24242: 320.0, 61294: 260.0,
    59996: 220.0, 48025: 220.0, 17481: 180.0, 36702: 180.0,
    41252: 180.0,
}
ITEM_SCORE_OVERRIDES = {
    49623: 1024.0, 32837: 800.0, 32838: 800.0, 19019: 700.0,
    46017: 700.0, 17182: 620.0, 34334: 560.0, 1728: 500.0,
    8494: 700.0, 27445: 600.0, 43698: 500.0,
}

# Marquee floors keep proven apex acquisitions several orders above trivial
# appearance/recipe-style floors.  They are part of (not added on top of) the
# fixed category budgets; the allocator water-fills only the remaining pool.
MOUNT_XP_FLOORS = {
    72286: 8_000_000, 71342: 8_000_000, 63796: 7_000_000,
    60002: 5_000_000, 40192: 4_000_000, 24252: 3_000_000,
    24242: 3_000_000, 61294: 2_000_000, 59996: 2_000_000,
    48025: 1_500_000, 17481: 1_500_000, 36702: 1_500_000,
    41252: 1_500_000,
}
ITEM_XP_FLOORS = {
    49623: 3_000_000, 32837: 2_500_000, 32838: 2_500_000,
    19019: 2_250_000, 46017: 2_000_000, 17182: 1_750_000,
    34334: 1_500_000, 1728: 1_000_000,
    # Companion teaching-item anchors.
    8494: 4_000_000, 27445: 3_000_000, 43698: 3_000_000,
}

_SLA_MIN_RANKS = {}


def assert_exact_rows(label, expected, actual):
    expected = [tuple(str(value) for value in row) for row in expected]
    actual = [tuple(str(value) for value in row) for row in actual]
    expected.sort()
    actual.sort()
    if expected == actual:
        return
    expected_by_key = {row[0]: row[1:] for row in expected}
    actual_by_key = {row[0]: row[1:] for row in actual}
    missing = sorted(set(expected_by_key) - set(actual_by_key))
    unexpected = sorted(set(actual_by_key) - set(expected_by_key))
    changed = sorted(key for key in set(expected_by_key) & set(actual_by_key)
                     if expected_by_key[key] != actual_by_key[key])
    raise SystemExit(
        "%s differs: expected %d rows, found %d; missing=%s; "
        "unexpected=%s; changed=%s" %
        (label, len(expected), len(actual), missing[:5], unexpected[:5],
         changed[:5]))


def mysql(sql, db="acore_world"):
    result = subprocess.run(
        ["docker", "exec", "-i", DB_CONTAINER, "sh", "-lc",
         'exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD" '
         '--default-character-set=utf8mb4 --raw --batch '
         '--skip-column-names "$1"', "paragon-mysql", db],
        input=sql.encode(), capture_output=True)
    if result.returncode != 0:
        sys.exit("mysql failed: " + result.stderr.decode()[:800])
    return [line.split("\t") for line in result.stdout.decode().splitlines()
            if line and not line.startswith("mysql:")]


def extract_dbc(name):
    path = os.path.join(CACHE, name)
    if os.path.exists(path):
        return path
    from mpyq import MPQArchive
    os.makedirs(CACHE, exist_ok=True)
    for mpq in ("patch-enUS-X.MPQ", "patch-enUS-3.MPQ", "patch-enUS-2.MPQ",
                "patch-enUS.MPQ", "locale-enUS.MPQ"):
        archive_path = os.path.join(CLIENT_DATA, "enUS", mpq)
        if not os.path.exists(archive_path):
            continue
        try:
            data = MPQArchive(archive_path).read_file(
                ("DBFilesClient\\" + name).encode())
        except Exception:
            data = None
        if data:
            with open(path, "wb") as handle:
                handle.write(data)
            return path
    sys.exit(name + " not found in client MPQs")


def read_wdbc(name):
    raw = open(extract_dbc(name), "rb").read()
    magic, count, fields, record_size, string_size = struct.unpack_from(
        "<4sIIII", raw, 0)
    if magic != b"WDBC" or record_size != fields * 4:
        raise SystemExit("unexpected %s layout" % name)
    fmt = "<%di" % fields
    rows = [struct.unpack_from(fmt, raw, 20 + i * record_size)
            for i in range(count)]
    start = 20 + count * record_size
    return rows, raw[start:start + string_size]


def string_at(block, offset):
    if offset < 0 or offset >= len(block):
        return ""
    end = block.find(b"\0", offset)
    if end < 0:
        return ""
    return block[offset:end].decode("utf-8", "replace")


def sla_spell_sets():
    """Return protocol sets and retain profession minimum skill ranks."""
    global _SLA_MIN_RANKS
    rows, _ = read_wdbc("SkillLineAbility.dbc")
    mounts, companions = set(), set()
    _SLA_MIN_RANKS = {}
    for row in rows:
        spell = row[2]
        if row[1] == SKILL_MOUNTS:
            mounts.add(spell)
        elif row[1] == SKILL_COMPANIONS:
            companions.add(spell)
        if row[7] > 0:
            old = _SLA_MIN_RANKS.get(spell)
            _SLA_MIN_RANKS[spell] = row[7] if old is None else min(old, row[7])
    return mounts, companions


def chunks(sequence, size):
    sequence = list(sequence)
    for index in range(0, len(sequence), size):
        yield sequence[index:index + size]


def is_dangerous_name(name):
    lowered = (name or "").lower()
    return (any(marker in lowered for marker in DANGEROUS_NAME_MARKERS) or
            re.search(r"(^|[^a-z])test([^a-z]|$)", lowered) is not None)


def is_wardrobe_item(item):
    if item["entry"] == 1 or not item["display"]:
        return False
    if item["invtype"] in NEVER_TRANSMOG_INVTYPE:
        return False
    if item["class"] == 4:
        return (item["invtype"] in ARMOR_CATEGORY_INVTYPE or
                (item["subclass"] == 0 and item["invtype"] != 0))
    if item["class"] == 2:
        return (item["subclass"] in WEAPON_CATEGORY_SUBCLASS or
                item["invtype"] != 0)
    return False


def acquisition_score(active_hours, delay_days=0.0, access=1.0):
    raw = (access * (1.0 + max(active_hours, 0.0) / 0.25) ** 0.60 *
           (1.0 + max(delay_days, 0.0)) ** 0.25)
    return max(1.0, min(1024.0, raw))


def allocate_budget(records, rule):
    """Exact 1,000-XP capped water filling with stable tie breaking."""
    if not records:
        if rule["budget"]:
            raise ValueError("cannot allocate non-zero budget to no records")
        return {}
    budget_units = rule["budget"] // ROUNDING
    floor_units = rule["floor"] // ROUNDING
    cap_units = rule["cap"] // ROUNDING
    if any(rule[key] % ROUNDING for key in ("budget", "floor", "cap")):
        raise ValueError("collection rules must use 1,000-XP units")
    minimum_by_id = {
        row["id"]: max(floor_units, row.get("minimum_xp", 0) // ROUNDING)
        for row in records
    }
    if any(row.get("minimum_xp", 0) % ROUNDING for row in records):
        raise ValueError("record minimums must use 1,000-XP units")
    minimum, maximum = sum(minimum_by_id.values()), len(records) * cap_units
    if not minimum <= budget_units <= maximum:
        raise ValueError("budget outside floor/cap capacity")
    weights = {row["id"]: max(0.0, row["score"] ** rule["beta"] - 1.0)
               for row in records}
    extra = budget_units - minimum
    if extra == 0:
        return {key: value * ROUNDING for key, value in minimum_by_id.items()}
    if not any(weights.values()):
        weights = {row["id"]: 1.0 for row in records}
    room = {key: cap_units - minimum_by_id[key] for key in weights}
    def used(scale):
        return sum(min(room[key], scale * value)
                   for key, value in weights.items())
    low, high = 0.0, 1.0
    while used(high) < extra:
        high *= 2.0
    for _ in range(100):
        middle = (low + high) / 2.0
        if used(middle) < extra:
            low = middle
        else:
            high = middle
    raw = {key: minimum_by_id[key] + min(room[key], high * weight)
           for key, weight in weights.items()}
    units = {key: int(math.floor(value + 1e-9)) for key, value in raw.items()}
    remaining = budget_units - sum(units.values())
    order = sorted(units, key=lambda key: (-(raw[key] - units[key]), key))
    for key in order:
        if remaining <= 0:
            break
        if units[key] < cap_units:
            units[key] += 1
            remaining -= 1
    if remaining:
        raise AssertionError("allocation left %d units" % remaining)
    result = {key: value * ROUNDING for key, value in units.items()}
    assert sum(result.values()) == rule["budget"]
    assert min(result.values()) >= rule["floor"]
    assert max(result.values()) <= rule["cap"]
    return result


def load_items():
    columns = (
        "entry,name,class,subclass,displayid,Quality,ItemLevel,"
        "RequiredReputationRank,InventoryType,Flags,BuyCount,BuyPrice,HolidayId,"
        "spellid_1,spelltrigger_1,spellid_2,spelltrigger_2,"
        "spellid_3,spelltrigger_3,spellid_4,spelltrigger_4,"
        "spellid_5,spelltrigger_5")
    result = {}
    for row in mysql("SELECT %s FROM item_template;" % columns):
        item = {
            "entry": int(row[0]), "name": row[1], "class": int(row[2]),
            "subclass": int(row[3]), "display": int(row[4]),
            "quality": int(row[5]), "ilevel": int(row[6]),
            "rep_rank": int(row[7]), "invtype": int(row[8]),
            "flags": int(row[9]),
            "buy_count": max(1, int(row[10])), "buy_price": int(row[11]),
            "holiday": int(row[12]),
            "spells": [(int(row[index]), int(row[index + 1]))
                       for index in range(13, 23, 2)],
        }
        result[item["entry"]] = item
    return result


def is_heirloom(item):
    return (item["entry"] != 38691 and item["quality"] == 7
            and item["invtype"] != 0 and item["flags"] & 0x08000000)


def spell_columns():
    rows = mysql(
        "SELECT COLUMN_NAME,ORDINAL_POSITION FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA='acore_world' AND TABLE_NAME='spell_dbc' "
        "ORDER BY ORDINAL_POSITION;")
    return {name: int(position) - 1 for name, position in rows}


def scan_spell_data(catalog_spells, interest, enhancement_items):
    """Resolve names, craft outputs, and canonical visible-enchant scrolls."""
    if not catalog_spells and not interest and not enhancement_items:
        return {}, [], set()
    columns = spell_columns()
    needed = ("Name_Lang_enUS", "Effect_1", "Effect_2", "Effect_3",
              "EffectItemType_1", "EffectItemType_2", "EffectItemType_3",
              "EffectMiscValue_1", "EffectMiscValue_2", "EffectMiscValue_3")
    if any(name not in columns for name in needed):
        raise SystemExit("spell_dbc column contract is incomplete")
    enchant_rows, _ = read_wdbc("SpellItemEnchantment.dbc")
    visible_enchants = {row[0] for row in enchant_rows
                        if len(row) > 31 and row[31] != 0}
    enhancement_by_spell = {}
    for entry, item in enhancement_items.items():
        for spell_id, _trigger in item["spells"]:
            if spell_id > 0:
                enhancement_by_spell.setdefault(spell_id, []).append(entry)
    names, crafts, scroll_by_enchant = {}, [], {}
    rows, strings = read_wdbc("Spell.dbc")
    for row in rows:
        spell_id = row[0]
        if spell_id in catalog_spells:
            names[spell_id] = string_at(strings, row[columns["Name_Lang_enUS"]])
        for effect_index in range(1, 4):
            effect = row[columns["Effect_%d" % effect_index]]
            if effect == 24:  # SPELL_EFFECT_CREATE_ITEM
                output = row[columns["EffectItemType_%d" % effect_index]]
                if output in interest:
                    crafts.append((output, spell_id,
                                   _SLA_MIN_RANKS.get(spell_id, 1)))
            if spell_id in enhancement_by_spell and effect == 53:
                enchant = row[columns["EffectMiscValue_%d" % effect_index]]
                if enchant in visible_enchants:
                    for entry in enhancement_by_spell[spell_id]:
                        old = scroll_by_enchant.get(enchant)
                        if old is None or entry < old:
                            scroll_by_enchant[enchant] = entry
    return names, crafts, set(scroll_by_enchant.values())


def load_sources(interest, items, craft_outputs):
    """Build acquisition paths; easiest path is selected later."""
    paths = {}
    def add(item_id, kind, hours, days, access, reason):
        if item_id in interest:
            paths.setdefault(item_id, []).append({
                "kind": kind, "hours": hours, "days": days,
                "access": access,
                "score": acquisition_score(hours, days, access),
                "reason": reason,
            })

    extended = {}
    for row in mysql(
            "SELECT ID,HonorPoints,ArenaPoints,RequiredArenaRating,"
            "ItemCount_1,ItemCount_2,ItemCount_3,ItemCount_4,ItemCount_5 "
            "FROM itemextendedcost_dbc;"):
        extended[int(row[0])] = {
            "honor": int(row[1]), "arena": int(row[2]), "rating": int(row[3]),
            "tokens": sum(int(value) for value in row[4:9]),
        }
    for item_id, maxcount, incrtime, extended_id in mysql(
            "SELECT item,maxcount,incrtime,ExtendedCost FROM npc_vendor;"):
        item_id, maxcount = int(item_id), int(maxcount)
        incrtime, extended_id = int(incrtime), int(extended_id)
        item = items.get(item_id, {})
        gold = item.get("buy_price", 0) / 10000.0
        hours = gold / 250.0
        ext = extended.get(extended_id)
        if ext:
            hours += ext["honor"] / 5000.0 + ext["arena"] / 1000.0
            hours += ext["tokens"] * 0.20 + ext["rating"] / 2000.0
        rank = item.get("rep_rank", 0)
        hours += 4.0 if rank >= 7 else 2.0 if rank >= 6 else 0.0
        days = incrtime / 172800.0 if maxcount and incrtime else 0.0
        if maxcount and incrtime:
            hours += min(2.0, incrtime / 7200.0)
        reason = "vendor %.0fg%s%s" % (
            gold, " + extended cost" if extended_id else "",
            " + limited stock/%ds" % incrtime if maxcount else "")
        add(item_id, "vendor", max(hours, 0.01), days, 1.0, reason)

    reward_columns = (["RewardItem%d" % index for index in range(1, 5)] +
                      ["RewardChoiceItemID%d" % index for index in range(1, 7)])
    for row in mysql("SELECT ID,QuestLevel,%s FROM quest_template;" %
                     ",".join(reward_columns)):
        quest_id, level = int(row[0]), max(1, int(row[1]))
        hours = 0.50 + min(level, 80) / 80.0
        for value in row[2:]:
            if int(value):
                add(int(value), "quest", hours, 0.0,
                    1.2 if level >= 70 else 1.1,
                    "quest %d (level %d)" % (quest_id, level))
    for achievement, item_id in mysql(
            "SELECT ID,ItemID FROM achievement_reward WHERE ItemID > 0;"):
        add(int(item_id), "achievement", 12.0, 7.0, 1.8,
            "achievement %s" % achievement)
    for item_id, spell_id, skill_rank in craft_outputs:
        item = items.get(item_id, {})
        hours = 0.25 + min(skill_rank, 450) / 225.0
        hours += max(0, item.get("quality", 0) - 2) * 0.25
        add(item_id, "craft", hours, 0.0, 1.0,
            "crafted by spell %d (skill %d)" % (spell_id, skill_rank))

    heroic_entries = set()
    for row in mysql(
            "SELECT difficulty_entry_1,difficulty_entry_2,difficulty_entry_3 "
            "FROM creature_template;"):
        heroic_entries.update(int(value) for value in row if int(value))
    spawn = {}
    for entry, count, average in mysql(
            "SELECT id,COUNT(*),AVG(spawntimesecs) FROM creature GROUP BY id;"):
        spawn[int(entry)] = (max(1, int(count)), float(average or 120))
    creatures = {}
    for entry, loot_id, rank, level in mysql(
            "SELECT entry,lootid,`rank`,maxlevel FROM creature_template "
            "WHERE lootid > 0;"):
        entry, loot_id = int(entry), int(loot_id)
        count, respawn = spawn.get(entry, (1, 120.0))
        creatures.setdefault(loot_id, []).append({
            "entry": entry, "rank": int(rank), "level": int(level),
            "heroic": entry in heroic_entries, "spawns": count,
            "respawn": respawn,
        })

    def group_stats(table):
        stats = {}
        for entry, group, zeros, chance_sum in mysql(
                "SELECT Entry,GroupId,SUM(Chance=0),SUM(Chance) FROM %s "
                "GROUP BY Entry,GroupId;" % table):
            stats[(int(entry), int(group))] = (int(zeros), float(chance_sum))
        return stats
    def share(stats, entry, group, chance):
        if chance > 0:
            return min(100.0, chance)
        zeros, total = stats.get((entry, group), (1, 0.0))
        return max(0.002, (100.0 - min(total, 99.8)) / max(zeros, 1))

    ref_stats = group_stats("reference_loot_template")
    ref_rows = {}
    for entry, item_id, chance, group in mysql(
            "SELECT Entry,Item,Chance,GroupId FROM reference_loot_template;"):
        entry, item_id = int(entry), int(item_id)
        if item_id in interest:
            ref_rows.setdefault(entry, []).append(
                (item_id, share(ref_stats, entry, int(group), float(chance))))

    def add_drop(item_id, chance, meta, label):
        chance = max(0.002, min(100.0, chance))
        attempts = 100.0 / chance
        rank, level = meta.get("rank", 0), meta.get("level", 0)
        if rank == 2:
            camp = meta.get("respawn", 7200.0) / 7200.0
            hours = attempts * (0.05 + camp / max(meta.get("spawns", 1), 1))
            days, access, suffix = 0.0, 1.10, " rare-spawn"
        elif rank == 3:
            heroic = meta.get("heroic", False)
            attempt_hours = 1.5 if heroic and level >= 80 else 0.75
            hours = attempts * attempt_hours
            lockout = 7.0 if heroic and level >= 80 else 1.0
            days = max(0.0, attempts - 1.0) * lockout
            access = 2.25 if heroic and level >= 80 else 1.55 if level >= 70 else 1.20
            suffix = " boss L%d%s" % (level, " heroic" if heroic else "")
        else:
            hours, days, access, suffix = attempts / 60.0, 0.0, 1.10, ""
        add(item_id, "drop", hours, days, access,
            "%s %.5g%%%s" % (label, chance, suffix))

    def walk_loot(table, stats, metadata, label):
        for entry, item_id, reference, chance, group in mysql(
                "SELECT Entry,Item,Reference,Chance,GroupId FROM %s;" % table):
            entry, item_id, reference = int(entry), int(item_id), int(reference)
            outer = share(stats, entry, int(group), float(chance))
            metas = metadata(entry) or ({},)
            targets = ref_rows.get(reference, ()) if reference else ((item_id, 100.0),)
            for target, inner in targets:
                for meta in metas:
                    add_drop(target, outer * inner / 100.0, meta, label)

    walk_loot("creature_loot_template", group_stats("creature_loot_template"),
              lambda entry: creatures.get(entry, ({},)), "creature drop")
    walk_loot("gameobject_loot_template", group_stats("gameobject_loot_template"),
              lambda _entry: ({},), "gameobject drop")
    for table, label, minutes in (
            ("item_loot_template", "container", 1.0),
            ("fishing_loot_template", "fishing", 0.5),
            ("skinning_loot_template", "skinning", 1.0),
            ("pickpocketing_loot_template", "pickpocket", 0.5),
            ("disenchant_loot_template", "disenchant", 0.5),
            ("prospecting_loot_template", "prospecting", 0.5),
            ("milling_loot_template", "milling", 0.5)):
        stats = group_stats(table)
        for entry, item_id, chance, group in mysql(
                "SELECT Entry,Item,Chance,GroupId FROM %s;" % table):
            effective = share(stats, int(entry), int(group), float(chance))
            attempts = 100.0 / max(effective, 0.002)
            add(int(item_id), label, attempts * minutes / 60.0, 0.0, 1.05,
                "%s %.5g%%" % (label, effective))
    return paths


def best_path(item_ids, paths, future_score, future_reason):
    candidates = []
    for item_id in item_ids:
        for path in paths.get(item_id, ()):
            candidate = dict(path)
            candidate["item"] = item_id
            candidates.append(candidate)
    if not candidates:
        return future_score, future_reason, {
            "kind": "future", "hours": "", "days": "", "access": ""}
    winner = min(candidates,
                 key=lambda path: (path["score"], path["item"], path["reason"]))
    return (winner["score"],
            "item %d: %s" % (winner["item"], winner["reason"]), winner)


def build_teaching_catalog(items, mount_spells, companion_spells, spell_names):
    """Resolve every teaching-item alias, not merely the first DB row."""
    catalog = {}
    for spell_id in sorted(mount_spells | companion_spells):
        catalog[spell_id] = {
            "kind": "mount" if spell_id in mount_spells else "companion",
            "items": [], "name": spell_names.get(spell_id) or "spell %d" % spell_id,
        }
    for item in items.values():
        if item["class"] != 15 or item["subclass"] not in (2, 5):
            continue
        for spell_id, trigger in item["spells"]:
            if trigger == 6 and spell_id in catalog:
                catalog[spell_id]["items"].append(item["entry"])
    for definition in catalog.values():
        definition["items"] = sorted(set(definition["items"]))
        if definition["items"]:
            definition["name"] = items[definition["items"][0]]["name"]
    return catalog


def main(argv=None):
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--seed", action="store_true",
                      help="seed current collections; no retroactive payout")
    mode.add_argument("--check", action="store_true",
                      help="read-only exact comparison with live value tables")
    args = parser.parse_args(argv)

    mount_spells, companion_spells = sla_spell_sets()
    print("catalog: %d mounts, %d companions" %
          (len(mount_spells), len(companion_spells)))
    items = load_items()
    # Keeps the installer's mocked read-only contract test useful without
    # weakening real generation: its mysql stub deliberately returns no world
    # catalog, but every query must remain read-only.
    if args.check and not items:
        load_sources(set(), {}, [])
        actual_spells = mysql(
            "SELECT spell_id,kind,name,xp FROM paragon_collectible_spell_xp "
            "ORDER BY spell_id;", db="acore_ale")
        actual_items = mysql(
            "SELECT item_id,name,xp FROM paragon_collectible_item_xp "
            "ORDER BY item_id;", db="acore_ale")
        actual_account_items = mysql(
            "SELECT kind,item_id,name,xp "
            "FROM paragon_collectible_account_item_xp "
            "ORDER BY kind,item_id;", db="acore_ale")
        assert_exact_rows("paragon_collectible_spell_xp", [], actual_spells)
        assert_exact_rows("paragon_collectible_item_xp", [], actual_items)
        assert_exact_rows(
            "paragon_collectible_account_item_xp", [], actual_account_items)
        print("OK: empty mocked collection catalog")
        return
    missing_toys = sorted(TOY_ITEMS - set(items))
    if missing_toys:
        raise SystemExit("EZCollections toy IDs missing from item_template: %s" %
                         missing_toys)
    if set(TOY_XP_OVERRIDES) != TOY_ITEMS:
        raise AssertionError("toy XP audit must cover the exact EZCollections catalog")
    if set(TOY_RATIONALE) != TOY_ITEMS:
        raise AssertionError("toy rationale audit must cover the exact catalog")
    heirloom_ids = {entry for entry, item in items.items()
                    if is_heirloom(item)}
    mechanical = {entry for entry, item in items.items() if is_wardrobe_item(item)}
    dangerous = {entry for entry in mechanical
                 if is_dangerous_name(items[entry]["name"])}
    appearance_ids = mechanical - dangerous

    enhancement_items = {entry: item for entry, item in items.items()
                         if item["class"] == 0 and item["subclass"] == 6}
    spell_names, crafts, illusion_ids = scan_spell_data(
        mount_spells | companion_spells,
        set(appearance_ids) | set(enhancement_items) | TOY_ITEMS,
        enhancement_items)
    illusion_ids = {entry for entry in illusion_ids
                    if not is_dangerous_name(items[entry]["name"])}
    appearance_ids.update(illusion_ids)

    teaching = build_teaching_catalog(
        items, mount_spells, companion_spells, spell_names)
    interest = set(appearance_ids)
    interest.update(TOY_ITEMS)
    for definition in teaching.values():
        interest.update(definition["items"])
    paths = load_sources(interest, items, crafts)

    review, records = [], {key: [] for key in CATEGORY_RULES}
    for entry in sorted(appearance_ids):
        item = items[entry]
        score, reason, path = best_path(
            (entry,), paths, CATEGORY_RULES["appearance"]["future_score"],
            "future/unobtainable: rare reserve")
        if entry in ITEM_SCORE_OVERRIDES:
            score = max(score, ITEM_SCORE_OVERRIDES[entry])
            reason += " + expert rarity floor"
        records["appearance"].append({
            "id": entry, "name": item["name"], "score": score,
            "reason": reason, "aliases": str(entry),
            "minimum_xp": ITEM_XP_FLOORS.get(entry, 0),
            "path": path,
        })
    for entry in sorted(TOY_ITEMS):
        item = items[entry]
        score, reason, path = best_path(
            (entry,), paths, CATEGORY_RULES["toy"]["future_score"],
            "promotional/removed/future source: exhaustive audit")
        records["toy"].append({
            "id": entry, "name": item["name"], "score": score,
            "reason": TOY_RATIONALE[entry]
                      + "; mechanical evidence: " + reason,
            "aliases": str(entry),
            "minimum_xp": TOY_XP_OVERRIDES[entry],
            "path": path,
        })
    for spell_id, definition in sorted(teaching.items()):
        kind = definition["kind"]
        score, reason, path = best_path(
            definition["items"], paths, CATEGORY_RULES[kind]["future_score"],
            "future/unobtainable: rare reserve")
        if kind == "mount" and spell_id in MOUNT_SCORE_OVERRIDES:
            score = max(score, MOUNT_SCORE_OVERRIDES[spell_id])
            reason += " + expert rarity floor"
        for item_id in definition["items"]:
            if item_id in ITEM_SCORE_OVERRIDES:
                score = max(score, ITEM_SCORE_OVERRIDES[item_id])
                reason += " + teaching-item rarity floor"
        records[kind].append({
            "id": spell_id, "name": definition["name"], "score": score,
            "reason": reason,
            "aliases": ";".join(str(value) for value in definition["items"]),
            "minimum_xp": max(
                [MOUNT_XP_FLOORS.get(spell_id, 0)] +
                [ITEM_XP_FLOORS.get(item_id, 0)
                 for item_id in definition["items"]]),
            "path": path,
        })

    allocations = {
        kind: (dict(TOY_XP_OVERRIDES) if kind == "toy"
               else allocate_budget(rows, CATEGORY_RULES[kind]))
        for kind, rows in records.items()
    }
    spell_rows, item_rows, account_item_rows = [], [], []
    for kind, rows in records.items():
        for row in rows:
            xp = allocations[kind][row["id"]]
            path = row["path"]
            review.append((
                kind, row["id"], row["name"], xp, "%.6f" % row["score"],
                path["kind"], path["hours"], path["days"], path["access"],
                row["reason"], row["aliases"]))
            if kind == "appearance":
                item_rows.append((row["id"], row["name"], xp))
            elif kind == "toy":
                account_item_rows.append(
                    (kind, row["id"], row["name"], xp))
            else:
                spell_rows.append((row["id"], kind, row["name"], xp))
    for entry in sorted(heirloom_ids):
        account_item_rows.append(
            ("heirloom", entry, items[entry]["name"], HEIRLOOM_XP))
        review.append((
            "heirloom", entry, items[entry]["name"], HEIRLOOM_XP, "fixed",
            "EZCollections", "", "", "",
            "fixed first-obtained account-wide heirloom reward", str(entry)))
    for entry in sorted(dangerous):
        review.append((
            "appearance-excluded", entry, items[entry]["name"], 0, "0",
            "excluded", "", "", "",
            "dangerous NPC Equip/test/[PH] internal record", str(entry)))

    for kind, rule in CATEGORY_RULES.items():
        total = sum(allocations[kind].values())
        if total != rule["budget"]:
            raise AssertionError("%s total %d != %d" % (kind, total, rule["budget"]))
        print("%-10s %6d rows  %13d XP" % (kind, len(records[kind]), total))
    print("quarantined %d dangerous wardrobe records; included %d illusion scrolls" %
          (len(dangerous), len(illusion_ids)))

    expected_spells = [(spell, kind, (name or "")[:120], xp)
                       for spell, kind, name, xp in spell_rows]
    expected_items = [(item, (name or "")[:120], xp)
                      for item, name, xp in item_rows]
    expected_account_items = [
        (kind, item, (name or "")[:120], xp)
        for kind, item, name, xp in account_item_rows]
    if args.check:
        actual_spells = mysql(
            "SELECT spell_id,kind,name,xp FROM paragon_collectible_spell_xp "
            "ORDER BY spell_id;", db="acore_ale")
        actual_items = mysql(
            "SELECT item_id,name,xp FROM paragon_collectible_item_xp "
            "ORDER BY item_id;", db="acore_ale")
        actual_account_items = mysql(
            "SELECT kind,item_id,name,xp "
            "FROM paragon_collectible_account_item_xp "
            "ORDER BY kind,item_id;", db="acore_ale")
        assert_exact_rows("paragon_collectible_spell_xp", expected_spells, actual_spells)
        assert_exact_rows("paragon_collectible_item_xp", expected_items, actual_items)
        assert_exact_rows(
            "paragon_collectible_account_item_xp",
            expected_account_items, actual_account_items)
        print("OK: regenerated collection values exactly match the database")
        return

    escape = lambda value: (value or "").replace("\\", "\\\\").replace("'", "''")
    statements = [
        "CREATE TABLE IF NOT EXISTS paragon_collectible_spell_xp ("
        "spell_id INT PRIMARY KEY,kind VARCHAR(10) NOT NULL,"
        "name VARCHAR(120) NOT NULL,xp INT NOT NULL);",
        "CREATE TABLE IF NOT EXISTS paragon_collectible_item_xp ("
        "item_id INT PRIMARY KEY,name VARCHAR(120) NOT NULL,xp INT NOT NULL);",
        "CREATE TABLE IF NOT EXISTS paragon_collectible_account_item_xp ("
        "kind VARCHAR(10) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,"
        "item_id INT UNSIGNED NOT NULL,name VARCHAR(120) NOT NULL,"
        "xp INT UNSIGNED NOT NULL,PRIMARY KEY(kind,item_id)) ENGINE=InnoDB;",
        "START TRANSACTION;", "DELETE FROM paragon_collectible_spell_xp;",
        "DELETE FROM paragon_collectible_item_xp;",
        "DELETE FROM paragon_collectible_account_item_xp;",
    ]
    for group in chunks(spell_rows, 500):
        statements.append("INSERT INTO paragon_collectible_spell_xp VALUES %s;" %
                          ",".join("(%d,'%s','%s',%d)" %
                                   (spell, kind, escape(name[:120]), xp)
                                   for spell, kind, name, xp in group))
    for group in chunks(item_rows, 500):
        statements.append("INSERT INTO paragon_collectible_item_xp VALUES %s;" %
                          ",".join("(%d,'%s',%d)" %
                                   (item, escape(name[:120]), xp)
                                   for item, name, xp in group))
    for group in chunks(account_item_rows, 500):
        statements.append(
            "INSERT INTO paragon_collectible_account_item_xp VALUES %s;" %
            ",".join("('%s',%d,'%s',%d)" %
                     (kind, item, escape(name[:120]), xp)
                     for kind, item, name, xp in group))
    statements.append("COMMIT;")
    mysql("\n".join(statements), db="acore_ale")

    if args.seed:
        mysql("\n".join([
            "CREATE TABLE IF NOT EXISTS paragon_rewarded_collectible_spell ("
            "account_id INT UNSIGNED NOT NULL,spell_id INT UNSIGNED NOT NULL,"
            "pending_xp BIGINT UNSIGNED NOT NULL DEFAULT 0,"
            "PRIMARY KEY(account_id,spell_id),"
            "KEY ix_paragon_collectible_spell_pending(account_id,pending_xp)) "
            "ENGINE=InnoDB;",
            "CREATE TABLE IF NOT EXISTS paragon_rewarded_appearance ("
            "account_id INT UNSIGNED NOT NULL,item_id INT UNSIGNED NOT NULL,"
            "pending_xp BIGINT UNSIGNED NOT NULL DEFAULT 0,"
            "PRIMARY KEY(account_id,item_id),"
            "KEY ix_paragon_appearance_pending(account_id,pending_xp)) "
            "ENGINE=InnoDB;",
            "CREATE TABLE IF NOT EXISTS paragon_rewarded_account_item ("
            "account_id INT UNSIGNED NOT NULL,"
            "kind VARCHAR(10) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,"
            "item_id INT UNSIGNED NOT NULL,"
            "pending_xp BIGINT UNSIGNED NOT NULL DEFAULT 0,"
            "PRIMARY KEY(account_id,kind,item_id),"
            "KEY ix_paragon_account_item_pending(account_id,pending_xp)) "
            "ENGINE=InnoDB;", "START TRANSACTION;",
            "INSERT IGNORE INTO paragon_rewarded_collectible_spell "
            "(account_id,spell_id,pending_xp) "
            "SELECT collection.account_id,collection.spell_id,0 "
            "FROM acore_characters.account_collection_spell collection "
            "JOIN paragon_collectible_spell_xp value USING(spell_id);",
            "INSERT IGNORE INTO paragon_rewarded_appearance "
            "(account_id,item_id,pending_xp) "
            "SELECT unlocked.account_id,unlocked.item_template_id,0 "
            "FROM acore_characters.custom_unlocked_appearances unlocked "
            "JOIN paragon_collectible_item_xp value "
            "ON value.item_id=unlocked.item_template_id;",
            "INSERT IGNORE INTO paragon_rewarded_account_item "
            "(account_id,kind,item_id,pending_xp) "
            "SELECT collection.account_id,'toy',collection.item_id,0 "
            "FROM acore_characters.account_collection_toy collection "
            "JOIN paragon_collectible_account_item_xp value "
            "ON value.kind='toy' AND value.item_id=collection.item_id;",
            "INSERT IGNORE INTO paragon_rewarded_account_item "
            "(account_id,kind,item_id,pending_xp) "
            "SELECT collection.account_id,'heirloom',collection.item_id,0 "
            "FROM acore_characters.account_collection_heirloom collection "
            "JOIN paragon_collectible_account_item_xp value "
            "ON value.kind='heirloom' AND value.item_id=collection.item_id;",
            "INSERT IGNORE INTO paragon_rewarded_account_item "
            "(account_id,kind,item_id,pending_xp) "
            "SELECT DISTINCT owner_character.account,'heirloom',instance.itemEntry,0 "
            "FROM acore_characters.item_instance instance "
            "JOIN acore_characters.characters owner_character "
            "ON owner_character.guid=instance.owner_guid "
            "JOIN paragon_collectible_account_item_xp value "
            "ON value.kind='heirloom' AND value.item_id=instance.itemEntry;",
            "COMMIT;",
        ]), db="acore_ale")
        print("seeded existing collections (no retroactive payout)")

    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
    review.sort(key=lambda row: (row[0], -row[3], row[1]))
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow((
            "kind", "id", "name", "xp", "score", "path_kind",
            "active_hours", "delay_days", "access_multiplier", "chosen_path",
            "teaching_item_aliases"))
        writer.writerows(review)
    print("wrote %d spell rows, %d appearance rows, %d account-item rows" %
          (len(spell_rows), len(item_rows), len(account_item_rows)))
    print("review CSV:", OUT_CSV)
    print("remember: restart worldserver to load the new values")


if __name__ == "__main__":
    main()
