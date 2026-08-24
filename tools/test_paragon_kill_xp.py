import os
import unittest

try:
    from lupa.lua52 import LuaRuntime
except ImportError:  # pragma: no cover - reported as skipped by unittest
    LuaRuntime = None


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCES = os.path.join(
    ROOT, "serverside", "paragon", "modules", "paragon_rework_sources.lua")
PARTY = os.path.join(
    ROOT, "serverside", "paragon", "modules", "paragon_rework_party.lua")
HOOK = os.path.join(ROOT, "serverside", "paragon", "paragon_hook.lua")
COLLECTION_XP = os.path.join(
    ROOT, "serverside", "paragon", "modules", "paragon_collection_xp.lua")
ALE_PATCH = os.path.join(ROOT, "patches", "05-mod-ale.patch")


@unittest.skipUnless(LuaRuntime, "lupa is required for Lua behavior tests")
class ParagonKillXPTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute(
            """
            Config = {
                experience = { creature = {}, quest = {}, achievement = {} }
            }
            function Config:GetByField(field)
                if field == "PARAGON_GROUP_XP_DISTANCE" then return "74" end
                if field == "PARAGON_ACHIEVEMENT_POINT_XP" then return "1000" end
                return nil
            end
            package.preload["paragon_config"] = function() return Config end
            mediator_handlers = {}
            function RegisterMediatorEvent(name, callback)
                mediator_handlers[name] = callback
            end
            function WorldDBQuery(_) return nil end
            ParagonReworkData_QuestXP = {}
            ParagonReworkData_AchievementPoints = {}

            function MakePlayer(level, group)
                return {
                    GetLevel = function(_) return level end,
                    GetGroup = function(_) return group end,
                }
            end

            function MakeCreature(level, reward, map)
                return {
                    GetLevel = function(_) return level end,
                    GetEntry = function(_) return 123 end,
                    GetMapId = function(_) return 1 end,
                    GetMap = function(_) return map end,
                    GetAtLevelXPReward = function(_) return reward end,
                }
            end

            function MakeDungeonGroup(count)
                local members = {}
                for i = 1, count do
                    members[i] = {
                        IsAlive = function(_) return true end,
                        GetLevel = function(_) return i == 1 and 80 or 1 end,
                        IsPlayerBot = function(_) return i ~= 1 end,
                        GetMapId = function(_) return 1 end,
                        GetDistance = function(_, _) return 9999 end,
                    }
                end
                return {
                    GetMembers = function(_) return members end,
                    IsRaidGroup = function(_) return false end,
                }
            end

            DungeonMap = {
                IsDungeon = function(_) return true end,
                IsRaid = function(_) return false end,
            }
            """
        )
        with open(SOURCES, encoding="utf-8") as handle:
            self.lua.execute(handle.read())

    def compute(self, player_level, creature_level, reward,
                participant_count=1, is_raid=False):
        player = self.lua.globals().MakePlayer(player_level, None)
        creature = self.lua.globals().MakeCreature(
            creature_level, reward, self.lua.globals().DungeonMap)
        return self.lua.globals().ParagonRework_ComputeKillShare(
            player, creature, participant_count, is_raid)

    def test_level_72_is_full_value_for_level_80_recipient(self):
        self.assertEqual(10, self.compute(80, 72, 39, 5))

    def test_nine_levels_below_still_receives_full_value(self):
        self.assertEqual(10, self.compute(80, 71, 39, 5))

    def test_ten_levels_below_receives_flat_half_before_group_share(self):
        self.assertEqual(5, self.compute(80, 70, 39, 5))

    def test_native_no_xp_creature_stays_zero(self):
        self.assertEqual(0, self.compute(80, 72, 0, 5))

    def test_tiny_divided_reward_is_not_inflated_to_one(self):
        self.assertEqual(0, self.compute(80, 72, 1, 5))

    def test_ineligible_bots_still_occupy_the_five_player_divisor(self):
        group = self.lua.globals().MakeDungeonGroup(5)
        player = self.lua.globals().MakePlayer(80, group)
        creature = self.lua.globals().MakeCreature(
            72, 1000, self.lua.globals().DungeonMap)
        self.assertEqual(
            280,
            self.lua.globals().ParagonRework_ComputeKillShare(
                player, creature, None, None),
        )

    def test_four_player_boundary_is_not_lost_to_float32_rate(self):
        player = self.lua.globals().MakePlayer(80, None)
        creature = self.lua.globals().MakeCreature(
            72, 1000, self.lua.globals().DungeonMap)
        self.assertEqual(
            325,
            self.lua.globals().ParagonRework_ComputeKillShare(
                player, creature, 4, False),
        )

    def test_three_player_boundary_uses_standard_group_bonus(self):
        self.assertEqual(4664, self.compute(80, 72, 12000, 3))

    def test_raid_share_has_no_party_bonus(self):
        self.assertEqual(250, self.compute(80, 72, 1000, 4, True))


@unittest.skipUnless(LuaRuntime, "lupa.lua52 is required for Lua behavior tests")
class ParagonPersonalBonusTests(unittest.TestCase):
    def test_ten_percent_personal_bonus_applies_to_repeatable_not_skillup(self):
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.execute(
            """
            Config = {}
            function Config:GetByField(field)
                if field == "MINIMUM_LEVEL_FOR_PARAGON_XP" then return "80" end
                return nil
            end
            package.preload["paragon_config"] = function() return Config end
            mediator_handlers = {}
            function RegisterMediatorEvent(name, callback)
                mediator_handlers[name] = callback
            end
            function RegisterPlayerEvent(_, _) end
            ParagonMountSpells = {}
            ParagonCompanionSpells = {}
            ParagonCodex_ExperiencePercent = function(_) return 10 end
            Player = {
                GetLevel = function(_) return 80 end,
                GetData = function(_, _) return nil end,
                SetData = function(_, _, _) end,
            }
            Paragon = { GetLevel = function(_) return 600 end }
            """
        )
        with open(COLLECTION_XP, encoding="utf-8") as handle:
            lua.execute(handle.read())
        handler = lua.globals().mediator_handlers["OnExperienceCalculated"]
        kill_result = handler(
            lua.globals().Player, lua.globals().Paragon, 1, 280)
        craft_result = handler(
            lua.globals().Player, lua.globals().Paragon, 5, 50)
        skillup_result = handler(
            lua.globals().Player, lua.globals().Paragon, 3, 50)
        self.assertEqual(308, kill_result[1])
        self.assertEqual(55, craft_result[1])
        self.assertIsNone(skillup_result)


class ParagonKillXPContractTests(unittest.TestCase):
    def test_party_module_uses_reward_credit_event_not_killing_blow(self):
        with open(PARTY, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("RegisterPlayerEvent(75, OnKillReward)", text)
        self.assertNotIn("RegisterPlayerEvent(7,", text)

        with open(HOOK, encoding="utf-8") as handle:
            hook = handle.read()
        self.assertNotIn("RegisterPlayerEvent(7, Hook.OnPlayerKillCreature)", hook)
        self.assertIn("OnAfterCreatureExperienceAwarded", hook)

    def test_ale_patch_carries_exact_reward_surface(self):
        with open(ALE_PATCH, encoding="utf-8") as handle:
            text = handle.read()
        for required in (
            "PLAYERHOOK_ON_REWARD_KILL_REWARDER",
            "PLAYER_EVENT_ON_KILL_REWARD",
            "GetAtLevelXPReward",
            "Acore::XP::BaseGain",
            "ModExperience",
            "ModHealth",
            "CREATURE_FLAG_EXTRA_NO_XP",
            "participantCount",
            "isRaid",
        ):
            self.assertIn(required, text)


if __name__ == "__main__":
    unittest.main()
