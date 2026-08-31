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
SKILL_MASTERY_MIGRATION = os.path.join(
    ROOT, "sql", "10_expand_skill_mastery_rewards.sql"
)
INSTALLER = os.path.join(ROOT, "tools", "install.py")

PROFESSION_SKILLS = {
    129, 164, 165, 171, 182, 185, 186,
    197, 202, 333, 356, 393, 755, 773,
}

WEAPON_SKILLS = {
    43, 44, 45, 46, 54, 55, 136, 160,
    162, 172, 173, 176, 226, 228, 229, 473,
}

SKILLUP_SKILLS = PROFESSION_SKILLS | (WEAPON_SKILLS - {473}) | {633}


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
                UNIVERSAL_SKILL_EXPERIENCE = "5000",
                UNIVERSAL_CREATURE_EXPERIENCE = "50",
                UNIVERSAL_ACHIEVEVEMENT_EXPERIENCE = "100",
                UNIVERSAL_QUEST_EXPERIENCE = "1",
                PARAGON_LEVEL_CAP = "10000",
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
            Operations = {}
            function ParagonRework_CurveCost(_) return 30000 end
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
                    table.insert(Operations, "AWARD")
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
                SetLevel = function(self, value) self.Level = value end,
                SetExperience = function(self, value) self.Experience = value end,
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
            Progression = {}
            ExecutedSQL = {}
            ApplyDBWrites = true
            ApplyCommitWrites = true
            FailProgressVerification = false
            ProgressWriteApplied = false
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
                local function ALEUInt64(value)
                    return setmetatable({ value = value }, {
                        __tostring = function(self) return tostring(self.value) end,
                    })
                end
                return {
                    GetUInt32 = function(_, column) return rows[index][column + 1] end,
                    GetUInt64 = function(_, column)
                        return ALEUInt64(rows[index][column + 1])
                    end,
                    GetString = function(_, column)
                        return tostring(rows[index][column + 1])
                    end,
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
                        local previous, point_xp = sql:match(
                            "GREATEST%(high_water,%s*(%d+)%)%)%s*%*%s*(%d+)")
                        previous, point_xp = tonumber(previous), tonumber(point_xp)
                        local delta = pending
                        if previous and point_xp then
                            delta = math.max(
                                0, high_water - math.max(row[4], previous)
                            ) * point_xp
                        end
                        row[4] = math.max(row[4], high_water)
                        row[5] = row[5] + delta
                    else
                        DBState[key] = { owner_type, owner_id, skill, high_water, pending }
                    end
                    ProgressWriteApplied = true
                    return
                end
                local progression_kind
                if sql:find("INSERT INTO acore_ale.account_paragon", 1, true) then
                    progression_kind = "account"
                elseif sql:find("INSERT INTO acore_ale.character_paragon", 1, true) then
                    progression_kind = "character"
                end
                if progression_kind then
                    local owner, level, experience = sql:match(
                        "VALUES%s*%((%d+),%s*(%d+),%s*(%d+)%)")
                    if owner then
                        Progression[progression_kind .. ":" .. owner] = {
                            tonumber(level), tonumber(experience)
                        }
                    end
                    return
                end
                local update_owner_type = sql:match(
                    "profession%.owner_type%s*=%s*(%d+)")
                local update_owner_id = sql:match(
                    "profession%.owner_id%s*=%s*(%d+)")
                if update_owner_type and update_owner_id
                        and sql:find("profession.pending_xp = 0", 1, true) then
                    if not ApplyCommitWrites then return end
                    update_owner_type, update_owner_id = tonumber(update_owner_type), tonumber(update_owner_id)
                    local owner = sql:match("progression%.account_id%s*=%s*(%d+)")
                    local kind = "account"
                    if not owner then
                        owner = sql:match("progression%.guid%s*=%s*(%d+)")
                        kind = "character"
                    end
                    local newLevel, newExperience = sql:match(
                        "SET%s+progression%.level%s*=%s*(%d+),%s*progression%.experience%s*=%s*(%d+)")
                    local oldLevel, oldExperience = sql:match(
                        "AND%s+progression%.level%s*=%s*(%d+)%s+AND%s+progression%.experience%s*=%s*(%d+)")
                    local progression = owner and Progression[kind .. ":" .. owner]
                    if not progression or progression[1] ~= tonumber(oldLevel)
                            or progression[2] ~= tonumber(oldExperience) then
                        return
                    end
                    progression[1], progression[2] =
                        tonumber(newLevel), tonumber(newExperience)
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
                    table.insert(Operations, sql)
                    if ApplyDBWrites then ApplyDBSQL(sql) end
                    return nil
                end
                if sql:find("COALESCE(SUM(pending_xp)", 1, true) then
                    local owner_type, owner_id = sql:match(
                        "owner_type%s*=%s*(%d+)%s+AND owner_id%s*=%s*(%d+)")
                    owner_type, owner_id = tonumber(owner_type), tonumber(owner_id)
                    local pending = 0
                    for _, row in pairs(DBState) do
                        if row[1] == owner_type and row[2] == owner_id then
                            pending = pending + row[5]
                        end
                    end
                    return MakeResult({ { pending } })
                end
                if sql:find("SELECT COUNT(*)", 1, true)
                        and sql:find("_paragon", 1, true) then
                    local kind = sql:find("account_paragon", 1, true)
                        and "account" or "character"
                    local idColumn = kind == "account" and "account_id" or "guid"
                    local owner = sql:match(idColumn .. "%s*=%s*(%d+)")
                    local level = tonumber(sql:match("level%s*=%s*(%d+)"))
                    local experience = tonumber(sql:match("experience%s*=%s*(%d+)"))
                    local row = owner and Progression[kind .. ":" .. owner]
                    return MakeResult({ {
                        row and row[1] == level and row[2] == experience and 1 or 0
                    } })
                end
                local owner_type, owner_id = sql:match(
                    "owner_type%s*=%s*(%d+)%s+AND owner_id%s*=%s*(%d+)")
                if not owner_type then return nil end
                owner_type, owner_id = tonumber(owner_type), tonumber(owner_id)
                local skill = sql:match("skill_id%s*=%s*(%d+)")
                local rows = {}
                if skill then
                    if FailProgressVerification and ProgressWriteApplied then
                        FailProgressVerification = false
                        ProgressWriteApplied = false
                        return nil
                    end
                    ProgressWriteApplied = false
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
                Operations = {}
                ModifierCalls = 0
                ModifierSources = {}
                ModifierFactor = 1
                DBState = {}
                Progression = {}
                ExecutedSQL = {}
                ApplyDBWrites = true
                ApplyCommitWrites = true
                FailProgressVerification = false
                ProgressWriteApplied = false
                Player.Data = { Paragon = Paragon }
                Player.Skills = {}
                Player.Level = 80
                Player.Account = 7
                Player.Guid = 70
                Player.Bot = false
                ConfigValues.ENABLE_PARAGON_SYSTEM = "1"
                ConfigValues.LEVEL_LINKED_TO_ACCOUNT = "1"
                ConfigValues.UNIVERSAL_SKILL_EXPERIENCE = "5000"
                SkillOverrides = {}
                Paragon.Level = 1
                Paragon.Experience = 0
            end
            function AwardCount() return #AwardedXP end
            function AwardAmount(index) return AwardedXP[index] end
            function ModifierSource(index) return ModifierSources[index] end
            function ExecutedCount() return #ExecutedSQL end
            function ExecutedAt(index) return ExecutedSQL[index] end
            function OperationCount() return #Operations end
            function OperationAt(index) return Operations[index] end
            function ProgressionLevel(kind, owner)
                local row = Progression[kind .. ":" .. tostring(owner)]
                return row and row[1] or nil
            end
            function ProgressionExperience(kind, owner)
                local row = Progression[kind .. ":" .. tostring(owner)]
                return row and row[2] or nil
            end
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

    def test_exact_weapon_and_skillup_allowlists(self):
        actual_weapons = {
            skill_id
            for skill_id in range(1, 1001)
            if self.module.WEAPON_SKILLS[skill_id]
        }
        actual_skillups = {
            skill_id
            for skill_id in range(1, 1001)
            if self.module.SKILLUP_SKILLS[skill_id]
        }
        self.assertEqual(WEAPON_SKILLS, actual_weapons)
        self.assertEqual(SKILLUP_SKILLS, actual_skillups)
        self.assertEqual(162, self.module.CanonicalSkill(473))

    def test_skillup_is_exactly_5000_and_bypasses_personal_modifiers(self):
        self.lua.globals().ModifierFactor = 9
        self.lua.globals().SkillOverrides[164] = 25
        self.skill(164, 100, 101)
        self.assertEqual([5000], self.awards())
        self.assertEqual(0, self.lua.globals().ModifierCalls)
        operations = [
            self.lua.globals().ExecutedAt(index)
            for index in range(1, self.lua.globals().ExecutedCount() + 1)
        ]
        self.assertIn("INSERT INTO", operations[0])
        self.assertIn("INSERT INTO acore_ale.account_paragon", operations[1])
        self.assertIn("UPDATE acore_ale.account_paragon progression", operations[2])
        self.assertIn("profession.pending_xp = 0", operations[2])
        operation_trace = [
            self.lua.globals().OperationAt(index)
            for index in range(1, self.lua.globals().OperationCount() + 1)
        ]
        self.assertLess(
            next(i for i, value in enumerate(operation_trace)
                 if "profession.pending_xp = 0" in value),
            operation_trace.index("AWARD"),
        )

        # The common hook enforces the flat skill-up contract even if another
        # module mistakenly asks for the modified award path.
        self.assertTrue(
            self.hook.AwardExperience(
                self.lua.globals().Player, 3, 164, 5000, True
            )
        )
        self.assertEqual([5000, 5000], self.awards())
        self.assertEqual(0, self.lua.globals().ModifierCalls)

    def test_missing_config_uses_rebalanced_runtime_fallback(self):
        self.lua.globals().ConfigValues.UNIVERSAL_SKILL_EXPERIENCE = None
        self.skill(164, 100, 101)
        self.assertEqual([5000], self.awards())

    def test_all_flat_sources_bypass_the_common_modifier_boundary(self):
        self.lua.globals().ModifierFactor = 9
        for source_type, entry, experience in (
            (2, 9001, 125),
            (3, 164, 5000),
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

        self.assertEqual([125, 5000, 75, 500], self.awards())
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
        # Deliberately arbitrary pre-existing pending XP: this test verifies
        # preservation while disabled, not the configured per-point amount.
        self.lua.globals().SetProgress(1, 7, 164, 101, 1234)
        self.lua.globals().ConfigValues.ENABLE_PARAGON_SYSTEM = "0"

        self.assertFalse(self.module.PayPending(self.lua.globals().Player))
        self.assertEqual([], self.awards())
        self.assertEqual(1234, self.lua.globals().GetProgress(1, 7, 164, 5))

        self.lua.globals().ConfigValues.ENABLE_PARAGON_SYSTEM = 1
        self.assertTrue(self.module.PayPending(self.lua.globals().Player))
        self.assertEqual([1234], self.awards())
        self.assertEqual(0, self.lua.globals().GetProgress(1, 7, 164, 5))

    def test_multi_point_gain_scales_and_non_increases_do_not(self):
        self.skill(164, 100, 105)
        self.skill(164, 105, 105)
        self.skill(164, 105, 100)
        self.assertEqual([25000], self.awards())

    def test_account_high_water_blocks_replay_relearn_and_alt_farming(self):
        self.skill(164, 100, 105)
        self.skill(164, 100, 105)
        self.skill(164, 0, 103)
        self.skill(164, 105, 108)
        self.assertEqual([25000, 15000], self.awards())

        self.lua.globals().Player.Guid = 71
        self.lua.globals().Player.Data = self.lua.table_from({"Paragon": self.lua.globals().Paragon})
        self.skill(164, 0, 109)
        self.assertEqual([25000, 15000, 5000], self.awards())

    def test_failed_claim_write_never_creates_phantom_live_xp_across_alts(self):
        self.lua.globals().ApplyDBWrites = False
        self.skill(164, 100, 101)
        self.assertEqual([], self.awards())
        self.assertIsNone(self.lua.globals().GetProgress(1, 7, 164, 4))

        self.lua.globals().Player.Guid = 71
        self.lua.globals().Player.Data = self.lua.table_from(
            {"Paragon": self.lua.globals().Paragon}
        )
        self.lua.globals().ApplyDBWrites = True
        self.skill(164, 100, 101)
        self.assertEqual([5000], self.awards())
        self.assertEqual(101, self.lua.globals().GetProgress(1, 7, 164, 4))
        self.assertEqual(0, self.lua.globals().GetProgress(1, 7, 164, 5))

        self.skill(164, 100, 101)
        self.assertEqual([5000], self.awards())

    def test_ambiguous_claim_write_retries_without_double_pending_xp(self):
        self.lua.globals().FailProgressVerification = True
        self.skill(164, 100, 101)
        self.assertEqual([], self.awards())
        self.assertEqual(101, self.lua.globals().GetProgress(1, 7, 164, 4))
        self.assertEqual(5000, self.lua.globals().GetProgress(1, 7, 164, 5))

        self.skill(164, 100, 101)
        self.assertEqual([5000], self.awards())
        self.assertEqual(0, self.lua.globals().GetProgress(1, 7, 164, 5))

    def test_failed_settlement_commit_preserves_pending_and_retries_once(self):
        self.lua.globals().SetProgress(1, 7, 164, 101, 5000)
        self.lua.globals().ApplyCommitWrites = False

        self.assertFalse(self.module.PayPending(self.lua.globals().Player))
        self.assertEqual([], self.awards())
        self.assertEqual(5000, self.lua.globals().GetProgress(1, 7, 164, 5))

        self.lua.globals().ApplyCommitWrites = True
        self.assertTrue(self.module.PayPending(self.lua.globals().Player))
        self.assertEqual([5000], self.awards())
        self.assertEqual(0, self.lua.globals().GetProgress(1, 7, 164, 5))
        self.assertFalse(self.module.PayPending(self.lua.globals().Player))
        self.assertEqual([5000], self.awards())

    def test_fractional_live_experience_is_floored_before_settlement(self):
        self.lua.globals().SetProgress(1, 7, 164, 101, 5000)
        self.lua.globals().Paragon.Experience = 100.8

        self.assertTrue(self.module.PayPending(self.lua.globals().Player))
        self.assertEqual(5100, self.lua.globals().Paragon.Experience)
        self.assertEqual(
            5100, self.lua.globals().ProgressionExperience("account", 7)
        )
        self.assertEqual([5000], self.awards())

    def test_failed_fractional_settlement_leaves_live_remainder_untouched(self):
        self.lua.globals().SetProgress(1, 7, 164, 101, 5000)
        self.lua.globals().Paragon.Experience = 100.8
        self.lua.globals().ApplyCommitWrites = False

        self.assertFalse(self.module.PayPending(self.lua.globals().Player))
        self.assertEqual(100.8, self.lua.globals().Paragon.Experience)
        self.assertEqual([], self.awards())
        self.assertEqual(5000, self.lua.globals().GetProgress(1, 7, 164, 5))

    def test_nonfinite_live_experience_fails_closed_with_pending_intact(self):
        self.lua.globals().SetProgress(1, 7, 164, 101, 5000)
        self.lua.globals().Paragon.Experience = float("inf")

        self.assertFalse(self.module.PayPending(self.lua.globals().Player))
        self.assertEqual(float("inf"), self.lua.globals().Paragon.Experience)
        self.assertEqual([], self.awards())
        self.assertEqual(5000, self.lua.globals().GetProgress(1, 7, 164, 5))

    def test_character_linked_mode_scopes_high_water_by_guid(self):
        self.lua.globals().ConfigValues.LEVEL_LINKED_TO_ACCOUNT = "0"
        self.skill(164, 100, 101)
        self.lua.globals().Player.Guid = 71
        self.lua.globals().Player.Data = self.lua.table_from({"Paragon": self.lua.globals().Paragon})
        self.skill(164, 100, 101)
        self.assertEqual([5000, 5000], self.awards())

    def test_numeric_account_mode_and_invalid_owner_ids_fail_closed(self):
        self.lua.globals().ConfigValues.LEVEL_LINKED_TO_ACCOUNT = 1
        self.skill(164, 100, 101)
        self.assertEqual([5000], self.awards())
        self.assertEqual(101, self.lua.globals().GetProgress(1, 7, 164, 4))

        self.lua.globals().Player.Data = self.lua.table_from(
            {"Paragon": self.lua.globals().Paragon}
        )
        self.lua.globals().Player.Account = 0
        self.skill(164, 101, 102)
        self.assertEqual([5000], self.awards())

    def test_pre80_future_points_bank_and_pay_once_when_eligible(self):
        self.lua.globals().Player.Level = 79
        self.skill(164, 100, 103)
        self.assertEqual([], self.awards())
        self.assertEqual(15000, self.lua.globals().GetProgress(1, 7, 164, 5))

        self.lua.globals().Player.Level = 80
        self.assertTrue(self.module.PayPending(self.lua.globals().Player))
        self.assertEqual([15000], self.awards())
        self.assertEqual(0, self.lua.globals().GetProgress(1, 7, 164, 5))
        self.assertFalse(self.module.PayPending(self.lua.globals().Player))
        self.assertEqual([15000], self.awards())

    def test_weapon_and_lockpicking_skillups_award_flat_xp_once(self):
        for skill_id in sorted(WEAPON_SKILLS | {633}):
            self.skill(skill_id, 100, 101)

        # Fist Weapons and Unarmed are two callbacks for one conceptual skill,
        # so the 17 raw IDs resolve to 16 one-time mastery tracks.
        self.assertEqual([5000] * 16, self.awards())
        self.assertEqual(101, self.lua.globals().GetProgress(1, 7, 162, 4))
        self.assertIsNone(self.lua.globals().GetProgress(1, 7, 473, 4))

        self.skill(473, 101, 102)
        self.assertEqual([5000] * 17, self.awards())
        self.assertEqual(102, self.lua.globals().GetProgress(1, 7, 162, 4))

    def test_weapon_and_lockpicking_pre80_points_bank_normally(self):
        self.lua.globals().Player.Level = 79
        for skill_id in (43, 633):
            self.skill(skill_id, 100, 105)
        self.assertEqual([], self.awards())
        self.assertEqual(25000, self.lua.globals().GetProgress(1, 7, 43, 5))
        self.assertEqual(25000, self.lua.globals().GetProgress(1, 7, 633, 5))

    def test_defense_dual_wield_feral_shield_riding_and_runeforging_stay_excluded(self):
        for skill_id in (95, 118, 134, 433, 762, 776):
            self.skill(skill_id, 100, 105)
        self.assertEqual([], self.awards())
        for skill_id in (95, 118, 134, 433, 762, 776):
            self.assertIsNone(self.lua.globals().GetProgress(1, 7, skill_id, 4))

    def test_fist_and_unarmed_seed_to_one_canonical_high_water(self):
        self.lua.globals().Player.Skills[162] = 120
        self.lua.globals().Player.Skills[473] = 140
        self.module.SeedPlayer(self.lua.globals().Player)

        self.assertEqual(140, self.lua.globals().GetProgress(1, 7, 162, 4))
        self.assertEqual(0, self.lua.globals().GetProgress(1, 7, 162, 5))
        self.assertIsNone(self.lua.globals().GetProgress(1, 7, 473, 4))

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

    def test_shipped_configs_set_direct_rebalanced_one_time_rewards(self):
        value_pattern = re.compile(
            r"\('UNIVERSAL_SKILL_EXPERIENCE',\s*'(?P<value>\d+)'\)"
        )
        for path in (DEFAULT_CONFIG, ANNIVERSARY_CONFIG):
            with self.subTest(path=os.path.relpath(path, ROOT)):
                text = self.read(path)
                value = value_pattern.search(text)
                self.assertIsNotNone(value)
                self.assertEqual("5000", value.group("value"))

        achievement_pattern = re.compile(
            r"\('PARAGON_ACHIEVEMENT_POINT_XP',\s*'(?P<value>\d+)'\)"
        )
        for path in (DEFAULT_CONFIG, ANNIVERSARY_CONFIG):
            with self.subTest(path=os.path.relpath(path, ROOT)):
                value = achievement_pattern.search(self.read(path))
                self.assertIsNotNone(value)
                self.assertEqual("10000", value.group("value"))

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

    def test_skill_override_schema_defaults_to_5000(self):
        table_pattern = re.compile(
            r"paragon_config_experience_skill.*?"
            r"`experience`\s+INT(?:\(11\))?\s+NOT NULL\s+DEFAULT\s+['\"]?5000",
            re.DOTALL | re.IGNORECASE,
        )
        for path in (SCHEMA, ANNIVERSARY_CONFIG):
            with self.subTest(path=os.path.relpath(path, ROOT)):
                self.assertRegex(self.read(path), table_pattern)

    def test_installer_verifies_rebalanced_canonical_values(self):
        installer = self.read(INSTALLER)
        self.assertRegex(
            installer,
            r"(?s)value = '5000'.*?UNIVERSAL_SKILL_EXPERIENCE",
        )
        self.assertRegex(
            installer,
            r"(?s)value = '10000'.*?PARAGON_ACHIEVEMENT_POINT_XP",
        )

    def test_upgrade_creates_and_seeds_both_progress_scopes(self):
        sql = self.read(ANNIVERSARY_CONFIG)
        self.assertRegex(sql, r"CREATE TABLE IF NOT EXISTS.*paragon_profession_progress")
        self.assertRegex(
            sql, r"(?s)SELECT\s+1,\s*c\.`account`.*?MAX\(cs\.`value`\)"
        )
        self.assertRegex(
            sql, r"(?s)SELECT\s+0,\s*cs\.`guid`.*?MAX\(cs\.`value`\)"
        )
        self.assertIn("ON DUPLICATE KEY UPDATE", sql)

    def test_upgrade_seeds_weapon_and_lockpicking_high_water_without_backpay(self):
        expected_raw_skills = PROFESSION_SKILLS | WEAPON_SKILLS | {633}
        for path in (ANNIVERSARY_CONFIG, SKILL_MASTERY_MIGRATION):
            sql = self.read(path)
            with self.subTest(path=os.path.relpath(path, ROOT)):
                matches = re.findall(
                    r"WHERE\s+cs\.`skill`\s+IN\s*\((?P<ids>[^)]+)\)",
                    sql,
                    flags=re.IGNORECASE,
                )
                self.assertEqual(2, len(matches))
                for values in matches:
                    actual = {int(value) for value in re.findall(r"\d+", values)}
                    self.assertEqual(expected_raw_skills, actual)
                self.assertGreaterEqual(
                    len(re.findall(
                        r"CASE\s+WHEN\s+cs\.`skill`\s*=\s*473\s+THEN\s+162",
                        sql,
                        flags=re.IGNORECASE,
                    )),
                    4,
                )
                self.assertGreaterEqual(len(re.findall(r"MAX\(cs\.`value`\)", sql)), 2)
                self.assertNotRegex(
                    sql,
                    r"(?is)pending_xp`?\s*=\s*pending_xp`?\s*\+",
                )

    def test_config_rewrite_does_not_reconcile_historical_claims(self):
        sql = self.read(ANNIVERSARY_CONFIG)
        transaction = re.search(
            r"START TRANSACTION;(?P<body>.*?)COMMIT;",
            sql,
            re.DOTALL | re.IGNORECASE,
        )
        self.assertIsNotNone(transaction)
        body = transaction.group("body")
        self.assertNotRegex(
            body,
            r"(?is)SET\s+(?:\w+\.)?`?pending_xp`?\s*=\s*"
            r"(?:\w+\.)?`?pending_xp`?\s*\*",
        )
        self.assertNotRegex(
            body,
            r"(?is)SET\s+(?:\w+\.)?`?amount`?\s*=\s*"
            r"(?:\w+\.)?`?amount`?\s*\*",
        )
        self.assertRegex(
            body,
            r"UNIVERSAL_SKILL_EXPERIENCE',\s*'5000'",
        )
        self.assertRegex(
            body,
            r"PARAGON_ACHIEVEMENT_POINT_XP',\s*'10000'",
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
