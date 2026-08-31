import collections
import importlib.util
import json
import os
import sys
import unittest

try:
    from lupa.lua52 import LuaRuntime
except ImportError:  # pragma: no cover
    LuaRuntime = None


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE = os.path.join(
    ROOT, "serverside", "paragon", "modules", "paragon_recipe_rewards.lua"
)
DATA = os.path.join(
    ROOT, "serverside", "paragon", "modules", "paragon_recipe_data.lua"
)
MIGRATION = os.path.join(ROOT, "sql", "06_add_recipe_rewards.sql")
GENERATOR = os.path.join(ROOT, "tools", "gen_recipe_rewards.py")
AUDIT = os.path.join(ROOT, "tools", "generated", "recipe_reward_audit.json")


@unittest.skipUnless(LuaRuntime, "lupa.lua52 is required for Lua behavior tests")
class ParagonRecipeRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute(
            r'''
            ConfigValues = {
                ENABLE_PARAGON_SYSTEM = "1",
                MINIMUM_LEVEL_FOR_PARAGON_XP = "80",
                LEVEL_LINKED_TO_ACCOUNT = "1",
                PARAGON_LEVEL_CAP = "10000",
            }
            Config = {}
            function Config:GetByField(key) return ConfigValues[key] end
            package.preload["paragon_config"] = function() return Config end
            package.preload["paragon_constant"] = function()
                return { DB_NAME = "acore_ale" }
            end

            RecipeRows = {
                [1001] = { xp = 5000, skillId = 164, name = "Trainer Recipe", source = "trainer" },
                [1002] = { xp = 125000, skillId = 164, name = "Rare Recipe", source = "recipe_item" },
            }
            RecipeData = { VERSION = 1, BUDGET = 130000, COUNT = 2 }
            function RecipeData.Get(spellId)
                local row = RecipeRows[tonumber(spellId)]
                if not row then return nil end
                return {
                    spellId = tonumber(spellId), xp = row.xp,
                    skillId = row.skillId, name = row.name, source = row.source,
                }
            end
            function RecipeData.Iterate() return pairs(RecipeRows) end
            package.preload["paragon.modules.paragon_recipe_data"] = function()
                return RecipeData
            end

            Awarded = {}
            Operations = {}
            function ParagonRework_CurveCost(_) return 30000 end
            Hook = { ExperienceSource = { COLLECTIBLE = 8 } }
            function Hook.AwardFlatExperience(player, source, entry, amount)
                Operations[#Operations + 1] = "AWARD"
                Awarded[#Awarded + 1] = { source, entry, amount }
                local paragon = player.Data.Paragon
                paragon.Experience = paragon.Experience + amount
                while paragon.Experience >= 30000 do
                    paragon.Experience = paragon.Experience - 30000
                    paragon.Level = paragon.Level + 1
                end
                return true, amount
            end
            package.preload["paragon_hook"] = function() return Hook end

            PlayerEvents = {}
            function RegisterPlayerEvent(eventId, callback)
                PlayerEvents[eventId] = callback
            end
            MediatorEvents = {}
            function RegisterMediatorEvent(name, callback)
                MediatorEvents[name] = callback
            end

            Paragon = {
                Level = 1, Experience = 0,
                GetLevel = function(self) return self.Level end,
                GetExperience = function(self) return self.Experience end,
                SetLevel = function(self, value) self.Level = value end,
                SetExperience = function(self, value) self.Experience = value end,
            }
            Player = {
                Account = 7, Guid = 70, Level = 80, Bot = false,
                Data = { Paragon = Paragon }, Known = {}, Messages = {},
            }
            function Player:GetAccountId() return self.Account end
            function Player:GetGUIDLow() return self.Guid end
            function Player:GetLevel() return self.Level end
            function Player:IsPlayerBot() return self.Bot end
            function Player:GetData(key) return self.Data[key] end
            function Player:SetData(key, value) self.Data[key] = value end
            function Player:HasSpell(spellId) return self.Known[spellId] == true end
            function Player:SendBroadcastMessage(message)
                self.Messages[#self.Messages + 1] = message
            end

            Claims = {}
            Seeds = {}
            Progression = {}
            SQL = {}
            ApplyClaimWrites = true
            ApplySeedWrites = true
            ApplySyncWrites = true
            ApplyCommitWrites = true
            local function claimKey(account, spell)
                return tostring(account) .. ":" .. tostring(spell)
            end
            local function makeResult(rows)
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
            local function apply(sql)
                if sql:find("paragon_recipe_reward_claim", 1, true)
                        and sql:find("INSERT IGNORE", 1, true) then
                    if not ApplyClaimWrites then return end
                    for account, spell, pending in sql:gmatch(
                            "%(%s*(%d+),%s*(%d+),%s*(%d+)%s*%)") do
                        local key = claimKey(tonumber(account), tonumber(spell))
                        if not Claims[key] then
                            Claims[key] = { tonumber(account), tonumber(spell), tonumber(pending) }
                        end
                    end
                elseif sql:find("paragon_recipe_reward_seed", 1, true)
                        and sql:find("INSERT INTO", 1, true) then
                    if not ApplySeedWrites then return end
                    local guid, account, version = sql:match(
                        "VALUES%s*%((%d+),%s*(%d+),%s*(%d+)%)")
                    if guid then
                        Seeds[tonumber(guid)] = {
                            tonumber(account), tonumber(version)
                        }
                    end
                elseif sql:find("INSERT INTO acore_ale.account_paragon", 1, true) then
                    if not ApplySyncWrites then return end
                    local owner, level, experience = sql:match(
                        "VALUES%s*%((%d+),%s*(%d+),%s*(%d+)%)")
                    if owner then
                        Progression["account:" .. owner] = {
                            tonumber(level), tonumber(experience)
                        }
                    end
                elseif sql:find("INSERT INTO acore_ale.character_paragon", 1, true) then
                    if not ApplySyncWrites then return end
                    local owner, level, experience = sql:match(
                        "VALUES%s*%((%d+),%s*(%d+),%s*(%d+)%)")
                    if owner then
                        Progression["character:" .. owner] = {
                            tonumber(level), tonumber(experience)
                        }
                    end
                elseif sql:find("recipe.pending_xp = 0", 1, true) then
                    if not ApplyCommitWrites then return end
                    local account = tonumber(sql:match("recipe.account_id%s*=%s*(%d+)"))
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
                    local row = owner and Progression[kind .. ":" .. owner]
                    if not row or row[1] ~= tonumber(oldLevel)
                            or row[2] ~= tonumber(oldExperience) then
                        return
                    end
                    row[1], row[2] = tonumber(newLevel), tonumber(newExperience)
                    for _, row in pairs(Claims) do
                        if row[1] == account then row[3] = 0 end
                    end
                end
            end
            function CharDBQuery(sql)
                if sql:match("^%s*INSERT") or sql:match("^%s*UPDATE") then
                    SQL[#SQL + 1] = sql
                    Operations[#Operations + 1] = sql
                    apply(sql)
                    return nil
                end
                if sql:find("SELECT spell_id, pending_xp", 1, true) then
                    local account = tonumber(sql:match("account_id%s*=%s*(%d+)"))
                    local rows = {}
                    for _, row in pairs(Claims) do
                        if row[1] == account then rows[#rows + 1] = { row[2], row[3] } end
                    end
                    return makeResult(rows)
                end
                if sql:find("SELECT catalog_version", 1, true) then
                    local guid = tonumber(sql:match("guid%s*=%s*(%d+)"))
                    return Seeds[guid] and makeResult({ { Seeds[guid][2] } }) or nil
                end
                if sql:find("SELECT pending_xp", 1, true)
                        and sql:find("spell_id =", 1, true) then
                    local account = tonumber(sql:match("account_id%s*=%s*(%d+)"))
                    local spell = tonumber(sql:match("spell_id%s*=%s*(%d+)"))
                    local row = Claims[claimKey(account, spell)]
                    return row and makeResult({ { row[3] } }) or nil
                end
                if sql:find("COALESCE(SUM(pending_xp)", 1, true) then
                    local account = tonumber(sql:match("account_id%s*=%s*(%d+)"))
                    local pending = 0
                    for _, row in pairs(Claims) do
                        if row[1] == account then pending = pending + row[3] end
                    end
                    return makeResult({ { pending } })
                end
                if sql:find("SELECT COUNT(*)", 1, true)
                        and sql:find("spell_id IN", 1, true) then
                    local account = tonumber(sql:match("account_id%s*=%s*(%d+)"))
                    local list = sql:match("spell_id IN%s*%(([^%)]+)%)")
                    local count = 0
                    for spell in list:gmatch("%d+") do
                        if Claims[claimKey(account, tonumber(spell))] then
                            count = count + 1
                        end
                    end
                    return makeResult({ { count } })
                end
                if sql:find("SELECT COUNT(*)", 1, true)
                        and sql:find("paragon_recipe_reward_seed", 1, true) then
                    local guid = tonumber(sql:match("guid%s*=%s*(%d+)"))
                    local account = tonumber(sql:match("account_id%s*=%s*(%d+)"))
                    local version = tonumber(sql:match("catalog_version%s*=%s*(%d+)"))
                    local seed = Seeds[guid]
                    return makeResult({ {
                        seed and seed[1] == account and seed[2] == version and 1 or 0
                    } })
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
                    return makeResult({ {
                        row and row[1] == level and row[2] == experience and 1 or 0
                    } })
                end
                return nil
            end

            function AwardCount() return #Awarded end
            function AwardAmount(index) return Awarded[index][3] end
            function ClaimPending(account, spell)
                local row = Claims[claimKey(account, spell)]
                return row and row[3] or nil
            end
            function SeedVersion(guid)
                local row = Seeds[guid]
                return row and row[2] or nil
            end
            function ProgressionLevel(kind, owner)
                local row = Progression[kind .. ":" .. tostring(owner)]
                return row and row[1] or nil
            end
            function ProgressionExperience(kind, owner)
                local row = Progression[kind .. ":" .. tostring(owner)]
                return row and row[2] or nil
            end
            function OperationCount() return #Operations end
            function OperationAt(index) return Operations[index] end
            '''
        )
        with open(MODULE, encoding="utf-8") as handle:
            self.module = self.lua.execute(handle.read())

    def ready(self):
        self.lua.globals().MediatorEvents["OnAfterPlayerStatReady"](
            self.lua.globals().Player, self.lua.globals().Paragon
        )

    def learn(self, spell_id):
        self.lua.globals().PlayerEvents[44](
            44, self.lua.globals().Player, spell_id
        )

    def test_login_seed_records_existing_recipe_without_backpay(self):
        self.lua.globals().Player.Known[1001] = True
        self.ready()

        self.assertEqual(0, self.lua.globals().AwardCount())
        self.assertEqual(0, self.lua.globals().ClaimPending(7, 1001))
        self.assertEqual(1, self.lua.globals().SeedVersion(70))

    def test_catalog_version_change_reseeds_new_catalog_rows_without_backpay(self):
        self.lua.globals().Seeds[70] = self.lua.table_from([7, 1])
        self.lua.globals().RecipeData.VERSION = 2
        self.lua.globals().Player.Known[1002] = True

        self.ready()

        self.assertEqual(0, self.lua.globals().AwardCount())
        self.assertEqual(0, self.lua.globals().ClaimPending(7, 1002))
        self.assertEqual(2, self.lua.globals().SeedVersion(70))

    def test_new_recipe_pays_once_by_final_spell_id(self):
        self.ready()
        self.learn(1002)
        self.learn(1002)

        self.assertEqual(1, self.lua.globals().AwardCount())
        self.assertEqual(125000, self.lua.globals().AwardAmount(1))
        self.assertEqual(0, self.lua.globals().ClaimPending(7, 1002))
        self.assertIn("+125,000 Paragon XP", self.lua.globals().Player.Messages[1])

    def test_unknown_spell_and_bot_fail_closed(self):
        self.ready()
        self.learn(9999)
        self.lua.globals().Player.Bot = True
        self.learn(1002)
        self.assertEqual(0, self.lua.globals().AwardCount())
        self.assertIsNone(self.lua.globals().ClaimPending(7, 9999))
        self.assertIsNone(self.lua.globals().ClaimPending(7, 1002))

    def test_pre_80_reward_is_banked_then_paid_flat(self):
        self.lua.globals().Player.Level = 70
        self.ready()
        self.learn(1002)

        self.assertEqual(0, self.lua.globals().AwardCount())
        self.assertEqual(125000, self.lua.globals().ClaimPending(7, 1002))

        self.lua.globals().Player.Level = 80
        self.lua.globals().PlayerEvents[13](
            13, self.lua.globals().Player, 79
        )
        self.assertEqual(1, self.lua.globals().AwardCount())
        self.assertEqual(125000, self.lua.globals().AwardAmount(1))
        self.assertEqual(0, self.lua.globals().ClaimPending(7, 1002))

    def test_learn_before_seed_is_not_retroactively_paid(self):
        self.learn(1001)
        self.lua.globals().Player.Known[1001] = True
        self.ready()
        self.assertEqual(0, self.lua.globals().AwardCount())
        self.assertEqual(0, self.lua.globals().ClaimPending(7, 1001))

    def test_disabled_system_consumes_claim_without_deferred_windfall(self):
        self.ready()
        self.lua.globals().ConfigValues.ENABLE_PARAGON_SYSTEM = "0"
        self.learn(1002)
        self.assertEqual(0, self.lua.globals().AwardCount())
        self.assertEqual(0, self.lua.globals().ClaimPending(7, 1002))

        self.lua.globals().ConfigValues.ENABLE_PARAGON_SYSTEM = "1"
        self.learn(1002)
        self.assertEqual(0, self.lua.globals().AwardCount())

    def test_claim_insert_failure_stays_retryable_and_never_awards_phantom_xp(self):
        self.ready()
        self.lua.globals().ApplyClaimWrites = False
        self.learn(1002)
        self.assertEqual(0, self.lua.globals().AwardCount())
        self.assertIsNone(self.lua.globals().ClaimPending(7, 1002))

        self.lua.globals().ApplyClaimWrites = True
        self.learn(1002)
        self.assertEqual(1, self.lua.globals().AwardCount())
        self.assertEqual(0, self.lua.globals().ClaimPending(7, 1002))

    def test_commit_failure_leaves_pending_and_does_not_touch_live_xp(self):
        self.lua.globals().Player.Level = 70
        self.ready()
        self.learn(1002)
        self.lua.globals().Player.Level = 80
        self.lua.globals().Paragon.Experience = 100.8
        self.lua.globals().ApplyCommitWrites = False

        self.assertFalse(self.module.PayPending(self.lua.globals().Player))
        self.assertEqual(0, self.lua.globals().AwardCount())
        self.assertEqual(125000, self.lua.globals().ClaimPending(7, 1002))
        self.assertEqual(100.8, self.lua.globals().Paragon.Experience)

        self.lua.globals().ApplyCommitWrites = True
        self.assertTrue(self.module.PayPending(self.lua.globals().Player))
        self.assertEqual(1, self.lua.globals().AwardCount())
        self.assertEqual(0, self.lua.globals().ClaimPending(7, 1002))
        self.assertEqual(5, self.lua.globals().Paragon.Level)
        self.assertEqual(5100, self.lua.globals().Paragon.Experience)

    def test_seed_batch_failure_never_enables_the_reward_session(self):
        self.lua.globals().Player.Known[1001] = True
        self.lua.globals().ApplyClaimWrites = False
        self.ready()
        self.assertIsNone(self.lua.globals().SeedVersion(70))
        self.assertIsNone(self.lua.globals().ClaimPending(7, 1001))

        self.learn(1002)
        self.assertIsNone(self.lua.globals().ClaimPending(7, 1002))
        self.assertEqual(0, self.lua.globals().AwardCount())

        self.lua.globals().ApplyClaimWrites = True
        self.ready()
        self.assertEqual(1, self.lua.globals().SeedVersion(70))
        self.assertEqual(0, self.lua.globals().ClaimPending(7, 1001))

    def test_fractional_live_remainder_is_floored_before_atomic_settlement(self):
        self.ready()
        self.lua.globals().Paragon.Experience = 100.8
        self.learn(1002)

        self.assertEqual(5, self.lua.globals().Paragon.Level)
        self.assertEqual(5100, self.lua.globals().Paragon.Experience)
        self.assertEqual(
            5, self.lua.globals().ProgressionLevel("account", 7)
        )
        self.assertEqual(
            5100, self.lua.globals().ProgressionExperience("account", 7)
        )

    def test_nonfinite_live_experience_fails_closed_with_pending_intact(self):
        self.ready()
        self.lua.globals().Player.Level = 70
        self.learn(1002)
        self.lua.globals().Player.Level = 80
        self.lua.globals().Paragon.Experience = float("inf")

        self.assertFalse(self.module.PayPending(self.lua.globals().Player))
        self.assertEqual(0, self.lua.globals().AwardCount())
        self.assertEqual(125000, self.lua.globals().ClaimPending(7, 1002))
        self.assertEqual(float("inf"), self.lua.globals().Paragon.Experience)

    def test_pending_is_committed_before_live_replay(self):
        self.ready()
        self.learn(1002)
        operations = [
            self.lua.globals().OperationAt(index)
            for index in range(1, self.lua.globals().OperationCount() + 1)
        ]
        award = operations.index("AWARD")
        self.assertIn("recipe.pending_xp = 0", operations[award - 1])
        self.assertEqual(0, self.lua.globals().ClaimPending(7, 1002))


class ParagonRecipeGeneratorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        spec = importlib.util.spec_from_file_location(
            "paragon_recipe_reward_generator", GENERATOR
        )
        cls.generator = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        sys.modules[spec.name] = cls.generator
        spec.loader.exec_module(cls.generator)
        with open(AUDIT, encoding="utf-8") as handle:
            cls.audit = json.load(handle)

    def test_exact_budget_catalog_and_source_partition(self):
        self.assertEqual(3_558, self.audit["totals"]["discovered"])
        self.assertEqual(3_481, self.audit["totals"]["rewardable"])
        self.assertEqual(77, self.audit["totals"]["quarantined"])
        self.assertEqual(140_000_000, self.audit["totals"]["xp"])
        self.assertEqual(
            {
                "trainer": 1507,
                "quest": 15,
                "recipe_item": 1713,
                "discovery": 246,
            },
            self.audit["countsByPrimarySource"],
        )
        self.assertGreater(self.audit["catalogVersion"], 0)
        self.assertRegex(self.audit["catalogSha256"], r"^[0-9a-f]{64}$")

    def test_catalog_version_is_deterministic_and_content_derived(self):
        path = self.generator.Path("trainer", "trainer", 1.0, {})
        first = self.generator.Reward(100, 164, "A", "trainer", 1.0, [path], 5000)
        second = self.generator.Reward(200, 165, "B", "quest", 2.0, [path], 9000)
        quarantine = [{"spell": 300, "skill": 171, "reason": "unavailable"}]

        fingerprint, version = self.generator.catalog_identity(
            [first, second], quarantine
        )
        reordered_fingerprint, reordered_version = self.generator.catalog_identity(
            [second, first], list(reversed(quarantine))
        )
        self.assertEqual(fingerprint, reordered_fingerprint)
        self.assertEqual(version, reordered_version)
        self.assertGreater(version, 0)
        self.assertLessEqual(version, 0x7FFFFFFF)

        changed = self.generator.Reward(
            200, 165, "renamed only", "quest", 2.0, [path], 10000
        )
        changed_fingerprint, changed_version = self.generator.catalog_identity(
            [first, changed], quarantine
        )
        self.assertNotEqual(fingerprint, changed_fingerprint)
        self.assertNotEqual(version, changed_version)

    def test_generated_lua_and_audit_share_catalog_identity(self):
        with open(DATA, encoding="utf-8") as handle:
            lua = handle.read()
        self.assertIn(
            f"M.VERSION = {self.audit['catalogVersion']}", lua
        )
        self.assertIn(
            f'M.CATALOG_SHA256 = "{self.audit["catalogSha256"]}"', lua
        )

    def test_values_are_rounded_bounded_and_have_one_floor_trainer_lane(self):
        rows = self.audit["rewards"]
        self.assertTrue(all(row["xp"] % 1000 == 0 for row in rows))
        self.assertEqual(5000, min(row["xp"] for row in rows))
        self.assertLessEqual(max(row["xp"] for row in rows), 1_000_000)
        self.assertEqual(
            {5000}, {row["xp"] for row in rows if row["source"] == "trainer"}
        )

    def test_final_spell_ids_are_unique_and_teaching_paths_collapse(self):
        ids = [row["spell"] for row in self.audit["rewards"]]
        self.assertEqual(len(ids), len(set(ids)))
        with open(DATA, encoding="utf-8") as handle:
            lua = handle.read()
        self.assertIn("M.COUNT = 3481", lua)
        self.assertIn("M.BUDGET = 140000000", lua)

        if LuaRuntime:
            runtime = LuaRuntime(unpack_returned_tuples=True)
            module = runtime.execute(lua)
            runtime.globals().GeneratedRecipeData = module
            count, total = runtime.execute(
                "local n,x=0,0; for id in GeneratedRecipeData.Iterate() do "
                "local row=GeneratedRecipeData.Get(id); n=n+1; x=x+row.xp end; "
                "return n,x"
            )
            self.assertEqual(3_481, count)
            self.assertEqual(140_000_000, total)

    def test_quarantine_is_explicit_and_contains_known_unavailable_rows(self):
        rows = {row["spell"]: row for row in self.audit["quarantine"]}
        for spell_id in (12062, 14891, 22704, 56017, 57231, 67790):
            with self.subTest(spell_id=spell_id):
                self.assertIn(spell_id, rows)
                self.assertTrue(rows[spell_id]["reason"])

    def test_schema_is_account_wide_versioned_and_pending(self):
        with open(MIGRATION, encoding="utf-8") as handle:
            sql = handle.read()
        self.assertIn("paragon_recipe_reward_claim", sql)
        self.assertIn("PRIMARY KEY (`account_id`, `spell_id`)", sql)
        self.assertIn("`pending_xp` BIGINT UNSIGNED", sql)
        self.assertIn("paragon_recipe_reward_seed", sql)
        self.assertIn("`catalog_version` INT UNSIGNED", sql)


if __name__ == "__main__":
    unittest.main()
