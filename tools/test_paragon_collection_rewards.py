import os
import importlib.util
import unittest

try:
    from lupa.lua52 import LuaRuntime
except ImportError:  # pragma: no cover - reported as skipped by unittest
    LuaRuntime = None


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COLLECTION_REWARDS = os.path.join(
    ROOT, "serverside", "paragon", "modules", "paragon_collection_rewards.lua")
COLLECTION_GENERATOR = os.path.join(ROOT, "tools", "paragon_collectible_xp.py")


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
            Hook = {
                Addon = { Prefix = "PARAGON" },
                ExperienceSource = { COLLECTIBLE = 8 },
            }
            awarded = 0
            award_sources = {}
            award_entries = {}
            award_bases = {}
            function Hook.AwardFlatExperience(player, source, entry, amount)
                awarded = awarded + amount
                award_sources[#award_sources + 1] = source
                award_entries[#award_entries + 1] = entry
                award_bases[#award_bases + 1] = amount
                return true, amount
            end
            package.preload["paragon_hook"] = function() return Hook end

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
                if string.find(sql, "SELECT spell_id, kind, name, xp", 1, true) then
                    return {
                        GetUInt32 = function(_, column) return 72286 end,
                        GetString = function(_, column)
                            return column == 1 and "mount" or "Invincible's Reins"
                        end,
                        GetInt32 = function(_, column) return 4000000 end,
                        NextRow = function(_) return false end,
                    }
                end
                if string.find(sql, "custom_unlocked_appearances", 1, true) then
                    return {
                        GetUInt32 = function(_, column) return 12345 end,
                        NextRow = function(_) return false end,
                    }
                end
                return nil
            end

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
                SendBroadcastMessage = function(_, message)
                    broadcasts[#broadcasts + 1] = message
                end,
            }
            broadcasts = {}

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

        self.assertEqual(2000, self.lua.globals().awarded)
        self.assertEqual(8, self.lua.globals().award_sources[1])
        self.assertEqual(2000, self.lua.globals().award_bases[1])
        self.assertIn("+2,000 Paragon XP", self.lua.globals().broadcasts[1])
        self.assertIsNone(
            self.lua.globals().player_data["ParagonCollectRewardDirty"])
        self.assertIn(
            "paragon_rewarded_appearance VALUES (42,12345)",
            self.lua.globals().executed_sql[1],
        )

    def test_mount_reward_uses_authoritative_generated_xp_directly(self):
        self.lua.globals().player_events[44](
            44, self.lua.globals().Player, 72286
        )

        self.assertEqual(4000000, self.lua.globals().awarded)
        self.assertEqual(8, self.lua.globals().award_sources[1])
        self.assertEqual(72286, self.lua.globals().award_entries[1])
        self.assertEqual(4000000, self.lua.globals().award_bases[1])
        self.assertIn("+4,000,000 Paragon XP", self.lua.globals().broadcasts[1])
        self.assertIn(
            "paragon_rewarded_collectible_spell VALUES (42, 72286)",
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


class ParagonCollectionGeneratorValueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "paragon_collectible_xp_contract", COLLECTION_GENERATOR
        )
        cls.generator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.generator)

    def test_final_values_are_doubled_in_the_generator(self):
        self.assertEqual(160000, self.generator.BASE_MOUNT)
        self.assertEqual(60000, self.generator.BASE_COMPANION)
        self.assertEqual(2000, self.generator.BASE_ITEM)
        self.assertEqual(1000, self.generator.SPELL_ROUNDING)
        self.assertEqual(
            {
                "raid_epic": 5000,
                "scarce": 10000,
                "prestige": 24000,
                "pinnacle": 60000,
                "mythic_world_drop": 1000000,
                "legendary": 1500000,
            },
            self.generator.ITEM_TIERS,
        )
        self.assertEqual(5000, self.generator.EASY_ITEM_CAP)
        self.assertEqual(
            {
                72286: 4000000,
                63796: 3000000,
                40192: 2400000,
                71342: 2000000,
                60002: 1600000,
                24252: 1000000,
                24242: 1000000,
                59996: 1000000,
                17481: 800000,
                36702: 800000,
                41252: 800000,
                46628: 600000,
                48025: 600000,
                61294: 600000,
            },
            self.generator.MOUNT_OVERRIDES,
        )
        self.assertEqual(
            {
                49623: 2000000,
                32837: 2000000,
                32838: 2000000,
                19019: 1800000,
                17182: 1600000,
                46017: 1600000,
                34334: 1400000,
                1728: 1200000,
            },
            self.generator.ITEM_OVERRIDES,
        )

    def test_generator_persists_baseline_appearance_rows(self):
        with open(COLLECTION_GENERATOR, encoding="utf-8") as handle:
            generator_source = handle.read()
        self.assertNotIn("if xp > BASE_ITEM", generator_source)
        self.assertIn("item_rows.append((iid, name, xp))", generator_source)

    def test_runtime_fallback_matches_generator_baseline(self):
        with open(COLLECTION_REWARDS, encoding="utf-8") as handle:
            runtime = handle.read()
        self.assertIn(
            "local BASELINE_ITEM_XP = %d" % self.generator.BASE_ITEM,
            runtime,
        )


if __name__ == "__main__":
    unittest.main()
