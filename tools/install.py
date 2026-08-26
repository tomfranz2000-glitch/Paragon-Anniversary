#!/usr/bin/env python3
"""Reproduce and verify a complete Paragon Anniversary installation.

This is the one orchestration entrypoint for the repository's install-time
generators.  It deliberately does not clone, patch, or build AzerothCore: the
pinned core/module revisions must already be built and AzerothCore's normal
auth/characters/world import must already be complete.

Examples (run from the repository root):

  python tools/install.py --dry-run --core-root /srv/azerothcore \
      --client-root /games/WowWotlk
  python tools/install.py --apply --core-root /srv/azerothcore \
      --client-root /games/WowWotlk
  python tools/install.py --check --core-root /srv/azerothcore \
      --client-root /games/WowWotlk

``--apply`` is rerunnable for both a fresh Paragon install and an upgrade.  It
always seeds current collections with INSERT IGNORE before the server starts,
so reinstalling cannot turn old collections into a retroactive XP windfall.
``--check`` is read-only.  ``--dry-run`` prints the exact ordered plan without
probing containers, reading secrets, writing files, or changing databases.
"""

import argparse
import dataclasses
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


ROOT = pathlib.Path(__file__).resolve().parents[1]
SQL_ENTRYPOINT = ROOT / "sql" / "install.sql"

APPLY_PHASES = (
    "preflight",
    "repository-tests",
    "database-bootstrap",
    "static-data",
    "profession-data",
    "server-payload",
    "class-data",
    "content-and-client-dbc",
    "collection-xp",
    "quest-xp",
    "ui-art",
    "client-addon",
    "verification",
)

CHECK_PHASES = (
    "preflight",
    "repository-tests",
    "static-data",
    "profession-data",
    "server-payload",
    "collection-xp",
    "quest-xp",
    "database-content",
    "client-payload",
)

STATIC_GENERATORS = (
    "gen_glyph_data.py",
    "gen_gem_data.py",
    "gen_mount_data.py",
    "gen_companion_data.py",
    "gen_enchant_text.py",
)

REPRODUCTION_TOOLS = (
    "build_mpq.py",
    "build_ui_art.py",
    "gen_class_talents.py",
    "gen_class_trainers.py",
    "paragon_client_patch.py",
)

CLASS_GENERATED_OUTPUTS = (
    "class_talent_ranks.py",
    "class_trainer_ranks.py",
)

CLIENT_PATCH_DBC_INPUTS = (
    "Achievement.dbc",
    "Achievement_Category.dbc",
    "Achievement_Criteria.dbc",
    "CharTitles.dbc",
    "SkillLineAbility.dbc",
    "SkillRaceClassInfo.dbc",
    "Spell.dbc",
    "SpellIcon.dbc",
    "Talent.dbc",
    "TalentTab.dbc",
)

REQUIRED_ALE_TABLES = (
    "account_paragon",
    "character_paragon",
    "character_paragon_stats",
    "paragon_banked_experience",
    "paragon_codex_alloc",
    "paragon_collectible_item_xp",
    "paragon_collectible_spell_xp",
    "paragon_config",
    "paragon_config_category",
    "paragon_config_experience_achievement",
    "paragon_config_experience_creature",
    "paragon_config_experience_quest",
    "paragon_config_experience_skill",
    "paragon_config_statistic",
    "paragon_custom_glyph",
    "paragon_profession_progress",
    "paragon_pvp_reward_claim",
    "paragon_racial_pick",
    "paragon_rare_kills",
    "paragon_rewarded_appearance",
    "paragon_rewarded_collectible_spell",
    "paragon_solo_clears",
)

REQUIRED_ALE_TRIGGERS = (
    "paragon_config_statistics_before_insert",
    "paragon_config_statistics_before_update",
)

CANONICAL_WORLD_TABLES = (
    "achievement_category_dbc",
    "achievement_criteria_dbc",
    "achievement_dbc",
    "achievement_reward",
    "chartitles_dbc",
    "npc_trainer",
    "skillraceclassinfo_dbc",
    "spell_bonus_data",
    "spell_dbc",
    "spell_proc",
    "spell_ranks",
    "spell_threat",
    "talent_dbc",
    "trainer_spell",
)

# These ranges are repository-owned namespaces, not merely the set of IDs
# emitted by today's generator.  Using the namespace as the live comparison
# scope makes ``--check`` catch a stale row after its ID has been removed from
# a future generator.  Tables which intentionally replace stock rows (talent,
# skill/race/class metadata, and titles) retain the exact DELETE predicates
# parsed from the canonical SQL below.
CANONICAL_WORLD_RESERVED_SCOPES = {
    "achievement_reward": ("ID BETWEEN 19000 AND 19304",),
    "npc_trainer": ("SpellID >= 1900000 AND SpellID < 2000000",),
    "spell_bonus_data": ("entry >= 1900000 AND entry < 2000000",),
    "spell_dbc": ("ID >= 1900000 AND ID < 2000000",),
    "spell_proc": (
        "ABS(SpellId) >= 1900000 AND ABS(SpellId) < 2000000",
    ),
    "spell_ranks": ("spell_id >= 1900000 AND spell_id < 2000000",),
    "spell_threat": ("entry >= 1900000 AND entry < 2000000",),
    "trainer_spell": ("SpellId >= 1900000 AND SpellId < 2000000",),
}

WORLD_DML_PATTERN = re.compile(
    r"^(?P<verb>DELETE\s+FROM|INSERT\s+INTO)\s+"
    r"(?P<quote>`?)(?P<table>[A-Za-z0-9_]+)(?P=quote)(?=\s|\()",
    re.IGNORECASE)
WORLD_DELETE_PATTERN = re.compile(
    r"^DELETE\s+FROM\s+`?(?P<table>[A-Za-z0-9_]+)`?\s+WHERE\s+"
    r"(?P<condition>.+);$", re.IGNORECASE | re.DOTALL)

EXPECTED_SQL_COMPONENTS = (
    "sql/01_create_database.sql",
    "sql/02_create_tables.sql",
    "sql/03_create_triggers.sql",
    "sql/04_insert_default_config.sql",
    "sql/05_apply_anniversary_config.sql",
)

# ``tools/install.py`` does not apply or build native patches.  It does,
# however, own the deployment contract, so both --apply and --check must reject
# a checkout in which a required native bridge was only partially applied.
# Keep these checks structural: seeing an override or a Lua event symbol is not
# enough when PlayerScript has an explicit enabled-hook list.
ALE_SCRIPT_RELATIVE_PATH = "modules/mod-ale/src/ALE_SC.cpp"
NATIVE_SOURCE_CONTRACTS = (
    (
        "src/server/game/Entities/Player/KillRewarder.cpp",
        (
            (
                "core kill-reward dispatch",
                re.compile(
                    r"\bsScriptMgr\s*->\s*OnPlayerRewardKillRewarder\s*\("),
            ),
        ),
    ),
    (
        "src/server/game/Scripting/ScriptDefines/PlayerScript.cpp",
        (
            (
                "enabled PlayerScript kill-reward dispatch",
                re.compile(
                    r"\bCALL_ENABLED_HOOKS\s*\(\s*PlayerScript\s*,\s*"
                    r"PLAYERHOOK_ON_REWARD_KILL_REWARDER\s*,"),
            ),
            (
                "enabled PlayerScript PvP honor dispatch",
                re.compile(
                    r"\bCALL_ENABLED_HOOKS\s*\(\s*PlayerScript\s*,\s*"
                    r"PLAYERHOOK_ON_PVP_HONOR\s*,"),
            ),
            (
                "enabled PlayerScript outdoor-PvP dispatch",
                re.compile(
                    r"\bCALL_ENABLED_HOOKS\s*\(\s*PlayerScript\s*,\s*"
                    r"PLAYERHOOK_ON_OUTDOOR_PVP_OBJECTIVE\s*,"),
            ),
        ),
    ),
    (
        "src/server/game/Entities/Player/Player.cpp",
        (
            (
                "core PvP honor dispatch",
                re.compile(r"\bsScriptMgr\s*->\s*OnPlayerPvPHonor\s*\("),
            ),
        ),
    ),
    (
        "src/server/game/OutdoorPvP/OutdoorPvP.cpp",
        (
            (
                "core outdoor-PvP objective dispatch",
                re.compile(
                    r"\bsScriptMgr\s*->\s*OnPlayerOutdoorPvPObjective\s*\("),
            ),
        ),
    ),
    (
        "src/server/game/Battlegrounds/Battleground.cpp",
        (
            (
                "core battleground score dispatch",
                re.compile(
                    r"\bsScriptMgr\s*->\s*OnBattlegroundPlayerScoreUpdate\s*\("),
            ),
        ),
    ),
    (
        "src/server/game/Battlefield/Battlefield.cpp",
        (
            (
                "core battlefield start dispatch",
                re.compile(
                    r"\bsScriptMgr\s*->\s*OnBattlefieldWarStart\s*\("),
            ),
            (
                "core battlefield objective dispatch",
                re.compile(
                    r"\bsScriptMgr\s*->\s*OnBattlefieldObjective\s*\("),
            ),
        ),
    ),
    (
        "src/server/game/Scripting/ScriptDefines/AllBattlegroundScript.cpp",
        (
            (
                "enabled battleground score dispatch",
                re.compile(
                    r"\bCALL_ENABLED_HOOKS\s*\(\s*AllBattlegroundScript\s*,\s*"
                    r"ALLBATTLEGROUNDHOOK_ON_BATTLEGROUND_PLAYER_SCORE_UPDATE\s*,"),
            ),
        ),
    ),
    (
        "src/server/game/Scripting/ScriptDefines/BattlefieldScript.cpp",
        (
            (
                "enabled battlefield start dispatch",
                re.compile(
                    r"\bCALL_ENABLED_HOOKS\s*\(\s*BattlefieldScript\s*,\s*"
                    r"BATTLEFIELDHOOK_ON_WAR_START\s*,"),
            ),
            (
                "enabled battlefield objective dispatch",
                re.compile(
                    r"\bCALL_ENABLED_HOOKS\s*\(\s*BattlefieldScript\s*,\s*"
                    r"BATTLEFIELDHOOK_ON_OBJECTIVE\s*,"),
            ),
        ),
    ),
    (
        ALE_SCRIPT_RELATIVE_PATH,
        (
            (
                "ALE kill-reward PlayerScript override",
                re.compile(
                    r"\bvoid\s+OnPlayerRewardKillRewarder\s*\([^)]*\)\s*"
                    r"override"),
            ),
            (
                "ALE-to-Lua kill-reward call",
                re.compile(r"\bsALE\s*->\s*OnKillReward\s*\("),
            ),
            (
                "ALE-to-Lua PvP honor call",
                re.compile(r"\bsALE\s*->\s*OnPvPHonor\s*\("),
            ),
            (
                "ALE-to-Lua outdoor-PvP call",
                re.compile(r"\bsALE\s*->\s*OnOutdoorPvPObjective\s*\("),
            ),
            (
                "PvP battleground settlement tracking",
                re.compile(
                    r"\bsPvPMeritTracker\.OnBattlegroundEndReward\s*\("),
            ),
            (
                "PvP battlefield settlement tracking",
                re.compile(
                    r"\bsPvPMeritTracker\.OnBattlefieldWarEnd\s*\("),
            ),
            (
                "PvP duel settlement tracking",
                re.compile(r"\bsPvPMeritTracker\.OnDuelEnd\s*\("),
            ),
            (
                "ALE BattlefieldScript construction",
                re.compile(r"\bnew\s+ALE_BattlefieldScript\s*\("),
            ),
        ),
    ),
    (
        "modules/mod-ale/src/LuaEngine/Hooks.h",
        (
            (
                "Lua player event 75 declaration",
                re.compile(r"\bPLAYER_EVENT_ON_KILL_REWARD\s*=\s*75\b"),
            ),
            (
                "Lua player event 77 declaration",
                re.compile(r"\bPLAYER_EVENT_ON_PVP_HONOR\s*=\s*77\b"),
            ),
            (
                "Lua player event 78 declaration",
                re.compile(
                    r"\bPLAYER_EVENT_ON_PVP_MATCH_COMPLETE\s*=\s*78\b"),
            ),
            (
                "Lua player event 79 declaration",
                re.compile(
                    r"\bPLAYER_EVENT_ON_PVP_BATTLEFIELD_COMPLETE\s*=\s*79\b"),
            ),
            (
                "Lua player event 80 declaration",
                re.compile(
                    r"\bPLAYER_EVENT_ON_PVP_OUTDOOR_OBJECTIVE\s*=\s*80\b"),
            ),
            (
                "Lua player event 81 declaration",
                re.compile(
                    r"\bPLAYER_EVENT_ON_PVP_DUEL_COMPLETE\s*=\s*81\b"),
            ),
        ),
    ),
    (
        "modules/mod-ale/src/LuaEngine/LuaEngine.h",
        (
            (
                "ALE kill-reward declaration",
                re.compile(r"\bvoid\s+OnKillReward\s*\("),
            ),
            (
                "ALE PvP honor declaration",
                re.compile(r"\bvoid\s+OnPvPHonor\s*\("),
            ),
            (
                "ALE PvP match declaration",
                re.compile(r"\bvoid\s+OnPvPMatchComplete\s*\("),
            ),
            (
                "ALE PvP battlefield declaration",
                re.compile(r"\bvoid\s+OnPvPBattlefieldComplete\s*\("),
            ),
            (
                "ALE outdoor-PvP declaration",
                re.compile(r"\bvoid\s+OnOutdoorPvPObjective\s*\("),
            ),
            (
                "ALE PvP duel declaration",
                re.compile(r"\bvoid\s+OnPvPDuelComplete\s*\("),
            ),
        ),
    ),
    (
        "modules/mod-ale/src/LuaEngine/hooks/PvPMeritHooks.cpp",
        tuple(
            (
                "Lua PvP event %d emission" % event_id,
                re.compile(r"\bSTART_PVP_HOOK\s*\(\s*%s\s*\)" % symbol),
            )
            for event_id, symbol in (
                (77, "PLAYER_EVENT_ON_PVP_HONOR"),
                (78, "PLAYER_EVENT_ON_PVP_MATCH_COMPLETE"),
                (79, "PLAYER_EVENT_ON_PVP_BATTLEFIELD_COMPLETE"),
                (80, "PLAYER_EVENT_ON_PVP_OUTDOOR_OBJECTIVE"),
                (81, "PLAYER_EVENT_ON_PVP_DUEL_COMPLETE"),
            )
        ),
    ),
    (
        "modules/mod-ale/src/PvPMeritTracker.cpp",
        (
            (
                "PvP match settlement emission",
                re.compile(r"\bsALE\s*->\s*OnPvPMatchComplete\s*\("),
            ),
            (
                "PvP battlefield settlement emission",
                re.compile(r"\bsALE\s*->\s*OnPvPBattlefieldComplete\s*\("),
            ),
            (
                "PvP duel settlement emission",
                re.compile(r"\bsALE\s*->\s*OnPvPDuelComplete\s*\("),
            ),
        ),
    ),
    (
        "modules/mod-ale/src/LuaEngine/hooks/PlayerHooks.cpp",
        (
            (
                "Lua event 75 emission",
                re.compile(
                    r"\bvoid\s+ALE::OnKillReward\s*\([^)]*\)\s*\{.*?"
                    r"\bSTART_HOOK\s*\(\s*PLAYER_EVENT_ON_KILL_REWARD\s*\)",
                    re.DOTALL),
            ),
        ),
    ),
    (
        "modules/mod-ale/src/LuaEngine/methods/CreatureMethods.h",
        (
            (
                "at-level creature XP implementation",
                re.compile(r"\bint\s+GetAtLevelXPReward\s*\("),
            ),
        ),
    ),
    (
        "modules/mod-ale/src/LuaEngine/methods/MapMethods.h",
        (
            (
                "map expansion implementation",
                re.compile(
                    r"\bint\s+GetExpansion\s*\([^)]*\)\s*\{.*?"
                    r"\bGetEntry\s*\(\s*\)\s*->\s*Expansion\s*\(\s*\)",
                    re.DOTALL),
            ),
        ),
    ),
    (
        "modules/mod-ale/src/LuaEngine/LuaFunctions.cpp",
        (
            (
                "at-level creature XP Lua registration",
                re.compile(
                    r"\{\s*\"GetAtLevelXPReward\"\s*,\s*"
                    r"&LuaCreature::GetAtLevelXPReward\s*\}"),
            ),
            (
                "map expansion Lua registration",
                re.compile(
                    r"\{\s*\"GetExpansion\"\s*,\s*"
                    r"&LuaMap::GetExpansion\s*\}"),
            ),
        ),
    ),
)

CPP_COMMENT_PATTERN = re.compile(r"//[^\r\n]*|/\*.*?\*/", re.DOTALL)
ALE_ENABLED_HOOK_LIST_CONTRACTS = (
    (
        "ALE_PlayerScript",
        "PlayerScript",
        (
            "PLAYERHOOK_ON_REWARD_KILL_REWARDER",
            "PLAYERHOOK_ON_DUEL_START",
            "PLAYERHOOK_ON_DUEL_END",
            "PLAYERHOOK_ON_BATTLEGROUND_DESERTION",
            "PLAYERHOOK_ON_PVP_HONOR",
            "PLAYERHOOK_ON_OUTDOOR_PVP_OBJECTIVE",
        ),
    ),
    (
        "ALE_BGScript",
        "BGScript",
        (
            "ALLBATTLEGROUNDHOOK_ON_BATTLEGROUND_START",
            "ALLBATTLEGROUNDHOOK_ON_BATTLEGROUND_END_REWARD",
            "ALLBATTLEGROUNDHOOK_ON_BATTLEGROUND_ADD_PLAYER",
            "ALLBATTLEGROUNDHOOK_ON_BATTLEGROUND_REMOVE_PLAYER_AT_LEAVE",
            "ALLBATTLEGROUNDHOOK_ON_BATTLEGROUND_PLAYER_SCORE_UPDATE",
            "ALLBATTLEGROUNDHOOK_ON_BATTLEGROUND_DESTROY",
        ),
    ),
    (
        "ALE_BattlefieldScript",
        "BattlefieldScript",
        (
            "BATTLEFIELDHOOK_ON_PLAYER_JOIN_WAR",
            "BATTLEFIELDHOOK_ON_PLAYER_LEAVE_WAR",
            "BATTLEFIELDHOOK_ON_WAR_END",
            "BATTLEFIELDHOOK_ON_PLAYER_KILL",
            "BATTLEFIELDHOOK_ON_WAR_START",
            "BATTLEFIELDHOOK_ON_OBJECTIVE",
        ),
    ),
)

SOURCE_PATTERN = re.compile(r"^\s*SOURCE\s+(.+?)\s*;\s*$", re.IGNORECASE)


class InstallError(RuntimeError):
    """An actionable installation-contract failure."""


def verify_native_source_contract(core_root: pathlib.Path) -> None:
    """Reject missing/partial native bridges in the selected core checkout.

    Patch text is only an instruction.  This verifies the source that will
    actually be built, including the enabled-hook registrations that make the
    otherwise-present ALE kill-reward and PvP overrides reachable.
    """
    sources: Dict[str, str] = {}
    failures = []
    for relative_path, requirements in NATIVE_SOURCE_CONTRACTS:
        source_path = core_root / relative_path
        try:
            source = source_path.read_text(encoding="utf-8")
        except OSError as error:
            failures.append("missing/unreadable %s: %s" %
                            (relative_path, error))
            continue
        source = CPP_COMMENT_PATTERN.sub("", source)
        sources[relative_path] = source
        for label, pattern in requirements:
            if pattern.search(source) is None:
                failures.append("%s lacks %s" % (relative_path, label))

    ale_source = sources.get(ALE_SCRIPT_RELATIVE_PATH)
    if ale_source is not None:
        for class_name, base_class, required_hooks in \
                ALE_ENABLED_HOOK_LIST_CONTRACTS:
            constructor_pattern = re.compile(
                r"\b%s\s*\(\s*\)\s*:\s*%s\s*\(\s*\"%s\"\s*,\s*"
                r"\{(?P<hooks>.*?)\}\s*\)" % (
                    re.escape(class_name), re.escape(base_class),
                    re.escape(class_name)),
                re.DOTALL)
            constructor = constructor_pattern.search(ale_source)
            if constructor is None:
                failures.append(
                    "%s lacks the %s enabled-hook list" %
                    (ALE_SCRIPT_RELATIVE_PATH, class_name))
                continue
            hooks = constructor.group("hooks")
            for hook in required_hooks:
                if re.search(r"\b%s\b" % re.escape(hook), hooks) is None:
                    failures.append(
                        "%s %s enabled-hook list does not register %s" %
                        (ALE_SCRIPT_RELATIVE_PATH, class_name, hook))

    if failures:
        raise InstallError(
            "patched native source contract is incomplete under %s:\n  - %s\n"
            "Apply the documented patches in order (especially "
            "patches/05-mod-ale.patch, patches/08-core-pvp-merit.patch, and "
            "patches/09-mod-ale-pvp-merit.patch), rebuild the worldserver, "
            "and rerun the installer." %
            (core_root, "\n  - ".join(failures)))


@dataclasses.dataclass(frozen=True)
class Config:
    mode: str
    core_root: pathlib.Path
    client_root: pathlib.Path
    lua_root: pathlib.Path
    database_container: str
    worldserver_container: str
    dbc_dir: Optional[pathlib.Path]
    python: str
    general_name: str
    locale_name: str
    ui_name: str

    @property
    def client_data(self) -> pathlib.Path:
        return self.client_root / "Data"

    @property
    def paragon_source(self) -> pathlib.Path:
        return ROOT / "serverside" / "paragon"

    @property
    def paragon_destination(self) -> pathlib.Path:
        return self.lua_root / "paragon"

    @property
    def extension_source(self) -> pathlib.Path:
        return (self.core_root / "modules" / "mod-ale" / "src" /
                "LuaEngine" / "extensions")

    @property
    def extension_destination(self) -> pathlib.Path:
        return self.lua_root / "extensions"

    @property
    def addon_source(self) -> pathlib.Path:
        return (ROOT / "clientside" / "Interface" / "AddOns" /
                "Paragon")

    @property
    def addon_destination(self) -> pathlib.Path:
        return (self.client_root / "Interface" / "AddOns" / "Paragon")


def _display_command(command: Sequence[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(list(command))
    return shlex.join(command)


def _relative_to_root(path: pathlib.Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def sql_components(entrypoint: pathlib.Path = SQL_ENTRYPOINT,
                   root: pathlib.Path = ROOT) -> Tuple[pathlib.Path, ...]:
    """Return the canonical SOURCE files, rejecting ambiguity and escapes."""
    try:
        lines = entrypoint.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise InstallError("cannot read SQL entrypoint %s: %s" %
                           (entrypoint, error))

    components = []
    resolved_root = root.resolve()
    for line_number, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        match = SOURCE_PATTERN.match(line)
        if not match:
            raise InstallError(
                "%s:%d must be a SOURCE directive (found %r)" %
                (entrypoint, line_number, line))
        raw = match.group(1).strip().strip("'\"")
        component = (root / pathlib.PurePosixPath(raw)).resolve()
        try:
            component.relative_to(resolved_root)
        except ValueError:
            raise InstallError("SQL SOURCE escapes the repository: %s" % raw)
        if not component.is_file():
            raise InstallError("SQL SOURCE does not exist: %s" % raw)
        components.append(component)

    if not components:
        raise InstallError("%s contains no SQL SOURCE directives" % entrypoint)
    relative = tuple(path.relative_to(resolved_root).as_posix()
                     for path in components)
    if relative != EXPECTED_SQL_COMPONENTS:
        raise InstallError(
            "sql/install.sql component order changed: expected %s, found %s" %
            (", ".join(EXPECTED_SQL_COMPONENTS), ", ".join(relative)))
    return tuple(components)


def canonical_world_plan(
        path: Optional[pathlib.Path] = None
) -> Tuple[Tuple[str, ...], Tuple[str, ...], Dict[str, Tuple[str, ...]]]:
    """Parse generated world DML into a temporary-table verification plan.

    Every executable line must be a DELETE or INSERT against an explicitly
    owned table.  Rewriting the target identifier here, before anything is
    sent to MySQL, is the safety boundary that keeps ``--check`` from ever
    changing a persistent world table.  DELETE predicates also define the
    exact live ownership scope, including stale rows the current generator no
    longer inserts.
    """
    source = (ROOT / "sql" / "content" / "01_paragon_content.sql"
              if path is None else path)
    try:
        content = source.read_text(encoding="utf-8")
    except OSError as error:
        raise InstallError("cannot read canonical world SQL %s: %s" %
                           (source, error))

    statements = []
    scopes: Dict[str, List[str]] = {}
    seen = set()
    allowed = set(CANONICAL_WORLD_TABLES)
    statements_in_source = []
    current = []
    in_string = False
    in_identifier = False
    index = 0
    while index < len(content):
        char = content[index]
        following = content[index + 1] if index + 1 < len(content) else ""
        if not in_string and not in_identifier and char == "-" and following == "-":
            after = content[index + 2] if index + 2 < len(content) else ""
            if not after or after.isspace():
                newline = content.find("\n", index + 2)
                index = len(content) if newline < 0 else newline + 1
                continue
        current.append(char)
        if in_string:
            if char == "\\" and following:
                current.append(following)
                index += 2
                continue
            if char == "'":
                if following == "'":
                    current.append(following)
                    index += 2
                    continue
                in_string = False
        elif in_identifier:
            if char == "`":
                in_identifier = False
        elif char == "'":
            in_string = True
        elif char == "`":
            in_identifier = True
        elif char == ";":
            statement = "".join(current).strip()
            if statement:
                statements_in_source.append(statement)
            current = []
        index += 1
    remainder = "".join(current).strip()
    if remainder or in_string or in_identifier:
        raise InstallError("canonical world SQL has an unterminated statement")

    for statement_number, stripped in enumerate(statements_in_source, 1):
        match = WORLD_DML_PATTERN.match(stripped)
        if not match or not stripped.endswith(";"):
            raise InstallError(
                "%s statement %d is not canonical INSERT/DELETE DML" %
                (source, statement_number))
        table = match.group("table").lower()
        if table not in allowed:
            raise InstallError("%s statement %d targets unowned world table %s" %
                               (source, statement_number, table))
        seen.add(table)
        temporary_table = "_paragon_verify_" + table
        start, end = match.span("table")
        statements.append(
            stripped[:start] + temporary_table + stripped[end:])
        if match.group("verb").upper().startswith("DELETE"):
            delete = WORLD_DELETE_PATTERN.match(stripped)
            if not delete or delete.group("table").lower() != table:
                raise InstallError("%s statement %d has an unverifiable DELETE scope" %
                                   (source, statement_number))
            scopes.setdefault(table, []).append(delete.group("condition"))

    if seen != allowed:
        missing = sorted(allowed - seen)
        unexpected = sorted(seen - allowed)
        raise InstallError(
            "canonical world SQL table contract differs (missing: %s; "
            "unexpected: %s)" %
            (", ".join(missing) or "none",
             ", ".join(unexpected) or "none"))
    without_scope = sorted(allowed - set(scopes))
    if without_scope:
        raise InstallError("canonical world tables lack DELETE ownership scopes: %s" %
                           ", ".join(without_scope))
    for table, reserved_scopes in CANONICAL_WORLD_RESERVED_SCOPES.items():
        if table not in allowed:
            raise InstallError("reserved scope targets unowned world table: %s" %
                               table)
        scopes[table] = list(reserved_scopes)
    return (tuple(CANONICAL_WORLD_TABLES), tuple(statements),
            {table: tuple(scopes[table]) for table in CANONICAL_WORLD_TABLES})


def child_environment(config: Config,
                      base: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Preserve the caller environment and add only documented overrides."""
    environment = dict(os.environ if base is None else base)
    environment["PARAGON_CLIENT_DATA"] = str(config.client_data)
    environment["ACORE_DB_CONTAINER"] = config.database_container
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


def _python_command(config: Config, script: str,
                    *arguments: str) -> Tuple[str, ...]:
    return (config.python, str(ROOT / "tools" / script)) + tuple(arguments)


def profession_command(config: Config, check: bool) -> Tuple[str, ...]:
    arguments = ["--database-container", config.database_container]
    if config.dbc_dir is not None:
        arguments.extend(("--dbc-dir", str(config.dbc_dir)))
    else:
        arguments.extend(("--dbc-container", config.worldserver_container))
    if check:
        arguments.append("--check")
    return _python_command(config, "gen_profession_xp.py", *arguments)


def apply_commands(config: Config) -> Tuple[Tuple[str, ...], ...]:
    """Commands in execution order, exposed for contract tests and dry-run."""
    commands = [
        (config.python, "-m", "unittest", "discover", "-s", "tools",
         "-p", "test_*.py"),
    ]
    commands.extend(_python_command(config, script)
                    for script in STATIC_GENERATORS)
    commands.extend((
        profession_command(config, False),
        profession_command(config, True),
        _python_command(config, "gen_class_talents.py", "--emit"),
        _python_command(config, "gen_class_trainers.py", "--emit"),
        _python_command(config, "paragon_client_patch.py", "--apply",
                        "--general-name", config.general_name,
                        "--locale-name", config.locale_name),
        _python_command(config, "paragon_collectible_xp.py", "--seed"),
        _python_command(config, "paragon_collectible_xp.py", "--check"),
        _python_command(config, "populate_quest_paragon_xp.py"),
        _python_command(config, "populate_quest_paragon_xp.py", "--check"),
        _python_command(config, "build_ui_art.py", "--client-data",
                        str(config.client_data), "--output-name",
                        config.ui_name),
        _python_command(config, "check_patch_collisions.py", "--ui-name",
                        config.ui_name, "--general-name", config.general_name,
                        "--locale-name", config.locale_name),
    ))
    return tuple(commands)


def check_commands(config: Config) -> Tuple[Tuple[str, ...], ...]:
    commands = [
        (config.python, "-m", "unittest", "discover", "-s", "tools",
         "-p", "test_*.py"),
    ]
    commands.extend(_python_command(config, script, "--check")
                    for script in STATIC_GENERATORS)
    commands.extend((
        profession_command(config, True),
        _python_command(config, "paragon_collectible_xp.py", "--check"),
        _python_command(config, "populate_quest_paragon_xp.py", "--check"),
        _python_command(config, "check_patch_collisions.py", "--ui-name",
                        config.ui_name, "--general-name", config.general_name,
                        "--locale-name", config.locale_name),
    ))
    return tuple(commands)


def plan_lines(config: Config) -> List[str]:
    """Return a stable, complete apply plan without touching external state."""
    components = sql_components()
    commands = iter(apply_commands(config))
    lines = []
    for number, phase in enumerate(APPLY_PHASES, 1):
        lines.append("%02d. %s" % (number, phase))
        if phase == "preflight":
            lines.append(
                "    verify patched native C++ source contract under %s" %
                config.core_root)
        elif phase == "repository-tests":
            lines.append("    " + _display_command(next(commands)))
        elif phase == "database-bootstrap":
            lines.append("    apply %s via %s" %
                         (_relative_to_root(SQL_ENTRYPOINT),
                          config.database_container))
            for component in components:
                lines.append("      SOURCE " + _relative_to_root(component))
        elif phase == "static-data":
            for _script in STATIC_GENERATORS:
                lines.append("    " + _display_command(next(commands)))
        elif phase == "profession-data":
            lines.append("    " + _display_command(next(commands)))
            lines.append("    " + _display_command(next(commands)))
        elif phase == "server-payload":
            lines.append("    replace %s -> %s" %
                         (config.paragon_source,
                          config.paragon_destination))
            lines.append("    replace %s -> %s" %
                         (config.extension_source,
                          config.extension_destination))
        elif phase == "class-data":
            lines.append("    " + _display_command(next(commands)))
            lines.append("    " + _display_command(next(commands)))
        elif phase == "content-and-client-dbc":
            lines.append("    " + _display_command(next(commands)))
        elif phase in ("collection-xp", "quest-xp"):
            lines.append("    " + _display_command(next(commands)))
            lines.append("    " + _display_command(next(commands)))
        elif phase == "ui-art":
            lines.append("    " + _display_command(next(commands)))
        elif phase == "client-addon":
            lines.append("    replace %s -> %s" %
                         (config.addon_source, config.addon_destination))
        elif phase == "verification":
            lines.append("    verify generated/deployed trees and database invariants")
            lines.append("    " + _display_command(next(commands)))
    try:
        unexpected = next(commands)
    except StopIteration:
        unexpected = None
    if unexpected is not None:
        raise AssertionError("unplanned command: %r" % (unexpected,))
    return lines


class Pipeline:
    def __init__(self, config: Config):
        self.config = config
        self.environment = child_environment(config)
        # Every invocation starts from an empty extraction cache.  This makes
        # the selected client the only DBC source and prevents a previous
        # client/build in tools/cache from silently contaminating output.  A
        # temporary cache also keeps --check read-only with respect to the
        # repository and installation targets.
        self._dbc_cache = tempfile.TemporaryDirectory(
            prefix="paragon-dbc-cache-")
        self.environment["PARAGON_DBC_CACHE"] = self._dbc_cache.name

    def close(self) -> None:
        self._dbc_cache.cleanup()

    def phase(self, number: int, total: int, name: str) -> None:
        print("\n[%d/%d] %s" % (number, total, name), flush=True)

    def command(self, command: Sequence[str],
                environment: Optional[Dict[str, str]] = None) -> None:
        print("+ " + _display_command(command), flush=True)
        try:
            subprocess.run(list(command), cwd=str(ROOT),
                           env=(self.environment if environment is None else
                                environment),
                           check=True)
        except FileNotFoundError as error:
            raise InstallError("command not found: %s" % command[0]) from error
        except subprocess.CalledProcessError as error:
            raise InstallError("command failed with exit %d: %s" %
                               (error.returncode,
                                _display_command(command))) from error

    def _container_running(self, name: str, required: bool = True) -> bool:
        command = ("docker", "container", "inspect", "--format",
                   "{{.State.Running}}", name)
        try:
            result = subprocess.run(
                command, cwd=str(ROOT), env=self.environment,
                text=True, capture_output=True)
        except FileNotFoundError as error:
            raise InstallError(
                "Docker is required but was not found on PATH") from error
        if result.returncode != 0:
            if required:
                raise InstallError("required Docker container does not exist: %s" % name)
            return False
        return result.stdout.strip().lower() == "true"

    def _mysql(self, sql: str, capture: bool = False) -> str:
        # The password stays inside the database container's existing
        # MYSQL_ROOT_PASSWORD environment; it is never printed or put in the
        # host process command line.
        command = (
            "docker", "exec", "-i", self.config.database_container,
            "sh", "-lc",
            'exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD" '
            "--default-character-set=utf8mb4 --raw --batch --skip-column-names",
        )
        try:
            result = subprocess.run(
                command, cwd=str(ROOT), env=self.environment, input=sql,
                text=True, capture_output=capture)
        except FileNotFoundError as error:
            raise InstallError(
                "Docker is required but was not found on PATH") from error
        if result.returncode != 0:
            detail = (result.stderr or "").strip() if capture else ""
            raise InstallError("database command failed%s" %
                               ((": " + detail[-800:]) if detail else ""))
        return result.stdout if capture else ""

    def preflight(self, applying: bool) -> None:
        missing = []
        for label, path in (
                ("AzerothCore root", self.config.core_root),
                ("mod-ale extensions", self.config.extension_source),
                ("client Data directory", self.config.client_data),
                ("client locale directory", self.config.client_data / "enUS"),
                ("server Lua source", self.config.paragon_source),
                ("client addon source", self.config.addon_source)):
            if not path.is_dir():
                missing.append("%s: %s" % (label, path))
        if self.config.dbc_dir is not None and not self.config.dbc_dir.is_dir():
            missing.append("active DBC directory: %s" % self.config.dbc_dir)
        if missing:
            raise InstallError("missing prerequisites:\n  " + "\n  ".join(missing))
        verify_native_source_contract(self.config.core_root)
        try:
            dependency_probe = subprocess.run(
                (self.config.python, "-c",
                 "import sys; assert sys.version_info >= (3, 10), "
                 "'Python 3.10 or newer is required'; import mpyq; "
                 "import lupa.lua52"),
                cwd=str(ROOT), env=self.environment, text=True,
                capture_output=True)
        except FileNotFoundError as error:
            raise InstallError(
                "configured Python interpreter was not found: %s" %
                self.config.python) from error
        if dependency_probe.returncode != 0:
            detail = (dependency_probe.stderr or
                      dependency_probe.stdout).strip().splitlines()
            raise InstallError(
                "Python 3.10+ with the repository requirements is required: %s" %
                (detail[-1] if detail else "dependency probe failed"))
        if not self._container_running(self.config.database_container):
            raise InstallError("database container is not running: %s" %
                               self.config.database_container)
        world_running = self._container_running(
            self.config.worldserver_container,
            required=self.config.dbc_dir is None)
        if applying and world_running:
            raise InstallError(
                "worldserver container %s is running; stop it before --apply "
                "so SQL, Lua, and DBC overrides change as one release" %
                self.config.worldserver_container)
        prerequisites = self._mysql(
            "SELECT "
            "(SELECT COUNT(*) FROM information_schema.schemata "
            " WHERE schema_name IN "
            "('acore_auth','acore_characters','acore_world')),"
            "(SELECT COUNT(*) FROM information_schema.tables "
            " WHERE table_schema='acore_world' AND table_name IN "
            "('item_template','quest_template','spell_dbc'));",
            capture=True).strip().splitlines()
        values = prerequisites[-1].split("\t") if prerequisites else []
        if values != ["3", "3"]:
            raise InstallError(
                "AzerothCore database import is incomplete (need auth, "
                "characters, world, item_template, quest_template, spell_dbc)")

    def apply_database(self) -> None:
        components = sql_components()
        print("canonical entrypoint: %s" % _relative_to_root(SQL_ENTRYPOINT))
        for component in components:
            print("  SOURCE %s" % _relative_to_root(component), flush=True)
            try:
                content = component.read_text(encoding="utf-8")
            except OSError as error:
                raise InstallError("cannot read %s: %s" %
                                   (component, error))
            self._mysql(content)

    @staticmethod
    def _replace_tree(source: pathlib.Path, destination: pathlib.Path) -> None:
        if destination.name.lower() != source.name.lower():
            raise InstallError("refusing unexpected tree replacement: %s" %
                               destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
                prefix=".%s-install-" % destination.name,
                dir=str(destination.parent)) as temporary:
            staged = pathlib.Path(temporary) / destination.name
            shutil.copytree(str(source), str(staged))
            backup = pathlib.Path(temporary) / (destination.name + ".previous")
            if destination.exists():
                os.replace(str(destination), str(backup))
            try:
                os.replace(str(staged), str(destination))
            except Exception:
                if backup.exists() and not destination.exists():
                    os.replace(str(backup), str(destination))
                raise

    @staticmethod
    def _tree_files(root: pathlib.Path) -> Dict[str, bytes]:
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in sorted(root.rglob("*")) if path.is_file()
        }

    def verify_tree(self, source: pathlib.Path, destination: pathlib.Path,
                    exact: bool) -> None:
        if not destination.is_dir():
            raise InstallError("deployed directory is missing: %s" % destination)
        source_files = self._tree_files(source)
        destination_files = self._tree_files(destination)
        mismatches = [name for name, content in source_files.items()
                      if destination_files.get(name) != content]
        extras = sorted(set(destination_files) - set(source_files)) if exact else []
        if mismatches or extras:
            details = []
            if mismatches:
                details.append("missing/different: " + ", ".join(mismatches[:10]))
            if extras:
                details.append("unexpected: " + ", ".join(extras[:10]))
            raise InstallError("deployed tree differs at %s (%s)" %
                               (destination, "; ".join(details)))

    @staticmethod
    def _verify_identical_file(expected: pathlib.Path,
                               generated: pathlib.Path,
                               label: str) -> None:
        if not expected.is_file():
            raise InstallError("%s is missing: %s" % (label, expected))
        if not generated.is_file():
            raise InstallError("reproduction did not generate %s: %s" %
                               (label, generated))
        if expected.read_bytes() != generated.read_bytes():
            raise InstallError(
                "%s is stale or was built from different source data: %s" %
                (label, expected))

    def verify_generated_client_payload(self) -> None:
        """Rebuild canonical class data, SQL, and all three MPQs off to the side.

        The legacy generators have repository-relative output paths.  Running
        copies in a temporary mini-checkout gives them those same semantics
        without letting ``--check`` touch the checkout or installed client.
        The pristine DBC inputs are first extracted into this Pipeline's fresh
        temporary cache; the patch build is then pointed at a temporary client
        Data directory, so its only possible outputs are temporary as well.
        """
        with tempfile.TemporaryDirectory(
                prefix="paragon-reproduction-") as temporary:
            workspace = pathlib.Path(temporary)
            tools_dir = workspace / "tools"
            generated_dir = tools_dir / "generated"
            generated_dir.mkdir(parents=True)
            for name in REPRODUCTION_TOOLS:
                source = ROOT / "tools" / name
                if not source.is_file():
                    raise InstallError("reproduction tool is missing: %s" % source)
                shutil.copy2(str(source), str(tools_dir / name))

            source_environment = dict(self.environment)
            source_environment["PARAGON_CLIENT_DATA"] = str(
                self.config.client_data)
            for script in ("gen_class_talents.py", "gen_class_trainers.py"):
                self.command(
                    (self.config.python, str(tools_dir / script), "--emit"),
                    environment=source_environment)

            for name in CLASS_GENERATED_OUTPUTS:
                self._verify_identical_file(
                    ROOT / "tools" / "generated" / name,
                    generated_dir / name,
                    "generated class data")

            # Populate every input explicitly while CLIENT_DATA still points
            # at the installed client's stock locale archives.  After this
            # command the patch generator needs no access to those archives.
            extract_code = (
                "import sys; sys.path.insert(0, sys.argv[1]); "
                "import paragon_client_patch as p; "
                "[p.extract_dbc(name) for name in sys.argv[2:]]"
            )
            self.command(
                (self.config.python, "-c", extract_code, str(tools_dir)) +
                CLIENT_PATCH_DBC_INPUTS,
                environment=source_environment)

            temporary_data = workspace / "client" / "Data"
            (temporary_data / "enUS").mkdir(parents=True)
            art_source = ROOT / "clientside" / "Interface"
            art_destination = workspace / "clientside" / "Interface"
            if not art_source.is_dir():
                raise InstallError("tracked UI art source is missing: %s" %
                                   art_source)
            shutil.copytree(
                str(art_source), str(art_destination),
                ignore=shutil.ignore_patterns("AddOns"))
            patch_environment = dict(source_environment)
            patch_environment["PARAGON_CLIENT_DATA"] = str(temporary_data)
            self.command(
                (self.config.python,
                 str(tools_dir / "paragon_client_patch.py"),
                 "--general-name", self.config.general_name,
                 "--locale-name", self.config.locale_name),
                environment=patch_environment)
            self.command(
                (self.config.python, str(tools_dir / "build_ui_art.py"),
                 "--client-data", str(temporary_data),
                 "--output-name", self.config.ui_name),
                environment=patch_environment)

            self._verify_identical_file(
                ROOT / "sql" / "content" / "01_paragon_content.sql",
                workspace / "sql" / "content" / "01_paragon_content.sql",
                "canonical Paragon content SQL")
            self._verify_identical_file(
                self.config.client_data / self.config.general_name,
                temporary_data / self.config.general_name,
                "general Paragon client archive")
            self._verify_identical_file(
                self.config.client_data / "enUS" / self.config.locale_name,
                temporary_data / "enUS" / self.config.locale_name,
                "locale Paragon client archive")
            self._verify_identical_file(
                self.config.client_data / self.config.ui_name,
                temporary_data / self.config.ui_name,
                "Paragon UI art archive")

    def _database_names(self, sql: str) -> Tuple[str, ...]:
        output = self._mysql(sql, capture=True)
        return tuple(sorted(line.strip() for line in output.splitlines()
                            if line.strip()))

    @staticmethod
    def _verify_exact_names(kind: str, actual: Sequence[str],
                            expected: Sequence[str]) -> None:
        actual_set = set(actual)
        expected_set = set(expected)
        missing = sorted(expected_set - actual_set)
        unexpected = sorted(actual_set - expected_set)
        if missing or unexpected or len(actual) != len(expected):
            details = []
            if missing:
                details.append("missing: " + ", ".join(missing))
            if unexpected:
                details.append("unexpected: " + ", ".join(unexpected))
            if not details:
                details.append("duplicate names returned")
            raise InstallError("acore_ale %s differ from the canonical schema (%s)" %
                               (kind, "; ".join(details)))

    def _world_table_layouts(
            self, tables: Sequence[str]
    ) -> Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]]:
        quoted = ",".join("'%s'" % table for table in tables)
        output = self._mysql(
            "SELECT c.TABLE_NAME,c.COLUMN_NAME,"
            "COALESCE(k.ORDINAL_POSITION,0) "
            "FROM information_schema.columns c "
            "LEFT JOIN information_schema.key_column_usage k "
            "ON k.TABLE_SCHEMA=c.TABLE_SCHEMA "
            "AND k.TABLE_NAME=c.TABLE_NAME "
            "AND k.COLUMN_NAME=c.COLUMN_NAME "
            "AND k.CONSTRAINT_NAME='PRIMARY' "
            "WHERE c.TABLE_SCHEMA='acore_world' "
            "AND c.TABLE_NAME IN (%s) "
            "ORDER BY c.TABLE_NAME,c.ORDINAL_POSITION;" % quoted,
            capture=True)
        columns: Dict[str, List[str]] = {table: [] for table in tables}
        primary: Dict[str, List[Tuple[int, str]]] = {
            table: [] for table in tables}
        for line in output.splitlines():
            if not line.strip():
                continue
            fields = line.split("\t")
            if len(fields) != 3 or fields[0] not in columns:
                raise InstallError("unexpected world schema metadata row: %r" % line)
            table, column, key_order = fields
            columns[table].append(column)
            if int(key_order):
                primary[table].append((int(key_order), column))
        layouts = {}
        for table in tables:
            if not columns[table]:
                raise InstallError("canonical world table is missing: %s" % table)
            keys = tuple(column for _order, column in sorted(primary[table]))
            if not keys:
                raise InstallError("canonical world table has no primary key: %s" %
                                   table)
            layouts[table] = (tuple(columns[table]), keys)
        return layouts

    @staticmethod
    def _world_row_hash(columns: Sequence[str]) -> str:
        encoded = [
            "IF(`%s` IS NULL,'N',CONCAT('V',HEX(CAST(`%s` AS BINARY))))" %
            (column, column)
            for column in columns
        ]
        return "SHA2(CONCAT_WS('|',%s),256)" % ",".join(encoded)

    @staticmethod
    def _verify_digest_rows(table: str, expected: Sequence[Tuple[str, ...]],
                            actual: Sequence[Tuple[str, ...]]) -> None:
        expected_by_key = {row[:-1]: row[-1] for row in expected}
        actual_by_key = {row[:-1]: row[-1] for row in actual}
        if (len(expected_by_key) != len(expected)
                or len(actual_by_key) != len(actual)):
            raise InstallError("duplicate primary key while verifying %s" % table)
        missing = sorted(set(expected_by_key) - set(actual_by_key))
        unexpected = sorted(set(actual_by_key) - set(expected_by_key))
        changed = sorted(
            key for key in set(expected_by_key) & set(actual_by_key)
            if expected_by_key[key] != actual_by_key[key])
        if missing or unexpected or changed:
            def show(keys: Sequence[Tuple[str, ...]]) -> str:
                return ", ".join("/".join(key) for key in keys[:5])

            details = ["expected %d rows, found %d" %
                       (len(expected), len(actual))]
            if missing:
                details.append("missing keys: " + show(missing))
            if unexpected:
                details.append("unexpected keys: " + show(unexpected))
            if changed:
                details.append("changed keys: " + show(changed))
            raise InstallError("canonical world table %s differs (%s)" %
                               (table, "; ".join(details)))

    def verify_canonical_world_content(self) -> None:
        """Compare every generator-owned world row without persistent writes.

        The canonical DML is applied only to session-local temporary tables in
        a READ ONLY transaction.  Expected and live rows are then compared by
        primary key plus a digest over every column.  The live query uses the
        generator's DELETE predicates as its ownership scope, so missing,
        changed, and stale-extra custom rows all fail verification.
        """
        tables, statements, scopes = canonical_world_plan()
        layouts = self._world_table_layouts(tables)
        sql = ["USE `acore_world`;"]
        for table in tables:
            sql.append(
                "CREATE TEMPORARY TABLE `_paragon_verify_%s` "
                "LIKE `acore_world`.`%s`;" % (table, table))
        sql.append("START TRANSACTION READ ONLY;")
        sql.extend(statements)
        for table in tables:
            columns, keys = layouts[table]
            key_sql = ",".join("`%s`" % key for key in keys)
            order_sql = ",".join("`%s`" % key for key in keys)
            digest = self._world_row_hash(columns)
            sql.append("SELECT '__PARAGON_EXPECTED__:%s';" % table)
            sql.append(
                "SELECT %s,%s FROM `_paragon_verify_%s` ORDER BY %s;" %
                (key_sql, digest, table, order_sql))
            sql.append("SELECT '__PARAGON_ACTUAL__:%s';" % table)
            ownership = " OR ".join("(%s)" % condition
                                    for condition in scopes[table])
            sql.append(
                "SELECT %s,%s FROM `acore_world`.`%s` WHERE %s "
                "ORDER BY %s;" %
                (key_sql, digest, table, ownership, order_sql))
        sql.append("ROLLBACK;")
        output = self._mysql("\n".join(sql), capture=True)

        sections = {
            "expected": {table: [] for table in tables},
            "actual": {table: [] for table in tables},
        }
        seen_markers = set()
        current = None
        for line in output.splitlines():
            if line.startswith("__PARAGON_EXPECTED__:"):
                table = line.split(":", 1)[1]
                current = ("expected", table)
                seen_markers.add(current)
            elif line.startswith("__PARAGON_ACTUAL__:"):
                table = line.split(":", 1)[1]
                current = ("actual", table)
                seen_markers.add(current)
            elif line:
                if (current is None or current[1] not in sections[current[0]]):
                    raise InstallError(
                        "unexpected canonical world verification output: %r" %
                        line)
                sections[current[0]][current[1]].append(tuple(line.split("\t")))
        required_markers = {
            (kind, table) for kind in ("expected", "actual")
            for table in tables
        }
        if seen_markers != required_markers:
            missing = sorted(required_markers - seen_markers)
            raise InstallError("canonical world verification output is incomplete: %s" %
                               ", ".join("%s/%s" % marker
                                         for marker in missing))
        for table in tables:
            self._verify_digest_rows(
                table, sections["expected"][table], sections["actual"][table])

    def verify_database(self) -> None:
        tables = self._database_names(
            "SELECT TABLE_NAME FROM information_schema.tables "
            "WHERE table_schema='acore_ale' AND table_type='BASE TABLE' "
            "ORDER BY TABLE_NAME;")
        self._verify_exact_names("tables", tables, REQUIRED_ALE_TABLES)
        triggers = self._database_names(
            "SELECT TRIGGER_NAME FROM information_schema.triggers "
            "WHERE trigger_schema='acore_ale' ORDER BY TRIGGER_NAME;")
        self._verify_exact_names("triggers", triggers, REQUIRED_ALE_TRIGGERS)
        self.verify_canonical_world_content()

        sql = (
            "SELECT "
            "(SELECT COUNT(*) >= 88 FROM acore_ale.paragon_config),"
            "(SELECT value = '2000' FROM acore_ale.paragon_config "
            " WHERE field='UNIVERSAL_SKILL_EXPERIENCE'),"
            "(SELECT value = '2000' FROM acore_ale.paragon_config "
            " WHERE field='PARAGON_ACHIEVEMENT_POINT_XP'),"
            "(SELECT value = '1.25' FROM acore_ale.paragon_config "
            " WHERE field='PARAGON_CREATURE_XP_TBC_HEROIC_DUNGEON_MULTIPLIER'),"
            "(SELECT value = '1.5' FROM acore_ale.paragon_config "
            " WHERE field='PARAGON_CREATURE_XP_WOTLK_HEROIC_DUNGEON_MULTIPLIER'),"
            "(SELECT value = '2' FROM acore_ale.paragon_config "
            " WHERE field='PARAGON_CREATURE_XP_TBC_RAID_MULTIPLIER'),"
            "(SELECT value = '2.5' FROM acore_ale.paragon_config "
            " WHERE field='PARAGON_CREATURE_XP_WOTLK_NORMAL_RAID_MULTIPLIER'),"
            "(SELECT value = '4' FROM acore_ale.paragon_config "
            " WHERE field='PARAGON_CREATURE_XP_WOTLK_HEROIC_RAID_MULTIPLIER'),"
            "(SELECT COUNT(*) = 0 FROM acore_ale.paragon_config "
            " WHERE field='PARAGON_ONE_TIME_XP_MULTIPLIER'),"
            "(SELECT COUNT(*) > 0 FROM acore_ale.paragon_collectible_spell_xp),"
            "(SELECT COUNT(*) > 0 FROM acore_ale.paragon_config_experience_quest),"
            "(SELECT COUNT(*) >= 764 FROM acore_world.spell_dbc "
            " WHERE ID >= 1900000 AND ID < 2000000),"
            "(SELECT COUNT(*) = 96 FROM acore_world.achievement_dbc "
            " WHERE ID BETWEEN 19000 AND 19304),"
            "(SELECT COALESCE(SUM(Points),0) = 1045 "
            " FROM acore_world.achievement_dbc "
            " WHERE ID BETWEEN 19000 AND 19304),"
            "(SELECT COUNT(*) = 1 FROM information_schema.tables "
            " WHERE table_schema='acore_ale' "
            " AND table_name='paragon_profession_progress'),"
            "(SELECT COUNT(*) = 1 FROM information_schema.tables "
            " WHERE table_schema='acore_ale' "
            " AND table_name='paragon_pvp_reward_claim' "
            " AND engine='InnoDB'),"
            "(SELECT COUNT(*) = 1 FROM information_schema.columns "
            " WHERE table_schema='acore_ale' "
            " AND table_name='paragon_pvp_reward_claim' "
            " AND column_name='recipient_guid' "
            " AND data_type='int' AND column_type LIKE '%unsigned%' "
            " AND is_nullable='NO'),"
            "(SELECT value = '8' FROM acore_ale.paragon_config "
            " WHERE field='PARAGON_PVP_HONOR_XP_PER_POINT'),"
            "(SELECT value = '4000' FROM acore_ale.paragon_config "
            " WHERE field='PARAGON_PVP_BG_XP_PER_ACTIVE_MINUTE'),"
            "(SELECT value = '20000' FROM acore_ale.paragon_config "
            " WHERE field='PARAGON_PVP_WEEKLY_BREADTH_XP'),"
            "(SELECT COUNT(*) >= 4 FROM acore_ale.paragon_config_category "
            " WHERE id IN (1,2,3,4)),"
            "(SELECT COUNT(*) = 17 FROM acore_ale.paragon_config_statistic "
            " WHERE (id,type,type_value) IN ("
            "(1,'UNIT_MODS','ARMOR'),"
            "(2,'COMBAT_RATING','PARRY'),"
            "(3,'COMBAT_RATING','BLOCK'),"
            "(4,'COMBAT_RATING','DEFENSE_SKILL'),"
            "(5,'COMBAT_RATING','DODGE'),"
            "(6,'UNIT_MODS','STAT_STRENGTH'),"
            "(7,'UNIT_MODS','STAT_AGILITY'),"
            "(8,'COMBAT_RATING','CRIT_MELEE'),"
            "(9,'COMBAT_RATING','HASTE_MELEE'),"
            "(10,'COMBAT_RATING','ARMOR_PENETRATION'),"
            "(11,'UNIT_MODS','STAT_INTELLECT'),"
            "(12,'UNIT_MODS','STAT_SPIRIT'),"
            "(13,'COMBAT_RATING','HIT_SPELL'),"
            "(14,'COMBAT_RATING','HASTE_SPELL'),"
            "(15,'AURA','EXPERIENCE'),"
            "(17,'AURA','LOOT'),"
            "(19,'AURA','REPUTATION'))),"
            "(SELECT COUNT(*) = 1 FROM information_schema.columns "
            " WHERE table_schema='acore_ale' "
            " AND table_name='paragon_config_statistic' "
            " AND column_name='type_value' AND data_type='varchar' "
            " AND character_maximum_length=32);")
        output = self._mysql(sql, capture=True).strip().splitlines()
        values = output[-1].split("\t") if output else []
        if values != ["1"] * 23:
            raise InstallError(
                "database verification failed (config, instance creature XP, direct one-time XP, "
                "collection/quest rows, content, achievements, profession "
                "progress, PvP Merit, categories, statistics, or type_value schema is "
                "incomplete): %s" % (values or "no output"))

    def apply(self) -> None:
        commands = iter(apply_commands(self.config))
        total = len(APPLY_PHASES)
        for number, phase in enumerate(APPLY_PHASES, 1):
            self.phase(number, total, phase)
            if phase == "preflight":
                self.preflight(applying=True)
            elif phase == "repository-tests":
                self.command(next(commands))
            elif phase == "database-bootstrap":
                self.apply_database()
            elif phase == "static-data":
                for _script in STATIC_GENERATORS:
                    self.command(next(commands))
            elif phase == "profession-data":
                self.command(next(commands))
                self.command(next(commands))
            elif phase == "server-payload":
                self._replace_tree(self.config.paragon_source,
                                   self.config.paragon_destination)
                self._replace_tree(self.config.extension_source,
                                   self.config.extension_destination)
            elif phase == "class-data":
                self.command(next(commands))
                self.command(next(commands))
            elif phase == "content-and-client-dbc":
                self.command(next(commands))
            elif phase in ("collection-xp", "quest-xp"):
                self.command(next(commands))
                self.command(next(commands))
            elif phase == "ui-art":
                self.command(next(commands))
            elif phase == "client-addon":
                self._replace_tree(self.config.addon_source,
                                   self.config.addon_destination)
            elif phase == "verification":
                self.verify_tree(self.config.paragon_source,
                                 self.config.paragon_destination, exact=True)
                self.verify_tree(self.config.extension_source,
                                 self.config.extension_destination, exact=True)
                self.verify_tree(self.config.addon_source,
                                 self.config.addon_destination, exact=True)
                self.verify_database()
                self.command(next(commands))
        try:
            unexpected = next(commands)
        except StopIteration:
            unexpected = None
        if unexpected is not None:
            raise AssertionError("unexecuted pipeline command: %r" %
                                 (unexpected,))

    def check(self) -> None:
        commands = iter(check_commands(self.config))
        total = len(CHECK_PHASES)
        for number, phase in enumerate(CHECK_PHASES, 1):
            self.phase(number, total, phase)
            if phase == "preflight":
                self.preflight(applying=False)
            elif phase == "repository-tests":
                self.command(next(commands))
            elif phase == "static-data":
                for _script in STATIC_GENERATORS:
                    self.command(next(commands))
            elif phase == "profession-data":
                self.command(next(commands))
            elif phase == "server-payload":
                self.verify_tree(self.config.paragon_source,
                                 self.config.paragon_destination, exact=True)
                self.verify_tree(self.config.extension_source,
                                 self.config.extension_destination, exact=True)
            elif phase in ("collection-xp", "quest-xp"):
                self.command(next(commands))
            elif phase == "database-content":
                self.verify_database()
            elif phase == "client-payload":
                self.verify_tree(self.config.addon_source,
                                 self.config.addon_destination, exact=True)
                self.verify_generated_client_payload()
                self.command(next(commands))
        try:
            unexpected = next(commands)
        except StopIteration:
            unexpected = None
        if unexpected is not None:
            raise AssertionError("unexecuted check command: %r" %
                                 (unexpected,))


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--apply", action="store_true",
                      help="generate, install, populate, and verify everything")
    mode.add_argument("--check", action="store_true",
                      help="read-only verification of generated and installed state")
    mode.add_argument("--dry-run", action="store_true",
                      help="print the full --apply plan without external access")
    parser.add_argument(
        "--core-root", default=os.environ.get("PARAGON_CORE_ROOT"),
        help="patched AzerothCore checkout (or PARAGON_CORE_ROOT)")
    parser.add_argument(
        "--client-root", default=os.environ.get("PARAGON_CLIENT_ROOT"),
        help="WoW root containing Data and Interface (or PARAGON_CLIENT_ROOT)")
    parser.add_argument(
        "--lua-root", default=os.environ.get("PARAGON_LUA_ROOT"),
        help="ALE.ScriptPath; defaults to CORE/env/dist/etc/lua_scripts")
    parser.add_argument(
        "--database-container",
        default=os.environ.get("ACORE_DB_CONTAINER", "ac-database"))
    parser.add_argument(
        "--worldserver-container",
        default=os.environ.get("ACORE_WORLDSERVER_CONTAINER", "ac-worldserver"))
    parser.add_argument(
        "--dbc-dir", help="host active-DBC directory instead of worldserver container")
    parser.add_argument("--python", default=sys.executable,
                        help="Python interpreter used for child tools")
    parser.add_argument("--general-name", default="patch-X.MPQ")
    parser.add_argument("--locale-name", default="patch-enUS-X.MPQ")
    parser.add_argument("--ui-name", default="patch-W.MPQ")
    return parser.parse_args(argv)


def config_from_args(args: argparse.Namespace) -> Config:
    if not args.core_root:
        raise InstallError("--core-root (or PARAGON_CORE_ROOT) is required")
    if not args.client_root:
        raise InstallError("--client-root (or PARAGON_CLIENT_ROOT) is required")
    core_root = pathlib.Path(args.core_root).expanduser().resolve()
    client_root = pathlib.Path(args.client_root).expanduser().resolve()
    lua_root = (pathlib.Path(args.lua_root).expanduser().resolve()
                if args.lua_root else
                core_root / "env" / "dist" / "etc" / "lua_scripts")
    mode = "apply" if args.apply else "check" if args.check else "dry-run"
    return Config(
        mode=mode,
        core_root=core_root,
        client_root=client_root,
        lua_root=lua_root,
        database_container=args.database_container,
        worldserver_container=args.worldserver_container,
        dbc_dir=(pathlib.Path(args.dbc_dir).expanduser().resolve()
                 if args.dbc_dir else None),
        python=args.python,
        general_name=args.general_name,
        locale_name=args.locale_name,
        ui_name=args.ui_name,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        config = config_from_args(parse_args(argv))
        if config.mode == "dry-run":
            print("Paragon reproducible installation plan (no changes made):")
            print("\n".join(plan_lines(config)))
            return 0
        pipeline = Pipeline(config)
        try:
            if config.mode == "apply":
                pipeline.apply()
                print("\nParagon installation applied and verified.")
            else:
                pipeline.check()
                print("\nParagon installation is reproducible and current.")
        finally:
            pipeline.close()
        return 0
    except InstallError as error:
        print("Paragon installation failed: %s" % error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
