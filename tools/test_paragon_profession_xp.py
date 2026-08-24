import os
import re
import unittest

try:
    from lupa.lua52 import LuaRuntime
except ImportError:  # pragma: no cover - reported as skipped by unittest
    LuaRuntime = None


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, "serverside", "paragon", "paragon_hook.lua")
MEDIATOR = os.path.join(
    ROOT, "serverside", "paragon", "lib", "Mediator", "mediator.lua"
)
CLASSIC = os.path.join(
    ROOT, "serverside", "paragon", "lib", "classic", "classic.ext"
)
MODULE = os.path.join(
    ROOT, "serverside", "paragon", "modules", "paragon_profession_xp.lua"
)
CONSTANT = os.path.join(ROOT, "serverside", "paragon", "paragon_constant.lua")
REPOSITORY = os.path.join(
    ROOT, "serverside", "paragon", "paragon_repository.lua"
)
SCHEMA = os.path.join(ROOT, "sql", "02_create_tables.sql")
DEFAULT_CONFIG = os.path.join(ROOT, "sql", "04_insert_default_config.sql")
ANNIVERSARY_CONFIG = os.path.join(ROOT, "sql", "05_apply_anniversary_config.sql")

PROFESSION_SKILLS = {
    129, 164, 165, 171, 182, 185, 186,
    197, 202, 333, 356, 393, 755, 773,
}


@unittest.skipUnless(LuaRuntime, "lupa.lua52 is required for Lua behavior tests")
class ParagonProfessionXPTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute(
            r"""
            ConfigValues = {
                ENABLE_PARAGON_SYSTEM = "1",
                MINIMUM_LEVEL_FOR_PARAGON_XP = "80",
                LEVEL_LINKED_TO_ACCOUNT = "1",
                UNIVERSAL_SKILL_EXPERIENCE = "2000",
                UNIVERSAL_CREATURE_EXPERIENCE = "50",
                UNIVERSAL_ACHIEVEVEMENT_EXPERIENCE = "100",
                UNIVERSAL_QUEST_EXPERIENCE = "1",
            }
            SkillOverrides = {}
            Config = { experience = { creature = {}, achievement = {}, skill = {}, quest = {} } }
            function Config:GetByField(field) return ConfigValues[field] end
            function Config:GetCreatureExperience(_) return nil end
            function Config:GetAchievementExperience(_) return nil end
            function Config:GetSkillExperience(id) return SkillOverrides[id] end
            function Config:GetQuestExperience(_) return nil end
            function Config:GetCategories() return {} end

            package.preload["paragon_class"] = function() return function() return {} end end
            package.preload["paragon_config"] = function() return Config end
            package.preload["paragon_repository"] = function() return {} end
            package.preload["paragon_constant"] = function()
                return { DB_NAME = "acore_ale", STATISTICS = {} }
            end

            ProfessionData = {
                ACTION = {
                    CRAFT = 1,
                    GATHER_GAMEOBJECT = 2,
                    GATHER_CREATURE = 3,
                    FISHING_AREA = 4,
                    FISHING_HOLE = 5,
                    PROSPECT = 6,
                    MILL = 7,
                    DISENCHANT = 8,
                }
            }
            function ProfessionData.Resolve(kind, skill, context, quantity)
                if kind == 1 and skill == 164 and context == 5001 then
                    return 160, { tier = "wrath" }
                elseif kind == 2 and skill == 186 and context == 5002 then
                    return 50 * math.min(quantity, 4), { tier = "classic" }
                elseif kind == 6 and skill == 755 and context == 5003 then
                    return 200, { tier = "wrath" }
                end
                return nil, "unknown or mismatched profession action"
            end
            package.preload["paragon.modules.paragon_profession_data"] = function()
                return ProfessionData
            end

            RegisteredPlayerEvents = {}
            function RegisterPlayerEvent(event_id, callback)
                RegisteredPlayerEvents[event_id] = RegisteredPlayerEvents[event_id] or {}
                table.insert(RegisteredPlayerEvents[event_id], callback)
            end
            function RegisterServerEvent(_, _) end
            function RegisterClientRequests(_) end
            RegisteredMediatorEvents = {}
            function RegisterMediatorEvent(name, callback)
                RegisteredMediatorEvents[name] = RegisteredMediatorEvents[name] or {}
                table.insert(RegisteredMediatorEvents[name], callback)
            end

            AwardedXP = {}
            ModifierCalls = 0
            ModifierSources = {}
            ModifierFactor = 1
            Mediator = {}
            function Mediator.On(name, params)
                if name == "OnExperienceCalculated" then
                    ModifierCalls = ModifierCalls + 1
                    table.insert(ModifierSources, params.arguments[3])
                    return params.arguments[4] * ModifierFactor
                elseif name == "OnUpdatePlayerExperience" then
                    table.insert(AwardedXP, params.arguments[3])
                    params.arguments[2].Experience =
                        params.arguments[2].Experience + params.arguments[3]
                end
                if params.defaults then
                    return table.unpack(params.defaults)
                end
                return nil
            end

            Paragon = {
                Level = 1,
                Experience = 0,
                GetLevel = function(self) return self.Level end,
                GetPoints = function(_) return 0 end,
                GetExperience = function(self) return self.Experience end,
                GetExperienceForNextLevel = function(_) return 30000 end,
            }
            Player = { Data = {}, Skills = {}, Level = 80, Account = 7, Guid = 70, Bot = false }
            function Player:GetData(key) return self.Data[key] end
            function Player:SetData(key, value) self.Data[key] = value end
            function Player:GetLevel() return self.Level end
            function Player:GetAccountId() return self.Account end
            function Player:GetGUIDLow() return self.Guid end
            function Player:IsPlayerBot() return self.Bot end
            function Player:GetPureSkillValue(skill) return self.Skills[skill] or 0 end
            function Player:SendServerResponse(...) end

            DBState = {}
            ExecutedSQL = {}
            ApplyDBWrites = true
            local function DBKey(owner_type, owner_id, skill)
                return tostring(owner_type) .. ":" .. tostring(owner_id) .. ":" .. tostring(skill)
            end
            function SetProgress(owner_type, owner_id, skill, high_water, pending)
                DBState[DBKey(owner_type, owner_id, skill)] = {
                    owner_type, owner_id, skill, high_water, pending
                }
            end
            function GetProgress(owner_type, owner_id, skill, column)
                local row = DBState[DBKey(owner_type, owner_id, skill)]
                return row and row[column] or nil
            end
            local function MakeResult(rows)
                if #rows == 0 then return nil end
                local index = 1
                return {
                    GetUInt32 = function(_, column) return rows[index][column + 1] end,
                    GetUInt64 = function(_, column) return rows[index][column + 1] end,
                    NextRow = function(_)
                        if index < #rows then index = index + 1 return true end
                        return false
                    end,
                }
            end
            local function ApplyDBSQL(sql)
                local owner_type, owner_id, skill, high_water, pending = sql:match(
                    "VALUES%s*%(%s*(%d+),%s*(%d+),%s*(%d+),%s*(%d+),%s*(%d+)%)")
                if owner_type then
                    owner_type, owner_id, skill = tonumber(owner_type), tonumber(owner_id), tonumber(skill)
                    high_water, pending = tonumber(high_water), tonumber(pending)
                    local key = DBKey(owner_type, owner_id, skill)
                    local row = DBState[key]
                    if row then
                        row[4] = math.max(row[4], high_water)
                        row[5] = row[5] + pending
                    else
                        DBState[key] = { owner_type, owner_id, skill, high_water, pending }
                    end
                    return
                end
                local update_owner_type = sql:match(
                    "profession%.owner_type%s*=%s*(%d+)")
                local update_owner_id = sql:match(
                    "profession%.owner_id%s*=%s*(%d+)")
                if update_owner_type and update_owner_id
                        and sql:find("profession.pending_xp = 0", 1, true) then
                    update_owner_type, update_owner_id = tonumber(update_owner_type), tonumber(update_owner_id)
                    for _, row in pairs(DBState) do
                        if row[1] == update_owner_type and row[2] == update_owner_id then
                            row[5] = 0
                        end
                    end
                end
            end
            function CharDBQuery(sql)
                if sql:match("^%s*INSERT") or sql:match("^%s*UPDATE") then
                    table.insert(ExecutedSQL, sql)
                    if ApplyDBWrites then ApplyDBSQL(sql) end
                    return nil
                end
                local owner_type, owner_id = sql:match(
                    "owner_type%s*=%s*(%d+)%s+AND owner_id%s*=%s*(%d+)")
                if not owner_type then return nil end
                owner_type, owner_id = tonumber(owner_type), tonumber(owner_id)
                local skill = sql:match("skill_id%s*=%s*(%d+)")
                local rows = {}
                if skill then
                    local row = DBState[DBKey(owner_type, owner_id, tonumber(skill))]
                    if row then rows[1] = { row[4], row[5] } end
                else
                    for _, row in pairs(DBState) do
                        if row[1] == owner_type and row[2] == owner_id then
                            rows[#rows + 1] = { row[3], row[4], row[5] }
                        end
                    end
                end
                return MakeResult(rows)
            end
            function CharDBExecute(sql)
                table.insert(ExecutedSQL, sql)
                if not ApplyDBWrites then return end
                ApplyDBSQL(sql)
            end

            function ResetHarness()
                AwardedXP = {}
                ModifierCalls = 0
                ModifierSources = {}
                ModifierFactor = 1
                DBState = {}
                ExecutedSQL = {}
                ApplyDBWrites = true
                Player.Data = { Paragon = Paragon }
                Player.Skills = {}
                Player.Level = 80
                Player.Account = 7
                Player.Guid = 70
                Player.Bot = false
                ConfigValues.ENABLE_PARAGON_SYSTEM = "1"
                ConfigValues.LEVEL_LINKED_TO_ACCOUNT = "1"
                ConfigValues.UNIVERSAL_SKILL_EXPERIENCE = "2000"
                SkillOverrides = {}
                Paragon.Level = 1
                Paragon.Experience = 0
            end
            function AwardCount() return #AwardedXP end
            function AwardAmount(index) return AwardedXP[index] end
            function ModifierSource(index) return ModifierSources[index] end
            function ExecutedCount() return #ExecutedSQL end
            function ExecutedAt(index) return ExecutedSQL[index] end
            """
        )
        with open(HOOK, encoding="utf-8") as handle:
            self.hook = self.lua.execute(handle.read())
        self.lua.globals().LoadedHook = self.hook
        self.lua.execute('package.loaded["paragon_hook"] = LoadedHook')
        with open(MODULE, encoding="utf-8") as handle:
            self.module = self.lua.execute(handle.read())
        self.lua.globals().ResetHarness()

    def awards(self):
        return [
            self.lua.globals().AwardAmount(i)
            for i in range(1, self.lua.globals().AwardCount() + 1)
        ]

    def skill(self, skill_id, old_value, new_value):
        self.hook.OnPlayerSkillUpdate(
            62, self.lua.globals().Player, skill_id, old_value, 450, 6, new_value
        )

    def action(self, kind, skill, context, quantity, token):
        self.module.OnProfessionAction(
            76, self.lua.globals().Player, kind, skill, context, quantity, token
        )

    def test_exact_wotlk_profession_allowlist(self):
        actual = {
            skill_id
            for skill_id in range(1, 1001)
            if self.module.PROFESSION_SKILLS[skill_id]
        }
        self.assertEqual(PROFESSION_SKILLS, actual)

    def test_skillup_is_exactly_2000_and_bypasses_personal_modifiers(self):
        self.lua.globals().ModifierFactor = 9
        self.lua.globals().SkillOverrides[164] = 25
        self.skill(164, 100, 101)
        self.assertEqual([2000], self.awards())
        self.assertEqual(0, self.lua.globals().ModifierCalls)
        operations = [
            self.lua.globals().ExecutedAt(index)
            for index in range(1, self.lua.globals().ExecutedCount() + 1)
        ]
        self.assertIn("INSERT INTO", operations[0])
        self.assertIn("INSERT IGNORE INTO acore_ale.account_paragon", operations[1])
        self.assertIn("UPDATE acore_ale.account_paragon progression", operations[2])
        self.assertIn("profession.pending_xp = 0", operations[2])

        # The common hook enforces the flat skill-up contract even if another
        # module mistakenly asks for the modified award path.
        self.assertTrue(
            self.hook.AwardExperience(
                self.lua.globals().Player, 3, 164, 2000, True
            )
        )
        self.assertEqual([2000, 2000], self.awards())
        self.assertEqual(0, self.lua.globals().ModifierCalls)

    def test_all_flat_sources_bypass_the_common_modifier_boundary(self):
        self.lua.globals().ModifierFactor = 9
        for source_type, entry, experience in (
            (2, 9001, 125),
            (3, 164, 2000),
            (4, 9002, 75),
            (8, 9003, 500),
        ):
            with self.subTest(source_type=source_type):
                self.assertTrue(
                    self.hook.AwardExperience(
                        self.lua.globals().Player,
                        source_type,
                        entry,
                        experience,
                        True,
                    )
                )

        self.assertEqual([125, 2000, 75, 500], self.awards())
        self.assertEqual(0, self.lua.globals().ModifierCalls)

    def test_disabled_system_blocks_awards_banks_and_token_consumption(self):
        self.lua.globals().ConfigValues.ENABLE_PARAGON_SYSTEM = 0

        self.skill(164, 100, 101)
        self.action(1, 164, 5001, 1, "disabled-action")
        self.assertFalse(
            self.hook.AwardExperience(
                self.lua.globals().Player, 1, 9001, 50, True
            )
        )

        self.assertEqual([], self.awards())
        self.assertEqual(0, self.lua.globals().ModifierCalls)
        self.assertIsNone(self.lua.globals().GetProgress(1, 7, 164, 4))

        # A disabled action must not consume its server token. Once enabled,
        # the same valid action can be awarded exactly once.
        self.lua.globals().ConfigValues.ENABLE_PARAGON_SYSTEM = "1"
        self.action(1, 164, 5001, 1, "disabled-action")
        self.assertEqual([160], self.awards())

    def test_disabled_system_preserves_previously_earned_pending_xp(self):
        self.lua.globals().SetProgress(1, 7, 164, 101, 2000)
        self.lua.globals().ConfigValues.ENABLE_PARAGON_SYSTEM = "0"

        self.assertFalse(self.module.PayPending(self.lua.globals().Player))
        self.assertEqual([], self.awards())
        self.assertEqual(2000, self.lua.globals().GetProgress(1, 7, 164, 5))

        self.lua.globals().ConfigValues.ENABLE_PARAGON_SYSTEM = 1
        self.assertTrue(self.module.PayPending(self.lua.globals().Player))
        self.assertEqual([2000], self.awards())
        self.assertEqual(0, self.lua.globals().GetProgress(1, 7, 164, 5))

    def test_multi_point_gain_scales_and_non_increases_do_not(self):
        self.skill(164, 100, 105)
        self.skill(164, 105, 105)
        self.skill(164, 105, 100)
        self.assertEqual([10000], self.awards())

    def test_account_high_water_blocks_replay_relearn_and_alt_farming(self):
        self.skill(164, 100, 105)
        self.skill(164, 100, 105)
        self.skill(164, 0, 103)
        self.skill(164, 105, 108)
        self.assertEqual([10000, 6000], self.awards())

        self.lua.globals().Player.Guid = 71
        self.lua.globals().Player.Data = self.lua.table_from({"Paragon": self.lua.globals().Paragon})
        self.skill(164, 0, 109)
        self.assertEqual([10000, 6000, 2000], self.awards())

    def test_account_cache_serializes_alts_while_database_writes_are_pending(self):
        self.lua.globals().ApplyDBWrites = False
        self.skill(164, 100, 101)
        self.assertEqual([2000], self.awards())
        self.assertIsNone(self.lua.globals().GetProgress(1, 7, 164, 4))

        self.lua.globals().Player.Guid = 71
        self.lua.globals().Player.Data = self.lua.table_from(
            {"Paragon": self.lua.globals().Paragon}
        )
        self.skill(164, 100, 101)
        self.assertEqual([2000], self.awards())

    def test_character_linked_mode_scopes_high_water_by_guid(self):
        self.lua.globals().ConfigValues.LEVEL_LINKED_TO_ACCOUNT = "0"
        self.skill(164, 100, 101)
        self.lua.globals().Player.Guid = 71
        self.lua.globals().Player.Data = self.lua.table_from({"Paragon": self.lua.globals().Paragon})
        self.skill(164, 100, 101)
        self.assertEqual([2000, 2000], self.awards())

    def test_numeric_account_mode_and_invalid_owner_ids_fail_closed(self):
        self.lua.globals().ConfigValues.LEVEL_LINKED_TO_ACCOUNT = 1
        self.skill(164, 100, 101)
        self.assertEqual([2000], self.awards())
        self.assertEqual(101, self.lua.globals().GetProgress(1, 7, 164, 4))

        self.lua.globals().Player.Data = self.lua.table_from(
            {"Paragon": self.lua.globals().Paragon}
        )
        self.lua.globals().Player.Account = 0
        self.skill(164, 101, 102)
        self.assertEqual([2000], self.awards())

    def test_pre80_future_points_bank_and_pay_once_when_eligible(self):
        self.lua.globals().Player.Level = 79
        self.skill(164, 100, 103)
        self.assertEqual([], self.awards())
        self.assertEqual(6000, self.lua.globals().GetProgress(1, 7, 164, 5))

        self.lua.globals().Player.Level = 80
        self.assertTrue(self.module.PayPending(self.lua.globals().Player))
        self.assertEqual([6000], self.awards())
        self.assertEqual(0, self.lua.globals().GetProgress(1, 7, 164, 5))
        self.assertFalse(self.module.PayPending(self.lua.globals().Player))
        self.assertEqual([6000], self.awards())

    def test_weapon_defense_riding_and_lockpicking_never_award_or_bank(self):
        self.lua.globals().Player.Level = 79
        for skill_id in (43, 95, 633, 762):
            self.skill(skill_id, 100, 105)
        self.assertEqual([], self.awards())
        for skill_id in (43, 95, 633, 762):
            self.assertIsNone(self.lua.globals().GetProgress(1, 7, skill_id, 4))

    def test_repeatable_sources_resolve_scale_once_and_dedupe_tokens(self):
        self.lua.globals().ModifierFactor = 2.5
        self.action(1, 164, 5001, 200, "craft-1")  # output count ignored by resolver
        self.action(2, 186, 5002, 3, "gather-1")
        self.action(6, 755, 5003, 5, "process-1")
        self.action(1, 164, 5001, 200, "craft-1")
        self.action(2, 186, 5002, 3, "craft-1")  # token is global across kinds
        self.assertEqual([400, 375, 500], self.awards())
        self.assertEqual(3, self.lua.globals().ModifierCalls)
        self.assertEqual(
            [5, 6, 7],
            [self.lua.globals().ModifierSource(i) for i in range(1, 4)],
        )

    def test_repeatable_actions_fail_closed(self):
        self.action(1, 164, 9999, 1, "unknown")
        self.action(1, 165, 5001, 1, "wrong-skill")
        self.action(1, 164, 5001, 1, None)
        self.action(1, 164, 5001, 1, "")
        self.action(1, 164, 5001, 1, 0)
        self.action(1, 164, 5001, 1, "0")
        self.action(1, 164, 5001, 1, " 0 ")
        self.action(1, 164, 5001, 1, -1)
        self.action(1, 164, 5001, 1, True)
        self.action(1.5, 164, 5001, 1, "fractional-kind")
        self.action(1, 164, 5001.5, 1, "fractional-context")
        self.action(1, 164, 5001, 1.5, "fractional-quantity")
        self.action(1, 164, 5001, 0, "zero-quantity")
        self.lua.globals().Player.Level = 79
        self.action(1, 164, 5001, 1, "sub80")
        self.lua.globals().Player.Level = 80
        self.lua.globals().Player.Bot = True
        self.action(1, 164, 5001, 1, "bot")
        self.assertEqual([], self.awards())
        self.assertEqual(0, self.lua.globals().ModifierCalls)


@unittest.skipUnless(LuaRuntime, "lupa.lua52 is required for Lua behavior tests")
class MediatorModifierCompositionTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        with open(CLASSIC, encoding="utf-8") as handle:
            self.lua.execute(handle.read())
        with open(MEDIATOR, encoding="utf-8") as handle:
            self.lua.execute(handle.read())

    def test_modifier_reducer_composes_in_registration_order_with_nil_noop(self):
        self.lua.execute(
            r"""
            ModifierOrder = {}
            ModifierInputs = {}
            RegisterMediatorEvent("ReducerRegression", function(_, _, _, xp)
                table.insert(ModifierOrder, 1)
                table.insert(ModifierInputs, xp)
                return xp * 2
            end)
            RegisterMediatorEvent("ReducerRegression", function(_, _, _, xp)
                table.insert(ModifierOrder, 2)
                table.insert(ModifierInputs, xp)
                return nil
            end)
            RegisterMediatorEvent("ReducerRegression", function(_, _, _, xp)
                table.insert(ModifierOrder, 3)
                table.insert(ModifierInputs, xp)
                return { xp + 5 }
            end)
            ReducedXP = Mediator.On("ReducerRegression", {
                arguments = { {}, {}, 5, 10 },
                defaults = { 10 },
                reduce = 4,
            })
            """
        )

        self.assertEqual(25, self.lua.globals().ReducedXP)
        self.assertEqual(
            [1, 2, 3],
            [self.lua.globals().ModifierOrder[i] for i in range(1, 4)],
        )
        self.assertEqual(
            [10, 20, 20],
            [self.lua.globals().ModifierInputs[i] for i in range(1, 4)],
        )

    def test_reducer_without_subscribers_returns_the_input_value(self):
        result = self.lua.globals().Mediator.On(
            "NoReducerSubscribers",
            self.lua.table_from(
                {
                    "arguments": self.lua.table_from([{}, {}, 5, 17]),
                    "defaults": self.lua.table_from([17]),
                    "reduce": 4,
                }
            ),
        )
        self.assertEqual(17, result)

    def test_ordinary_events_keep_first_non_nil_merge_semantics(self):
        self.lua.execute(
            r"""
            RegisterMediatorEvent("OrdinaryRegression", function() return 7 end)
            RegisterMediatorEvent("OrdinaryRegression", function() return 11 end)
            OrdinaryResult = Mediator.On("OrdinaryRegression", {
                defaults = { 3 },
            })
            """
        )
        self.assertEqual(7, self.lua.globals().OrdinaryResult)


class ParagonProfessionXPConfigContractTests(unittest.TestCase):
    def read(self, path):
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def test_shipped_configs_set_direct_2000_skill_reward(self):
        value_pattern = re.compile(
            r"\('UNIVERSAL_SKILL_EXPERIENCE',\s*'(?P<value>\d+)'\)"
        )
        for path in (DEFAULT_CONFIG, ANNIVERSARY_CONFIG):
            with self.subTest(path=os.path.relpath(path, ROOT)):
                text = self.read(path)
                value = value_pattern.search(text)
                self.assertIsNotNone(value)
                self.assertEqual("2000", value.group("value"))

        self.assertNotIn("PARAGON_ONE_TIME_XP_MULTIPLIER", self.read(DEFAULT_CONFIG))
        upgrade = self.read(ANNIVERSARY_CONFIG)
        self.assertNotRegex(
            upgrade,
            r"\('PARAGON_ONE_TIME_XP_MULTIPLIER',\s*'[^']+'\)",
        )
        self.assertRegex(
            upgrade,
            r"(?s)DELETE FROM .*paragon_config.*PARAGON_ONE_TIME_XP_MULTIPLIER",
        )

    def test_skill_override_schema_defaults_to_2000(self):
        table_pattern = re.compile(
            r"paragon_config_experience_skill.*?"
            r"`experience`\s+INT(?:\(11\))?\s+NOT NULL\s+DEFAULT\s+['\"]?2000",
            re.DOTALL | re.IGNORECASE,
        )
        for path in (SCHEMA,):
            with self.subTest(path=os.path.relpath(path, ROOT)):
                self.assertRegex(self.read(path), table_pattern)

    def test_upgrade_creates_and_seeds_both_progress_scopes(self):
        sql = self.read(ANNIVERSARY_CONFIG)
        self.assertRegex(sql, r"CREATE TABLE IF NOT EXISTS.*paragon_profession_progress")
        self.assertRegex(sql, r"SELECT\s+1,\s*c\.`account`.*MAX\(cs\.`value`\)")
        self.assertRegex(sql, r"SELECT\s+0,\s*cs\.`guid`.*cs\.`value`")
        self.assertIn("ON DUPLICATE KEY UPDATE", sql)

    def test_unpaid_claim_uplift_and_config_rewrite_are_one_transaction(self):
        sql = self.read(ANNIVERSARY_CONFIG)
        transaction = re.search(
            r"START TRANSACTION;(?P<body>.*?)COMMIT;",
            sql,
            re.DOTALL | re.IGNORECASE,
        )
        self.assertIsNotNone(transaction)
        body = transaction.group("body")
        self.assertRegex(
            body,
            r"(?s)UNIVERSAL_SKILL_EXPERIENCE.*?'1000'.*?pending_xp.*?\*\s*2",
        )
        self.assertRegex(
            body,
            r"(?s)PARAGON_ACHIEVEMENT_POINT_XP.*?'1000'.*?amount.*?\*\s*2",
        )
        self.assertRegex(
            body,
            r"UNIVERSAL_SKILL_EXPERIENCE',\s*'2000'",
        )
        self.assertRegex(
            body,
            r"PARAGON_ACHIEVEMENT_POINT_XP',\s*'2000'",
        )

    def test_atomic_payout_tables_are_forced_to_innodb(self):
        create_pattern = lambda table: re.compile(
            rf"CREATE TABLE IF NOT EXISTS.*?{table}.*?ENGINE=InnoDB",
            re.DOTALL | re.IGNORECASE,
        )
        for path in (SCHEMA,):
            text = self.read(path)
            with self.subTest(path=os.path.relpath(path, ROOT), table="character"):
                self.assertRegex(text, create_pattern("character_paragon"))
            with self.subTest(path=os.path.relpath(path, ROOT), table="account"):
                self.assertRegex(text, create_pattern("account_paragon"))
            with self.subTest(path=os.path.relpath(path, ROOT), table="profession"):
                self.assertRegex(text, create_pattern("paragon_profession_progress"))

        upgrade = self.read(ANNIVERSARY_CONFIG)
        for table in (
            "character_paragon",
            "account_paragon",
            "paragon_profession_progress",
        ):
            with self.subTest(path=os.path.relpath(ANNIVERSARY_CONFIG, ROOT), table=table):
                self.assertRegex(
                    upgrade,
                    rf"ALTER TABLE\s+`acore_ale`\.`{table}`\s+ENGINE=InnoDB",
                )

        module = self.read(MODULE)
        self.assertRegex(module, r"UPDATE %s progression\s+JOIN %s profession")
        self.assertIn("profession.pending_xp = 0", module)

    def test_sql_is_the_only_schema_and_default_source(self):
        constant = self.read(CONSTANT)
        for obsolete in (
            "CR_DB",
            "CR_TABLE_",
            "CT_TRIGGER_",
            "INS_DEFAULT_CONFIG",
            "CREATE TABLE",
            "CREATE TRIGGER",
        ):
            self.assertNotIn(obsolete, constant)
        self.assertIn("sql/install.sql", constant)

    def test_runtime_contract_uses_event76_and_generated_resolver(self):
        module = self.read(MODULE)
        self.assertIn(
            'require("paragon.modules.paragon_profession_data")', module
        )
        self.assertIn("RegisterPlayerEvent(76, OnProfessionAction)", module)
        self.assertRegex(
            module,
            r"ProfessionData\.Resolve\(action_kind, skill_id, context_id, quantity\)",
        )

    def test_character_delete_removes_only_character_scoped_profession_progress(self):
        constant = self.read(CONSTANT)
        cleanup = re.search(
            r'DEL_PROFESSION_PROGRESS_CHARACTER\s*=\s*"(?P<sql>[^"]+)"',
            constant,
        )
        self.assertIsNotNone(cleanup)
        sql = cleanup.group("sql")
        self.assertRegex(sql, r"DELETE FROM .*paragon_profession_progress")
        self.assertRegex(sql, r"owner_type\s*=\s*0")
        self.assertRegex(sql, r"owner_id\s*=\s*%d")
        self.assertNotRegex(sql, r"owner_type\s*=\s*1")

        repository = self.read(REPOSITORY)
        self.assertIn(
            "Constants.QUERY.DEL_PROFESSION_PROGRESS_CHARACTER",
            repository,
        )


if __name__ == "__main__":
    unittest.main()
