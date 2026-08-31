import os
import unittest

try:
    from lupa.lua52 import LuaRuntime
except ImportError:  # pragma: no cover - reported as skipped by unittest
    LuaRuntime = None


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE = os.path.join(
    ROOT, "serverside", "paragon", "modules", "paragon_rework_banking.lua")


@unittest.skipUnless(LuaRuntime, "lupa.lua52 is required for Lua behavior tests")
class ParagonBankedRewardTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute(
            r"""
            package.preload["paragon_constant"] = function()
                return { DB_NAME = "acore_ale" }
            end
            Config = {}
            function Config:GetByField(field)
                if field == "MINIMUM_LEVEL_FOR_PARAGON_XP" then return "80" end
                return nil
            end
            package.preload["paragon_config"] = function() return Config end

            Hook = {
                Addon = { Prefix = "PARAGON" },
                ExperienceSource = { ACHIEVEMENT = 2 },
            }
            AwardSucceeds = true
            Awards = {}
            function Hook.AwardFlatExperience(player, source, entry, base)
                Awards[#Awards + 1] = {
                    source = source, entry = entry, base = base,
                }
                if not AwardSucceeds then return false end
                return true, base
            end
            package.preload["paragon_hook"] = function() return Hook end

            Mediators = {}
            PlayerEvents = {}
            function RegisterMediatorEvent(name, callback)
                Mediators[name] = callback
            end
            function RegisterPlayerEvent(event, callback)
                PlayerEvents[event] = callback
            end

            ExecutedSQL = {}
            Broadcasts = {}
            QueryAmount = 2000
            function CharDBExecute(sql)
                ExecutedSQL[#ExecutedSQL + 1] = sql
            end
            function CharDBQueryAsync(sql, callback)
                callback({
                    GetUInt32 = function(_, column) return QueryAmount end,
                })
            end
            function GetPlayerGUID(guidLow)
                return "player-guid-" .. tostring(guidLow)
            end
            function GetPlayerByGUID(guid)
                if guid == "player-guid-77" then return Player end
                return nil
            end

            Paragon = {}
            Player = { level = 80 }
            function Player:GetLevel() return self.level end
            function Player:GetGUIDLow() return 77 end
            function Player:GetData(key)
                if key == "Paragon" then return Paragon end
                return nil
            end
            function Player:SendBroadcastMessage(message)
                Broadcasts[#Broadcasts + 1] = message
            end
            ParagonRework_AchievementValue = function(_) return 2000 end
            """
        )
        with open(MODULE, encoding="utf-8") as handle:
            self.lua.execute(handle.read())

    def cross_level_threshold(self):
        self.lua.globals().PlayerEvents[13](
            13, self.lua.globals().Player, 79
        )

    def test_payout_uses_shared_flat_boundary_and_reports_stored_xp(self):
        self.cross_level_threshold()

        self.assertEqual(1, len(self.lua.globals().Awards))
        award = self.lua.globals().Awards[1]
        self.assertEqual(2, award["source"])
        self.assertEqual(2000, award["base"])
        self.assertIn("2000 paragon experience", self.lua.globals().Broadcasts[1])
        self.assertIn(
            "DELETE FROM acore_ale.paragon_banked_experience WHERE guid = 77",
            self.lua.globals().ExecutedSQL[1],
        )

    def test_failed_shared_award_preserves_the_bank_for_retry(self):
        self.lua.globals().AwardSucceeds = False
        self.cross_level_threshold()

        self.assertEqual(1, len(self.lua.globals().Awards))
        self.assertEqual(0, len(self.lua.globals().ExecutedSQL))
        self.assertEqual(0, len(self.lua.globals().Broadcasts))

    def test_legacy_module_no_longer_accrues_new_achievement_rewards(self):
        self.lua.globals().Player.level = 79
        self.assertIsNone(
            self.lua.globals().Mediators["OnBeforeAchievementExperience"]
        )
        self.assertEqual(0, len(self.lua.globals().ExecutedSQL))


if __name__ == "__main__":
    unittest.main()
