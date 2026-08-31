import os
import re
import unittest

try:
    from lupa.lua52 import LuaRuntime
except ImportError:  # pragma: no cover - reported as skipped by unittest
    LuaRuntime = None


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE = os.path.join(
    ROOT, "serverside", "paragon", "modules", "paragon_achievement_claims.lua"
)
HOOK = os.path.join(ROOT, "serverside", "paragon", "paragon_hook.lua")
BANKING = os.path.join(
    ROOT, "serverside", "paragon", "modules", "paragon_rework_banking.lua"
)
MIGRATION = os.path.join(ROOT, "sql", "07_add_achievement_reward_claims.sql")
BASE_SCHEMA = os.path.join(ROOT, "sql", "02_create_tables.sql")


@unittest.skipUnless(LuaRuntime, "lupa.lua52 is required for Lua behavior tests")
class ParagonAchievementClaimTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute(
            r"""
            ConfigValues = {
                ENABLE_PARAGON_SYSTEM = "1",
                MINIMUM_LEVEL_FOR_PARAGON_XP = "80",
                LEVEL_LINKED_TO_ACCOUNT = "1",
                PARAGON_LEVEL_CAP = "0",
            }
            Config = {}
            function Config:GetByField(field) return ConfigValues[field] end
            package.preload["paragon_config"] = function() return Config end
            package.preload["paragon_constant"] = function()
                return { DB_NAME = "acore_ale" }
            end

            local function Result(rows)
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

            function WorldDBQuery(sql)
                if sql:find("player_factionchange_achievement", 1, true) then
                    return Result({ { 33, 1358 }, { 58, 593 } })
                end
                if sql:find("(Flags & 1)", 1, true) then
                    return Result({ { 9900 } })
                end
                error("unexpected world query: " .. sql)
            end

            Claims = {}
            Progression = {}
            ExecutedSQL = {}
            Trace = {}
            CommitSucceeds = true

            local function AccountClaims(account)
                local claims = Claims[account]
                if not claims then claims = {} Claims[account] = claims end
                return claims
            end

            function SeedClaim(account, achievement, pending)
                AccountClaims(account)[achievement] = pending
            end
            function Pending(account, achievement)
                return AccountClaims(account)[achievement]
            end
            function TotalPending(account)
                local total = 0
                for _, pending in pairs(AccountClaims(account)) do
                    total = total + pending
                end
                return total
            end
            function SetProgression(account, level, experience)
                Progression[account] = { level = level, experience = experience }
            end
            function ProgressionLevel(account)
                return Progression[account] and Progression[account].level
            end
            function ProgressionExperience(account)
                return Progression[account] and Progression[account].experience
            end

            function CharDBQuery(sql)
                ExecutedSQL[#ExecutedSQL + 1] = sql

                if sql:find("SELECT achievement_id, pending_xp", 1, true) then
                    local account = tonumber(sql:match("account_id%s*=%s*(%d+)"))
                    local rows = {}
                    for achievement, pending in pairs(AccountClaims(account)) do
                        rows[#rows + 1] = { achievement, pending }
                    end
                    return Result(rows)
                end

                if sql:find("SELECT COALESCE(SUM(pending_xp), 0)", 1, true) then
                    local account = tonumber(sql:match("account_id%s*=%s*(%d+)"))
                    return Result({ { TotalPending(account) } })
                end

                if sql:find("SELECT pending_xp FROM", 1, true) then
                    local account = tonumber(sql:match("account_id%s*=%s*(%d+)"))
                    local achievement = tonumber(sql:match("achievement_id%s*=%s*(%d+)"))
                    local pending = AccountClaims(account)[achievement]
                    return pending ~= nil and Result({ { pending } }) or nil
                end

                if sql:find("INSERT IGNORE INTO acore_ale.paragon_rewarded_achievement", 1, true) then
                    local account, achievement, pending = sql:match(
                        "VALUES%s*%((%d+),%s*(%d+),%s*(%d+)%)")
                    account, achievement, pending = tonumber(account), tonumber(achievement), tonumber(pending)
                    local claims = AccountClaims(account)
                    if claims[achievement] == nil then claims[achievement] = pending end
                    Trace[#Trace + 1] = "claim"
                    return nil
                end

                if sql:find("INSERT INTO acore_ale.account_paragon", 1, true) then
                    local account, level, experience = sql:match(
                        "VALUES%s*%((%d+),%s*(%d+),%s*(%d+)%)")
                    account, level, experience = tonumber(account), tonumber(level), tonumber(experience)
                    Progression[account] = { level = level, experience = experience }
                    Trace[#Trace + 1] = "checkpoint"
                    return nil
                end

                if sql:find("SELECT COUNT(*) FROM acore_ale.account_paragon", 1, true) then
                    local account = tonumber(sql:match("account_id%s*=%s*(%d+)"))
                    local level = tonumber(sql:match("level%s*=%s*(%d+)"))
                    local experience = tonumber(sql:match("experience%s*=%s*(%d+)"))
                    local row = Progression[account]
                    local count = row and row.level == level
                        and row.experience == experience and 1 or 0
                    return Result({ { count } })
                end

                if sql:find("UPDATE acore_ale.account_paragon progression", 1, true) then
                    local account = tonumber(sql:match("achievement.account_id%s*=%s*(%d+)"))
                    local new_level = tonumber(sql:match("progression.level%s*=%s*(%d+)"))
                    local new_experience = tonumber(sql:match("progression.experience%s*=%s*(%d+)"))
                    local old_level = tonumber(sql:match("AND progression.level%s*=%s*(%d+)"))
                    local old_experience = tonumber(sql:match("AND progression.experience%s*=%s*(%d+)"))
                    local row = Progression[account]
                    if CommitSucceeds and row and row.level == old_level
                            and row.experience == old_experience then
                        row.level = new_level
                        row.experience = new_experience
                        for achievement in pairs(AccountClaims(account)) do
                            AccountClaims(account)[achievement] = 0
                        end
                        Trace[#Trace + 1] = "commit"
                    else
                        Trace[#Trace + 1] = "commit-failed"
                    end
                    return nil
                end

                error("unexpected character query: " .. sql)
            end

            AchievementValues = {
                [33] = 100000,
                [1358] = 100000,
                [58] = 100000,
                [593] = 100000,
                [1068] = 50000,
                [9001] = 100000,
                [9900] = 100000,
            }
            function ParagonRework_AchievementValue(id)
                return AchievementValues[id] or 0
            end

            CurveCosts = {
                [1] = 30000,
                [2] = 40000,
                [3] = 55000,
                [4] = 70000,
                [5] = 90000,
                [6] = 110000,
                [7] = 135000,
            }
            function ParagonRework_CurveCost(level)
                return CurveCosts[level] or (135000 + (level - 7) * 25000)
            end

            Hook = { ExperienceSource = { ACHIEVEMENT = 2 } }
            Awards = {}
            AwardSucceeds = true
            AwardDiverges = false
            AwardThrows = false
            function Hook.AwardFlatExperience(player, source, entry, amount)
                Awards[#Awards + 1] = { source = source, entry = entry, amount = amount }
                Trace[#Trace + 1] = "award"
                if AwardThrows then error("synthetic replay failure") end
                if not AwardSucceeds then return false end
                if AwardDiverges then return true, amount end
                local paragon = player:GetData("Paragon")
                local level = paragon:GetLevel()
                local experience = paragon:GetExperience() + amount
                while experience >= ParagonRework_CurveCost(level) do
                    experience = experience - ParagonRework_CurveCost(level)
                    level = level + 1
                end
                paragon:SetLevel(level)
                paragon:SetExperience(experience)
                return true, amount
            end
            package.preload["paragon_hook"] = function() return Hook end

            MediatorEvents = {}
            PlayerEvents = {}
            function RegisterMediatorEvent(name, callback)
                MediatorEvents[name] = callback
            end
            function RegisterPlayerEvent(event, callback)
                PlayerEvents[event] = callback
            end

            function MakePlayer(account, guid, character_level, paragon_level, paragon_experience, bot)
                local paragon = { level = paragon_level, experience = paragon_experience }
                function paragon:GetLevel() return self.level end
                function paragon:GetExperience() return self.experience end
                function paragon:SetLevel(value) self.level = value end
                function paragon:SetExperience(value) self.experience = value end

                local player = {
                    account = account,
                    guid = guid,
                    level = character_level,
                    bot = bot or false,
                    data = { Paragon = paragon },
                }
                function player:GetAccountId() return self.account end
                function player:GetGUIDLow() return self.guid end
                function player:GetLevel() return self.level end
                function player:IsPlayerBot() return self.bot end
                function player:GetData(key) return self.data[key] end
                function player:SetData(key, value) self.data[key] = value end
                return player
            end
            """
        )
        with open(MODULE, encoding="utf-8") as handle:
            self.module = self.lua.execute(handle.read())

    def player(
        self,
        account=7,
        guid=77,
        character_level=80,
        paragon_level=1,
        paragon_experience=0,
        bot=False,
    ):
        return self.lua.globals().MakePlayer(
            account,
            guid,
            character_level,
            paragon_level,
            paragon_experience,
            bot,
        )

    def claim(self, player, achievement_id):
        return self.lua.globals().ParagonAchievementClaim_Try(
            player, achievement_id
        )

    def pending(self, account, achievement_id):
        return self.lua.globals().Pending(account, achievement_id)

    def test_pre80_claim_is_account_pending_and_alt_can_settle_curved_award(self):
        child = self.player(character_level=19)
        self.assertTrue(self.claim(child, 1358))
        self.assertFalse(self.claim(child, 33))
        self.assertEqual(100000, self.pending(7, 33))
        self.assertEqual(0, len(self.lua.globals().Awards))

        alt = self.player(guid=88, character_level=80)
        self.assertTrue(self.module["PayPending"](alt))
        self.assertEqual(0, self.pending(7, 33))
        self.assertEqual(3, alt.GetData(alt, "Paragon").GetLevel(
            alt.GetData(alt, "Paragon")
        ))
        self.assertEqual(30000, alt.GetData(alt, "Paragon").GetExperience(
            alt.GetData(alt, "Paragon")
        ))
        self.assertEqual(1, len(self.lua.globals().Awards))

        trace = [
            self.lua.globals().Trace[index]
            for index in range(1, len(self.lua.globals().Trace) + 1)
        ]
        self.assertLess(trace.index("claim"), trace.index("commit"))
        self.assertLess(trace.index("commit"), trace.index("award"))

    def test_seeded_claim_blocks_faction_counterpart_without_backpay(self):
        self.lua.globals().SeedClaim(7, 33, 0)
        player = self.player()
        self.assertFalse(self.claim(player, 1358))
        self.assertFalse(self.module["PayPending"](player))
        self.assertEqual(0, len(self.lua.globals().Awards))

    def test_failed_commit_preserves_pending_and_retry_awards_once(self):
        child = self.player(character_level=19)
        self.assertTrue(self.claim(child, 9001))
        eligible = self.player(guid=88, paragon_experience=100.8)

        self.lua.globals().CommitSucceeds = False
        self.assertFalse(self.module["PayPending"](eligible))
        self.assertEqual(100000, self.pending(7, 9001))
        self.assertEqual(0, len(self.lua.globals().Awards))
        paragon = eligible.GetData(eligible, "Paragon")
        self.assertEqual(100.8, paragon.GetExperience(paragon))

        self.lua.globals().CommitSucceeds = True
        self.assertTrue(self.module["PayPending"](eligible))
        self.assertEqual(0, self.pending(7, 9001))
        self.assertEqual(1, len(self.lua.globals().Awards))
        self.assertEqual(3, paragon.GetLevel(paragon))
        self.assertEqual(30100, paragon.GetExperience(paragon))
        self.assertFalse(self.module["PayPending"](eligible))
        self.assertEqual(1, len(self.lua.globals().Awards))

    def test_replay_failure_forces_committed_state_for_later_logout(self):
        child = self.player(character_level=19)
        self.assertTrue(self.claim(child, 9001))
        eligible = self.player(guid=88)
        self.lua.globals().AwardSucceeds = False

        self.assertTrue(self.module["PayPending"](eligible))
        paragon = eligible.GetData(eligible, "Paragon")
        self.assertEqual(3, paragon.GetLevel(paragon))
        self.assertEqual(30000, paragon.GetExperience(paragon))
        self.assertEqual(3, self.lua.globals().ProgressionLevel(7))
        self.assertEqual(30000, self.lua.globals().ProgressionExperience(7))
        self.assertEqual(0, self.pending(7, 9001))

    def test_replay_error_also_forces_committed_state(self):
        child = self.player(character_level=19)
        self.assertTrue(self.claim(child, 9001))
        eligible = self.player(guid=88)
        self.lua.globals().AwardThrows = True

        self.assertTrue(self.module["PayPending"](eligible))
        paragon = eligible.GetData(eligible, "Paragon")
        self.assertEqual(3, paragon.GetLevel(paragon))
        self.assertEqual(30000, paragon.GetExperience(paragon))
        self.assertEqual(0, self.pending(7, 9001))

    def test_stale_db_is_checkpointed_from_live_state_before_cas(self):
        child = self.player(character_level=19)
        self.assertTrue(self.claim(child, 9001))
        self.lua.globals().SetProgression(7, 1, 100)
        eligible = self.player(
            guid=88, paragon_level=2, paragon_experience=5000
        )

        self.assertTrue(self.module["PayPending"](eligible))
        self.assertEqual(4, self.lua.globals().ProgressionLevel(7))
        self.assertEqual(10000, self.lua.globals().ProgressionExperience(7))
        self.assertEqual(0, self.pending(7, 9001))
        self.assertEqual(1, len(self.lua.globals().Awards))

    def test_fractional_live_experience_is_floored_before_settlement(self):
        self.lua.globals().SeedClaim(7, 9001, 100000)
        eligible = self.player(
            guid=88, paragon_level=1, paragon_experience=100.8
        )

        self.assertTrue(self.module["PayPending"](eligible))
        paragon = eligible.GetData(eligible, "Paragon")
        self.assertEqual(3, paragon.GetLevel(paragon))
        self.assertEqual(30100, paragon.GetExperience(paragon))
        self.assertEqual(3, self.lua.globals().ProgressionLevel(7))
        self.assertEqual(30100, self.lua.globals().ProgressionExperience(7))
        self.assertEqual(0, self.pending(7, 9001))
        self.assertEqual(1, len(self.lua.globals().Awards))

    def test_nonfinite_live_experience_fails_closed_with_pending_intact(self):
        self.lua.globals().SeedClaim(7, 9001, 100000)
        eligible = self.player(
            guid=88, paragon_level=1, paragon_experience=float("inf")
        )

        self.assertFalse(self.module["PayPending"](eligible))
        paragon = eligible.GetData(eligible, "Paragon")
        self.assertEqual(float("inf"), paragon.GetExperience(paragon))
        self.assertEqual(100000, self.pending(7, 9001))
        self.assertEqual(0, len(self.lua.globals().Awards))

    def test_missing_curve_authority_fails_closed_with_pending_intact(self):
        child = self.player(character_level=19)
        self.assertTrue(self.claim(child, 9001))
        eligible = self.player(guid=88)
        self.lua.globals().ParagonRework_CurveCost = None

        self.assertFalse(self.module["PayPending"](eligible))
        self.assertEqual(100000, self.pending(7, 9001))
        self.assertEqual(0, len(self.lua.globals().Awards))

    def test_ready_hook_drains_pending_for_an_eligible_account_alt(self):
        child = self.player(character_level=19)
        self.assertTrue(self.claim(child, 9001))
        alt = self.player(guid=88)
        self.lua.globals().MediatorEvents["OnAfterPlayerStatReady"](
            alt, alt.GetData(alt, "Paragon")
        )
        self.assertEqual(0, self.pending(7, 9001))
        self.assertEqual(1, len(self.lua.globals().Awards))

    def test_disabled_bot_zero_value_and_counter_fail_closed(self):
        player = self.player()
        self.lua.globals().ConfigValues.ENABLE_PARAGON_SYSTEM = "0"
        self.assertFalse(self.claim(player, 9001))
        self.lua.globals().ConfigValues.ENABLE_PARAGON_SYSTEM = "1"
        self.assertFalse(self.claim(self.player(bot=True), 9001))
        self.assertFalse(self.claim(player, 9999))
        self.assertFalse(self.claim(player, 1068))
        self.assertFalse(self.claim(player, 9900))


class ParagonAchievementClaimContractTests(unittest.TestCase):
    def read(self, path):
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def test_schema_has_account_pending_ledger_and_seed_is_zero(self):
        sql = self.read(MIGRATION)
        base = self.read(BASE_SCHEMA)
        for content in (sql, base):
            self.assertRegex(
                content,
                r"`pending_xp`\s+BIGINT UNSIGNED NOT NULL DEFAULT 0",
            )
        self.assertIn("`information_schema`.`COLUMNS`", sql)
        self.assertIn("PREPARE paragon_achievement_pending_stmt", sql)
        self.assertIn(
            "ADD COLUMN `pending_xp` BIGINT UNSIGNED NOT NULL DEFAULT 0", sql
        )
        self.assertRegex(
            sql,
            r"INSERT IGNORE INTO\s+`acore_ale`\."
            r"`paragon_rewarded_achievement`",
        )
        self.assertRegex(
            sql,
            r"\(`account_id`,\s*`achievement_id`,\s*`pending_xp`\)",
        )
        self.assertIn("`acore_characters`.`character_achievement`", sql)
        self.assertIn("`acore_world`.`player_factionchange_achievement`", sql)
        self.assertRegex(
            sql,
            r"LEAST\(faction_pair\.`alliance_id`,\s*"
            r"faction_pair\.`horde_id`\)",
        )
        self.assertRegex(sql, r"END,\s*0\s+FROM")

        statements = re.sub(r"--[^\r\n]*", "", sql)
        self.assertNotIn("paragon_banked_experience", statements)
        self.assertNotIn("character_paragon", statements)
        self.assertNotIn("account_paragon", statements)

    def test_event45_delegates_exclusively_to_durable_settlement(self):
        hook = self.read(HOOK)
        handler = re.search(
            r"function Hook\.OnPlayerAchievementComplete.*?\nend",
            hook,
            re.DOTALL,
        )
        self.assertIsNotNone(handler)
        body = handler.group(0)
        self.assertIn("ParagonAchievementReward_OnComplete", body)
        self.assertNotIn("OnBeforeAchievementExperience", body)
        self.assertNotIn("UpdatePlayerExperience(", body)

    def test_legacy_guid_bank_cannot_accrue_new_achievement_rewards(self):
        banking = self.read(BANKING)
        self.assertNotIn(
            'RegisterMediatorEvent("OnBeforeAchievementExperience"', banking
        )
        self.assertNotRegex(
            banking,
            r"INSERT\s+INTO\s+%s\s+\(guid,\s*amount\)",
        )
        self.assertIn("DELETE FROM %s WHERE guid", banking)


if __name__ == "__main__":
    unittest.main()
