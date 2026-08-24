#!/usr/bin/env python3
"""Generate deterministic Paragon XP values for WotLK profession actions.

The generator reads the DBC files used by the running worldserver and the live
world database.  It emits a small Lua resolver plus a machine-readable audit.
Generation is deliberately strict: every discovered action is either assigned
positive XP or receives an explicit exclusion reason.
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import hashlib
import json
import math
import os
import pathlib
import statistics
import struct
import subprocess
import sys
import tempfile
from typing import Any, Iterable, Mapping, Sequence


TOOL_VERSION = 1
PROFESSIONS = {
    129: "First Aid",
    164: "Blacksmithing",
    165: "Leatherworking",
    171: "Alchemy",
    182: "Herbalism",
    185: "Cooking",
    186: "Mining",
    197: "Tailoring",
    202: "Engineering",
    333: "Enchanting",
    356: "Fishing",
    393: "Skinning",
    755: "Jewelcrafting",
    773: "Inscription",
}
ACTION = {
    "craft": 1,
    "gather_gameobject": 2,
    "gather_creature": 3,
    "fishing_area": 4,
    "fishing_hole": 5,
    "prospect": 6,
    "mill": 7,
    "disenchant": 8,
}
ACTION_LUA_NAMES = {
    "craft": "CRAFT",
    "gather_gameobject": "GATHER_GAMEOBJECT",
    "gather_creature": "GATHER_CREATURE",
    "fishing_area": "FISHING_AREA",
    "fishing_hole": "FISHING_HOLE",
    "prospect": "PROSPECT",
    "mill": "MILL",
    "disenchant": "DISENCHANT",
}

# SpellEffectName values in the 3.3.5a client.
EFFECT_CREATE_ITEM = 24
EFFECT_OPEN_LOCK = 33
EFFECT_ENCHANT_ITEM_PERMANENT = 53
EFFECT_CREATE_RANDOM_ITEM = 59
EFFECT_SKINNING = 95
EFFECT_DISENCHANT = 99
EFFECT_PROSPECTING = 127
EFFECT_ENCHANT_ITEM_PRISMATIC = 156
EFFECT_CREATE_ITEM_2 = 157
EFFECT_MILLING = 158
CRAFT_EFFECTS = {
    EFFECT_CREATE_ITEM,
    EFFECT_ENCHANT_ITEM_PERMANENT,
    EFFECT_CREATE_RANDOM_ITEM,
    EFFECT_ENCHANT_ITEM_PRISMATIC,
    EFFECT_CREATE_ITEM_2,
}

TIER_WEIGHTS = (1.0, 1.5, 2.0, 2.5, 3.0, 4.0)
QUALITY_WEIGHTS = (0.75, 1.0, 1.15, 1.4, 1.75, 2.0, 2.0, 2.0)
LOOT_TABLES = {
    "creature": ("creature_loot_template", 1),
    "disenchant": ("disenchant_loot_template", 2),
    "fishing": ("fishing_loot_template", 3),
    "gameobject": ("gameobject_loot_template", 4),
    "item": ("item_loot_template", 5),
    "milling": ("milling_loot_template", 7),
    "pickpocketing": ("pickpocketing_loot_template", 8),
    "prospecting": ("prospecting_loot_template", 9),
    "reference": ("reference_loot_template", 10),
    "skinning": ("skinning_loot_template", 11),
    "spell": ("spell_loot_template", 12),
}


class GenerationError(RuntimeError):
    pass


def signed32(value: int) -> int:
    return struct.unpack("<i", struct.pack("<I", value))[0]


def float32(value: int) -> float:
    return struct.unpack("<f", struct.pack("<I", value))[0]


def as_int(value: str | None, default: int = 0) -> int:
    if value in (None, "", r"\N"):
        return default
    return int(value)


def as_float(value: str | None, default: float = 0.0) -> float:
    if value in (None, "", r"\N"):
        return default
    return float(value)


def unhex(value: str | None) -> str:
    if not value or value == r"\N":
        return ""
    return bytes.fromhex(value).decode("utf-8", "replace")


def clean_label(value: str, fallback: str) -> str:
    value = " ".join(value.replace("\x00", " ").split())
    return value[:120] if value else fallback


def lua_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False).replace("</", "<\\/")


def percentile(values: Sequence[int], fraction: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return int(ordered[index])


def rank_tier(rank: int) -> int:
    if rank <= 75:
        return 1
    if rank <= 150:
        return 2
    if rank <= 225:
        return 3
    if rank <= 300:
        return 4
    if rank <= 375:
        return 5
    return 6


def level_tier(level: int) -> int:
    if level <= 20:
        return 1
    if level <= 40:
        return 2
    if level <= 60:
        return 3
    if level <= 70:
        return 4
    if level <= 75:
        return 5
    return 6


def bounded_cooldown_multiplier(seconds: int) -> float:
    if seconds <= 0:
        return 1.0
    hours = seconds / 3600.0
    return min(2.5, 1.0 + 1.5 * math.log1p(hours) / math.log(25.0))


def bounded_spawn_multiplier(spawn_count: int) -> float:
    # Scarcity only nudges values; sparse/event content cannot create outliers.
    if spawn_count <= 0:
        return 1.0
    return min(1.5, 1.0 + max(0.0, math.log10(100.0 / spawn_count)) * 0.15)


def capped_base_xp(raw_xp: float, details: dict[str, Any], cap: int) -> int:
    uncapped = max(1, round(raw_xp))
    if uncapped > cap:
        details["uncappedXp"] = uncapped
        details["capped"] = True
        return cap
    return uncapped


@dataclasses.dataclass(frozen=True)
class DBC:
    path: pathlib.Path
    rows: tuple[tuple[int, ...], ...]
    strings: bytes
    field_count: int

    @classmethod
    def load(cls, path: pathlib.Path) -> "DBC":
        raw = path.read_bytes()
        if len(raw) < 20:
            raise GenerationError(f"truncated DBC: {path}")
        magic, records, fields, record_size, string_size = struct.unpack(
            "<4s4I", raw[:20]
        )
        if magic != b"WDBC" or record_size != fields * 4:
            raise GenerationError(f"unsupported DBC header: {path}")
        row_end = 20 + records * record_size
        if row_end + string_size != len(raw):
            raise GenerationError(f"DBC size mismatch: {path}")
        fmt = "<" + "I" * fields
        rows = tuple(
            struct.unpack_from(fmt, raw, 20 + index * record_size)
            for index in range(records)
        )
        return cls(path, rows, raw[row_end:], fields)

    def string(self, offset: int) -> str:
        if offset <= 0 or offset >= len(self.strings):
            return ""
        end = self.strings.find(b"\0", offset)
        if end < 0:
            end = len(self.strings)
        return self.strings[offset:end].decode("utf-8", "replace")


@dataclasses.dataclass
class Spell:
    spell_id: int
    name: str
    reagents: list[tuple[int, int]]
    effects: list[int]
    die_sides: list[int]
    real_points_per_level: list[float]
    base_points: list[int]
    item_types: list[int]
    misc_values: list[int]
    trigger_spells: list[int]
    recovery_time: int
    category_recovery_time: int
    max_level: int
    base_level: int
    spell_level: int


@dataclasses.dataclass(frozen=True)
class SkillAbility:
    row_id: int
    skill: int
    spell: int
    min_rank: int
    trivial_high: int
    trivial_low: int


@dataclasses.dataclass(frozen=True)
class LootRow:
    entry: int
    item: int
    reference: int
    chance: float
    quest: int
    mode: int
    group: int
    minimum: int
    maximum: int
    conditioned: bool = False


@dataclasses.dataclass
class Item:
    entry: int
    name: str
    item_class: int
    subclass: int
    quality: int
    flags: int
    buy_count: int
    buy_price: int
    sell_price: int
    item_level: int
    required_level: int
    required_skill: int
    required_rank: int
    required_spell: int
    max_count: int
    stackable: int
    spell_ids: tuple[int, ...]
    bonding: int
    lock_id: int
    material: int
    bag_family: int
    totem_category: int
    disenchant_skill: int
    disenchant_id: int


@dataclasses.dataclass
class Recipe:
    spell: Spell
    skill: int
    rank: int
    outputs: dict[int, float]
    service: bool
    cooldown: int
    cyclic: bool = False
    cyclic_items: frozenset[int] = dataclasses.field(default_factory=frozenset)


@dataclasses.dataclass
class Result:
    kind: str
    context: int
    skill: int
    xp: int | None
    tier: int
    per_unit: bool
    label: str
    reason: str | None = None
    details: dict[str, Any] = dataclasses.field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.context}"


class Source:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.db_digest = hashlib.sha256()
        self.db_query_count = 0
        self.dbc_checksums: dict[str, str] = {}

    def mysql(self, sql: str) -> list[list[str]]:
        command = [
            "docker",
            "exec",
            "-e",
            f"PARAGON_DB={self.args.database_name}",
            "-e",
            f"PARAGON_SQL={sql}",
            self.args.database_container,
            "sh",
            "-lc",
            'exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD" --default-character-set=utf8mb4 --raw --batch --skip-column-names "$PARAGON_DB" -e "$PARAGON_SQL"',
        ]
        proc = subprocess.run(command, capture_output=True, check=False)
        if proc.returncode:
            raise GenerationError(
                f"world DB query failed ({proc.returncode}):\n"
                + proc.stderr.decode("utf-8", "replace")
            )
        output = proc.stdout
        self.db_digest.update(sql.encode("utf-8"))
        self.db_digest.update(b"\0")
        self.db_digest.update(output)
        self.db_query_count += 1
        text = output.decode("utf-8", "replace")
        return [line.split("\t") for line in text.splitlines() if line]

    def table_exists(self, table: str) -> bool:
        rows = self.mysql(
            "SELECT COUNT(*) FROM information_schema.tables "
            f"WHERE table_schema=DATABASE() AND table_name='{table}'"
        )
        return bool(rows and as_int(rows[0][0]))

    def materialize_dbcs(self, temporary: pathlib.Path) -> dict[str, pathlib.Path]:
        needed = (
            "Spell",
            "SkillLineAbility",
            "SkillLine",
            "Lock",
            "AreaTable",
            "Map",
        )
        result: dict[str, pathlib.Path] = {}
        for name in needed:
            if self.args.dbc_dir:
                path = pathlib.Path(self.args.dbc_dir) / f"{name}.dbc"
                if not path.is_file():
                    raise GenerationError(f"missing DBC: {path}")
            else:
                path = temporary / f"{name}.dbc"
                source = (
                    f"{self.args.dbc_container}:"
                    f"{self.args.dbc_root.rstrip('/')}/{name}.dbc"
                )
                proc = subprocess.run(
                    ["docker", "cp", source, str(path)],
                    capture_output=True,
                    check=False,
                )
                if proc.returncode:
                    raise GenerationError(
                        f"failed to copy {source}: "
                        + proc.stderr.decode("utf-8", "replace")
                    )
            self.dbc_checksums[name] = hashlib.sha256(path.read_bytes()).hexdigest()
            result[name] = path
        return result


def parse_spells(dbc: DBC) -> dict[int, Spell]:
    if dbc.field_count != 234:
        raise GenerationError(
            f"Spell.dbc has {dbc.field_count} fields; expected WotLK layout 234"
        )
    result: dict[int, Spell] = {}
    for row in dbc.rows:
        spell_id = row[0]
        reagents = []
        for index in range(8):
            item = signed32(row[52 + index])
            count = signed32(row[60 + index])
            if item > 0 and count > 0:
                reagents.append((item, count))
        result[spell_id] = Spell(
            spell_id=spell_id,
            name=dbc.string(row[136]),
            reagents=reagents,
            effects=[row[71 + i] for i in range(3)],
            die_sides=[signed32(row[74 + i]) for i in range(3)],
            real_points_per_level=[float32(row[77 + i]) for i in range(3)],
            base_points=[signed32(row[80 + i]) for i in range(3)],
            item_types=[row[107 + i] for i in range(3)],
            misc_values=[signed32(row[110 + i]) for i in range(3)],
            trigger_spells=[row[116 + i] for i in range(3)],
            recovery_time=row[29],
            category_recovery_time=row[30],
            max_level=row[37],
            base_level=row[38],
            spell_level=row[39],
        )
    return result


def overlay_spells(source: Source, spells: dict[int, Spell]) -> None:
    if not source.table_exists("spell_dbc"):
        return
    columns = [
        "ID",
        "HEX(Name_Lang_enUS)",
        "RecoveryTime",
        "CategoryRecoveryTime",
        "MaxLevel",
        "BaseLevel",
        "SpellLevel",
    ]
    columns += [f"Reagent_{i}" for i in range(1, 9)]
    columns += [f"ReagentCount_{i}" for i in range(1, 9)]
    for stem in (
        "Effect",
        "EffectDieSides",
        "EffectRealPointsPerLevel",
        "EffectBasePoints",
        "EffectItemType",
        "EffectMiscValue",
        "EffectTriggerSpell",
    ):
        columns += [f"{stem}_{i}" for i in range(1, 4)]
    rows = source.mysql(
        f"SELECT {','.join(columns)} FROM spell_dbc ORDER BY ID"
    )
    for values in rows:
        cursor = 0
        spell_id = as_int(values[cursor]); cursor += 1
        name = unhex(values[cursor]); cursor += 1
        recovery = as_int(values[cursor]); cursor += 1
        category_recovery = as_int(values[cursor]); cursor += 1
        max_level = as_int(values[cursor]); cursor += 1
        base_level = as_int(values[cursor]); cursor += 1
        spell_level = as_int(values[cursor]); cursor += 1
        reagent_ids = [as_int(x) for x in values[cursor:cursor + 8]]; cursor += 8
        reagent_counts = [as_int(x) for x in values[cursor:cursor + 8]]; cursor += 8
        effects = [as_int(x) for x in values[cursor:cursor + 3]]; cursor += 3
        die_sides = [as_int(x) for x in values[cursor:cursor + 3]]; cursor += 3
        real_points = [as_float(x) for x in values[cursor:cursor + 3]]; cursor += 3
        base_points = [as_int(x) for x in values[cursor:cursor + 3]]; cursor += 3
        item_types = [as_int(x) for x in values[cursor:cursor + 3]]; cursor += 3
        misc_values = [as_int(x) for x in values[cursor:cursor + 3]]; cursor += 3
        triggers = [as_int(x) for x in values[cursor:cursor + 3]]
        existing = spells.get(spell_id)
        # SQL numeric fields replace the DBC record. Empty SQL strings preserve
        # the binary localized string, matching DBCDatabaseLoader semantics.
        spells[spell_id] = Spell(
            spell_id=spell_id,
            name=name or (existing.name if existing else ""),
            reagents=[
                (item, count)
                for item, count in zip(reagent_ids, reagent_counts)
                if item > 0 and count > 0
            ],
            effects=effects,
            die_sides=die_sides,
            real_points_per_level=real_points,
            base_points=base_points,
            item_types=item_types,
            misc_values=misc_values,
            trigger_spells=triggers,
            recovery_time=recovery,
            category_recovery_time=category_recovery,
            max_level=max_level,
            base_level=base_level,
            spell_level=spell_level,
        )


def parse_skill_abilities(dbc: DBC) -> dict[int, SkillAbility]:
    if dbc.field_count != 14:
        raise GenerationError("SkillLineAbility.dbc is not the WotLK layout")
    return {
        row[0]: SkillAbility(
            row_id=row[0],
            skill=signed32(row[1]),
            spell=signed32(row[2]),
            min_rank=signed32(row[7]),
            trivial_high=signed32(row[10]),
            trivial_low=signed32(row[11]),
        )
        for row in dbc.rows
    }


def overlay_skill_abilities(
    source: Source, abilities: dict[int, SkillAbility]
) -> None:
    if not source.table_exists("skilllineability_dbc"):
        return
    rows = source.mysql(
        "SELECT ID,SkillLine,Spell,MinSkillLineRank,"
        "TrivialSkillLineRankHigh,TrivialSkillLineRankLow "
        "FROM skilllineability_dbc ORDER BY ID"
    )
    for row in rows:
        ability = SkillAbility(*(as_int(value) for value in row))
        abilities[ability.row_id] = ability


def parse_locks(dbc: DBC) -> dict[int, tuple[tuple[int, int, int], ...]]:
    if dbc.field_count != 33:
        raise GenerationError("Lock.dbc is not the WotLK layout")
    result = {}
    for row in dbc.rows:
        result[row[0]] = tuple(
            (row[1 + i], row[9 + i], row[17 + i]) for i in range(8)
        )
    return result


def overlay_locks(
    source: Source, locks: dict[int, tuple[tuple[int, int, int], ...]]
) -> None:
    if not source.table_exists("lock_dbc"):
        return
    columns = ["ID"]
    columns += [f"Type_{i}" for i in range(1, 9)]
    columns += [f"Index_{i}" for i in range(1, 9)]
    columns += [f"Skill_{i}" for i in range(1, 9)]
    rows = source.mysql(f"SELECT {','.join(columns)} FROM lock_dbc ORDER BY ID")
    for row in rows:
        values = [as_int(value) for value in row]
        locks[values[0]] = tuple(
            (values[1 + i], values[9 + i], values[17 + i]) for i in range(8)
        )


def parse_areas(dbc: DBC) -> dict[int, tuple[int, str]]:
    if dbc.field_count < 29:
        raise GenerationError("AreaTable.dbc is not the WotLK layout")
    return {row[0]: (row[2], dbc.string(row[11])) for row in dbc.rows}


def overlay_areas(source: Source, areas: dict[int, tuple[int, str]]) -> None:
    if not source.table_exists("areatable_dbc"):
        return
    for row in source.mysql(
        "SELECT ID,ParentAreaID,HEX(AreaName_Lang_enUS) "
        "FROM areatable_dbc ORDER BY ID"
    ):
        area_id, parent = as_int(row[0]), as_int(row[1])
        name = unhex(row[2])
        areas[area_id] = (parent, name or areas.get(area_id, (0, ""))[1])


def load_items(source: Source) -> dict[int, Item]:
    columns = [
        "entry", "HEX(name)", "class", "subclass", "Quality", "Flags",
        "BuyCount", "BuyPrice", "SellPrice", "ItemLevel", "RequiredLevel",
        "RequiredSkill", "RequiredSkillRank", "requiredspell", "maxcount",
        "stackable", "spellid_1", "spellid_2", "spellid_3", "spellid_4",
        "spellid_5", "bonding", "lockid", "Material", "BagFamily",
        "TotemCategory", "RequiredDisenchantSkill", "DisenchantID",
    ]
    rows = source.mysql(
        f"SELECT {','.join(columns)} FROM item_template ORDER BY entry"
    )
    result = {}
    for row in rows:
        numeric = [as_int(value) for value in row[2:]]
        result[as_int(row[0])] = Item(
            entry=as_int(row[0]),
            name=unhex(row[1]),
            item_class=numeric[0],
            subclass=numeric[1],
            quality=numeric[2],
            flags=numeric[3],
            buy_count=numeric[4],
            buy_price=numeric[5],
            sell_price=numeric[6],
            item_level=numeric[7],
            required_level=numeric[8],
            required_skill=numeric[9],
            required_rank=numeric[10],
            required_spell=numeric[11],
            max_count=numeric[12],
            stackable=numeric[13],
            spell_ids=tuple(numeric[14:19]),
            bonding=numeric[19],
            lock_id=numeric[20],
            material=numeric[21],
            bag_family=numeric[22],
            totem_category=numeric[23],
            disenchant_skill=numeric[24],
            disenchant_id=numeric[25],
        )
    return result


def load_conditions(source: Source) -> dict[int, set[tuple[int, int]]]:
    result: dict[int, set[tuple[int, int]]] = collections.defaultdict(set)
    rows = source.mysql(
        "SELECT SourceTypeOrReferenceId,SourceGroup,SourceEntry "
        "FROM conditions WHERE SourceTypeOrReferenceId BETWEEN 1 AND 12 "
        "ORDER BY SourceTypeOrReferenceId,SourceGroup,SourceEntry"
    )
    for row in rows:
        result[as_int(row[0])].add((as_int(row[1]), as_int(row[2])))
    return result


def load_loot(
    source: Source, conditions: Mapping[int, set[tuple[int, int]]]
) -> dict[str, dict[int, list[LootRow]]]:
    stores: dict[str, dict[int, list[LootRow]]] = {}
    for key, (table, condition_type) in LOOT_TABLES.items():
        rows = source.mysql(
            "SELECT Entry,Item,Reference,Chance,QuestRequired,LootMode,"
            f"GroupId,MinCount,MaxCount FROM {table} "
            "ORDER BY Entry,GroupId,Item,Reference,Chance,MinCount,MaxCount"
        )
        by_entry: dict[int, list[LootRow]] = collections.defaultdict(list)
        conditioned = conditions.get(condition_type, set())
        for row in rows:
            entry, item, reference = (as_int(row[i]) for i in range(3))
            by_entry[entry].append(
                LootRow(
                    entry=entry,
                    item=item,
                    reference=reference,
                    chance=abs(as_float(row[3])),
                    quest=as_int(row[4]),
                    mode=as_int(row[5]),
                    group=as_int(row[6]),
                    minimum=as_int(row[7]),
                    maximum=as_int(row[8]),
                    conditioned=(entry, item) in conditioned,
                )
            )
        stores[key] = dict(by_entry)
    return stores


class LootResolver:
    def __init__(self, stores: Mapping[str, Mapping[int, Sequence[LootRow]]]):
        self.stores = stores
        self.reference_cycles: set[tuple[int, ...]] = set()

    def expected(
        self,
        store: str,
        entry: int,
        stack: tuple[int, ...] = (),
        include_restricted: bool = False,
        include_all_modes: bool = False,
    ) -> dict[int, float]:
        store_id = tuple(LOOT_TABLES).index(store) + 1
        marker = store_id << 32 | entry
        if marker in stack:
            cycle_at = stack.index(marker)
            self.reference_cycles.add(stack[cycle_at:] + (marker,))
            return {}
        rows = list(self.stores.get(store, {}).get(entry, ()))
        if not rows:
            return {}
        output: dict[int, float] = collections.defaultdict(float)
        groups: dict[int, list[LootRow]] = collections.defaultdict(list)
        for row in rows:
            if (row.quest or row.conditioned) and not include_restricted:
                continue
            if not include_all_modes and not (row.mode & 1):
                continue
            groups[row.group].append(row)
        for group, candidates in groups.items():
            probabilities: list[float]
            if group == 0:
                probabilities = [
                    min(1.0, row.chance / 100.0) if row.chance > 0 else 1.0
                    for row in candidates
                ]
            else:
                explicit = sum(row.chance / 100.0 for row in candidates if row.chance)
                zero_count = sum(1 for row in candidates if not row.chance)
                remaining = max(0.0, 1.0 - explicit)
                if explicit > 1.0 and zero_count == 0:
                    scale = 1.0 / explicit
                else:
                    scale = 1.0
                probabilities = [
                    (row.chance / 100.0) * scale
                    if row.chance
                    else (remaining / zero_count if zero_count else 0.0)
                    for row in candidates
                ]
            for row, probability in zip(candidates, probabilities):
                count = max(1.0, (row.minimum + row.maximum) / 2.0)
                factor = probability * count
                if row.reference > 0:
                    nested = self.expected(
                        "reference",
                        row.reference,
                        stack + (marker,),
                        include_restricted,
                        include_all_modes,
                    )
                    for item, expected_count in nested.items():
                        output[item] += factor * expected_count
                elif row.item > 0:
                    output[row.item] += factor
        return dict(output)


def expected_create_count(spell: Spell, effect_index: int) -> float:
    # CalcValue's fixed portion is EffectBasePoints + 1. Positive die sides add
    # a uniform [1, die] roll. RealPointsPerLevel is bounded using SpellLevel;
    # the value is only used for static co-product allocation.
    base = spell.base_points[effect_index] + 1
    die = spell.die_sides[effect_index]
    random_part = (die + 1) / 2.0 if die > 1 else (1.0 if die == 1 else 0.0)
    level_part = max(0, spell.spell_level - spell.base_level) * max(
        0.0, spell.real_points_per_level[effect_index]
    )
    return max(1.0, base + random_part + level_part)


def load_craft_metadata(source: Source) -> tuple[dict[int, tuple[float, int]], dict[int, tuple[float, int]], dict[int, int], dict[int, tuple[int, int]]]:
    extra = {
        as_int(row[0]): (as_float(row[1]), as_int(row[2]))
        for row in source.mysql(
            "SELECT spellId,additionalCreateChance,additionalMaxNum "
            "FROM skill_extra_item_template ORDER BY spellId"
        )
    }
    perfect = {
        as_int(row[0]): (as_float(row[1]), as_int(row[2]))
        for row in source.mysql(
            "SELECT spellId,perfectCreateChance,perfectItemType "
            "FROM skill_perfect_item_template ORDER BY spellId"
        )
    }
    cooldown = {
        as_int(row[0]): max(as_int(row[1]), as_int(row[2]), as_int(row[3]))
        for row in source.mysql(
            "SELECT Id,RecoveryTime,CategoryRecoveryTime,StartRecoveryTime "
            "FROM spell_cooldown_overrides ORDER BY Id"
        )
    }
    trainers: dict[int, tuple[int, int]] = {}
    for row in source.mysql(
        "SELECT SpellId,ReqSkillLine,ReqSkillRank FROM trainer_spell "
        "WHERE ReqSkillLine IN (" + ",".join(map(str, sorted(PROFESSIONS))) + ") "
        "ORDER BY SpellId,ReqSkillLine,ReqSkillRank"
    ):
        spell, skill, rank = (as_int(value) for value in row)
        previous = trainers.get(spell)
        if previous is None or rank < previous[1]:
            trainers[spell] = (skill, rank)
    return extra, perfect, cooldown, trainers


def build_recipes(
    spells: Mapping[int, Spell],
    abilities: Mapping[int, SkillAbility],
    trainers: Mapping[int, tuple[int, int]],
    items: Mapping[int, Item],
    extra: Mapping[int, tuple[float, int]],
    perfect: Mapping[int, tuple[float, int]],
    cooldown_overrides: Mapping[int, int],
    loot: LootResolver,
) -> tuple[list[Recipe], list[dict[str, Any]]]:
    candidates: dict[int, list[tuple[int, int, str]]] = collections.defaultdict(list)
    for ability in abilities.values():
        if ability.skill in PROFESSIONS:
            # MinSkillLineRank is commonly 1 for recipes; TrivialLow carries
            # the useful recipe-band signal for non-trainer/drop recipes.
            candidates[ability.spell].append(
                (ability.skill, max(ability.min_rank, ability.trivial_low), "dbc")
            )
    for spell, (skill, rank) in trainers.items():
        if skill in PROFESSIONS:
            candidates[spell].append((skill, rank, "trainer"))
    # Custom/drop recipes can exist only as recipe-item payloads. The taught
    # craft spell is one of the item's spell slots; the generic learn wrapper
    # is ignored later because it has no craft effect.
    for item in items.values():
        if item.item_class != 9 or item.required_skill not in PROFESSIONS:
            continue
        for spell_id in item.spell_ids:
            if spell_id > 0:
                candidates[spell_id].append(
                    (item.required_skill, item.required_rank, "recipe_item")
                )

    recipes: list[Recipe] = []
    skipped: list[dict[str, Any]] = []
    for spell_id in sorted(candidates):
        spell = spells.get(spell_id)
        if not spell:
            skipped.append({"spell": spell_id, "reason": "missing_spell_record"})
            continue
        if not any(effect in CRAFT_EFFECTS for effect in spell.effects):
            continue
        skill_rows = sorted(set((skill, rank) for skill, rank, _ in candidates[spell_id]))
        skills = sorted(set(skill for skill, _ in skill_rows))
        if len(skills) != 1:
            skipped.append(
                {"spell": spell_id, "reason": "ambiguous_profession_skill", "skills": skills}
            )
            continue
        skill = skills[0]
        rank = max((rank for row_skill, rank in skill_rows if row_skill == skill), default=0)
        outputs: dict[int, float] = collections.defaultdict(float)
        service = False
        for index, effect in enumerate(spell.effects):
            if effect in (EFFECT_CREATE_ITEM, EFFECT_CREATE_ITEM_2):
                item = spell.item_types[index]
                if item > 0:
                    outputs[item] += expected_create_count(spell, index)
                elif effect == EFFECT_CREATE_ITEM_2:
                    for loot_item, count in loot.expected("spell", spell_id).items():
                        outputs[loot_item] += count
            elif effect == EFFECT_CREATE_RANDOM_ITEM:
                for loot_item, count in loot.expected("spell", spell_id).items():
                    outputs[loot_item] += count
            elif effect in (
                EFFECT_ENCHANT_ITEM_PERMANENT,
                EFFECT_ENCHANT_ITEM_PRISMATIC,
            ):
                service = True

        if spell_id in extra and outputs:
            chance, maximum = extra[spell_id]
            probability = min(1.0, max(0.0, chance / 100.0))
            # The core rolls once for each possible additional item, stopping
            # at the first failure: E[total] = 1 + p + ... + p^maximum.
            multiplier = sum(probability ** roll for roll in range(max(0, maximum) + 1))
            outputs = collections.defaultdict(
                float, {item: count * multiplier for item, count in outputs.items()}
            )
        if spell_id in perfect and outputs:
            chance, perfect_item = perfect[spell_id]
            chance = min(1.0, max(0.0, chance / 100.0))
            original = dict(outputs)
            outputs = collections.defaultdict(float)
            for item, count in original.items():
                outputs[item] += count * (1.0 - chance)
            if perfect_item > 0:
                outputs[perfect_item] += sum(original.values()) * chance

        cooldown_ms = max(
            spell.recovery_time,
            spell.category_recovery_time,
            cooldown_overrides.get(spell_id, 0),
        )
        cooldown = max(0, math.ceil(cooldown_ms / 1000.0))
        recipes.append(
            Recipe(spell, skill, max(0, rank), dict(outputs), service, cooldown)
        )
    return recipes, skipped


def cyclic_recipe_components(
    recipes: Sequence[Recipe],
) -> dict[int, frozenset[int]]:
    adjacency: dict[int, set[int]] = collections.defaultdict(set)
    nodes: set[int] = set()
    for recipe in recipes:
        for reagent, _ in recipe.spell.reagents:
            nodes.add(reagent)
            for output in recipe.outputs:
                nodes.add(output)
                adjacency[reagent].add(output)

    index = 0
    indices: dict[int, int] = {}
    low: dict[int, int] = {}
    stack: list[int] = []
    on_stack: set[int] = set()
    components: list[set[int]] = []

    def visit(node: int) -> None:
        nonlocal index
        indices[node] = low[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for target in sorted(adjacency.get(node, ())):
            if target not in indices:
                visit(target)
                low[node] = min(low[node], low[target])
            elif target in on_stack:
                low[node] = min(low[node], indices[target])
        if low[node] == indices[node]:
            component = set()
            while True:
                member = stack.pop()
                on_stack.remove(member)
                component.add(member)
                if member == node:
                    break
            components.append(component)

    for node in sorted(nodes):
        if node not in indices:
            visit(node)
    cyclic_components = [
        component
        for component in components
        if len(component) > 1
        or any(member in adjacency.get(member, ()) for member in component)
    ]
    membership = {
        member: component_index
        for component_index, component in enumerate(cyclic_components)
        for member in component
    }
    recipe_components: dict[int, frozenset[int]] = {}
    for recipe_index, recipe in enumerate(recipes):
        matched_components: set[int] = set()
        for reagent, _ in recipe.spell.reagents:
            component = membership.get(reagent)
            if component is None:
                continue
            if any(membership.get(output) == component for output in recipe.outputs):
                matched_components.add(component)
        if matched_components:
            recipe_components[recipe_index] = frozenset(
                item
                for component_index in sorted(matched_components)
                for item in cyclic_components[component_index]
            )
    return recipe_components


def cyclic_recipe_indices(recipes: Sequence[Recipe]) -> set[int]:
    """Compatibility helper for callers that only need cyclic recipe indices."""
    return set(cyclic_recipe_components(recipes))


def split_cycle_reagents(
    recipe: Recipe,
) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """Separate returned SCC members from externally consumed materials."""
    returned = []
    external = []
    for reagent in recipe.spell.reagents:
        (returned if reagent[0] in recipe.cyclic_items else external).append(reagent)
    return returned, external


def item_tier(item: Item | None, fallback_rank: int = 0) -> int:
    if fallback_rank > 0:
        return rank_tier(fallback_rank)
    if item:
        if item.required_rank > 0:
            return rank_tier(item.required_rank)
        return level_tier(max(item.required_level, item.item_level))
    return rank_tier(0)


def intrinsic_item_value(item: Item | None, fallback_rank: int = 0) -> float:
    tier = item_tier(item, fallback_rank)
    quality = item.quality if item else 1
    quality_weight = QUALITY_WEIGHTS[min(max(quality, 0), len(QUALITY_WEIGHTS) - 1)]
    return TIER_WEIGHTS[tier - 1] * quality_weight


def vendor_material_value(item: Item | None) -> float:
    """Conservative positive value for a genuinely purchased material.

    Vendor goods use only 25%-50% of intrinsic value. Unit copper price moves
    within that narrow band, so expensive seasonal/custom reagents register as
    resources without allowing gold-priced items to dominate Paragon XP.
    """
    intrinsic = intrinsic_item_value(item)
    if not item:
        return intrinsic * 0.25
    unit_price = item.buy_price / max(1, item.buy_count)
    price_signal = min(1.0, math.log10(1.0 + max(0.0, unit_price)) / 5.0)
    return intrinsic * (0.25 + 0.25 * price_signal)


def build_item_values(
    items: Mapping[int, Item],
    recipes: Sequence[Recipe],
    loot_stores: Mapping[str, Mapping[int, Sequence[LootRow]]],
    unlimited_vendor: set[int],
    overrides: Mapping[str, Any],
) -> tuple[dict[int, float], set[int]]:
    nonvendor: set[int] = set()
    for store in loot_stores.values():
        for rows in store.values():
            for row in rows:
                if row.item > 0:
                    nonvendor.add(row.item)
    for recipe in recipes:
        nonvendor.update(recipe.outputs)
    vendor_only = unlimited_vendor - nonvendor

    values = {entry: intrinsic_item_value(item) for entry, item in items.items()}
    for entry in vendor_only:
        values[entry] = vendor_material_value(items.get(entry))
    for key, config in overrides.get("items", {}).items():
        entry = int(key)
        if "value" in config:
            values[entry] = max(0.0, float(config["value"]))

    # Propagate material costs through the acyclic recipe graph. The intrinsic
    # value is a floor, and allocated craft cost is capped at six times that
    # floor so custom economic loops or rare co-products cannot explode XP.
    for _ in range(max(1, len(recipes))):
        changed = False
        for recipe in recipes:
            if recipe.cyclic or not recipe.outputs:
                continue
            total_cost = sum(values.get(item, 0.0) * count for item, count in recipe.spell.reagents)
            if total_cost <= 0:
                continue
            output_weights = {
                item: max(0.25, intrinsic_item_value(items.get(item), recipe.rank)) * count
                for item, count in recipe.outputs.items()
            }
            weight_total = sum(output_weights.values())
            if weight_total <= 0:
                continue
            for output, expected_count in recipe.outputs.items():
                if expected_count <= 0:
                    continue
                floor = intrinsic_item_value(items.get(output), recipe.rank)
                allocated = total_cost * output_weights[output] / weight_total / expected_count
                candidate = min(floor * 6.0, max(floor, allocated))
                current = values.get(output, floor)
                # Multiple production routes use the cheapest bounded route,
                # while never falling below the item's intrinsic tier value.
                new_value = candidate if current <= floor + 1e-9 else min(current, candidate)
                if abs(new_value - current) > 1e-9:
                    values[output] = new_value
                    changed = True
        if not changed:
            break
    return values, vendor_only


def load_unlimited_vendors(source: Source) -> set[int]:
    conditioned = {
        (as_int(row[0]), as_int(row[1]))
        for row in source.mysql(
            "SELECT SourceGroup,SourceEntry FROM conditions "
            "WHERE SourceTypeOrReferenceId=23 ORDER BY SourceGroup,SourceEntry"
        )
    }
    result = set()
    for row in source.mysql(
        "SELECT entry,item,maxcount,ExtendedCost FROM npc_vendor "
        "WHERE item > 0 ORDER BY entry,item,slot"
    ):
        vendor, item, maximum, extended = (as_int(value) for value in row)
        if maximum == 0 and extended == 0 and (vendor, item) not in conditioned:
            result.add(item)
    for row in source.mysql(
        "SELECT guid,item,maxcount,ExtendedCost FROM game_event_npc_vendor "
        "ORDER BY eventEntry,guid,item,slot"
    ):
        vendor, item, maximum, extended = (as_int(value) for value in row)
        if maximum == 0 and extended == 0 and (vendor, item) not in conditioned:
            result.add(item)
    return result


def loot_value(
    expected: Mapping[int, float], values: Mapping[int, float]
) -> float:
    # Expected value already accounts for drop chance and co-product counts.
    # A bounded rare-item premium prevents very low probability custom rows from
    # either disappearing or dominating the reward.
    total = 0.0
    for item, count in expected.items():
        if count <= 0:
            continue
        scarcity = min(2.0, max(1.0, (1.0 / min(1.0, count)) ** 0.20))
        total += values.get(item, 0.0) * count * scarcity
    return total


def gather_material_value(
    loot: LootResolver,
    store: str,
    entry: int,
    values: Mapping[int, float],
    tier: int,
    details: dict[str, Any],
) -> float:
    raw_value = loot_value(loot.expected(store, entry), values)
    details["rawExpectedMaterialValue"] = raw_value
    value = raw_value
    if value <= 0:
        restricted = loot.expected(
            store,
            entry,
            include_restricted=True,
            include_all_modes=True,
        )
        value = loot_value(restricted, values)
        details["restrictedExpectedMaterialValue"] = value
        details["restrictedLootFallback"] = True
        details["fallback"] = "quest_condition_or_special_loot"

    # Every successful gather has at least the value of its profession tier.
    # This is deliberately applied even when a tiny generic by-product exists:
    # otherwise incidental clam/pearl rows can mask the conditioned primary
    # material and collapse a legitimate niche gather to one XP.
    floor = TIER_WEIGHTS[max(1, min(6, tier)) - 1]
    details["materialValueBeforeTierFloor"] = value
    details["professionTierMaterialFloor"] = floor
    details["tierFloorApplied"] = value < floor
    if value < floor:
        details["fallback"] = "profession_tier_floor"
        value = floor
    return value


def required_skinning_rank(level: int) -> int:
    if level < 10:
        return 0
    if level < 20:
        return (level - 10) * 10
    return level * 5


def build_results(
    source: Source,
    items: Mapping[int, Item],
    locks: Mapping[int, tuple[tuple[int, int, int], ...]],
    areas: Mapping[int, tuple[int, str]],
    recipes: Sequence[Recipe],
    recipe_skips: Sequence[Mapping[str, Any]],
    loot_stores: Mapping[str, Mapping[int, Sequence[LootRow]]],
    loot: LootResolver,
    values: Mapping[int, float],
    vendor_only: set[int],
    overrides: Mapping[str, Any],
) -> list[Result]:
    results: list[Result] = []
    base_xp_cap = source.args.base_xp_cap

    for recipe in recipes:
        spell = recipe.spell
        tier = rank_tier(recipe.rank)
        details = {
            "rank": recipe.rank,
            "cooldownSeconds": recipe.cooldown,
            "reagents": len(spell.reagents),
            "outputs": len(recipe.outputs),
        }
        vendor_reagents = [
            item for item, _ in spell.reagents if item in vendor_only
        ]
        if vendor_reagents:
            details["vendorOnlyReagents"] = vendor_reagents
            details["vendorMaterialFallback"] = True
        missing_reagents = [item for item, _ in spell.reagents if item not in items]
        missing_outputs = [item for item in recipe.outputs if item not in items]
        if missing_reagents:
            details["missingItems"] = missing_reagents
            results.append(Result("craft", spell.spell_id, recipe.skill, None, tier, False, clean_label(spell.name, f"Spell {spell.spell_id}"), "missing_reagent_item", details))
            continue
        if missing_outputs:
            details["missingItems"] = missing_outputs
            results.append(Result("craft", spell.spell_id, recipe.skill, None, tier, False, clean_label(spell.name, f"Spell {spell.spell_id}"), "missing_output_item", details))
            continue
        material = sum(values.get(item, 0.0) * count for item, count in spell.reagents)
        if recipe.cyclic:
            returned_reagents, external_reagents = split_cycle_reagents(recipe)
            external_material = sum(
                values.get(item, 0.0) * count for item, count in external_reagents
            )
            details["cyclicReturnedReagents"] = [
                {"item": item, "count": count}
                for item, count in sorted(returned_reagents)
            ]
            details["externalConsumedReagents"] = [
                {"item": item, "count": count}
                for item, count in sorted(external_reagents)
            ]
            details["externalConsumedMaterialValue"] = external_material
            if external_material > 0:
                # The SCC member is returned/recharged by the action. Charge
                # only genuinely consumed materials outside that component.
                material = external_material
                details["cycleExternalMaterial"] = True
                details["valuationFallback"] = "external_consumed_material"
            elif recipe.cooldown > 0:
                # A long cooldown makes an otherwise reversible conversion
                # finite in practice. Break recursion at intrinsic/source value
                # and retain the bounded cooldown premium.
                details["cycleFallback"] = True
                details["valuationFallback"] = "intrinsic_input_value"
            else:
                results.append(Result("craft", spell.spell_id, recipe.skill, None, tier, False, clean_label(spell.name, f"Spell {spell.spell_id}"), "cyclic_recipe", details))
                continue
        if material <= 0:
            reason = "no_consumed_materials" if not spell.reagents else "zero_material_value"
            results.append(Result("craft", spell.spell_id, recipe.skill, None, tier, False, clean_label(spell.name, f"Spell {spell.spell_id}"), reason, details))
            continue
        xp = capped_base_xp(
            10.0 * material * bounded_cooldown_multiplier(recipe.cooldown),
            details,
            base_xp_cap,
        )
        results.append(Result("craft", spell.spell_id, recipe.skill, xp, tier, False, clean_label(spell.name, f"Spell {spell.spell_id}"), details=details))

    for skipped in recipe_skips:
        # These candidates are supported profession actions but cannot safely be
        # keyed to one skill/record. Keep them machine-visible.
        spell_id = int(skipped["spell"])
        results.append(Result("craft", spell_id, 0, None, 1, False, f"Spell {spell_id}", str(skipped["reason"]), dict(skipped)))

    go_rows = source.mysql(
        "SELECT t.entry,HEX(t.name),t.Data0,t.Data1,COUNT(g.guid) "
        "FROM gameobject_template t LEFT JOIN gameobject g ON g.id=t.entry "
        "WHERE t.type=3 GROUP BY t.entry,t.name,t.Data0,t.Data1 ORDER BY t.entry"
    )
    for row in go_rows:
        entry, name, lock_id, loot_id, spawns = as_int(row[0]), unhex(row[1]), as_int(row[2]), as_int(row[3]), as_int(row[4])
        skills = []
        for lock_type, lock_index, required in locks.get(lock_id, ()):
            if lock_type == 2 and lock_index in (2, 3):
                skills.append((182 if lock_index == 2 else 186, required))
        if not skills:
            continue
        skill_set = sorted(set(skill for skill, _ in skills))
        rank = max((required for _, required in skills), default=0)
        tier = rank_tier(rank)
        label = clean_label(name, f"GameObject {entry}")
        details = {"lockId": lock_id, "lootId": loot_id, "rank": rank, "spawns": spawns}
        if len(skill_set) != 1:
            results.append(Result("gather_gameobject", entry, 0, None, tier, False, label, "ambiguous_gather_skill", details)); continue
        skill = skill_set[0]
        value = gather_material_value(
            loot, "gameobject", loot_id, values, tier, details
        )
        if loot_id <= 0 or not loot_stores["gameobject"].get(loot_id):
            results.append(Result("gather_gameobject", entry, skill, None, tier, False, label, "missing_loot_template", details)); continue
        if spawns <= 0:
            details["fallback"] = "template_without_current_spawn"
        xp = capped_base_xp(
            50.0 * value * bounded_spawn_multiplier(spawns), details, base_xp_cap
        )
        results.append(Result("gather_gameobject", entry, skill, xp, tier, False, label, details=details))

    creature_rows = source.mysql(
        "SELECT t.entry,HEX(t.name),t.minlevel,t.maxlevel,t.type_flags,t.skinloot,COUNT(c.guid) "
        "FROM creature_template t LEFT JOIN creature c ON c.id=t.entry "
        "WHERE t.skinloot<>0 GROUP BY t.entry,t.name,t.minlevel,t.maxlevel,t.type_flags,t.skinloot ORDER BY t.entry"
    )
    for row in creature_rows:
        entry, name = as_int(row[0]), unhex(row[1])
        minimum, maximum, flags, loot_id, spawns = (as_int(value) for value in row[2:])
        if flags & 0x100:
            skill = 182
        elif flags & 0x200:
            skill = 186
        elif flags & 0x8000:
            skill = 202
        else:
            skill = 393
        rank = required_skinning_rank(maximum)
        tier = rank_tier(rank)
        label = clean_label(name, f"Creature {entry}")
        details = {"lootId": loot_id, "rank": rank, "spawns": spawns, "level": [minimum, maximum]}
        value = gather_material_value(
            loot, "skinning", loot_id, values, tier, details
        )
        if not loot_stores["skinning"].get(loot_id):
            results.append(Result("gather_creature", entry, skill, None, tier, False, label, "missing_loot_template", details)); continue
        if spawns <= 0:
            details["fallback"] = "template_without_current_spawn"
        xp = capped_base_xp(
            50.0 * value * bounded_spawn_multiplier(spawns), details, base_xp_cap
        )
        results.append(Result("gather_creature", entry, skill, xp, tier, False, label, details=details))

    fishing_skill = {
        as_int(row[0]): as_int(row[1])
        for row in source.mysql(
            "SELECT entry,skill FROM skill_fishing_base_level ORDER BY entry"
        )
    }
    for area in sorted(loot_stores["fishing"]):
        parent_area, area_name = areas.get(area, (0, ""))
        skill_source = area
        configured_rank = fishing_skill.get(area)
        if configured_rank is None and parent_area:
            skill_source = parent_area
            configured_rank = fishing_skill.get(parent_area)
        if configured_rank is None:
            skill_source = 1
            configured_rank = fishing_skill.get(1, 1)
        rank = max(0, configured_rank)
        tier = rank_tier(rank)
        details = {
            "lootId": area,
            "rank": rank,
            "skillSourceArea": skill_source,
            "fallbackSkill": skill_source != area,
        }
        label = clean_label(area_name, f"Fishing area {area}")
        value = gather_material_value(
            loot, "fishing", area, values, tier, details
        )
        results.append(Result("fishing_area", area, 356, capped_base_xp(50.0 * value, details, base_xp_cap), tier, False, label, details=details))

    hole_rows = source.mysql(
        "SELECT t.entry,HEX(t.name),t.Data1,t.Data2,t.Data3,COUNT(g.guid) "
        "FROM gameobject_template t LEFT JOIN gameobject g ON g.id=t.entry "
        "WHERE t.type=25 GROUP BY t.entry,t.name,t.Data1,t.Data2,t.Data3 ORDER BY t.entry"
    )
    for row in hole_rows:
        entry, name, loot_id, min_opens, max_opens, spawns = as_int(row[0]), unhex(row[1]), *(as_int(value) for value in row[2:])
        tier = max((item_tier(items.get(item)) for item in loot.expected("gameobject", loot_id)), default=1)
        label = clean_label(name, f"Fishing hole {entry}")
        details = {"lootId": loot_id, "spawns": spawns, "opens": [min_opens, max_opens]}
        value = gather_material_value(
            loot, "gameobject", loot_id, values, tier, details
        )
        if not loot_stores["gameobject"].get(loot_id):
            results.append(Result("fishing_hole", entry, 356, None, tier, False, label, "missing_loot_template", details)); continue
        if spawns <= 0:
            details["fallback"] = "template_without_current_spawn"
        results.append(Result("fishing_hole", entry, 356, capped_base_xp(50.0 * value * bounded_spawn_multiplier(spawns), details, base_xp_cap), tier, False, label, details=details))

    for kind, store, skill in (("prospect", "prospecting", 755), ("mill", "milling", 773)):
        required_flag = 0x00040000 if kind == "prospect" else 0x20000000
        for item_id in sorted(loot_stores[store]):
            item = items.get(item_id)
            tier = item_tier(item)
            details = {"lootId": item_id, "inputCount": 5}
            if not item:
                results.append(Result(kind, item_id, skill, None, tier, False, f"Item {item_id}", "missing_input_item", details)); continue
            if not (item.flags & required_flag):
                # Spell::CheckCast rejects the action before it can emit event
                # 76, even if an orphan loot template happens to exist.
                continue
            value = values.get(item_id, 0.0)
            if item_id in vendor_only:
                details["vendorMaterialFallback"] = True
            if value <= 0:
                results.append(Result(kind, item_id, skill, None, tier, False, clean_label(item.name, f"Item {item_id}"), "zero_input_material_value", details)); continue
            if loot_value(loot.expected(store, item_id), values) <= 0:
                results.append(Result(kind, item_id, skill, None, tier, False, clean_label(item.name, f"Item {item_id}"), "no_unconditional_output_loot", details)); continue
            results.append(Result(kind, item_id, skill, capped_base_xp(5.0 * value, details, base_xp_cap), tier, False, clean_label(item.name, f"Item {item_id}"), details=details))

    for item_id, item in sorted(items.items()):
        # Mirrors Spell::CheckCast for SPELL_EFFECT_DISENCHANT. Rows outside
        # these gates cannot produce a successful profession action/event.
        if (
            item.disenchant_id <= 0
            or item.item_class not in (2, 4)
            or item.quality < 2
            or item.quality > 4
            or item.disenchant_skill == -1
        ):
            continue
        tier = item_tier(item)
        details = {"lootId": item.disenchant_id, "requiredSkill": item.disenchant_skill}
        if not loot_stores["disenchant"].get(item.disenchant_id):
            results.append(Result("disenchant", item_id, 333, None, tier, False, clean_label(item.name, f"Item {item_id}"), "missing_loot_template", details)); continue
        if loot_value(loot.expected("disenchant", item.disenchant_id), values) <= 0:
            results.append(Result("disenchant", item_id, 333, None, tier, False, clean_label(item.name, f"Item {item_id}"), "no_unconditional_output_loot", details)); continue
        input_value = values.get(item_id, 0.0)
        if item_id in vendor_only:
            details["vendorMaterialFallback"] = True
        if input_value <= 0:
            results.append(Result("disenchant", item_id, 333, None, tier, False, clean_label(item.name, f"Item {item_id}"), "zero_input_material_value", details)); continue
        results.append(Result("disenchant", item_id, 333, capped_base_xp(5.0 * input_value, details, base_xp_cap), tier, False, clean_label(item.name, f"Item {item_id}"), details=details))

    action_overrides = overrides.get("actions", {})
    by_key: dict[str, Result] = {}
    duplicate_keys: set[str] = set()
    for result in results:
        if result.key in by_key:
            duplicate_keys.add(result.key)
        by_key[result.key] = result
    if duplicate_keys:
        raise GenerationError("duplicate action contexts: " + ", ".join(sorted(duplicate_keys)[:20]))
    for key, config in sorted(action_overrides.items()):
        if key not in by_key:
            raise GenerationError(f"manual action override targets undiscovered action: {key}")
        if "exclude" in config and "xp" in config:
            raise GenerationError(f"manual action override cannot set both exclude and xp: {key}")
        result = by_key[key]
        if "exclude" in config:
            result.xp = None
            result.reason = "manual:" + str(config["exclude"])
        if "xp" in config:
            xp = int(config["xp"])
            if xp <= 0:
                raise GenerationError(f"manual XP must be positive: {key}")
            result.details.pop("capped", None)
            result.details.pop("uncappedXp", None)
            result.xp = capped_base_xp(xp, result.details, base_xp_cap)
            result.reason = None
        if "skill" in config:
            result.skill = int(config["skill"])
        if "tier" in config:
            tier = int(config["tier"])
            if tier < 1 or tier > 6:
                raise GenerationError(f"manual tier must be 1..6: {key}")
            result.tier = tier
        if "per_unit" in config:
            result.per_unit = bool(config["per_unit"])
    return sorted(results, key=lambda result: (ACTION[result.kind], result.context))


def audit_payload(
    source: Source,
    results: Sequence[Result],
    items: Mapping[int, Item],
    recipes: Sequence[Recipe],
    vendor_only: set[int],
    loot: LootResolver,
) -> dict[str, Any]:
    override_path = pathlib.Path(source.args.overrides)
    override_bytes = override_path.read_bytes() if override_path.exists() else b""
    source_snapshot = hashlib.sha256()
    source_snapshot.update(source.db_digest.digest())
    for name, digest in sorted(source.dbc_checksums.items()):
        source_snapshot.update(name.encode("ascii"))
        source_snapshot.update(bytes.fromhex(digest))
    source_snapshot.update(override_bytes)
    coverage: dict[str, Any] = {}
    gaps = []
    exclusions = []
    valued = [result for result in results if result.xp is not None]
    for kind in ACTION:
        subset = [result for result in results if result.kind == kind]
        kind_gaps = [result.key for result in subset if result.xp is None and not result.reason]
        coverage[kind] = {
            "discovered": len(subset),
            "valued": sum(result.xp is not None and result.xp > 0 for result in subset),
            "excluded": sum(bool(result.reason) for result in subset),
            "silentGaps": kind_gaps,
        }
        gaps.extend(kind_gaps)
    for result in results:
        if result.xp is not None and result.xp <= 0:
            gaps.append(result.key)
        if result.reason:
            exclusions.append(
                {
                    "key": result.key,
                    "kind": result.kind,
                    "context": result.context,
                    "skill": result.skill,
                    "reason": result.reason,
                    "details": result.details,
                }
            )

    grouped_stats: dict[str, list[int]] = collections.defaultdict(list)
    grouped_raw_stats: dict[str, list[int]] = collections.defaultdict(list)
    for result in valued:
        profession = PROFESSIONS.get(result.skill, f"Skill {result.skill}")
        key = f"{profession}|{result.tier}"
        grouped_stats[key].append(int(result.xp))
        grouped_raw_stats[key].append(
            int(result.details.get("uncappedXp", result.xp))
        )

    def summarize(grouped: Mapping[str, Sequence[int]]) -> dict[str, dict[str, Any]]:
        summary: dict[str, dict[str, Any]] = {}
        for key, values in sorted(grouped.items()):
            profession, tier = key.rsplit("|", 1)
            summary.setdefault(profession, {})[tier] = {
                "count": len(values),
                "p50": percentile(values, 0.50),
                "p95": percentile(values, 0.95),
                "max": max(values),
            }
        return summary

    stats = summarize(grouped_stats)
    raw_stats = summarize(grouped_raw_stats)
    reason_counts = collections.Counter(result.reason for result in results if result.reason)
    action_rows = [
        {
            "key": result.key,
            "kind": result.kind,
            "action": ACTION[result.kind],
            "context": result.context,
            "skill": result.skill,
            "tier": result.tier,
            "xp": result.xp,
            "rawXp": result.details.get("uncappedXp", result.xp),
            "perUnit": result.per_unit,
            "label": result.label,
            "reason": result.reason,
            "details": result.details,
        }
        for result in results
    ]
    return {
        "schemaVersion": 1,
        "toolVersion": TOOL_VERSION,
        "source": {
            "database": source.args.database_name,
            "databaseContainer": source.args.database_container,
            "dbcContainer": None if source.args.dbc_dir else source.args.dbc_container,
            "dbcRoot": str(source.args.dbc_dir or source.args.dbc_root),
            "dbcSha256": dict(sorted(source.dbc_checksums.items())),
            "databaseSnapshotSha256": source.db_digest.hexdigest(),
            "overridesSha256": hashlib.sha256(override_bytes).hexdigest(),
            "generatorSha256": hashlib.sha256(
                pathlib.Path(__file__).read_bytes()
            ).hexdigest(),
            "snapshotSha256": source_snapshot.hexdigest(),
            "databaseQueries": source.db_query_count,
        },
        "model": {
            "tierWeights": list(TIER_WEIGHTS),
            "craftMultiplier": 10,
            "gatherMultiplier": 50,
            "processingMultiplier": 5,
            "baseXpCap": source.args.base_xp_cap,
            "cooldownMultiplierBounds": [1, 2.5],
            "spawnScarcityMultiplierBounds": [1, 1.5],
            "lootScarcityMultiplierBounds": [1, 2],
            "recursiveCraftValueCap": "6x intrinsic item value",
            "vendorOnlyContribution": "25%-50% of intrinsic tier value, bounded by unit vendor price",
            "rationale": [
                "Material tiers follow profession ranks 1-75/76-150/151-225/226-300/301-375/376+.",
                "Craft XP is ten times recursively valued consumed material; gathers are fifty times expected primary loot value; processing is five times input value.",
                "Grouped/reference loot uses runtime-style expected probabilities; quest-only, conditioned, and non-default-mode rows do not inflate generic rewards.",
                "Every successful gather has a profession-tier material floor before bounded spawn scarcity; raw expected material value and floor use are audited.",
                "Successful special/quest gathering templates with no generic yield also inspect restricted loot and are marked as fallbacks.",
                "Vendor-only inputs retain a conservative price-bounded fraction of tier value so legitimate actions remain rewarding without gold-price outliers.",
                "Cooldown and source scarcity bonuses are deliberately bounded to prevent daily/custom or sparse content outliers.",
                "Cyclic recipes ignore returned SCC components but retain positive externally consumed material; only repeatable no-cost cycles are excluded.",
                "Time-gated cycles without external consumption use intrinsic input value so recursion remains finite.",
            ],
        },
        "inputs": {
            "items": len(items),
            "recipes": len(recipes),
            "vendorOnlyItems": len(vendor_only),
            "referenceLootCycles": len(loot.reference_cycles),
            "cycleFallbackActions": sum(
                bool(result.details.get("cycleFallback")) for result in results
            ),
            "cycleExternalMaterialActions": sum(
                bool(result.details.get("cycleExternalMaterial"))
                for result in results
            ),
            "cappedActions": sum(
                bool(result.details.get("capped")) for result in results
            ),
            "restrictedLootFallbackActions": sum(
                bool(result.details.get("restrictedLootFallback"))
                for result in results
            ),
            "tierFloorAppliedActions": sum(
                bool(result.details.get("tierFloorApplied"))
                for result in results
            ),
            "vendorMaterialFallbackActions": sum(
                bool(result.details.get("vendorMaterialFallback"))
                for result in results
            ),
        },
        "coverage": coverage,
        "totals": {
            "discovered": len(results),
            "valued": len(valued),
            "excluded": len(exclusions),
            "silentGaps": len(gaps),
        },
        "exclusionReasons": dict(sorted(reason_counts.items())),
        "exclusions": exclusions,
        "statisticsByProfessionTier": stats,
        "statisticsByProfessionTierRaw": raw_stats,
        "actions": action_rows,
        "silentGaps": sorted(set(gaps)),
    }


def render_lua(results: Sequence[Result], source_hash: str, base_xp_cap: int) -> str:
    lines = [
        "-- Generated by tools/gen_profession_xp.py. DO NOT EDIT.",
        f"-- Source snapshot SHA-256: {source_hash}",
        "local M = {}",
        "",
        "M.ACTION = {",
    ]
    for kind, action_id in ACTION.items():
        lines.append(f"    {ACTION_LUA_NAMES[kind]} = {action_id},")
    lines += [
        "}",
        "",
        "-- Defensive bound for any manual override that opts into per-unit XP.",
        "M.MAX_PER_UNIT_QUANTITY = 4",
        f"M.BASE_XP_CAP = {base_xp_cap}",
        "",
        "local DATA = {",
    ]
    for kind, action_id in ACTION.items():
        lines.append(f"    [{action_id}] = {{")
        for result in results:
            if result.kind != kind or result.xp is None:
                continue
            lines.append(
                f"        [{result.context}] = "
                + "{%d,%d,%d,%s,%s,%d,%s}," % (
                    int(result.xp),
                    result.skill,
                    result.tier,
                    "true" if result.per_unit else "false",
                    lua_quote(result.label),
                    int(result.details.get("uncappedXp", result.xp)),
                    "true" if result.details.get("capped") else "false",
                )
            )
        lines.append("    },")
    lines += ["}", "", "local EXCLUSIONS = {"]
    for result in results:
        if result.reason:
            lines.append(
                f"    [{lua_quote(result.key)}] = {lua_quote(result.reason)},"
            )
    lines += [
        "}",
        "",
        "local ACTION_NAMES = {",
    ]
    for kind, action_id in ACTION.items():
        lines.append(f"    [{action_id}] = {lua_quote(kind)},")
    lines += [
        "}",
        "",
        "function M.Resolve(actionKind, skillId, contextId, quantity)",
        "    actionKind = tonumber(actionKind)",
        "    skillId = tonumber(skillId)",
        "    contextId = tonumber(contextId)",
        "    if not actionKind or not contextId or not DATA[actionKind] then",
        "        return nil, \"invalid_action_context\"",
        "    end",
        "    local row = DATA[actionKind][contextId]",
        "    if not row then",
        "        local kindName = ACTION_NAMES[actionKind]",
        "        local reason = kindName and EXCLUSIONS[kindName .. \":\" .. contextId]",
        "        return nil, reason or \"unsupported_action\"",
        "    end",
        "    if row[2] ~= 0 and skillId ~= row[2] then",
        "        return nil, \"skill_mismatch\"",
        "    end",
        "    local units = 1",
        "    if row[4] then",
        "        units = math.min(M.MAX_PER_UNIT_QUANTITY, math.max(1, math.floor(tonumber(quantity) or 1)))",
        "    end",
        "    local xp = row[1] * units",
        "    return xp, {",
        "        actionKind = actionKind,",
        "        contextId = contextId,",
        "        skillId = row[2],",
        "        tier = row[3],",
        "        perUnit = row[4],",
        "        quantity = units,",
        "        baseXP = row[1],",
        "        label = row[5],",
        "        uncappedXP = row[6],",
        "        capped = row[7],",
        "    }",
        "end",
        "",
        "package.loaded[\"paragon.modules.paragon_profession_data\"] = M",
        "ParagonProfessionData = M",
        "return M",
        "",
    ]
    return "\n".join(lines)


def load_overrides(path: pathlib.Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "items": {}, "actions": {}}
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("version") != 1:
        raise GenerationError("profession override file must have version 1")
    if not isinstance(value.get("items", {}), dict) or not isinstance(value.get("actions", {}), dict):
        raise GenerationError("profession overrides items/actions must be objects")
    return value


def generate(args: argparse.Namespace) -> tuple[str, str, dict[str, Any]]:
    source = Source(args)
    overrides = load_overrides(pathlib.Path(args.overrides))
    with tempfile.TemporaryDirectory(prefix="paragon-profession-dbc-") as temp:
        paths = source.materialize_dbcs(pathlib.Path(temp))
        spell_dbc = DBC.load(paths["Spell"])
        ability_dbc = DBC.load(paths["SkillLineAbility"])
        lock_dbc = DBC.load(paths["Lock"])
        area_dbc = DBC.load(paths["AreaTable"])
        # Load and checksum the remaining routing DBCs even though their active
        # numeric data is represented by world tables in this model.
        DBC.load(paths["SkillLine"])
        DBC.load(paths["Map"])
        spells = parse_spells(spell_dbc)
        overlay_spells(source, spells)
        abilities = parse_skill_abilities(ability_dbc)
        overlay_skill_abilities(source, abilities)
        locks = parse_locks(lock_dbc)
        overlay_locks(source, locks)
        areas = parse_areas(area_dbc)
        overlay_areas(source, areas)

    items = load_items(source)
    conditions = load_conditions(source)
    stores = load_loot(source, conditions)
    loot = LootResolver(stores)
    extra, perfect, cooldowns, trainers = load_craft_metadata(source)
    recipes, recipe_skips = build_recipes(
        spells, abilities, trainers, items, extra, perfect, cooldowns, loot
    )
    cyclic = cyclic_recipe_components(recipes)
    for index, component in cyclic.items():
        recipes[index].cyclic = True
        recipes[index].cyclic_items = component
    unlimited_vendor = load_unlimited_vendors(source)
    values, vendor_only = build_item_values(
        items, recipes, stores, unlimited_vendor, overrides
    )
    results = build_results(
        source,
        items,
        locks,
        areas,
        recipes,
        recipe_skips,
        stores,
        loot,
        values,
        vendor_only,
        overrides,
    )
    audit = audit_payload(source, results, items, recipes, vendor_only, loot)
    if audit["silentGaps"]:
        raise GenerationError(
            "silent profession action gaps: " + ", ".join(audit["silentGaps"][:20])
        )
    lua = render_lua(
        results,
        audit["source"]["snapshotSha256"],
        args.base_xp_cap,
    )
    audit_text = json.dumps(audit, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    return lua, audit_text, audit


def write_or_check(path: pathlib.Path, content: str, check: bool) -> None:
    if check:
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            raise GenerationError(f"generated artifact is stale: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=path.name + ".",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(content)
            temporary_name = handle.name
        os.replace(temporary_name, path)
        temporary_name = None
    finally:
        if temporary_name:
            pathlib.Path(temporary_name).unlink(missing_ok=True)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    root = pathlib.Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dbc-dir", help="host directory containing active DBC files")
    parser.add_argument("--dbc-container", default="ac-worldserver")
    parser.add_argument("--dbc-root", default="/azerothcore/env/dist/data/dbc")
    parser.add_argument("--database-container", default="ac-database")
    parser.add_argument("--database-name", default="acore_world")
    parser.add_argument(
        "--base-xp-cap",
        type=int,
        default=5000,
        help="hard cap applied to each generated base action reward",
    )
    parser.add_argument(
        "--overrides",
        default=str(root / "tools" / "profession_xp_overrides.json"),
    )
    parser.add_argument(
        "--output",
        default=str(root / "serverside" / "paragon" / "modules" / "paragon_profession_data.lua"),
    )
    parser.add_argument(
        "--audit",
        default=str(root / "tools" / "generated" / "profession_xp_audit.json"),
    )
    parser.add_argument("--check", action="store_true", help="fail instead of writing stale artifacts")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.base_xp_cap <= 0:
        print("profession XP generation failed: --base-xp-cap must be positive", file=sys.stderr)
        return 1
    try:
        lua, audit_text, audit = generate(args)
        write_or_check(pathlib.Path(args.output), lua, args.check)
        write_or_check(pathlib.Path(args.audit), audit_text, args.check)
    except (GenerationError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"profession XP generation failed: {error}", file=sys.stderr)
        return 1
    totals = audit["totals"]
    print(
        "profession XP: "
        f"{totals['valued']} valued, {totals['excluded']} excluded, "
        f"{totals['silentGaps']} silent gaps / {totals['discovered']} discovered"
    )
    print(f"Lua: {args.output}")
    print(f"Audit: {args.audit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
