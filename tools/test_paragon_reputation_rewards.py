import os
import re
import unittest

try:
    from lupa.lua52 import LuaRuntime
except ImportError:  # pragma: no cover - reported as skipped by unittest
    LuaRuntime = None


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE = os.path.join(
    ROOT, "serverside", "paragon", "modules", "paragon_reputation_rewards.lua"
)
INSTALLER = os.path.join(ROOT, "tools", "install.py")
SCHEMA = os.path.join(ROOT, "sql", "02_create_tables.sql")
MIGRATION = os.path.join(
    ROOT, "sql", "09_add_reputation_and_account_collection_rewards.sql"
)
SQL_INSTALL = os.path.join(ROOT, "sql", "install.sql")


@unittest.skipUnless(LuaRuntime, "lupa.lua52 is required for Lua behavior tests")
class ParagonReputationRewardTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute(
            r'''
            ConfigValues = {
                ENABLE_PARAGON_SYSTEM = "1",
                PARAGON_REPUTATION_XP_ENABLED = "1",
                PARAGON_REPUTATION_XP_PER_POINT = "50",
                MINIMUM_LEVEL_FOR_PARAGON_XP = "80",
                LEVEL_LINKED_TO_ACCOUNT = "1",
                PARAGON_LEVEL_CAP = "2000",
            }
            Config = {}
            function Config:GetByField(field) return ConfigValues[field] end
            package.preload["paragon_config"] = function() return Config end
            package.preload["paragon_constant"] = function()
                return { DB_NAME = "acore_ale" }
            end

            function ParagonRework_CurveCost(level)
                return 10000000
            end

            local function Result(rows)
                if #rows == 0 then return nil end
                local index = 1
                return {
                    GetUInt32 = function(_, column)
                        return rows[index][column + 1]
                    end,
                    GetInt32 = function(_, column)
                        return rows[index][column + 1]
                    end,
                    GetString = function(_, column)
                        return tostring(rows[index][column + 1])
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

            -- Representative Alliance/Horde counterparts. The module builds
            -- this map once at load, just like a real world database.
            FactionChangePairs = {
                { 72, 76 },
                { 47, 54 },
            }
            function WorldDBQuery(sql)
                if sql:find("player_factionchange_reputations", 1, true) then
                    return Result(FactionChangePairs)
                end
                error("unexpected WorldDBQuery: " .. sql)
            end

            Progress = {}
            Progression = {}
            CharacterReputationRows = {}
            BaseReputations = {}
            AwardAmounts = {}
            AwardSources = {}
            AwardEntries = {}
            Broadcasts = {}
            SQLLog = {}
            RegisteredPlayerEvents = {}
            RegisteredMediatorEvents = {}

            local function ProgressKey(account, faction)
                return tostring(account) .. ":" .. tostring(faction)
            end

            local function ProgressionKey(table_name, owner)
                return table_name .. ":" .. tostring(owner)
            end

            function SetProgress(account, faction, high_water, pending_xp)
                Progress[ProgressKey(account, faction)] = {
                    high_water = high_water,
                    pending_xp = pending_xp,
                }
            end

            function GetProgress(account, faction, column)
                local row = Progress[ProgressKey(account, faction)]
                if not row then return nil end
                return row[column]
            end

            function AddCharacterReputation(race_id, class_id, faction_id, relative)
                CharacterReputationRows[#CharacterReputationRows + 1] = {
                    race_id, class_id, faction_id, relative,
                }
            end

            function SetBaseReputation(faction_id, race_id, class_id, standing)
                local key = string.format("%d:%d:%d", faction_id, race_id, class_id)
                BaseReputations[key] = standing
            end

            function GetFactionBaseReputation(faction_id, race_id, class_id)
                local key = string.format("%d:%d:%d", faction_id, race_id, class_id)
                return BaseReputations[key] or 0
            end

            local function ParsePersistGain(sql)
                local account, faction, high_water, initial_xp = sql:match(
                    "VALUES%s*%(%s*(%-?%d+)%s*,%s*(%-?%d+)%s*,%s*"
                        .. "(%-?%d+)%s*,%s*(%-?%d+)%s*%)")
                local new_standing, old_standing, xp_per_point = sql:match(
                    "GREATEST%s*%(%s*0%s*,%s*(%-?%d+)%s*%-%s*"
                        .. "GREATEST%s*%(%s*high_water%s*,%s*(%-?%d+)%s*%)"
                        .. "%s*%)%s*%*%s*(%-?%d+)")
                if not account or not new_standing then
                    error("could not parse reputation upsert: " .. sql)
                end
                account = tonumber(account)
                faction = tonumber(faction)
                high_water = tonumber(high_water)
                initial_xp = tonumber(initial_xp)
                new_standing = tonumber(new_standing)
                old_standing = tonumber(old_standing)
                xp_per_point = tonumber(xp_per_point)

                local key = ProgressKey(account, faction)
                local row = Progress[key]
                if not row then
                    Progress[key] = {
                        high_water = high_water,
                        pending_xp = initial_xp,
                    }
                    return
                end
                row.pending_xp = row.pending_xp
                    + math.max(0, new_standing - math.max(row.high_water, old_standing))
                        * xp_per_point
                row.high_water = math.max(
                    row.high_water, old_standing, new_standing)
            end

            local function ParseSeed(sql)
                local values = sql:match("VALUES%s+(.-)%s+ON DUPLICATE KEY UPDATE")
                if not values then error("could not parse reputation seed: " .. sql) end
                for account, faction, high_water, pending_xp in values:gmatch(
                        "%(%s*(%-?%d+)%s*,%s*(%-?%d+)%s*,%s*"
                            .. "(%-?%d+)%s*,%s*(%-?%d+)%s*%)") do
                    account = tonumber(account)
                    faction = tonumber(faction)
                    high_water = tonumber(high_water)
                    pending_xp = tonumber(pending_xp)
                    local key = ProgressKey(account, faction)
                    local row = Progress[key]
                    if row then
                        row.high_water = math.max(row.high_water, high_water)
                    else
                        Progress[key] = {
                            high_water = high_water,
                            pending_xp = pending_xp,
                        }
                    end
                end
            end

            function CharDBQuery(sql)
                SQLLog[#SQLLog + 1] = sql
                local compact = sql:gsub("%s+", " ")

                if compact:find("FROM acore_characters.characters c", 1, true)
                        and compact:find("character_reputation", 1, true) then
                    return Result(CharacterReputationRows)
                end

                if compact:find(
                        "INSERT INTO acore_ale.paragon_reputation_progress", 1, true) then
                    if compact:find(
                            "pending_xp = pending_xp + GREATEST", 1, true) then
                        ParsePersistGain(compact)
                    else
                        ParseSeed(compact)
                    end
                    return nil
                end

                if compact:find(
                        "SELECT high_water, pending_xp FROM "
                            .. "acore_ale.paragon_reputation_progress", 1, true) then
                    local account, faction = compact:match(
                        "WHERE account_id = (%d+) AND faction_id = (%d+)")
                    local row = Progress[ProgressKey(
                        tonumber(account), tonumber(faction))]
                    if not row then return nil end
                    return Result({ { row.high_water, row.pending_xp } })
                end

                if compact:find(
                        "SELECT COALESCE(SUM(pending_xp), 0) FROM "
                            .. "acore_ale.paragon_reputation_progress", 1, true) then
                    local account = tonumber(compact:match(
                        "WHERE account_id = (%d+) AND pending_xp > 0"))
                    local pending = 0
                    for key, row in pairs(Progress) do
                        if key:match("^(%d+):") == tostring(account) then
                            pending = pending + row.pending_xp
                        end
                    end
                    return Result({ { pending } })
                end

                if compact:find("INSERT INTO acore_ale.", 1, true)
                        and compact:find("_paragon", 1, true)
                        and compact:find("level, experience", 1, true) then
                    local table_name, id_column, owner, level, experience = compact:match(
                        "INSERT INTO acore_ale%.([%w_]+) %(([%w_]+), level, experience%) "
                            .. "VALUES %((%d+), (%d+), (%d+)%)")
                    if not table_name then
                        error("could not parse progression sync: " .. sql)
                    end
                    Progression[ProgressionKey(table_name, tonumber(owner))] = {
                        level = tonumber(level),
                        experience = tonumber(experience),
                        id_column = id_column,
                    }
                    return nil
                end

                if compact:find("SELECT COUNT(*) FROM acore_ale.", 1, true) then
                    local table_name, id_column, owner, level, experience = compact:match(
                        "SELECT COUNT%(%*%) FROM acore_ale%.([%w_]+) "
                            .. "WHERE ([%w_]+) = (%d+) AND level = (%d+) "
                            .. "AND experience = (%d+)")
                    if not table_name then
                        error("could not parse progression verification: " .. sql)
                    end
                    local row = Progression[ProgressionKey(
                        table_name, tonumber(owner))]
                    local matches = row and row.id_column == id_column
                        and row.level == tonumber(level)
                        and row.experience == tonumber(experience)
                    return Result({ { matches and 1 or 0 } })
                end

                if compact:find("UPDATE acore_ale.", 1, true)
                        and compact:find("JOIN acore_ale.paragon_reputation_progress", 1, true) then
                    local table_name = compact:match(
                        "UPDATE acore_ale%.([%w_]+) progression")
                    local account = tonumber(compact:match(
                        "reputation.account_id = (%d+)"))
                    local new_level, new_experience = compact:match(
                        "SET progression.level = (%d+), progression.experience = (%d+)")
                    local id_column, owner, old_level, old_experience = compact:match(
                        "WHERE progression%.([%w_]+) = (%d+) AND progression.level = (%d+) "
                            .. "AND progression.experience = (%d+)")
                    local row = Progression[ProgressionKey(
                        table_name, tonumber(owner))]
                    if row and row.id_column == id_column
                            and row.level == tonumber(old_level)
                            and row.experience == tonumber(old_experience) then
                        row.level = tonumber(new_level)
                        row.experience = tonumber(new_experience)
                        for key, progress in pairs(Progress) do
                            if key:match("^(%d+):") == tostring(account)
                                    and progress.pending_xp > 0 then
                                progress.pending_xp = 0
                            end
                        end
                    end
                    return nil
                end

                error("unexpected CharDBQuery: " .. sql)
            end

            Hook = {
                ExperienceSource = { REPUTATION = 12 },
            }
            function Hook.AwardFlatExperience(player, source, entry, amount)
                AwardAmounts[#AwardAmounts + 1] = amount
                AwardSources[#AwardSources + 1] = source
                AwardEntries[#AwardEntries + 1] = entry
                local paragon = player:GetData("Paragon")
                local experience = paragon:GetExperience() + amount
                local level = paragon:GetLevel()
                local cost = ParagonRework_CurveCost(level)
                while experience >= cost do
                    experience = experience - cost
                    level = level + 1
                    cost = ParagonRework_CurveCost(level)
                end
                paragon:SetLevel(level)
                paragon:SetExperience(experience)
                return true, amount
            end
            package.preload["paragon_hook"] = function() return Hook end

            Paragon = { Level = 4, Experience = 100 }
            function Paragon:GetLevel() return self.Level end
            function Paragon:GetExperience() return self.Experience end
            function Paragon:SetLevel(value) self.Level = value end
            function Paragon:SetExperience(value) self.Experience = value end

            Player = {
                Account = 42,
                Guid = 7,
                Level = 80,
                Bot = false,
                Data = { Paragon = Paragon },
            }
            function Player:GetAccountId() return self.Account end
            function Player:GetGUIDLow() return self.Guid end
            function Player:GetLevel() return self.Level end
            function Player:IsPlayerBot() return self.Bot end
            function Player:GetData(key) return self.Data[key] end
            function Player:SetData(key, value) self.Data[key] = value end
            function Player:SendBroadcastMessage(message)
                Broadcasts[#Broadcasts + 1] = message
            end

            function RegisterPlayerEvent(event_id, callback)
                RegisteredPlayerEvents[event_id] = callback
            end
            function RegisterMediatorEvent(name, callback)
                RegisteredMediatorEvents[name] = callback
            end
            print = function() end
            ''')
        with open(MODULE, encoding="utf-8") as handle:
            self.module = self.lua.execute(handle.read())

    def fire(self, faction_id, old_standing, new_standing, incremental=True):
        callback = self.lua.globals().RegisteredPlayerEvents[82]
        self.assertIsNotNone(callback, "reputation module did not register event 82")
        callback(
            82,
            self.lua.globals().Player,
            faction_id,
            old_standing,
            new_standing,
            incremental,
        )

    def awards(self):
        values = self.lua.globals().AwardAmounts
        return [values[index] for index in range(1, len(values) + 1)]

    def progress(self, faction_id, column):
        return self.lua.globals().GetProgress(42, faction_id, column)

    def test_registers_post_commit_event_82_and_pays_flat_50_per_point(self):
        self.assertIsNotNone(self.lua.globals().RegisteredPlayerEvents[82])
        self.assertIsNone(self.lua.globals().RegisteredPlayerEvents[15])

        self.fire(930, 0, 3)

        self.assertEqual([150], self.awards())
        self.assertEqual(3, self.progress(930, "high_water"))
        self.assertEqual(0, self.progress(930, "pending_xp"))
        self.assertEqual(12, self.lua.globals().AwardSources[1])
        self.assertEqual(930, self.lua.globals().AwardEntries[1])

    def test_negative_standings_are_real_high_water_not_clamped_to_zero(self):
        self.fire(930, -6000, -5997)

        self.assertEqual([150], self.awards())
        self.assertEqual(-5997, self.progress(930, "high_water"))

    def test_loss_and_regain_cannot_replay_reputation_xp(self):
        self.fire(930, 0, 10)
        self.fire(930, 10, 5)
        self.fire(930, 5, 10)
        self.fire(930, 10, 12)

        self.assertEqual([500, 100], self.awards())
        self.assertEqual(12, self.progress(930, "high_water"))
        self.assertEqual(0, self.progress(930, "pending_xp"))

    def test_faction_change_counterparts_share_one_canonical_ledger(self):
        self.assertEqual(72, self.module.CanonicalFaction(72))
        self.assertEqual(72, self.module.CanonicalFaction(76))

        self.fire(72, 0, 5)
        self.fire(76, 0, 4)
        self.fire(76, 4, 7)

        self.assertEqual([250, 100], self.awards())
        self.assertEqual(7, self.progress(72, "high_water"))
        self.assertIsNone(self.progress(76, "high_water"))

    def test_missing_row_uses_native_old_standing_as_no_backpay_baseline(self):
        self.assertIsNone(self.progress(930, "high_water"))

        self.fire(930, 5000, 5010)

        self.assertEqual([500], self.awards())
        self.assertEqual(5010, self.progress(930, "high_water"))

    def test_disabled_source_advances_baseline_without_pending_backpay(self):
        self.lua.globals().ConfigValues.PARAGON_REPUTATION_XP_ENABLED = "0"
        self.fire(930, 0, 10)

        self.assertEqual([], self.awards())
        self.assertEqual(10, self.progress(930, "high_water"))
        self.assertEqual(0, self.progress(930, "pending_xp"))

        self.lua.globals().ConfigValues.PARAGON_REPUTATION_XP_ENABLED = "1"
        self.fire(930, 10, 12)
        self.assertEqual([100], self.awards())

        self.lua.globals().ConfigValues.ENABLE_PARAGON_SYSTEM = "0"
        self.fire(609, -100, 0)
        self.assertEqual([100], self.awards())
        self.assertEqual(0, self.progress(609, "high_water"))
        self.assertEqual(0, self.progress(609, "pending_xp"))

        self.lua.globals().ConfigValues.ENABLE_PARAGON_SYSTEM = "1"
        self.fire(609, 0, 1)
        self.assertEqual([100, 50], self.awards())

    def test_pre80_gain_banks_exact_xp_and_drains_once_at_level_80(self):
        self.lua.globals().Player.Level = 79
        self.fire(930, 0, 4)

        self.assertEqual([], self.awards())
        self.assertEqual(4, self.progress(930, "high_water"))
        self.assertEqual(200, self.progress(930, "pending_xp"))

        self.lua.globals().Player.Level = 80
        paid, amount = self.module.PayPending(self.lua.globals().Player)
        self.assertTrue(paid)
        self.assertEqual(200, amount)
        self.assertEqual([200], self.awards())
        self.assertEqual(0, self.progress(930, "pending_xp"))

        paid_again, amount_again = self.module.PayPending(
            self.lua.globals().Player
        )
        self.assertFalse(paid_again)
        self.assertEqual(0, amount_again)
        self.assertEqual([200], self.awards())

    def test_playerbots_neither_advance_high_water_nor_bank_xp(self):
        self.lua.globals().Player.Bot = True
        self.fire(930, 0, 100)

        self.assertEqual([], self.awards())
        self.assertIsNone(self.progress(930, "high_water"))

    def test_account_seed_reconstructs_absolute_standing_and_canonical_maximum(self):
        self.lua.globals().SetBaseReputation(72, 1, 1, 0)
        self.lua.globals().AddCharacterReputation(1, 1, 72, 100)
        self.lua.globals().SetBaseReputation(76, 2, 2, 100)
        self.lua.globals().AddCharacterReputation(2, 2, 76, 50)
        self.lua.globals().SetBaseReputation(930, 1, 1, -3000)
        self.lua.globals().AddCharacterReputation(1, 1, 930, -1000)

        self.assertTrue(self.module.SeedAccount(self.lua.globals().Player))

        self.assertEqual(150, self.progress(72, "high_water"))
        self.assertEqual(0, self.progress(72, "pending_xp"))
        self.assertEqual(-4000, self.progress(930, "high_water"))
        self.assertEqual(0, self.progress(930, "pending_xp"))
        self.assertIsNone(self.progress(76, "high_water"))
        self.assertEqual([], self.awards())


class ParagonReputationStaticContractTests(unittest.TestCase):
    def read(self, path):
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def test_module_uses_only_authoritative_post_commit_event(self):
        module = self.read(MODULE)
        self.assertIn("RegisterPlayerEvent(82, OnAfterReputationChange)", module)
        self.assertNotRegex(module, r"RegisterPlayerEvent\(\s*15\s*,")
        self.assertIn("old_standing, new_standing, incremental", module)

    def test_seed_contract_reconstructs_every_account_characters_absolute_rep(self):
        module = self.read(MODULE)
        self.assertIn("acore_characters.character_reputation", module)
        self.assertRegex(module, r"WHERE\s+c\.account\s*=\s*%d")
        self.assertIn("GetFactionBaseReputation", module)
        self.assertRegex(module, r"ClampStanding\(base\s*\+\s*relative\)")
        self.assertIn("player_factionchange_reputations", module)

    def test_installer_rejects_every_partial_native_event_82_bridge(self):
        installer = self.read(INSTALLER)
        required_fragments = (
            "src/server/game/Reputation/ReputationMgr.cpp",
            "OnPlayerAfterReputationChange",
            "PLAYERHOOK_ON_AFTER_REPUTATION_CHANGE",
            "PLAYER_EVENT_ON_AFTER_REPUTATION_CHANGE",
            "PLAYER_EVENT_ON_AFTER_REPUTATION_CHANGE\\s*=\\s*82",
            "GetFactionBaseReputation",
            "patches/10-core-reputation-xp.patch",
            "patches/11-mod-ale-reputation-xp.patch",
        )
        for fragment in required_fragments:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, installer)

    def test_schema_and_upgrade_define_signed_durable_account_high_water(self):
        table_pattern = re.compile(
            r"CREATE TABLE IF NOT EXISTS\s+`acore_ale`\."
            r"`paragon_reputation_progress`(?P<body>.*?)ENGINE=InnoDB",
            re.DOTALL | re.IGNORECASE,
        )
        for path in (SCHEMA, MIGRATION):
            with self.subTest(path=os.path.relpath(path, ROOT)):
                match = table_pattern.search(self.read(path))
                self.assertIsNotNone(match)
                body = match.group("body")
                self.assertRegex(body, r"`high_water`\s+INT\s+NOT NULL")
                self.assertNotRegex(body, r"`high_water`\s+INT\s+UNSIGNED")
                self.assertRegex(
                    body,
                    r"`pending_xp`\s+BIGINT\s+UNSIGNED\s+NOT NULL\s+DEFAULT\s+0",
                )
                self.assertIn(
                    "PRIMARY KEY (`account_id`, `faction_id`)", body
                )

        self.assertIn(
            "SOURCE sql/09_add_reputation_and_account_collection_rewards.sql;",
            self.read(SQL_INSTALL),
        )


if __name__ == "__main__":
    unittest.main()
