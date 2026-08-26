import os
import re
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
DEFAULT_CONFIG = os.path.join(ROOT, "sql", "04_insert_default_config.sql")
ANNIVERSARY_CONFIG = os.path.join(ROOT, "sql", "05_apply_anniversary_config.sql")

INSTANCE_MULTIPLIERS = {
    "PARAGON_CREATURE_XP_TBC_HEROIC_DUNGEON_MULTIPLIER": "1.25",
    "PARAGON_CREATURE_XP_WOTLK_HEROIC_DUNGEON_MULTIPLIER": "1.5",
    "PARAGON_CREATURE_XP_TBC_RAID_MULTIPLIER": "2",
    "PARAGON_CREATURE_XP_WOTLK_NORMAL_RAID_MULTIPLIER": "2.5",
    "PARAGON_CREATURE_XP_WOTLK_HEROIC_RAID_MULTIPLIER": "4",
}


@unittest.skipUnless(LuaRuntime, "lupa is required for Lua behavior tests")
class ParagonKillXPTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute(
            """
            Config = {
                experience = { creature = {}, quest = {}, achievement = {} }
            }
            ConfigValues = {
                PARAGON_GROUP_XP_DISTANCE = "74",
                PARAGON_ACHIEVEMENT_POINT_XP = "2000",
                PARAGON_CREATURE_XP_TBC_HEROIC_DUNGEON_MULTIPLIER = "1.25",
                PARAGON_CREATURE_XP_WOTLK_HEROIC_DUNGEON_MULTIPLIER = "1.5",
                PARAGON_CREATURE_XP_TBC_RAID_MULTIPLIER = "2",
                PARAGON_CREATURE_XP_WOTLK_NORMAL_RAID_MULTIPLIER = "2.5",
                PARAGON_CREATURE_XP_WOTLK_HEROIC_RAID_MULTIPLIER = "4",
            }
            function Config:GetByField(field)
                return ConfigValues[field]
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

            function MakeMap(expansion, isDungeon, isRaid, isHeroic, mapId)
                return {
                    GetExpansion = function(_) return expansion end,
                    GetMapId = function(_) return mapId or 0 end,
                    IsDungeon = function(_) return isDungeon end,
                    IsRaid = function(_) return isRaid end,
                    IsHeroic = function(_) return isHeroic end,
                }
            end

            DungeonMap = MakeMap(2, true, false, false)
            WorldMap = MakeMap(2, false, false, false)
            LegacyDungeonMap = {
                IsDungeon = function(_) return true end,
                IsRaid = function(_) return false end,
                IsHeroic = function(_) return true end,
            }
            """
        )
        with open(SOURCES, encoding="utf-8") as handle:
            self.lua.execute(handle.read())

    def compute(self, player_level, creature_level, reward,
                participant_count=1, is_raid=False, instance_map=None):
        player = self.lua.globals().MakePlayer(player_level, None)
        creature = self.lua.globals().MakeCreature(
            creature_level, reward,
            instance_map or self.lua.globals().DungeonMap)
        return self.lua.globals().ParagonRework_ComputeKillShare(
            player, creature, participant_count, is_raid)

    def make_map(self, expansion, is_dungeon, is_raid, is_heroic, map_id=0):
        return self.lua.globals().MakeMap(
            expansion, is_dungeon, is_raid, is_heroic, map_id)

    def test_instance_multiplier_matrix(self):
        cases = (
            ("TBC heroic dungeon", 1, True, False, True, 1250),
            ("WotLK heroic dungeon", 2, True, False, True, 1500),
            ("TBC raid", 1, True, True, False, 2000),
            ("TBC raid ignores impossible heroic flag", 1, True, True, True, 2000),
            ("WotLK normal raid", 2, True, True, False, 2500),
            ("WotLK heroic raid", 2, True, True, True, 4000),
        )
        for name, expansion, dungeon, raid, heroic, expected in cases:
            with self.subTest(name=name):
                instance_map = self.make_map(
                    expansion, dungeon, raid, heroic)
                self.assertEqual(
                    expected,
                    self.compute(
                        80, 80, 1000, instance_map=instance_map),
                )

    def test_unmatched_content_remains_unscaled(self):
        cases = (
            ("world", 2, False, False, False),
            ("Classic raid", 0, True, True, False),
            ("TBC normal dungeon", 1, True, False, False),
            ("WotLK normal dungeon", 2, True, False, False),
        )
        for name, expansion, dungeon, raid, heroic in cases:
            with self.subTest(name=name):
                instance_map = self.make_map(
                    expansion, dungeon, raid, heroic)
                self.assertEqual(
                    1000,
                    self.compute(
                        80, 80, 1000, instance_map=instance_map),
                )

    def test_level_80_onyxia_overrides_stale_classic_map_expansion(self):
        onyxia = self.make_map(0, True, True, False, 249)
        self.assertEqual(
            2500,
            self.compute(80, 80, 1000, instance_map=onyxia),
        )

    def test_missing_map_expansion_api_falls_back_to_unscaled_xp(self):
        self.assertEqual(
            1000,
            self.compute(
                80, 80, 1000,
                instance_map=self.lua.globals().LegacyDungeonMap),
        )

    def test_invalid_multiplier_uses_authoritative_default(self):
        self.lua.globals().ConfigValues[
            "PARAGON_CREATURE_XP_WOTLK_HEROIC_RAID_MULTIPLIER"] = "-2"
        instance_map = self.make_map(2, True, True, True)
        self.assertEqual(
            4000,
            self.compute(80, 80, 1000, instance_map=instance_map),
        )

    def test_configured_multiplier_is_used(self):
        self.lua.globals().ConfigValues[
            "PARAGON_CREATURE_XP_TBC_RAID_MULTIPLIER"] = "1.75"
        instance_map = self.make_map(1, True, True, False)
        self.assertEqual(
            1750,
            self.compute(80, 80, 1000, instance_map=instance_map),
        )

    def test_level_72_is_full_value_for_level_80_recipient(self):
        self.assertEqual(10, self.compute(80, 72, 39, 5))

    def test_nine_levels_below_still_receives_full_value(self):
        self.assertEqual(10, self.compute(80, 71, 39, 5))

    def test_ten_levels_below_receives_flat_half_before_group_share(self):
        self.assertEqual(5, self.compute(80, 70, 39, 5))

    def test_native_no_xp_creature_stays_zero(self):
        heroic_raid = self.make_map(2, True, True, True)
        self.assertEqual(
            0,
            self.compute(80, 72, 0, 5, True, heroic_raid),
        )

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

    def test_raid_share_has_no_party_bonus_after_content_multiplier(self):
        tbc_raid = self.make_map(1, True, True, False)
        wotlk_normal = self.make_map(2, True, True, False)
        wotlk_heroic = self.make_map(2, True, True, True)
        self.assertEqual(200, self.compute(80, 72, 1000, 10, True, tbc_raid))
        self.assertEqual(100, self.compute(80, 72, 1000, 25, True, wotlk_normal))
        self.assertEqual(160, self.compute(80, 72, 1000, 25, True, wotlk_heroic))

    def test_gray_penalty_composes_before_group_share(self):
        wotlk_heroic = self.make_map(2, True, True, True)
        self.assertEqual(
            200,
            self.compute(80, 70, 1000, 10, True, wotlk_heroic),
        )

    def test_heroic_dungeon_group_examples(self):
        tbc_heroic = self.make_map(1, True, False, True)
        wotlk_heroic = self.make_map(2, True, False, True)
        self.assertEqual(
            350,
            self.compute(80, 72, 1000, 5, False, tbc_heroic),
        )
        self.assertEqual(
            420,
            self.compute(80, 72, 1000, 5, False, wotlk_heroic),
        )

    def test_instance_share_receives_personal_bonus_once_at_mediator_boundary(self):
        wotlk_heroic = self.make_map(2, True, False, True)
        share = self.compute(80, 72, 1000, 5, False, wotlk_heroic)
        self.assertEqual(420, share)

        self.lua.execute(
            """
            function RegisterPlayerEvent(_, _) end
            ParagonMountSpells = {}
            ParagonCompanionSpells = {}
            ParagonCodex_ExperiencePercent = function(_) return 10 end
            PersonalBonusParagon = { GetLevel = function(_) return 1 end }
            """
        )
        with open(COLLECTION_XP, encoding="utf-8") as handle:
            self.lua.execute(handle.read())

        handler = self.lua.globals().mediator_handlers["OnExperienceCalculated"]
        result = handler(
            self.lua.globals().MakePlayer(80, None),
            self.lua.globals().PersonalBonusParagon,
            1,
            share,
        )
        self.assertEqual(462, result[1])


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
            "GetExpansion",
            "map->GetEntry()->Expansion()",
            "&LuaMap::GetExpansion",
            "Acore::XP::BaseGain",
            "ModExperience",
            "ModHealth",
            "CREATURE_FLAG_EXTRA_NO_XP",
            "participantCount",
            "isRaid",
        ):
            self.assertIn(required, text)

    def test_instance_multiplier_values_are_exact_in_both_install_paths(self):
        for path in (DEFAULT_CONFIG, ANNIVERSARY_CONFIG):
            with self.subTest(path=path), open(path, encoding="utf-8") as handle:
                text = handle.read()
            for field, value in INSTANCE_MULTIPLIERS.items():
                self.assertRegex(
                    text,
                    re.compile(
                        r"\('" + re.escape(field) + r"',\s*'"
                        + re.escape(value) + r"'\)"),
                )
            self.assertNotIn("PARAGON_CREATURE_XP_TBC_HEROIC_RAID", text)


if __name__ == "__main__":
    unittest.main()
