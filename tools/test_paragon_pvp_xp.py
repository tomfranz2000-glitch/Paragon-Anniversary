import os
import re
import unittest

try:
    from lupa.lua51 import LuaRuntime
except ImportError:  # pragma: no cover - reported as skipped by unittest
    LuaRuntime = None


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE = os.path.join(
    ROOT, "serverside", "paragon", "modules", "paragon_pvp_xp.lua")
HOOK = os.path.join(ROOT, "serverside", "paragon", "paragon_hook.lua")
REPOSITORY = os.path.join(
    ROOT, "serverside", "paragon", "paragon_repository.lua")
SCHEMA = os.path.join(ROOT, "sql", "02_create_tables.sql")
DEFAULT_CONFIG = os.path.join(ROOT, "sql", "04_insert_default_config.sql")
ANNIVERSARY_CONFIG = os.path.join(
    ROOT, "sql", "05_apply_anniversary_config.sql")
INSTALLER = os.path.join(ROOT, "tools", "install.py")
CORE_PATCH = os.path.join(ROOT, "patches", "08-core-pvp-merit.patch")
ALE_PATCH = os.path.join(ROOT, "patches", "09-mod-ale-pvp-merit.patch")


PVP_CONFIG = {
    "PARAGON_PVP_ENABLED": "1",
    "PARAGON_PVP_HONOR_XP_PER_POINT": "8",
    "PARAGON_PVP_HONOR_DR_WINDOW_MINUTES": "30",
    "PARAGON_PVP_HONOR_DR_FULL_CREDITS": "1",
    "PARAGON_PVP_HONOR_DR_HALF_CREDITS": "2",
    "PARAGON_PVP_HONOR_DR_TENTH_CREDITS": "3",
    "PARAGON_PVP_HONOR_DR_FULL_PERCENT": "100",
    "PARAGON_PVP_HONOR_DR_HALF_PERCENT": "50",
    "PARAGON_PVP_HONOR_DR_TENTH_PERCENT": "10",
    "PARAGON_PVP_HONOR_DR_LATER_PERCENT": "0",
    "PARAGON_PVP_MATCH_MIN_SECONDS": "60",
    "PARAGON_PVP_MATCH_MIN_ACTIVE_BUCKETS": "2",
    "PARAGON_PVP_MATCH_MIN_ACTIVE_PERCENT": "30",
    "PARAGON_PVP_BG_XP_PER_ACTIVE_MINUTE": "4000",
    "PARAGON_PVP_BG_WIN_XP_PER_ACTIVE_MINUTE": "1000",
    "PARAGON_PVP_BG_DRAW_XP_PER_ACTIVE_MINUTE": "500",
    "PARAGON_PVP_BG_OBJECTIVE_MAJOR_XP": "8000",
    "PARAGON_PVP_BG_OBJECTIVE_STANDARD_XP": "4000",
    "PARAGON_PVP_BG_OBJECTIVE_ASSIST_XP": "2000",
    "PARAGON_PVP_BG_OBJECTIVE_CAP_PERCENT": "20",
    "PARAGON_PVP_BG_CAP_WSG_MINUTES": "25",
    "PARAGON_PVP_BG_CAP_AB_MINUTES": "30",
    "PARAGON_PVP_BG_CAP_EOTS_MINUTES": "25",
    "PARAGON_PVP_BG_CAP_AV_MINUTES": "45",
    "PARAGON_PVP_BG_CAP_SOTA_MINUTES": "25",
    "PARAGON_PVP_BG_CAP_IOC_MINUTES": "40",
    "PARAGON_PVP_BG_CAP_GENERIC_MINUTES": "30",
    "PARAGON_PVP_WINTERGRASP_CAP_MINUTES": "40",
    "PARAGON_PVP_ARENA_MIN_SECONDS": "15",
    "PARAGON_PVP_ARENA_MIN_CONTRIBUTION": "10000",
    "PARAGON_PVP_ARENA_2V2_WIN_XP": "37500",
    "PARAGON_PVP_ARENA_2V2_LOSS_XP": "26250",
    "PARAGON_PVP_ARENA_3V3_WIN_XP": "45000",
    "PARAGON_PVP_ARENA_3V3_LOSS_XP": "31500",
    "PARAGON_PVP_ARENA_5V5_WIN_XP": "56250",
    "PARAGON_PVP_ARENA_5V5_LOSS_XP": "39000",
    "PARAGON_PVP_SKIRMISH_WIN_XP": "11250",
    "PARAGON_PVP_SKIRMISH_LOSS_XP": "7500",
    "PARAGON_PVP_SKIRMISH_DAILY_CAP_XP": "56250",
    "PARAGON_PVP_ARENA_ROSTER_DR_WINDOW_MINUTES": "60",
    "PARAGON_PVP_ARENA_ROSTER_DR_FULL_SETTLEMENTS": "3",
    "PARAGON_PVP_ARENA_ROSTER_DR_HALF_SETTLEMENTS": "5",
    "PARAGON_PVP_ARENA_ROSTER_DR_TENTH_SETTLEMENTS": "6",
    "PARAGON_PVP_ARENA_ROSTER_DR_FULL_PERCENT": "100",
    "PARAGON_PVP_ARENA_ROSTER_DR_HALF_PERCENT": "50",
    "PARAGON_PVP_ARENA_ROSTER_DR_TENTH_PERCENT": "10",
    "PARAGON_PVP_ARENA_ROSTER_DR_LATER_PERCENT": "0",
    "PARAGON_PVP_OUTDOOR_STANDARD_XP": "15000",
    "PARAGON_PVP_OUTDOOR_MAJOR_XP": "30000",
    "PARAGON_PVP_DUEL_WIN_XP": "5000",
    "PARAGON_PVP_DUEL_LOSS_XP": "2000",
    "PARAGON_PVP_DUEL_DISTINCT_OPPONENTS_PER_DAY": "3",
    "PARAGON_PVP_WEEKLY_BREADTH_XP": "20000",
    "PARAGON_PVP_DAILY_RESET_WORLDSTATE": "20005",
    "PARAGON_PVP_WEEKLY_RESET_WORLDSTATE": "20002",
    "PARAGON_PVP_DAILY_RESET_INTERVAL_SECONDS": "86400",
    "PARAGON_PVP_WEEKLY_RESET_INTERVAL_SECONDS": "604800",
    "PARAGON_PVP_RESET_FALLBACK_ANCHOR_UNIX": "0",
    "PARAGON_PVP_LEDGER_RETENTION_DAYS": "90",
    "PARAGON_PVP_PENDING_RETENTION_DAYS": "365",
    "PARAGON_PVP_CLEANUP_INTERVAL_SECONDS": "3600",
}

PVP_SOURCES = {
    "PVP_HONOR": 9,
    "PVP_BATTLEGROUND": 10,
    "PVP_ARENA": 11,
    "PVP_OBJECTIVE": 12,
    "PVP_DUEL": 13,
    "PVP_BREADTH": 14,
    "PVP_WINTERGRASP": 15,
}


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def pvp_config_from(path):
    values = dict(re.findall(
        r"\('([^']+)'\s*,\s*'([^']*)'\)", read(path)))
    return {key: value for key, value in values.items()
            if key.startswith("PARAGON_PVP_")}


def lua_table_dict(table):
    return {key: value for key, value in table.items()}


def lua_function_parameters(source, name):
    match = re.search(
        r"function\s+(?:(?:ParagonPvPXP|M)\.)?%s\s*\((.*?)\)\s*\n" %
        re.escape(name),
        source,
        flags=re.DOTALL,
    )
    if not match:
        return None
    return [value.strip() for value in match.group(1).split(",")]


class ParagonPvPStaticContractTests(unittest.TestCase):
    def test_authoritative_config_values_are_exact_in_both_install_paths(self):
        self.assertEqual(61, len(PVP_CONFIG))
        self.assertEqual(PVP_CONFIG, pvp_config_from(DEFAULT_CONFIG))
        self.assertEqual(PVP_CONFIG, pvp_config_from(ANNIVERSARY_CONFIG))

    def test_there_is_no_hidden_global_pvp_multiplier(self):
        for path in (DEFAULT_CONFIG, ANNIVERSARY_CONFIG):
            keys = pvp_config_from(path)
            self.assertFalse(
                [key for key in keys if "MULTIPLIER" in key], path)

        module = read(MODULE)
        self.assertNotRegex(
            module,
            r"PARAGON_PVP_(?:GLOBAL_)?(?:XP_)?MULTIPLIER",
        )
        self.assertNotIn("PARAGON_PVP_PRACTICE_", module)

    def test_source_ids_are_exact_and_remain_repeatable(self):
        hook = read(HOOK)
        source_block = re.search(
            r"local EXPERIENCE_SOURCE\s*=\s*\{(.*?)\n\}",
            hook,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(source_block)
        sources = {
            name: int(value) for name, value in re.findall(
                r"([A-Z][A-Z0-9_]*)\s*=\s*(\d+)",
                source_block.group(1),
            )
        }
        for name, value in PVP_SOURCES.items():
            self.assertEqual(value, sources.get(name), name)

        flat_block = re.search(
            r"local FLAT_EXPERIENCE_SOURCE\s*=\s*\{(.*?)\n\}",
            hook,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(flat_block)
        for name in PVP_SOURCES:
            self.assertNotIn("EXPERIENCE_SOURCE.%s" % name,
                             flat_block.group(1))

    def test_account_ledger_schema_is_durable_and_recipient_bound(self):
        blocks = []
        for path in (SCHEMA, ANNIVERSARY_CONFIG):
            match = re.search(
                r"CREATE TABLE IF NOT EXISTS\s+`acore_ale`\."
                r"`paragon_pvp_reward_claim`\s*\((.*?)\)\s*ENGINE=InnoDB",
                read(path),
                flags=re.DOTALL | re.IGNORECASE,
            )
            self.assertIsNotNone(match, path)
            blocks.append(match.group(1))

        for block in blocks:
            for contract in (
                r"`account_id`\s+INT UNSIGNED NOT NULL",
                r"`recipient_guid`\s+INT UNSIGNED NOT NULL",
                r"`event_token`\s+VARCHAR\(191\).*?ascii_bin NOT NULL",
                r"`component`\s+VARCHAR\(32\).*?ascii_bin NOT NULL",
                r"`source_type`\s+TINYINT UNSIGNED NOT NULL",
                r"`source_entry`\s+INT UNSIGNED NOT NULL",
                r"`base_xp`\s+BIGINT UNSIGNED NOT NULL",
                r"`awarded_xp`\s+BIGINT UNSIGNED NOT NULL",
                r"`counterpart_account_id`\s+INT UNSIGNED NOT NULL",
                r"`opponent_key`\s+VARCHAR\(191\).*?ascii_bin NOT NULL",
                r"`period_key`\s+VARCHAR\(64\).*?ascii_bin NOT NULL",
                r"`entitlement_key`\s+VARCHAR\(191\).*?ascii_bin NULL",
                r"`same_ip_risk`\s+TINYINT UNSIGNED NOT NULL",
                r"`created_at`\s+DATETIME NOT NULL",
                r"`paid_at`\s+DATETIME NULL",
                r"PRIMARY KEY\s*\(`account_id`,\s*`event_token`,\s*`component`\)",
                r"UNIQUE KEY\s+`uq_paragon_pvp_entitlement`\s*"
                r"\(`account_id`,\s*`entitlement_key`\)",
            ):
                self.assertRegex(block, re.compile(contract, re.DOTALL), path)
            self.assertRegex(
                block,
                re.compile(
                    r"KEY\s+`ix_paragon_pvp_pending_owner`\s*"
                    r"\(`account_id`,\s*`recipient_guid`,\s*`paid_at`,"
                    r"\s*`created_at`\)",
                    re.DOTALL,
                ),
                path,
            )

    def test_runtime_and_installer_require_the_ledger(self):
        table = '"paragon_pvp_reward_claim"'
        self.assertIn(table, read(REPOSITORY))
        self.assertIn(table, read(INSTALLER))

        installer = read(INSTALLER)
        self.assertIn("column_name='recipient_guid'", installer)
        self.assertIn('ROOT / "serverside" / "paragon"', installer)
        self.assertIn("self._replace_tree(self.config.paragon_source,",
                      installer)
        self.assertIn("self.verify_tree(self.config.paragon_source,",
                      installer)
        self.assertTrue(os.path.isfile(MODULE))

    def test_module_exports_handlers_and_registers_the_bridge_events_once(self):
        module = read(MODULE)
        for name in (
                "HonorDRPercent", "ArenaRosterDRPercent", "ComputeHonorXP",
                "IsMatchActive", "MatchMinuteCap",
                "ClassifyBattlegroundObjectives", "ComputeBattlegroundBase",
                "IsArenaActive", "ComputeArenaBase", "ResolvePeriodKey",
                "OnHonor", "OnMatchComplete", "OnBattlefieldComplete",
                "OnOutdoorObjective", "OnDuelComplete", "PayPendingClaims"):
            self.assertRegex(
                module,
                r"function\s+(?:(?:ParagonPvPXP|M)\.)?%s\b" % name,
            )

        for event_id, handler in (
                (77, "OnHonor"),
                (78, "OnMatchComplete"),
                (79, "OnBattlefieldComplete"),
                (80, "OnOutdoorObjective"),
                (81, "OnDuelComplete")):
            self.assertEqual(
                1,
                len(re.findall(
                    r"RegisterPlayerEvent\(\s*%d\s*,\s*"
                    r"(?:(?:ParagonPvPXP|M)\.)?%s\s*\)" %
                    (event_id, handler),
                    module,
                )),
                "event %d must register %s exactly once" %
                (event_id, handler),
            )

    def test_module_handler_signatures_match_the_ale_bridge(self):
        module = read(MODULE)
        signatures = {
            "OnHonor": (
                "event", "player", "victim", "final_honor", "honor_source",
                "battleground_type_id", "arena_type", "rated",
                "generated_battleground_xp", "event_token",
            ),
            "OnMatchComplete": (
                "event", "player", "match_kind", "result",
                "duration_seconds", "active_seconds", "presence_buckets",
                "active_buckets", "tactical_actions",
                "battleground_type_id", "map_id", "instance_id",
                "arena_type", "rated", "bracket_id", "player_team",
                "winner_team", "killing_blows", "deaths",
                "honorable_kills", "bonus_honor", "damage_done",
                "healing_done", "pvp_damage_done", "pvp_healing_done",
                "objective1", "objective2", "objective3", "objective4",
                "objective5", "is_bot", "account_id_argument",
                "opponent_count", "real_opponent_count",
                "bot_opponent_count", "unique_opponent_accounts",
                "same_account_opponent", "same_ip_opponent", "inactive",
                "deserter", "opponent_roster_key", "event_token",
            ),
            "OnBattlefieldComplete": (
                "event", "player", "battlefield_type_id", "battle_id",
                "zone_id", "map_id", "result", "duration_seconds",
                "active_seconds", "presence_buckets", "active_buckets",
                "tactical_actions", "player_team", "winner_team",
                "attacker_team", "defender_team_at_start", "ended_by_timer",
                "is_bot", "account_id_argument", "player_kills",
                "pvp_damage_done", "pvp_healing_done", "objective_major",
                "objective_standard", "objective_assist",
                "real_opponent_count", "bot_opponent_count",
                "unique_opponent_accounts", "same_account_opponent",
                "same_ip_opponent", "inactive", "deserter",
                "opponent_roster_key", "event_token",
            ),
            "OnOutdoorObjective": (
                "event", "player", "outdoor_pvp_type_id", "objective_id",
                "objective_entry", "objective_tier", "map_id", "zone_id",
                "team", "participant_count", "event_token",
            ),
            "OnDuelComplete": (
                "event", "winner", "loser", "duel_type",
                "duration_seconds", "same_account", "same_ip",
                "winner_is_bot", "loser_is_bot", "event_token",
            ),
        }
        for name, expected in signatures.items():
            self.assertEqual(list(expected), lua_function_parameters(module, name))

    def test_all_pvp_awards_use_one_normal_modifier_boundary(self):
        module = read(MODULE)
        calls = re.findall(
            r"Hook\.Award(?:Flat)?Experience\s*\((.*?)\)",
            module,
            flags=re.DOTALL,
        )
        self.assertTrue(calls)
        self.assertNotIn("AwardFlatExperience", module)
        for arguments in calls:
            self.assertRegex(arguments.strip(), r",\s*true\s*$")
        self.assertNotRegex(module, r"(?:AddXP|GiveXP|SetExperience)\s*\(")

    def test_legacy_battleground_xp_source_is_not_registered_for_conversion(self):
        module = read(MODULE)
        self.assertNotRegex(
            module,
            r"RegisterPlayerEvent\(\s*12\s*,",
        )
        self.assertNotRegex(
            module,
            r"XPSOURCE_BATTLEGROUND\s*(?:==|~=)",
        )

    def test_patch_payloads_define_the_exact_bridge_contract(self):
        core_patch = read(CORE_PATCH)
        ale_patch = read(ALE_PATCH)
        for event_id, symbol in (
                (77, "PLAYER_EVENT_ON_PVP_HONOR"),
                (78, "PLAYER_EVENT_ON_PVP_MATCH_COMPLETE"),
                (79, "PLAYER_EVENT_ON_PVP_BATTLEFIELD_COMPLETE"),
                (80, "PLAYER_EVENT_ON_PVP_OUTDOOR_OBJECTIVE"),
                (81, "PLAYER_EVENT_ON_PVP_DUEL_COMPLETE")):
            self.assertRegex(
                ale_patch,
                r"\b%s\s*=\s*%d\b" % (symbol, event_id),
            )
        for path in (
                "src/LuaEngine/hooks/PvPMeritHooks.cpp",
                "src/LuaEngine/LuaEngine.h",
                "src/ALE_SC.cpp",
                "src/PvPMeritTracker.h",
                "src/PvPMeritTracker.cpp"):
            self.assertIn(path, ale_patch)
        self.assertIn("PvPMerit", core_patch)


@unittest.skipUnless(LuaRuntime, "lupa.lua51 is required for Lua 5.1 tests")
class ParagonPvPPureLua51Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lua = LuaRuntime(unpack_returned_tuples=True)
        cls.lua.globals().ConfigValues = cls.lua.table_from(PVP_CONFIG)
        cls.lua.execute(
            r"""
            Config = {}
            function Config:GetByField(field) return ConfigValues[field] end
            package.preload["paragon_config"] = function() return Config end

            Hook = { ExperienceSource = {
                PVP_HONOR = 9,
                PVP_BATTLEGROUND = 10,
                PVP_ARENA = 11,
                PVP_OBJECTIVE = 12,
                PVP_DUEL = 13,
                PVP_BREADTH = 14,
                PVP_WINTERGRASP = 15,
            } }
            function Hook.AwardExperience(...) return true end
            package.preload["paragon_hook"] = function() return Hook end
            package.preload["paragon_constant"] = function()
                return { DB_NAME = "acore_ale" }
            end

            RegisteredPlayerEvents = {}
            function RegisterPlayerEvent(eventId, callback)
                RegisteredPlayerEvents[eventId] = callback
            end
            function RegisterServerEvent(_, _) end
            function RegisterMediatorEvent(_, _) end
            function CharDBQuery(_) return nil end
            function CharDBExecute(_) end
            """
        )
        cls.module = cls.lua.execute(read(MODULE))
        cls.lua.globals().ReturnedPvPXP = cls.module

    def test_loads_in_real_lua51_and_exports_one_table(self):
        self.assertEqual("Lua 5.1", self.lua.eval("_VERSION"))
        self.assertTrue(self.lua.eval("ReturnedPvPXP == ParagonPvPXP"))
        self.assertEqual(
            {77, 78, 79, 80, 81},
            set(lua_table_dict(
                self.lua.globals().RegisteredPlayerEvents).keys()),
        )

    def test_honor_same_victim_dr_boundaries_and_flooring(self):
        self.assertEqual(
            [100, 50, 10, 0, 0],
            [self.module.HonorDRPercent(prior) for prior in range(5)],
        )
        self.assertEqual((808, 100), self.module.ComputeHonorXP(101, 0))
        self.assertEqual((404, 50), self.module.ComputeHonorXP(101, 1))
        self.assertEqual((80, 10), self.module.ComputeHonorXP(101, 2))
        self.assertEqual((0, 0), self.module.ComputeHonorXP(101, 3))
        self.assertEqual((0, 0), self.module.ComputeHonorXP(0, 0))

    def test_match_activity_boundaries_cover_presence_afk_and_deserter(self):
        active = self.module.IsMatchActive
        self.assertFalse(active(59, 60, 6, 2, False, False))
        self.assertFalse(active(600, 59, 2, 2, False, False))
        self.assertFalse(active(60, 60, 6, 1, False, False))
        self.assertTrue(active(60, 60, 6, 2, False, False))
        self.assertFalse(active(60, 60, 7, 2, False, False))
        self.assertTrue(active(60, 60, 10, 3, False, False))
        self.assertFalse(active(60, 60, 6, 2, True, False))
        self.assertFalse(active(60, 60, 6, 2, False, True))

    def test_match_minute_caps_cover_every_wotlk_battleground(self):
        cap = self.module.MatchMinuteCap
        self.assertEqual(45, cap(1, 30, False))
        self.assertEqual(25, cap(2, 489, False))
        self.assertEqual(30, cap(3, 529, False))
        self.assertEqual(25, cap(7, 566, False))
        self.assertEqual(25, cap(9, 607, False))
        self.assertEqual(40, cap(30, 628, False))
        self.assertEqual(30, cap(999, 999, False))
        self.assertEqual(40, cap(999, 571, True))

    def test_objective_fields_are_classified_by_battleground_contract(self):
        classify = self.module.ClassifyBattlegroundObjectives
        self.assertEqual((2, 0, 3), classify(2, 489, 2, 3, 9, 9, 9))
        self.assertEqual((4, 0, 0), classify(7, 566, 4, 9, 9, 9, 9))
        self.assertEqual((0, 9, 0), classify(3, 529, 4, 5, 9, 9, 9))
        self.assertEqual((0, 9, 0), classify(30, 628, 4, 5, 9, 9, 9))
        self.assertEqual((3, 6, 7), classify(1, 30, 2, 3, 3, 4, 4))
        self.assertEqual((6, 5, 0), classify(9, 607, 5, 6, 9, 9, 9))
        self.assertEqual((0, 0, 0), classify(32, 0, 9, 9, 9, 9, 9))
        self.assertEqual((0, 0, 0), classify(999, 999, 9, 9, 9, 9, 9))

    def test_battleground_win_loss_draw_and_objective_cap(self):
        compute = self.module.ComputeBattlegroundBase
        self.assertEqual(
            (9600, 8000, 0, 1600, 2),
            compute(2, 0, 99, 99, 99, 30),
        )
        self.assertEqual(
            (11600, 8000, 2000, 1600, 2),
            compute(2, 1, 99, 99, 99, 30),
        )
        self.assertEqual(
            (15900, 12000, 1500, 2400, 3),
            compute(3, 2, 99, 99, 99, 30),
        )
        self.assertEqual(
            (10000, 8000, 2000, 0, 2),
            compute(2, 1, 0, 0, 0, 30),
        )

    def test_battleground_minutes_are_capped_before_every_component(self):
        self.assertEqual(
            (145000, 100000, 25000, 20000, 25),
            self.module.ComputeBattlegroundBase(
                99, 1, 99, 99, 99, 25),
        )

    def test_arena_activity_thresholds(self):
        active = self.module.IsArenaActive
        self.assertFalse(active(14, 1, 0, 0, 0, False, False))
        self.assertTrue(active(15, 1, 0, 0, 0, False, False))
        self.assertFalse(active(15, 0, 9999, 0, 0, False, False))
        self.assertTrue(active(15, 0, 9999, 1, 0, False, False))
        self.assertTrue(active(15, 0, 0, 0, 1, False, False))
        self.assertFalse(active(15, 1, 0, 0, 0, True, False))
        self.assertFalse(active(15, 1, 0, 0, 0, False, True))

    def test_arena_bracket_values_and_repeated_roster_dr(self):
        compute = self.module.ComputeArenaBase
        expected = {
            (2, 1): 37500,
            (2, 0): 26250,
            (3, 1): 45000,
            (3, 0): 31500,
            (5, 1): 56250,
            (5, 0): 39000,
        }
        for (bracket, result), amount in expected.items():
            self.assertEqual(
                (amount, 100), compute(True, bracket, result, 0))
        self.assertEqual(
            [100, 100, 100, 50, 50, 10, 0, 0],
            [self.module.ArenaRosterDRPercent(prior)
             for prior in range(8)],
        )
        self.assertEqual((18750, 50), compute(True, 2, 1, 3))
        self.assertEqual((3750, 10), compute(True, 2, 1, 5))
        self.assertEqual((0, 0), compute(True, 2, 1, 6))
        self.assertEqual((11250, 100), compute(False, 2, 1, 0))
        self.assertEqual((7500, 100), compute(False, 2, 0, 0))
        self.assertEqual((0, 0), compute(False, 2, 1, 6))

    def test_reset_period_keys_change_only_at_the_supplied_boundary(self):
        resolve = self.module.ResolvePeriodKey
        before = resolve(1999, 2000, 20005, 86400, 0)
        same_window = resolve(1500, 2000, 20005, 86400, 0)
        after = resolve(2000, 2000, 20005, 86400, 0)
        self.assertEqual(before, same_window)
        self.assertNotEqual(before, after)

        fallback_a = resolve(86399, 0, 20005, 86400, 0)
        fallback_b = resolve(86400, 0, 20005, 86400, 0)
        weekly = resolve(604800, 0, 20002, 604800, 0)
        self.assertNotEqual(fallback_a, fallback_b)
        self.assertNotEqual(fallback_b, weekly)


@unittest.skipUnless(LuaRuntime, "lupa.lua51 is required for Lua behavior tests")
class ParagonPvPHandlerTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        values = dict(PVP_CONFIG)
        values.update({
            "ENABLE_PARAGON_SYSTEM": "1",
            "MINIMUM_LEVEL_FOR_PARAGON_XP": "80",
            "LEVEL_LINKED_TO_ACCOUNT": "1",
        })
        self.lua.globals().ConfigValues = self.lua.table_from(values)
        self.lua.execute(
            r"""
            Config = {}
            function Config:GetByField(field) return ConfigValues[field] end
            package.preload["paragon_config"] = function() return Config end

            Hook = { ExperienceSource = {
                PVP_HONOR = 9,
                PVP_BATTLEGROUND = 10,
                PVP_ARENA = 11,
                PVP_OBJECTIVE = 12,
                PVP_DUEL = 13,
                PVP_BREADTH = 14,
                PVP_WINTERGRASP = 15,
            } }
            package.preload["paragon_hook"] = function() return Hook end
            package.preload["paragon_constant"] = function()
                return { DB_NAME = "acore_ale" }
            end

            RegisteredPlayerEvents = {}
            RegisteredMediatorEvents = {}
            function RegisterPlayerEvent(eventId, callback)
                RegisteredPlayerEvents[eventId] = callback
            end
            function RegisterMediatorEvent(name, callback)
                RegisteredMediatorEvents[name] = callback
            end
            function RegisterServerEvent(_, _) end

            Ledger = {}
            Entitlements = {}
            AwardCalls = {}
            SQL = {}
            ModifierCalls = 0
            ModifierFactor = 1
            AwardEnabled = true
            NowEpoch = 100000
            DailyNext = 110000
            WeeklyNext = 500000

            local function ClaimKey(account, token, component)
                return tostring(account) .. ":" .. token .. ":" .. component
            end

            function ALEUInt64(value)
                local rendered = tostring(value)
                local wrapped = newproxy(true)
                getmetatable(wrapped).__tostring = function()
                    return rendered
                end
                return wrapped
            end

            local function Result(rows)
                if #rows == 0 then return nil end
                local index = 1
                return {
                    GetUInt32 = function(_, column)
                        return tonumber(rows[index][column + 1]) or 0
                    end,
                    GetUInt64 = function(_, column)
                        return ALEUInt64(rows[index][column + 1])
                    end,
                    GetString = function(_, column)
                        local value = rows[index][column + 1]
                        return value == nil and "" or tostring(value)
                    end,
                    NextRow = function(_)
                        if index < #rows then
                            index = index + 1
                            return true
                        end
                        return false
                    end,
                }
            end

            local function InsertClaims(sql)
                local values = sql:match("VALUES%s+([%s%S]+);")
                if not values then return end
                for tuple in values:gmatch("%b()") do
                    local account, recipient, token, component, source, entry, base,
                        counterpart, opponent, period, entitlement, risk,
                        paid_at =
                        tuple:match(
                            "^%((%d+),(%d+),'([^']*)','([^']*)',(%d+),(%d+),(%d+),0," ..
                            "(%d+),'([^']*)','([^']*)',([^,]+),(%d+)," ..
                            "UTC_TIMESTAMP%(%),(.+)%)$")
                    if account then
                        account, recipient, source, entry, base, counterpart, risk =
                            tonumber(account), tonumber(recipient), tonumber(source), tonumber(entry),
                            tonumber(base), tonumber(counterpart), tonumber(risk)
                        if entitlement == "NULL" then
                            entitlement = nil
                        else
                            entitlement = entitlement:match("^'(.*)'$")
                        end
                        local paidEpoch = nil
                        if paid_at ~= "NULL" then paidEpoch = NowEpoch end
                        local key = ClaimKey(account, token, component)
                        local entitlementKey = entitlement and
                            (tostring(account) .. ":" .. entitlement) or nil
                        if not Ledger[key]
                                and (not entitlementKey or not Entitlements[entitlementKey]) then
                            Ledger[key] = {
                                account_id = account,
                                recipient_guid = recipient,
                                event_token = token,
                                component = component,
                                source_type = source,
                                source_entry = entry,
                                base_xp = base,
                                awarded_xp = 0,
                                counterpart_account_id = counterpart,
                                opponent_key = opponent,
                                period_key = period,
                                entitlement_key = entitlement,
                                same_ip_risk = risk,
                                created_at = NowEpoch,
                                paid_at = paidEpoch,
                            }
                            if entitlementKey then
                                Entitlements[entitlementKey] = key
                            end
                        end
                    end
                end
            end

            local function AcknowledgeClaim(sql)
                local account = tonumber(sql:match("claim%.account_id = (%d+)"))
                local token = sql:match("claim%.event_token = '([^']+)'" )
                local component = sql:match("claim%.component = '([^']+)'" )
                local recipient = tonumber(sql:match("claim%.recipient_guid = (%d+)"))
                local awarded = tonumber(sql:match("claim%.awarded_xp = (%d+)"))
                if account and token and component and awarded then
                    local row = Ledger[ClaimKey(account, token, component)]
                    if row and not row.paid_at
                            and (not recipient or row.recipient_guid == recipient) then
                        row.awarded_xp = awarded
                        row.paid_at = NowEpoch
                    end
                end
            end

            function CharDBQuery(sql)
                SQL[#SQL + 1] = sql
                if sql:find(
                        "INSERT IGNORE INTO acore_ale.paragon_pvp_reward_claim",
                        1, true) then
                    InsertClaims(sql)
                    return nil
                end
                if sql:find("UPDATE acore_ale.", 1, true)
                        and sql:find("JOIN acore_ale.paragon_pvp_reward_claim claim",
                            1, true) then
                    AcknowledgeClaim(sql)
                    return nil
                end
                if sql:find("INSERT IGNORE INTO acore_ale.account_paragon",
                        1, true)
                        or sql:find("INSERT IGNORE INTO acore_ale.character_paragon",
                            1, true) then
                    return nil
                end
                if sql:find("SELECT COUNT(*) FROM acore_ale.account_paragon",
                        1, true)
                        or sql:find(
                            "SELECT COUNT(*) FROM acore_ale.character_paragon",
                            1, true) then
                    return Result({ { 1 } })
                end
                if sql:find("SELECT UNIX_TIMESTAMP(UTC_TIMESTAMP())", 1, true) then
                    return Result({ { NowEpoch, DailyNext, WeeklyNext } })
                end

                local account = tonumber(sql:match("account_id = (%d+)"))
                local recipient = tonumber(sql:match("recipient_guid = (%d+)"))
                if sql:find("COALESCE(MAX(paid_at IS NOT NULL), 0)",
                        1, true) then
                    local token = sql:match("event_token = '([^']+)'" )
                    local component = sql:match("component = '([^']+)'" )
                    local row = account and token and component and
                        Ledger[ClaimKey(account, token, component)] or nil
                    if row and recipient and row.recipient_guid ~= recipient then
                        row = nil
                    end
                    return Result({ {
                        row and 1 or 0,
                        row and row.paid_at and 1 or 0,
                        row and row.source_type or 0,
                        row and row.source_entry or 0,
                        row and row.base_xp or 0,
                    } })
                end
                if sql:find("COALESCE(SUM(event_token =", 1, true) then
                    local token = sql:match("event_token = '([^']+)'" )
                    local component = sql:match("component = '([^']+)'" )
                    local entitlement = sql:match(
                        "SUM%(entitlement_key = '([^']+)'%)")
                    local exact = account and token and component and
                        Ledger[ClaimKey(account, token, component)] and 1 or 0
                    local entitled = 0
                    if account and entitlement then
                        for _, row in pairs(Ledger) do
                            if row.account_id == account
                                    and row.entitlement_key == entitlement then
                                entitled = entitled + 1
                            end
                        end
                    end
                    return Result({ { exact, entitled } })
                end
                if sql:find("SELECT event_token, component", 1, true) then
                    local rows = {}
                    for _, row in pairs(Ledger) do
                        if row.account_id == account and not row.paid_at
                                and (not recipient or row.recipient_guid == recipient) then
                            rows[#rows + 1] = { row.event_token, row.component }
                        end
                    end
                    return Result(rows)
                end
                if sql:find("SELECT source_type, source_entry, base_xp", 1, true) then
                    local token = sql:match("event_token = '([^']+)'" )
                    local component = sql:match("component = '([^']+)'" )
                    local row = account and token and component and
                        Ledger[ClaimKey(account, token, component)] or nil
                    if row and recipient and row.recipient_guid ~= recipient then
                        row = nil
                    end
                    if row and not row.paid_at then
                        return Result({ {
                            row.source_type, row.source_entry, row.base_xp,
                        } })
                    end
                    return nil
                end
                if sql:find("SELECT COUNT(*)", 1, true)
                        and sql:find("counterpart_account_id", 1, true) then
                    local counterpart = tonumber(
                        sql:match("counterpart_account_id = (%d+)"))
                    local minutes = tonumber(sql:match("INTERVAL (%d+) MINUTE")) or 0
                    local count = 0
                    for _, row in pairs(Ledger) do
                        if row.account_id == account and row.component == "honor"
                                and row.counterpart_account_id == counterpart
                                and row.base_xp > 0
                                and row.paid_at
                                and row.paid_at >= NowEpoch - minutes * 60 then
                            count = count + 1
                        end
                    end
                    return Result({ { count } })
                end
                if sql:find("SELECT COUNT(*)", 1, true)
                        and sql:find("opponent_key", 1, true) then
                    local opponent = sql:match("opponent_key = '([^']*)'")
                    local minutes = tonumber(sql:match("INTERVAL (%d+) MINUTE")) or 0
                    local count = 0
                    for _, row in pairs(Ledger) do
                        if row.account_id == account
                                and (row.component == "arena_rated"
                                    or row.component == "arena_skirmish")
                                and row.opponent_key == opponent
                                and row.created_at >= NowEpoch - minutes * 60 then
                            count = count + 1
                        end
                    end
                    return Result({ { count } })
                end
                if sql:find("SELECT COALESCE(SUM(base_xp), 0)", 1, true) then
                    local component = sql:match("component = '([^']+)'" )
                    local period = sql:match("period_key = '([^']+)'" )
                    local total = 0
                    for _, row in pairs(Ledger) do
                        if row.account_id == account and row.component == component
                                and row.period_key == period then
                            total = total + row.base_xp
                        end
                    end
                    return Result({ { total } })
                end
                if sql:find("SELECT COUNT(*)", 1, true)
                        and sql:find("component IN ('duel_win','duel_loss')", 1, true) then
                    local period = sql:match("period_key = '([^']+)'" )
                    local count = 0
                    for _, row in pairs(Ledger) do
                        if row.account_id == account and row.period_key == period
                                and (row.component == "duel_win"
                                    or row.component == "duel_loss")
                                and row.base_xp > 0 then
                            count = count + 1
                        end
                    end
                    return Result({ { count } })
                end
                if sql:find("SELECT COUNT(*)", 1, true)
                        and sql:find("entitlement_key = '", 1, true) then
                    local entitlement = sql:match(
                        "entitlement_key = '([^']+)'" )
                    local count = 0
                    for _, row in pairs(Ledger) do
                        if row.account_id == account
                                and row.entitlement_key == entitlement then
                            count = count + 1
                        end
                    end
                    return Result({ { count } })
                end
                if sql:find("SELECT COUNT(*)", 1, true)
                        and sql:find("event_token", 1, true)
                        and sql:find("paid_at IS NOT NULL", 1, true) then
                    local token = sql:match("event_token = '([^']+)'" )
                    local component = sql:match("component = '([^']+)'" )
                    local row = account and token and component and
                        Ledger[ClaimKey(account, token, component)] or nil
                    if row and recipient and row.recipient_guid ~= recipient then
                        row = nil
                    end
                    return Result({ { row and row.paid_at and 1 or 0 } })
                end
                if sql:find("SELECT COUNT(*)", 1, true)
                        and sql:find("paid_at IS NULL", 1, true) then
                    local count = 0
                    for _, row in pairs(Ledger) do
                        if row.account_id == account and not row.paid_at
                                and (not recipient or row.recipient_guid == recipient) then
                            count = count + 1
                        end
                    end
                    return Result({ { count } })
                end
                return nil
            end

            function CharDBExecute(sql)
                SQL[#SQL + 1] = sql
            end

            function MakePlayer(account, guid, bot)
                local paragon = { Level = 1, Experience = 0 }
                function paragon:GetLevel() return self.Level end
                function paragon:GetExperience() return self.Experience end
                local player = {
                    Account = account,
                    Guid = guid,
                    Bot = bot and true or false,
                    Level = 80,
                    Data = { Paragon = paragon },
                }
                function player:GetAccountId() return self.Account end
                function player:GetGUIDLow() return self.Guid end
                function player:IsPlayerBot() return self.Bot end
                function player:GetLevel() return self.Level end
                function player:GetData(key) return self.Data[key] end
                function player:SetData(key, value) self.Data[key] = value end
                return player
            end

            function Hook.AwardExperience(player, source, entry, base, modifiers)
                if not AwardEnabled then return false end
                if modifiers then ModifierCalls = ModifierCalls + 1 end
                local applied = math.floor(base * ModifierFactor)
                local paragon = player:GetData("Paragon")
                paragon.Experience = paragon.Experience + applied
                AwardCalls[#AwardCalls + 1] = {
                    account_id = player:GetAccountId(),
                    source = source,
                    entry = entry,
                    base = base,
                    applied = applied,
                    modifiers = modifiers,
                }
                return true, applied
            end

            function LedgerCount(account, component)
                local count = 0
                for _, row in pairs(Ledger) do
                    if (not account or row.account_id == account)
                            and (not component or row.component == component) then
                        count = count + 1
                    end
                end
                return count
            end

            function PendingCount(account)
                local count = 0
                for _, row in pairs(Ledger) do
                    if row.account_id == account and not row.paid_at then
                        count = count + 1
                    end
                end
                return count
            end

            function Claim(account, token, component)
                return Ledger[ClaimKey(account, token, component)]
            end
            """
        )
        self.module = self.lua.execute(read(MODULE))
        self.make_player = self.lua.globals().MakePlayer

    def award_rows(self, account=None, source=None):
        rows = []
        for index, row in sorted(
                self.lua.globals().AwardCalls.items(), key=lambda item: item[0]):
            del index
            if account is not None and row["account_id"] != account:
                continue
            if source is not None and row["source"] != source:
                continue
            rows.append({
                key: row[key] for key in (
                    "account_id", "source", "entry", "base", "applied",
                    "modifiers")
            })
        return rows

    def fire_honor(self, player, victim, token, **overrides):
        values = {
            "final_honor": 100,
            "honor_source": 1,
            "battleground_type_id": 0,
            "arena_type": 0,
            "rated": 0,
            "generated_battleground_xp": 0,
        }
        values.update(overrides)
        self.module.OnHonor(
            77, player, victim, values["final_honor"],
            values["honor_source"], values["battleground_type_id"],
            values["arena_type"], values["rated"],
            values["generated_battleground_xp"], token,
        )

    def fire_match(self, player, token, **overrides):
        values = {
            "match_kind": 1,
            "result": 1,
            "duration_seconds": 180,
            "active_seconds": 120,
            "presence_buckets": 3,
            "active_buckets": 2,
            "tactical_actions": 1,
            "battleground_type_id": 2,
            "map_id": 489,
            "instance_id": 700,
            "arena_type": 0,
            "rated": 0,
            "bracket_id": 0,
            "player_team": 0,
            "winner_team": 0,
            "killing_blows": 0,
            "deaths": 0,
            "honorable_kills": 1,
            "bonus_honor": 0,
            "damage_done": 10000,
            "healing_done": 0,
            "pvp_damage_done": 10000,
            "pvp_healing_done": 0,
            "objective1": 1,
            "objective2": 0,
            "objective3": 0,
            "objective4": 0,
            "objective5": 0,
            "is_bot": 1 if player["Bot"] else 0,
            "account_id_argument": player["Account"],
            "opponent_count": 5,
            "real_opponent_count": 0,
            "bot_opponent_count": 5,
            "unique_opponent_accounts": 5,
            "same_account_opponent": 0,
            "same_ip_opponent": 0,
            "inactive": 0,
            "deserter": 0,
            "opponent_roster_key": "roster_bots",
        }
        values.update(overrides)
        self.module.OnMatchComplete(
            78, player,
            values["match_kind"], values["result"],
            values["duration_seconds"], values["active_seconds"],
            values["presence_buckets"], values["active_buckets"],
            values["tactical_actions"], values["battleground_type_id"],
            values["map_id"], values["instance_id"], values["arena_type"],
            values["rated"], values["bracket_id"], values["player_team"],
            values["winner_team"], values["killing_blows"], values["deaths"],
            values["honorable_kills"], values["bonus_honor"],
            values["damage_done"], values["healing_done"],
            values["pvp_damage_done"], values["pvp_healing_done"],
            values["objective1"], values["objective2"], values["objective3"],
            values["objective4"], values["objective5"], values["is_bot"],
            values["account_id_argument"], values["opponent_count"],
            values["real_opponent_count"], values["bot_opponent_count"],
            values["unique_opponent_accounts"],
            values["same_account_opponent"], values["same_ip_opponent"],
            values["inactive"], values["deserter"],
            values["opponent_roster_key"], token,
        )

    def fire_arena(self, player, token, **overrides):
        values = {
            "match_kind": 2,
            "duration_seconds": 15,
            "active_seconds": 15,
            "presence_buckets": 1,
            "active_buckets": 1,
            "battleground_type_id": 0,
            "map_id": 559,
            "arena_type": 2,
            "rated": 1,
            "bracket_id": 2,
            "killing_blows": 1,
            "objective1": 0,
            "real_opponent_count": 0,
            "bot_opponent_count": 2,
            "opponent_count": 2,
            "unique_opponent_accounts": 2,
            "opponent_roster_key": "arena_bots",
        }
        values.update(overrides)
        self.fire_match(player, token, **values)

    def fire_battlefield(self, player, token, **overrides):
        values = {
            "battlefield_type_id": 1,
            "battle_id": 5,
            "zone_id": 4197,
            "map_id": 571,
            "result": 1,
            "duration_seconds": 180,
            "active_seconds": 120,
            "presence_buckets": 3,
            "active_buckets": 2,
            "tactical_actions": 1,
            "player_team": 0,
            "winner_team": 0,
            "attacker_team": 0,
            "defender_team_at_start": 1,
            "ended_by_timer": 0,
            "is_bot": 1 if player["Bot"] else 0,
            "account_id_argument": player["Account"],
            "player_kills": 1,
            "pvp_damage_done": 10000,
            "pvp_healing_done": 0,
            "objective_major": 1,
            "objective_standard": 0,
            "objective_assist": 0,
            "real_opponent_count": 0,
            "bot_opponent_count": 5,
            "unique_opponent_accounts": 5,
            "same_account_opponent": 0,
            "same_ip_opponent": 0,
            "inactive": 0,
            "deserter": 0,
            "opponent_roster_key": "wg_bots",
        }
        values.update(overrides)
        self.module.OnBattlefieldComplete(
            79, player, values["battlefield_type_id"], values["battle_id"],
            values["zone_id"], values["map_id"], values["result"],
            values["duration_seconds"], values["active_seconds"],
            values["presence_buckets"], values["active_buckets"],
            values["tactical_actions"], values["player_team"],
            values["winner_team"], values["attacker_team"],
            values["defender_team_at_start"], values["ended_by_timer"],
            values["is_bot"], values["account_id_argument"],
            values["player_kills"], values["pvp_damage_done"],
            values["pvp_healing_done"], values["objective_major"],
            values["objective_standard"], values["objective_assist"],
            values["real_opponent_count"], values["bot_opponent_count"],
            values["unique_opponent_accounts"],
            values["same_account_opponent"], values["same_ip_opponent"],
            values["inactive"], values["deserter"],
            values["opponent_roster_key"], token,
        )

    def fire_outdoor(self, player, token, **overrides):
        values = {
            "outdoor_pvp_type_id": 1,
            "objective_id": 2,
            "objective_entry": 3,
            "objective_tier": 1,
            "map_id": 530,
            "zone_id": 3518,
            "team": 0,
            "participant_count": 40,
        }
        values.update(overrides)
        self.module.OnOutdoorObjective(
            80, player, values["outdoor_pvp_type_id"],
            values["objective_id"], values["objective_entry"],
            values["objective_tier"], values["map_id"], values["zone_id"],
            values["team"], values["participant_count"], token,
        )

    def fire_duel(self, winner, loser, token, **overrides):
        values = {
            "duel_type": 1,
            "duration_seconds": 30,
            "same_account": 0,
            "same_ip": 0,
            "winner_is_bot": 1 if winner["Bot"] else 0,
            "loser_is_bot": 1 if loser["Bot"] else 0,
        }
        values.update(overrides)
        self.module.OnDuelComplete(
            81, winner, loser, values["duel_type"],
            values["duration_seconds"], values["same_account"],
            values["same_ip"], values["winner_is_bot"],
            values["loser_is_bot"], token,
        )

    def test_query_uint64_mock_matches_ale_userdata_contract(self):
        value_type, numeric_value, rendered = self.lua.execute(
            "local value = ALEUInt64('18446744073709551615'); "
            "return type(value), tonumber(value), tostring(value)"
        )
        self.assertEqual("userdata", value_type)
        self.assertIsNone(numeric_value)
        self.assertEqual("18446744073709551615", rendered)

    def test_ale_uint64_query_values_do_not_block_claim_payment(self):
        player = self.make_player(10, 100, False)
        victim = self.make_player(20, 200, False)
        self.fire_honor(player, victim, "ale_uint64")

        claim = self.lua.globals().Claim(10, "ale_uint64", "honor")
        self.assertEqual(800, claim["base_xp"])
        self.assertEqual(800, claim["awarded_xp"])
        self.assertEqual(self.lua.globals().NowEpoch, claim["paid_at"])
        self.assertEqual([800], [row["base"] for row in self.award_rows()])
        self.assertEqual(0, self.lua.globals().PendingCount(10))

    def test_honor_is_account_wide_and_independent_of_group_or_killing_blow(self):
        first = self.make_player(10, 100, False)
        same_account_alt = self.make_player(10, 101, False)
        victim = self.make_player(20, 200, False)
        for index, player in enumerate(
                (first, same_account_alt, first, same_account_alt), 1):
            self.fire_honor(player, victim, "honor_%d" % index)

        self.assertEqual(
            [800, 400, 80],
            [row["base"] for row in self.award_rows(account=10, source=9)],
        )
        self.assertEqual(4, self.lua.globals().LedgerCount(10, "honor"))
        self.assertEqual(
            0,
            self.lua.globals().Claim(10, "honor_4", "honor")["base_xp"],
        )

        definition = re.search(
            r"function (?:M|ParagonPvPXP)\.OnHonor\((.*?)\)\n",
            read(MODULE), re.DOTALL)
        self.assertIsNotNone(definition)
        self.assertNotRegex(definition.group(1), r"group|party|killing|killer")

    def test_honor_allows_bot_victim_but_rejects_bot_or_own_account_recipient(self):
        player = self.make_player(10, 100, False)
        bot_victim = self.make_player(20, 200, True)
        self.fire_honor(player, bot_victim, "honor_bot")
        self.assertEqual([800], [row["base"] for row in self.award_rows()])

        bot_recipient = self.make_player(30, 300, True)
        self.fire_honor(bot_recipient, player, "bot_recipient")
        same_account = self.make_player(10, 102, False)
        self.fire_honor(player, same_account, "own_account")
        self.assertEqual(1, len(self.award_rows()))

    def test_generated_battleground_xp_is_telemetry_not_a_second_award(self):
        player = self.make_player(10, 100, False)
        self.fire_honor(
            player, None, "fixed_0", honor_source=2,
            generated_battleground_xp=0)
        self.fire_honor(
            player, None, "fixed_1", honor_source=2,
            generated_battleground_xp=1)
        self.assertEqual([800, 800], [row["base"] for row in self.award_rows()])

    def test_bot_only_battleground_gets_full_values_and_weekly_breadth(self):
        player = self.make_player(10, 100, False)
        self.fire_match(player, "bg_bot_only")
        self.assertEqual(
            [(10, 11600), (14, 20000)],
            [(row["source"], row["base"]) for row in self.award_rows()],
        )

    def test_zero_breadth_value_does_not_block_primary_match_claim(self):
        self.lua.globals().ConfigValues["PARAGON_PVP_WEEKLY_BREADTH_XP"] = "0"
        player = self.make_player(10, 100, False)
        self.fire_match(player, "bg_no_breadth")
        self.assertEqual(
            [(10, 11600)],
            [(row["source"], row["base"]) for row in self.award_rows()],
        )

    def test_empty_optional_keys_do_not_reject_primary_claims(self):
        player = self.make_player(10, 100, False)
        victim = self.make_player(20, 200, False)
        self.fire_honor(player, victim, "empty_optional")
        claim = self.lua.globals().Claim(10, "empty_optional", "honor")
        self.assertIsNotNone(claim)
        self.assertEqual("", claim["opponent_key"])
        self.assertEqual("", claim["period_key"])
        self.assertEqual([800], [row["base"] for row in self.award_rows()])

    def test_battleground_idempotency_account_scope_and_weekly_reset(self):
        first = self.make_player(10, 100, False)
        alt = self.make_player(10, 101, False)
        self.fire_match(first, "bg_once")
        self.fire_match(first, "bg_once")
        self.fire_match(alt, "bg_alt")
        self.assertEqual(2, len(self.award_rows(account=10, source=10)))
        self.assertEqual(1, len(self.award_rows(account=10, source=14)))

        self.fire_match(
            alt, "bg_distinct", battleground_type_id=3, map_id=529)
        self.assertEqual(3, len(self.award_rows(account=10, source=10)))
        self.assertEqual(2, len(self.award_rows(account=10, source=14)))

        self.lua.globals().WeeklyNext = 600000
        self.fire_match(alt, "bg_new_week")
        self.assertEqual(4, len(self.award_rows(account=10, source=10)))
        self.assertEqual(3, len(self.award_rows(account=10, source=14)))

    def test_battleground_rejects_bot_recipient_same_account_afk_and_deserter(self):
        for index, overrides in enumerate((
                {"is_bot": 1},
                {"same_account_opponent": 1},
                {"inactive": 1},
                {"deserter": 1},
                {"presence_buckets": 7, "active_buckets": 2}), 1):
            player = self.make_player(10 + index, 100 + index, False)
            self.fire_match(player, "denied_%d" % index, **overrides)
        bot = self.make_player(50, 500, True)
        self.fire_match(bot, "bot_recipient")
        self.assertEqual([], self.award_rows())

    def test_late_boundary_join_cannot_qualify_in_bg_or_wintergrasp(self):
        battleground_player = self.make_player(10, 100, False)
        battlefield_player = self.make_player(20, 200, False)
        late_boundary = {
            "duration_seconds": 600,
            "active_seconds": 2,
            "presence_buckets": 2,
            "active_buckets": 2,
        }
        self.fire_match(
            battleground_player, "late_bg_boundary", **late_boundary)
        self.fire_battlefield(
            battlefield_player, "late_wg_boundary", **late_boundary)
        self.assertEqual([], self.award_rows())

    def test_same_ip_is_paid_and_durably_risk_flagged(self):
        player = self.make_player(10, 100, False)
        self.fire_match(player, "same_ip", same_ip_opponent=1)
        self.assertEqual(2, len(self.award_rows()))
        self.assertEqual(
            1,
            self.lua.globals().Claim(10, "same_ip", "battleground")[
                "same_ip_risk"],
        )
        self.assertEqual(
            1,
            self.lua.globals().Claim(10, "same_ip", "breadth")[
                "same_ip_risk"],
        )

    def test_modifier_is_applied_exactly_once_to_each_repeatable_claim(self):
        self.lua.globals().ConfigValues["PARAGON_PVP_WEEKLY_BREADTH_XP"] = "0"
        self.lua.globals().ModifierFactor = 2
        player = self.make_player(10, 100, False)
        self.fire_match(player, "modifier_once")
        rows = self.award_rows()
        self.assertEqual(1, len(rows))
        self.assertTrue(rows[0]["modifiers"])
        self.assertEqual(11600, rows[0]["base"])
        self.assertEqual(23200, rows[0]["applied"])
        self.assertEqual(1, self.lua.globals().ModifierCalls)

    def test_pending_claim_retries_once_after_award_failure(self):
        self.lua.globals().ConfigValues["PARAGON_PVP_WEEKLY_BREADTH_XP"] = "0"
        self.lua.globals().AwardEnabled = False
        player = self.make_player(10, 100, False)
        self.fire_match(player, "pending")
        self.assertEqual(1, self.lua.globals().PendingCount(10))
        self.assertEqual([], self.award_rows())

        self.lua.globals().AwardEnabled = True
        self.assertTrue(self.module.PayPendingClaims(player))
        self.assertEqual(0, self.lua.globals().PendingCount(10))
        self.assertEqual([11600], [row["base"] for row in self.award_rows()])
        self.assertTrue(self.module.PayPendingClaims(player))
        self.assertEqual(1, len(self.award_rows()))

    def test_character_linked_pending_claim_stays_with_original_recipient(self):
        self.lua.globals().ConfigValues["LEVEL_LINKED_TO_ACCOUNT"] = "0"
        self.lua.globals().ConfigValues["PARAGON_PVP_WEEKLY_BREADTH_XP"] = "0"
        original = self.make_player(10, 100, False)
        other_character = self.make_player(10, 101, False)

        self.lua.globals().AwardEnabled = False
        self.fire_match(original, "character_owned_pending")
        claim = self.lua.globals().Claim(
            10, "character_owned_pending", "battleground")
        self.assertEqual(100, claim["recipient_guid"])
        self.assertEqual(1, self.lua.globals().PendingCount(10))

        self.lua.globals().AwardEnabled = True
        self.assertTrue(self.module.PayPendingClaims(other_character))
        self.assertEqual([], self.award_rows())
        self.assertEqual(1, self.lua.globals().PendingCount(10))

        self.assertTrue(self.module.PayPendingClaims(original))
        self.assertEqual([11600], [row["base"] for row in self.award_rows()])
        self.assertEqual(0, self.lua.globals().PendingCount(10))

    def test_rated_arena_brackets_roster_dr_and_weekly_breadth(self):
        player = self.make_player(10, 100, False)
        for index in range(7):
            self.fire_arena(player, "arena_%d" % index)
        self.assertEqual(
            [37500, 37500, 37500, 18750, 18750, 3750],
            [row["base"] for row in self.award_rows(source=11)],
        )
        self.assertEqual([20000], [row["base"] for row in self.award_rows(source=14)])

        self.fire_arena(
            player, "arena_loss", result=0,
            opponent_roster_key="roster_loss")
        self.assertEqual(26250, self.award_rows(source=11)[-1]["base"])
        self.assertEqual(1, len(self.award_rows(source=14)))

        for bracket, amount in ((3, 45000), (5, 56250)):
            self.fire_arena(
                player, "arena_bracket_%d" % bracket, arena_type=bracket,
                bracket_id=bracket, opponent_roster_key="roster_%d" % bracket)
            self.assertIn(amount, [row["base"] for row in self.award_rows(source=11)])
        self.assertEqual(3, len(self.award_rows(source=14)))

    def test_bot_only_rated_arena_gets_full_normal_value(self):
        player = self.make_player(10, 100, False)
        self.fire_arena(player, "arena_bots_full")
        self.assertEqual(
            [(11, 37500), (14, 20000)],
            [(row["source"], row["base"]) for row in self.award_rows()],
        )

    def test_skirmish_daily_cap_is_partial_account_wide_and_resets(self):
        first = self.make_player(10, 100, False)
        alt = self.make_player(10, 101, False)
        for index in range(4):
            self.fire_arena(
                first, "skirmish_win_%d" % index, rated=0,
                opponent_roster_key="skirmish_%d" % index)
        self.fire_arena(
            alt, "skirmish_loss_1", rated=0, result=0,
            opponent_roster_key="skirmish_4")
        self.fire_arena(
            alt, "skirmish_loss_partial", rated=0, result=0,
            opponent_roster_key="skirmish_5")
        self.fire_arena(
            first, "skirmish_capped", rated=0,
            opponent_roster_key="skirmish_6")
        self.assertEqual(
            [11250, 11250, 11250, 11250, 7500, 3750],
            [row["base"] for row in self.award_rows(source=11)],
        )
        self.assertEqual([], self.award_rows(source=14))

        self.lua.globals().DailyNext = 120000
        self.fire_arena(
            alt, "skirmish_reset", rated=0,
            opponent_roster_key="skirmish_reset")
        self.assertEqual(11250, self.award_rows(source=11)[-1]["base"])

    def test_wintergrasp_bot_only_full_values_breadth_and_activity_gates(self):
        player = self.make_player(10, 100, False)
        self.fire_battlefield(player, "wg_full")
        self.assertEqual(
            [(15, 11600), (14, 20000)],
            [(row["source"], row["base"]) for row in self.award_rows()],
        )
        self.fire_battlefield(player, "wg_repeat")
        self.assertEqual(2, len(self.award_rows(source=15)))
        self.assertEqual(1, len(self.award_rows(source=14)))

        self.lua.globals().WeeklyNext = 600000
        self.fire_battlefield(player, "wg_reset")
        self.assertEqual(3, len(self.award_rows(source=15)))
        self.assertEqual(2, len(self.award_rows(source=14)))

        self.fire_battlefield(player, "wg_deserter", deserter=1)
        self.fire_battlefield(player, "wg_own", same_account_opponent=1)
        self.assertEqual(5, len(self.award_rows()))

    def test_outdoor_values_participant_independence_and_weekly_breadth(self):
        player = self.make_player(10, 100, False)
        self.fire_outdoor(player, "outdoor_standard", participant_count=40)
        self.fire_outdoor(
            player, "outdoor_major", objective_tier=2, participant_count=1)
        self.assertEqual(
            [15000, 30000],
            [row["base"] for row in self.award_rows(source=12)],
        )
        self.assertEqual([20000], [row["base"] for row in self.award_rows(source=14)])

        self.fire_outdoor(
            player, "outdoor_distinct", outdoor_pvp_type_id=2, zone_id=3518)
        self.assertEqual(2, len(self.award_rows(source=14)))

    def test_duel_distinct_opponent_cap_is_account_wide_and_resets(self):
        winner = self.make_player(10, 100, False)
        winner_alt = self.make_player(10, 101, False)
        opponents = [self.make_player(20 + index, 200 + index, False)
                     for index in range(4)]
        self.fire_duel(winner, opponents[0], "duel_1")
        self.fire_duel(winner_alt, opponents[0], "duel_repeat")
        self.fire_duel(winner_alt, opponents[1], "duel_2")
        self.fire_duel(winner, opponents[2], "duel_3")
        self.fire_duel(winner, opponents[3], "duel_4")
        self.assertEqual(
            [5000, 5000, 5000],
            [row["base"] for row in self.award_rows(account=10)],
        )

        self.lua.globals().DailyNext = 120000
        self.fire_duel(winner_alt, opponents[3], "duel_reset")
        self.assertEqual(4, len(self.award_rows(account=10)))

    def test_duel_bot_opponent_pays_real_side_and_same_account_is_denied(self):
        real = self.make_player(10, 100, False)
        losing_bot = self.make_player(20, 200, True)
        winning_bot = self.make_player(21, 201, True)
        self.fire_duel(real, losing_bot, "real_wins_bot")
        self.fire_duel(winning_bot, real, "real_loses_bot")
        self.assertEqual(
            [5000, 2000],
            [row["base"] for row in self.award_rows(account=10)],
        )
        self.assertEqual([], self.award_rows(account=20))
        self.assertEqual([], self.award_rows(account=21))

        same = self.make_player(10, 101, False)
        self.fire_duel(real, same, "same_account", same_account=1)
        self.assertEqual(2, len(self.award_rows(account=10)))

        self.fire_duel(real, losing_bot, "interrupted_duel", duel_type=0)
        self.fire_duel(real, winning_bot, "fled_duel", duel_type=2)
        self.assertEqual(2, len(self.award_rows(account=10)))
        self.assertIsNone(
            self.lua.globals().Claim(10, "interrupted_duel", "duel_win"))
        self.assertIsNone(
            self.lua.globals().Claim(10, "fled_duel", "duel_win"))

    def test_zero_duel_side_reward_does_not_block_other_recipient(self):
        self.lua.globals().ConfigValues["PARAGON_PVP_DUEL_LOSS_XP"] = "0"
        winner = self.make_player(10, 100, False)
        loser = self.make_player(20, 200, False)
        self.fire_duel(winner, loser, "zero_loser")
        self.assertEqual(
            [(10, 5000)],
            [(row["account_id"], row["base"]) for row in self.award_rows()],
        )

    def test_duel_same_ip_is_allowed_and_risk_flagged(self):
        winner = self.make_player(10, 100, False)
        loser = self.make_player(20, 200, False)
        self.fire_duel(winner, loser, "duel_same_ip", same_ip=1)
        self.assertEqual([5000, 2000], [row["base"] for row in self.award_rows()])
        self.assertEqual(
            1, self.lua.globals().Claim(10, "duel_same_ip", "duel_win")[
                "same_ip_risk"])
        self.assertEqual(
            1, self.lua.globals().Claim(20, "duel_same_ip", "duel_loss")[
                "same_ip_risk"])

    def test_invalid_event_token_cannot_reach_sql_or_award(self):
        player = self.make_player(10, 100, False)
        victim = self.make_player(20, 200, False)
        self.fire_honor(player, victim, "bad'token")
        sql = [statement for _, statement in
               sorted(self.lua.globals().SQL.items())]
        self.assertTrue(sql)
        self.assertTrue(all("bad'token" not in statement for statement in sql))
        self.assertFalse(any(
            "INSERT IGNORE INTO acore_ale.paragon_pvp_reward_claim" in statement
            for statement in sql))
        self.assertEqual([], self.award_rows())
        self.assertEqual(0, self.lua.globals().LedgerCount(None, None))


if __name__ == "__main__":
    unittest.main()
