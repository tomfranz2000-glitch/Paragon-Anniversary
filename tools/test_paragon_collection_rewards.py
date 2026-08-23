import os
import unittest

try:
    from lupa.lua52 import LuaRuntime
except ImportError:  # pragma: no cover - reported as skipped by unittest
    LuaRuntime = None


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COLLECTION_REWARDS = os.path.join(
    ROOT, "serverside", "paragon", "modules", "paragon_collection_rewards.lua")


@unittest.skipUnless(LuaRuntime, "lupa.lua52 is required for Lua behavior tests")
class ParagonCollectionRewardTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute(
            """
            package.preload["paragon_constant"] = function()
                return { DB_NAME = "acore_ale" }
            end

            Config = {}
            function Config:GetByField(field)
                if field == "MINIMUM_LEVEL_FOR_PARAGON_XP" then return "80" end
                return nil
            end
            package.preload["paragon_config"] = function() return Config end
            package.preload["paragon_hook"] = function()
                return { Addon = { Prefix = "PARAGON" } }
            end

            player_events = {}
            mediator_handlers = {}
            function RegisterPlayerEvent(eventId, callback)
                player_events[eventId] = callback
            end
            function RegisterMediatorEvent(name, callback)
                mediator_handlers[name] = callback
            end

            executed_sql = {}
            function CharDBExecute(sql)
                executed_sql[#executed_sql + 1] = sql
            end
            function CharDBQuery(sql)
                if string.find(sql, "custom_unlocked_appearances", 1, true) then
                    return {
                        GetUInt32 = function(_, column) return 12345 end,
                        NextRow = function(_) return false end,
                    }
                end
                return nil
            end

            awarded = 0
            Mediator = {}
            function Mediator.On(name, payload)
                if name == "OnUpdatePlayerExperience" then
                    awarded = awarded + payload.arguments[3]
                end
                return payload.defaults[1]
            end

            Paragon = {
                GetLevel = function(_) return 500 end,
                GetExperience = function(_) return 0 end,
                GetExperienceForNextLevel = function(_) return 1000 end,
                GetPoints = function(_) return 0 end,
            }
            player_data = { Paragon = Paragon }
            timers = {}
            register_count = 0
            Player = {
                IsPlayerBot = function(_) return false end,
                GetLevel = function(_) return 80 end,
                GetGUIDLow = function(_) return 77 end,
                GetAccountId = function(_) return 42 end,
                GetData = function(_, key) return player_data[key] end,
                SetData = function(_, key, value) player_data[key] = value end,
                RegisterEvent = function(_, callback, delay, repeats)
                    register_count = register_count + 1
                    timers[#timers + 1] = {
                        callback = callback, delay = delay, repeats = repeats,
                    }
                    return register_count
                end,
                SendServerResponse = function(...) end,
                SendBroadcastMessage = function(...) end,
            }

            function RunLatestTimer()
                local timer = timers[#timers]
                timer.callback(1, timer.delay, timer.repeats, Player)
            end
            """
        )
        with open(COLLECTION_REWARDS, encoding="utf-8") as handle:
            self.lua.execute(handle.read())

    def start_eligible_session(self):
        self.lua.globals().mediator_handlers[
            "OnAfterUpdatePlayerStatistics"
        ](self.lua.globals().Player, self.lua.globals().Paragon, True)

    def test_new_ticker_reconciles_unlock_without_an_item_event(self):
        self.assertIsNone(
            self.lua.globals().player_data["ParagonCollectRewardDirty"])

        self.start_eligible_session()

        self.assertTrue(
            self.lua.globals().player_data["ParagonCollectRewardDirty"])
        self.assertEqual(1, self.lua.globals().register_count)

        self.lua.globals().RunLatestTimer()

        self.assertEqual(1000, self.lua.globals().awarded)
        self.assertIsNone(
            self.lua.globals().player_data["ParagonCollectRewardDirty"])
        self.assertIn(
            "paragon_rewarded_appearance VALUES (42,12345)",
            self.lua.globals().executed_sql[1],
        )

    def test_next_session_starts_with_reconciliation_dirty(self):
        self.start_eligible_session()
        self.lua.globals().player_data["ParagonCollectRewardDirty"] = None
        self.lua.globals().player_events[4](4, self.lua.globals().Player)

        self.start_eligible_session()

        self.assertEqual(2, self.lua.globals().register_count)
        self.assertTrue(
            self.lua.globals().player_data["ParagonCollectRewardDirty"])


if __name__ == "__main__":
    unittest.main()
