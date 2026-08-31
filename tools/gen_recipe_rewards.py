#!/usr/bin/env python3
"""Generate the account-wide, one-time profession-recipe XP catalogue.

The catalogue is deliberately built from the same active WotLK DBC and world
database snapshot as ``gen_profession_xp.py``.  A row is a *final craft spell*,
never a teaching item or a generic ``Learn Spell`` wrapper.  Acquisition paths
collapse onto that final spell ID, so alternate patterns and specialization
switches cannot create additional entitlements.

The generator is strict and reproducible:

* every discovered final craft spell is classified as rewardable or
  quarantined with a reason;
* trainer/automatic, quest, recipe-item and discovery paths are resolved in
  that order (the easiest real path wins for valuation);
* recipe-item paths retain vendor stock/currency/reputation, quest and complete
  loot-reference information instead of treating every pattern equally;
* the 3,481 rewardable spells sum to exactly 140,000,000 XP, have a 5,000 XP
  floor, a 1,000,000 XP ceiling and are rounded to 1,000 XP;
* generated Lua and JSON audit artifacts can be checked byte-for-byte with
  ``--check``.
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import hashlib
import json
import math
import pathlib
import sys
import tempfile
from typing import Any, Iterable, Mapping, Sequence

TOOLS_DIR = str(pathlib.Path(__file__).resolve().parent)
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)
import gen_profession_xp as profession


TOOL_VERSION = 2
BUDGET = 140_000_000
FLOOR = 5_000
CAP = 1_000_000
ROUNDING = 1_000
EXPECTED_DISCOVERED = 3_558
EXPECTED_REWARDABLE = 3_481
EXPECTED_QUARANTINED = 77

# A vendor relationship is present for this otherwise item-only recipe, but
# its sole vendor template has no world spawn in the active snapshot.  Keeping
# this explicit prevents a dangling template row from pretending to be an
# obtainable path.  If the scripted vendor is enabled later, remove this
# override and let the generator fail its expected-count assertion so the
# catalogue version is deliberately reviewed and advanced.
SOURCE_QUARANTINE: dict[int, str] = {
    15853: "vendor_template_12246_has_no_active_world_spawn",
}

SOURCE_PRECEDENCE = {
    "trainer": 0,
    "quest": 1,
    "recipe_item": 2,
    "discovery": 3,
}


@dataclasses.dataclass(frozen=True)
class Path:
    kind: str
    label: str
    score: float
    details: Mapping[str, Any]


@dataclasses.dataclass
class Reward:
    spell_id: int
    skill_id: int
    name: str
    source: str
    score: float
    paths: list[Path]
    xp: int = 0


def integer(value: str | int | float | None, default: int = 0) -> int:
    if value in (None, "", r"\N"):
        return default
    return int(value)


def minimum_reputation_rank(mask: int) -> int:
    """Decode AzerothCore CONDITION_REPUTATION_RANK's allowed-rank mask."""
    mask = max(0, int(mask))
    for rank in range(8):
        if mask & (1 << rank):
            return rank
    return 0


def chunks(values: Sequence[str], size: int) -> Iterable[Sequence[str]]:
    for start in range(0, len(values), size):
        yield values[start:start + size]


def triggered_recipe_ids(
    spell_id: int,
    spells: Mapping[int, profession.Spell],
    recipe_ids: set[int],
) -> set[int]:
    """Resolve one level of the WotLK Learn Spell wrapper contract."""
    output: set[int] = set()
    if spell_id in recipe_ids:
        output.add(spell_id)
    spell = spells.get(spell_id)
    if spell:
        output.update(
            trigger for trigger in spell.trigger_spells if trigger in recipe_ids
        )
    return output


def load_catalog(args: argparse.Namespace) -> dict[str, Any]:
    source = profession.Source(args)
    with tempfile.TemporaryDirectory(prefix="paragon-recipe-dbc-") as temp:
        paths = source.materialize_dbcs(pathlib.Path(temp))
        spell_dbc = profession.DBC.load(paths["Spell"])
        ability_dbc = profession.DBC.load(paths["SkillLineAbility"])
        spells = profession.parse_spells(spell_dbc)
        profession.overlay_spells(source, spells)
        abilities = profession.parse_skill_abilities(ability_dbc)
        profession.overlay_skill_abilities(source, abilities)
        # materialize_dbcs intentionally hashes every routing DBC.  The
        # remaining files are validated by the shared generator contract.
        profession.DBC.load(paths["SkillLine"])
        profession.DBC.load(paths["Lock"])
        profession.DBC.load(paths["AreaTable"])
        profession.DBC.load(paths["Map"])

    items = profession.load_items(source)
    conditions = profession.load_conditions(source)
    stores = profession.load_loot(source, conditions)
    loot = profession.LootResolver(stores)
    extra, perfect, cooldowns, trainers = profession.load_craft_metadata(source)
    recipes, recipe_skips = profession.build_recipes(
        spells, abilities, trainers, items, extra, perfect, cooldowns, loot
    )
    if recipe_skips:
        raise profession.GenerationError(
            "recipe catalogue has unresolved craft candidates: "
            + json.dumps(recipe_skips[:10], sort_keys=True)
        )
    if len(recipes) != EXPECTED_DISCOVERED:
        raise profession.GenerationError(
            f"expected {EXPECTED_DISCOVERED} final craft spells, found {len(recipes)}"
        )

    return {
        "source": source,
        "spell_dbc": spell_dbc,
        "ability_dbc": ability_dbc,
        "spells": spells,
        "abilities": abilities,
        "items": items,
        "stores": stores,
        "recipes": recipes,
        "trainers": trainers,
    }


def load_recipe_sources(catalog: Mapping[str, Any]) -> dict[str, Any]:
    source: profession.Source = catalog["source"]
    spells: Mapping[int, profession.Spell] = catalog["spells"]
    items: Mapping[int, profession.Item] = catalog["items"]
    recipes: Sequence[profession.Recipe] = catalog["recipes"]
    trainers: Mapping[int, tuple[int, int]] = catalog["trainers"]
    ability_dbc: profession.DBC = catalog["ability_dbc"]
    stores: Mapping[str, Mapping[int, Sequence[profession.LootRow]]] = catalog["stores"]
    recipe_ids = {recipe.spell.spell_id for recipe in recipes}

    trainer_paths: dict[int, list[Path]] = collections.defaultdict(list)
    for spell_id, (skill_id, rank) in sorted(trainers.items()):
        for final_id in triggered_recipe_ids(spell_id, spells, recipe_ids):
            trainer_paths[final_id].append(Path(
                "trainer",
                "profession trainer",
                1.0,
                {"trainerSpell": spell_id, "skill": skill_id, "rank": rank},
            ))

    # SkillLineAbility.AcquireMethod == 1 denotes an automatically acquired
    # profession spell in the 3.3.5a layout.  These are floor rewards just like
    # ordinary trainers.
    for row in ability_dbc.rows:
        skill_id = profession.signed32(row[1])
        spell_id = profession.signed32(row[2])
        acquire_method = profession.signed32(row[9])
        if skill_id not in profession.PROFESSIONS or acquire_method != 1:
            continue
        for final_id in triggered_recipe_ids(spell_id, spells, recipe_ids):
            trainer_paths[final_id].append(Path(
                "automatic",
                "automatic profession spell",
                1.0,
                {"abilitySpell": spell_id, "skill": skill_id},
            ))

    quest_paths: dict[int, list[Path]] = collections.defaultdict(list)
    quest_rows = source.mysql(
        "SELECT ID,QuestLevel,MinLevel,QuestType,Flags,ABS(RewardSpell),"
        "RewardDisplaySpell FROM quest_template "
        "WHERE RewardSpell<>0 OR RewardDisplaySpell<>0 ORDER BY ID"
    )
    for row in quest_rows:
        quest_id, level, minimum, quest_type, flags = map(integer, row[:5])
        for teaching_spell in map(integer, row[5:]):
            for final_id in triggered_recipe_ids(teaching_spell, spells, recipe_ids):
                score = 4.0 + min(4.0, max(level, minimum) / 20.0)
                # Repeatable/daily quest flags represent time-gated access.
                if flags & 0x1000:
                    score += 3.0
                quest_paths[final_id].append(Path(
                    "quest",
                    f"quest {quest_id}",
                    score,
                    {
                        "quest": quest_id,
                        "level": level,
                        "minimumLevel": minimum,
                        "questType": quest_type,
                        "flags": flags,
                        "teachingSpell": teaching_spell,
                    },
                ))

    discovery_paths: dict[int, list[Path]] = collections.defaultdict(list)
    for row in source.mysql(
        "SELECT spellId,reqSpell,reqSkillValue,chance "
        "FROM skill_discovery_template ORDER BY spellId,reqSpell"
    ):
        spell_id, required_spell, required_skill = map(integer, row[:3])
        if spell_id not in recipe_ids:
            continue
        chance = abs(float(row[3]))
        # Discovery pools and research cooldowns are materially harder than a
        # trainer.  Explicit low chances receive a bounded additional premium.
        score = 16.0
        if chance > 0:
            score += min(32.0, math.sqrt(100.0 / max(0.01, chance)) * 2.0)
        required = spells.get(required_spell)
        cooldown_seconds = 0
        if required:
            cooldown_seconds = max(
                required.recovery_time, required.category_recovery_time
            ) // 1000
            if cooldown_seconds:
                score += min(16.0, math.log2(1.0 + cooldown_seconds / 3600.0) * 3.0)
        discovery_paths[spell_id].append(Path(
            "discovery",
            "profession discovery/research",
            score,
            {
                "requiredSpell": required_spell,
                "requiredSkill": required_skill,
                "chance": chance,
                "cooldownSeconds": cooldown_seconds,
            },
        ))

    teaching_items: dict[int, list[int]] = collections.defaultdict(list)
    for item in items.values():
        if item.item_class != 9 or item.required_skill not in profession.PROFESSIONS:
            continue
        for final_id in item.spell_ids:
            if final_id in recipe_ids:
                teaching_items[final_id].append(item.entry)

    item_paths = load_item_paths(source, stores, items, teaching_items)
    return {
        "trainer": trainer_paths,
        "quest": quest_paths,
        "discovery": discovery_paths,
        "teaching_items": teaching_items,
        "item_paths": item_paths,
    }


def loot_probability(
    rows: Sequence[profession.LootRow], target: profession.LootRow
) -> float:
    """Return the row probability using AzerothCore grouped-loot semantics."""
    if target.group == 0:
        return min(1.0, target.chance / 100.0) if target.chance > 0 else 1.0
    group = [row for row in rows if row.group == target.group]
    explicit = sum(row.chance / 100.0 for row in group if row.chance > 0)
    zero_count = sum(1 for row in group if row.chance <= 0)
    if target.chance > 0:
        return (target.chance / 100.0) * (1.0 / explicit if explicit > 1.0 and not zero_count else 1.0)
    return max(0.0, 1.0 - explicit) / max(1, zero_count)


def resolve_reference_item_probabilities(
    stores: Mapping[str, Mapping[int, Sequence[profession.LootRow]]],
    entry: int,
    stack: tuple[int, ...] = (),
) -> dict[int, float]:
    if entry in stack:
        return {}
    output: dict[int, float] = collections.defaultdict(float)
    rows = stores["reference"].get(entry, ())
    for row in rows:
        probability = loot_probability(rows, row)
        if row.reference > 0:
            for item_id, nested in resolve_reference_item_probabilities(
                stores, row.reference, stack + (entry,)
            ).items():
                output[item_id] += probability * nested
        elif row.item > 0:
            output[row.item] += probability
    return dict(output)


def load_item_paths(
    source: profession.Source,
    stores: Mapping[str, Mapping[int, Sequence[profession.LootRow]]],
    items: Mapping[int, profession.Item],
    teaching_items: Mapping[int, Sequence[int]],
) -> dict[int, list[Path]]:
    interest = {item for values in teaching_items.values() for item in values}
    by_item: dict[int, list[Path]] = collections.defaultdict(list)

    reputation: dict[tuple[int, int], int] = collections.defaultdict(int)
    for row in source.mysql(
        "SELECT SourceGroup,SourceEntry,ConditionValue2 "
        "FROM conditions WHERE SourceTypeOrReferenceId=23 "
        "AND ConditionTypeOrReference=5 ORDER BY SourceGroup,SourceEntry"
    ):
        key = (integer(row[0]), integer(row[1]))
        reputation[key] = max(
            reputation[key], minimum_reputation_rank(integer(row[2]))
        )

    spawned_creatures = {
        integer(row[0]) for row in source.mysql("SELECT DISTINCT id FROM creature")
    }
    vendor_sql = (
        "SELECT entry,item,maxcount,incrtime,ExtendedCost FROM npc_vendor "
        "WHERE item>0 ORDER BY entry,item,slot"
    )
    for row in source.mysql(vendor_sql):
        vendor, item_id, maximum, restock, extended = map(integer, row)
        if item_id not in interest:
            continue
        item = items[item_id]
        score = 2.0
        gold = max(0, item.buy_price)
        if gold:
            score += min(12.0, math.log10(1.0 + gold / 10_000.0) * 3.0)
        if extended:
            score += 10.0
        if maximum:
            score += 4.0 + min(12.0, math.log2(1.0 + max(0, restock) / 60.0))
        rep_rank = reputation.get((vendor, item_id), 0)
        if rep_rank:
            score += max(0, rep_rank - 3) * 4.0
        # A dangling vendor template is not proof of an active path.  The one
        # known sole-template case is quarantined at final-spell level below;
        # other scripted/event vendors retain their explicit relationships.
        by_item[item_id].append(Path(
            "vendor",
            "limited vendor" if maximum else "vendor",
            score,
            {
                "vendor": vendor,
                "spawned": vendor in spawned_creatures,
                "goldCopper": gold,
                "stock": maximum,
                "restockSeconds": restock,
                "extendedCost": extended,
                "reputationRank": rep_rank,
            },
        ))

    for row in source.mysql(
        "SELECT eventEntry,guid,item,maxcount,incrtime,ExtendedCost "
        "FROM game_event_npc_vendor WHERE item>0 "
        "ORDER BY eventEntry,guid,item,slot"
    ):
        event_id, guid, item_id, maximum, restock, extended = map(integer, row)
        if item_id not in interest:
            continue
        score = 10.0 + (8.0 if extended else 0.0)
        if maximum:
            score += 4.0 + min(12.0, math.log2(1.0 + restock / 60.0))
        by_item[item_id].append(Path(
            "event_vendor",
            f"event {event_id} vendor",
            score,
            {
                "event": event_id,
                "guid": guid,
                "stock": maximum,
                "restockSeconds": restock,
                "extendedCost": extended,
            },
        ))

    reward_columns = [f"RewardItem{i}" for i in range(1, 5)] + [
        f"RewardChoiceItemID{i}" for i in range(1, 7)
    ]
    for row in source.mysql(
        "SELECT ID,QuestLevel,MinLevel,Flags," + ",".join(reward_columns)
        + " FROM quest_template ORDER BY ID"
    ):
        quest_id, level, minimum, flags = map(integer, row[:4])
        for item_id in map(integer, row[4:]):
            if item_id not in interest:
                continue
            score = 4.0 + min(8.0, max(level, minimum) / 10.0)
            if flags & 0x1000:
                score += 4.0
            by_item[item_id].append(Path(
                "quest_item",
                f"quest {quest_id} reward",
                score,
                {"quest": quest_id, "level": level, "minimumLevel": minimum, "flags": flags},
            ))

    creature_meta: dict[int, tuple[int, int, int]] = {}
    heroic_entries = {
        integer(value)
        for row in source.mysql(
            "SELECT difficulty_entry_1,difficulty_entry_2,difficulty_entry_3 "
            "FROM creature_template"
        )
        for value in row
        if integer(value) > 0
    }
    for row in source.mysql(
        "SELECT lootid,entry,`rank`,maxlevel FROM creature_template WHERE lootid>0"
    ):
        loot_id, entry, rank, level = map(integer, row)
        candidate = (rank, level, 1 if entry in heroic_entries else 0)
        previous = creature_meta.get(loot_id)
        if previous is None or (candidate[2], candidate[0], candidate[1]) < (
            previous[2], previous[0], previous[1]
        ):
            creature_meta[loot_id] = candidate

    primary_kinds = {
        "creature": 1.0,
        "gameobject": 1.0,
        "item": 1.5,
        "fishing": 1.5,
        "pickpocketing": 1.5,
        "skinning": 1.5,
        "spell": 1.5,
    }
    for kind, access_base in primary_kinds.items():
        for entry, rows in stores[kind].items():
            for row in rows:
                candidates: dict[int, float] = {}
                row_probability = loot_probability(rows, row)
                if row.reference > 0:
                    for item_id, nested in resolve_reference_item_probabilities(
                        stores, row.reference
                    ).items():
                        candidates[item_id] = row_probability * nested
                elif row.item > 0:
                    candidates[row.item] = row_probability
                for item_id, probability in candidates.items():
                    if item_id not in interest or probability <= 0:
                        continue
                    attempts = min(10_000.0, 1.0 / max(0.0001, probability))
                    score = access_base + min(96.0, math.sqrt(attempts) * 2.0)
                    details: dict[str, Any] = {
                        "lootKind": kind,
                        "lootEntry": entry,
                        "probability": round(probability, 8),
                        "meanAttempts": round(attempts, 4),
                        "reference": row.reference,
                        "conditioned": row.conditioned,
                        "questRequired": bool(row.quest),
                    }
                    if kind == "creature":
                        rank, level, heroic = creature_meta.get(entry, (0, 0, 0))
                        access = 1.0
                        if rank == 2:
                            access = 2.0
                        elif rank == 3:
                            access = 2.5 if level >= 80 else 1.75
                        if heroic:
                            access *= 1.5
                        score *= access
                        details.update({"rank": rank, "level": level, "heroic": bool(heroic)})
                    by_item[item_id].append(Path(
                        kind + "_loot",
                        f"{kind} loot",
                        score,
                        details,
                    ))
    return dict(by_item)


def classify(catalog: Mapping[str, Any], sources: Mapping[str, Any]) -> tuple[list[Reward], list[dict[str, Any]]]:
    recipes: Sequence[profession.Recipe] = catalog["recipes"]
    item_paths: Mapping[int, list[Path]] = sources["item_paths"]
    rewards: list[Reward] = []
    quarantine: list[dict[str, Any]] = []

    for recipe in sorted(recipes, key=lambda value: value.spell.spell_id):
        spell_id = recipe.spell.spell_id
        source_kind: str | None = None
        trainer_paths = list(sources["trainer"].get(spell_id, ()))
        quest_paths = list(sources["quest"].get(spell_id, ()))
        recipe_item_paths: list[Path] = []
        for item_id in sources["teaching_items"].get(spell_id, ()):
            recipe_item_paths.extend(item_paths.get(item_id, ()))
        discovery_paths = list(sources["discovery"].get(spell_id, ()))
        # Primary source is only an audit partition.  Valuation still considers
        # every enabled alternate path and picks the genuinely easiest one.
        if trainer_paths:
            source_kind = "trainer"
        elif quest_paths:
            source_kind = "quest"
        elif recipe_item_paths:
            source_kind = "recipe_item"
        elif discovery_paths:
            source_kind = "discovery"
        paths = trainer_paths + quest_paths + recipe_item_paths + discovery_paths

        if spell_id in SOURCE_QUARANTINE:
            source_kind = None
            paths = []
            reason = SOURCE_QUARANTINE[spell_id]
        elif source_kind is None:
            reason = (
                "recipe_item_without_enabled_acquisition_path"
                if sources["teaching_items"].get(spell_id)
                else "no_enabled_learning_path"
            )
        else:
            reason = ""

        if source_kind is None:
            quarantine.append({
                "spell": spell_id,
                "skill": recipe.skill,
                "name": profession.clean_label(recipe.spell.name, f"Spell {spell_id}"),
                "reason": reason,
                "teachingItems": sorted(sources["teaching_items"].get(spell_id, ())),
            })
            continue

        best = min(paths, key=lambda path: (path.score, path.kind, path.label))
        rewards.append(Reward(
            spell_id=spell_id,
            skill_id=recipe.skill,
            name=profession.clean_label(recipe.spell.name, f"Spell {spell_id}"),
            source=source_kind,
            score=max(1.0, best.score),
            paths=sorted(paths, key=lambda path: (path.score, path.kind, path.label)),
        ))

    if len(rewards) != EXPECTED_REWARDABLE or len(quarantine) != EXPECTED_QUARANTINED:
        raise profession.GenerationError(
            f"recipe classification drift: {len(rewards)} rewardable / "
            f"{len(quarantine)} quarantined; expected {EXPECTED_REWARDABLE} / "
            f"{EXPECTED_QUARANTINED}"
        )
    return rewards, quarantine


def assign_budget(rewards: list[Reward]) -> None:
    floor_total = FLOOR * len(rewards)
    if floor_total > BUDGET:
        raise profession.GenerationError("recipe XP floor exceeds budget")
    extra_units = (BUDGET - floor_total) // ROUNDING
    cap_units = (CAP - FLOOR) // ROUNDING
    weights = [max(0.0, reward.score ** 0.75 - 1.0) for reward in rewards]
    if not any(weights):
        raise profession.GenerationError("recipe rarity model produced no premium weights")

    # Water-fill against the cap, then allocate integer 1,000-XP units by
    # largest fractional remainder.  Ties are stable by spell ID.
    units = [0] * len(rewards)
    remaining = extra_units
    active = set(range(len(rewards)))
    while active and remaining > 0:
        weight_total = sum(weights[index] for index in active)
        if weight_total <= 0:
            break
        capped: list[int] = []
        for index in active:
            share = remaining * weights[index] / weight_total
            if share >= cap_units - units[index]:
                remaining -= cap_units - units[index]
                units[index] = cap_units
                capped.append(index)
        if not capped:
            exact = {
                index: remaining * weights[index] / weight_total for index in active
            }
            assigned = 0
            for index, value in exact.items():
                amount = min(cap_units - units[index], math.floor(value))
                units[index] += amount
                assigned += amount
            remaining -= assigned
            order = sorted(
                active,
                key=lambda index: (-(exact[index] - math.floor(exact[index])), rewards[index].spell_id),
            )
            for index in order:
                if remaining <= 0:
                    break
                if units[index] < cap_units:
                    units[index] += 1
                    remaining -= 1
            break
        active.difference_update(capped)

    if remaining != 0:
        raise profession.GenerationError(f"could not allocate {remaining} recipe XP units")
    for reward, premium_units in zip(rewards, units):
        reward.xp = FLOOR + premium_units * ROUNDING
    if sum(reward.xp for reward in rewards) != BUDGET:
        raise profession.GenerationError("recipe XP normalization missed exact budget")
    if min(reward.xp for reward in rewards) != FLOOR:
        raise profession.GenerationError("recipe XP catalogue no longer reaches its floor")
    if max(reward.xp for reward in rewards) > CAP:
        raise profession.GenerationError("recipe XP catalogue exceeds its cap")


def source_snapshot(catalog: Mapping[str, Any]) -> dict[str, Any]:
    source: profession.Source = catalog["source"]
    generator_hash = hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()
    dbc_hash = hashlib.sha256()
    for name, checksum in sorted(source.dbc_checksums.items()):
        dbc_hash.update(name.encode("utf-8"))
        dbc_hash.update(bytes.fromhex(checksum))
    snapshot = hashlib.sha256(
        source.db_digest.digest() + dbc_hash.digest() + bytes.fromhex(generator_hash)
    ).hexdigest()
    return {
        "database": source.args.database_name,
        "databaseContainer": source.args.database_container,
        "databaseQueries": source.db_query_count,
        "databaseSnapshotSha256": source.db_digest.hexdigest(),
        "dbcSha256": dict(sorted(source.dbc_checksums.items())),
        "generatorSha256": generator_hash,
        "snapshotSha256": snapshot,
    }


def catalog_identity(
    rewards: Sequence[Reward],
    quarantine: Sequence[Mapping[str, Any]],
) -> tuple[str, int]:
    """Return a stable fingerprint and DB-safe version for the runtime catalogue.

    ``paragon_recipe_reward_seed`` uses the integer version to decide whether
    an existing character must be scanned again before new learn events are
    accepted.  Deriving it from the actual generated whitelist makes adding or
    removing a recipe fail safe automatically; nobody has to remember to bump
    a hand-maintained constant.

    Only runtime-relevant fields participate.  Names and verbose acquisition
    evidence are deliberately excluded so editorial audit changes do not make
    every character rescan, while changes to membership, XP, profession, or
    source classification do.
    """
    payload = {
        "rewards": [
            [reward.spell_id, reward.skill_id, reward.xp, reward.source]
            for reward in sorted(rewards, key=lambda row: row.spell_id)
        ],
        "quarantine": [
            [
                integer(row.get("spell")),
                integer(row.get("skill")),
            ]
            for row in sorted(quarantine, key=lambda row: integer(row.get("spell")))
        ],
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    fingerprint = hashlib.sha256(encoded).hexdigest()
    # ALE runs Lua 5.2, whose ``string.format("%d", value)`` only accepts a
    # signed C-int even though the SQL column itself is unsigned.  Keep the
    # derived token inside that runtime boundary.
    version = int(fingerprint[:8], 16) & 0x7FFFFFFF
    if version == 0:
        # The SQL column is UNSIGNED, but zero conventionally means
        # "unversioned".  A second independent digest lane keeps that sentinel
        # out of generated output without making the result non-deterministic.
        version = (int(fingerprint[8:16], 16) & 0x7FFFFFFF) or 1
    return fingerprint, version


def render_lua(
    rewards: Sequence[Reward],
    snapshot: str,
    catalog_sha256: str,
    catalog_version: int,
) -> str:
    rows = [
        "-- Generated by tools/gen_recipe_rewards.py. DO NOT EDIT.",
        f"-- Source snapshot SHA-256: {snapshot}",
        f"-- Runtime catalogue SHA-256: {catalog_sha256}",
        "local M = {}",
        "",
        f"M.VERSION = {catalog_version}",
        f'M.CATALOG_SHA256 = "{catalog_sha256}"',
        f"M.BUDGET = {BUDGET}",
        f"M.COUNT = {len(rewards)}",
        f"M.FLOOR = {FLOOR}",
        f"M.CAP = {CAP}",
        "",
        "local DATA = {",
    ]
    for reward in rewards:
        rows.append(
            f"    [{reward.spell_id}] = "
            + "{%d,%d,%s,%s}," % (
                reward.xp,
                reward.skill_id,
                profession.lua_quote(reward.name),
                profession.lua_quote(reward.source),
            )
        )
    rows += [
        "}",
        "",
        "function M.Get(spellId)",
        "    spellId = tonumber(spellId)",
        "    local row = spellId and DATA[spellId]",
        "    if not row then return nil end",
        "    return { spellId = spellId, xp = row[1], skillId = row[2], name = row[3], source = row[4] }",
        "end",
        "",
        "function M.Iterate()",
        "    return pairs(DATA)",
        "end",
        "",
        "package.loaded[\"paragon.modules.paragon_recipe_data\"] = M",
        "ParagonRecipeData = M",
        "return M",
        "",
    ]
    return "\n".join(rows)


def audit_payload(
    catalog: Mapping[str, Any],
    rewards: Sequence[Reward],
    quarantine: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    source = source_snapshot(catalog)
    catalog_sha256, catalog_version = catalog_identity(rewards, quarantine)
    counts = collections.Counter(reward.source for reward in rewards)
    values = [reward.xp for reward in rewards]

    def summarize_paths(reward: Reward) -> dict[str, Any]:
        # Some world-drop recipes have thousands of equivalent creature and
        # reference relationships.  The generator evaluates all of them, but
        # writing every duplicate path would produce a >200 MB audit that
        # cannot be reviewed or hosted.  Preserve the chosen route, counts by
        # kind, and the easiest representative of every kind.
        representatives: dict[str, Path] = {}
        for path in reward.paths:
            current = representatives.get(path.kind)
            if current is None or (path.score, path.label) < (
                current.score, current.label
            ):
                representatives[path.kind] = path
        return {
            "chosenPath": dataclasses.asdict(reward.paths[0]),
            "pathCount": len(reward.paths),
            "pathCountsByKind": dict(sorted(collections.Counter(
                path.kind for path in reward.paths
            ).items())),
            "easiestPathByKind": [
                dataclasses.asdict(representatives[kind])
                for kind in sorted(representatives)
            ],
        }

    return {
        "schemaVersion": 1,
        "toolVersion": TOOL_VERSION,
        "catalogVersion": catalog_version,
        "catalogSha256": catalog_sha256,
        "source": source,
        "model": {
            "budget": BUDGET,
            "floor": FLOOR,
            "cap": CAP,
            "rounding": ROUNDING,
            "weight": "easiest_path_score^0.75 - 1",
            "allocation": "cap-aware largest fractional remainder",
            "rewardKey": "final craft spell ID",
            "sourcePrecedence": ["trainer", "quest", "recipe_item", "discovery"],
        },
        "totals": {
            "discovered": len(rewards) + len(quarantine),
            "rewardable": len(rewards),
            "quarantined": len(quarantine),
            "xp": sum(values),
            "minimumXp": min(values),
            "maximumXp": max(values),
        },
        "countsByPrimarySource": dict(sorted(counts.items())),
        "rewards": [
            {
                "spell": reward.spell_id,
                "skill": reward.skill_id,
                "name": reward.name,
                "source": reward.source,
                "score": round(reward.score, 6),
                "xp": reward.xp,
                **summarize_paths(reward),
            }
            for reward in rewards
        ],
        "quarantine": list(quarantine),
    }


def generate(args: argparse.Namespace) -> tuple[str, str, dict[str, Any]]:
    catalog = load_catalog(args)
    sources = load_recipe_sources(catalog)
    rewards, quarantine = classify(catalog, sources)
    assign_budget(rewards)
    audit = audit_payload(catalog, rewards, quarantine)
    lua = render_lua(
        rewards,
        audit["source"]["snapshotSha256"],
        audit["catalogSha256"],
        audit["catalogVersion"],
    )
    return lua, json.dumps(audit, indent=2, sort_keys=True, ensure_ascii=False) + "\n", audit


def write_or_check(path: pathlib.Path, content: str, check: bool) -> None:
    if check:
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            raise profession.GenerationError(f"generated artifact is stale: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    root = pathlib.Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dbc-dir", help="host directory containing active DBC files")
    parser.add_argument("--dbc-container", default="ac-worldserver")
    parser.add_argument("--dbc-root", default="/azerothcore/env/dist/data/dbc")
    parser.add_argument("--database-container", default="ac-database")
    parser.add_argument("--database-name", default="acore_world")
    parser.add_argument(
        "--output",
        default=str(root / "serverside" / "paragon" / "modules" / "paragon_recipe_data.lua"),
    )
    parser.add_argument(
        "--audit",
        default=str(root / "tools" / "generated" / "recipe_reward_audit.json"),
    )
    parser.add_argument("--check", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        lua, audit_text, audit = generate(args)
        write_or_check(pathlib.Path(args.output), lua, args.check)
        write_or_check(pathlib.Path(args.audit), audit_text, args.check)
    except (profession.GenerationError, OSError, ValueError) as error:
        print(f"recipe reward generation failed: {error}", file=sys.stderr)
        return 1
    totals = audit["totals"]
    print(
        f"recipe rewards: {totals['rewardable']} rewardable, "
        f"{totals['quarantined']} quarantined, {totals['xp']} XP"
    )
    print(f"Lua: {args.output}")
    print(f"Audit: {args.audit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
