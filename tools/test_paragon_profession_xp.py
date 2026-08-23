import os
import re
import unittest

try:
    from lupa.lua52 import LuaRuntime
except ImportError:  # pragma: no cover - reported as skipped by unittest
    LuaRuntime = None


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, "serverside", "paragon", "paragon_hook.lua")
CONSTANT = os.path.join(ROOT, "serverside", "paragon", "paragon_constant.lua")
SCHEMA = os.path.join(ROOT, "sql", "02_create_tables.sql")
DEFAULT_CONFIG = os.path.join(ROOT, "sql", "04_insert_default_config.sql")
ANNIVERSARY_CONFIG = os.path.join(ROOT, "sql", "05_apply_anniversary_config.sql")
EXAMPLE_CONFIG = os.path.join(ROOT, "sql", "11-13-2026_Example_Data.sql")

# Mirrors AzerothCore's IsProfessionSkill for the WotLK client: eleven primary
# professions plus the three secondary professions.
PROFESSION_SKILLS = {
    129,  # First Aid
    164,  # Blacksmithing
    165,  # Leatherworking
    171,  # Alchemy
    182,  # Herbalism
    185,  # Cooking
    186,  # Mining
    197,  # Tailoring
    202,  # Engineering
    333,  # Enchanting
    356,  # Fishing
    393,  # Skinning
    755,  # Jewelcrafting
    773,  # Inscription
}


@unittest.skipUnless(LuaRuntime, "lupa.lua52 is required for Lua behavior tests")
class ParagonProfessionXPTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute(
            """
            SkillExperienceOverride = nil
            Config = { experience = { skill = {} } }
            function Config:GetByField(field)
                if field == "MINIMUM_LEVEL_FOR_PARAGON_XP" then return "80" end
                if field == "UNIVERSAL_SKILL_EXPERIENCE" then return "50" end
                return nil
            end
            function Config:GetCreatureExperience(_) return nil end
            function Config:GetAchievementExperience(_) return nil end
            function Config:GetSkillExperience(_) return SkillExperienceOverride end
            function Config:GetQuestExperience(_) return nil end

            package.preload["paragon_class"] = function() return {} end
            package.preload["paragon_config"] = function() return Config end
            package.preload["paragon_repository"] = function() return {} end
            package.preload["paragon_constant"] = function()
                return { STATISTICS = {} }
            end

            AwardedXP = {}
            Mediator = {}
            function Mediator.On(name, params)
                if name == "OnUpdatePlayerExperience" then
                    table.insert(AwardedXP, params.arguments[3])
                end
                if params.defaults then
                    return table.unpack(params.defaults)
                end
                return nil
            end

            RegisteredPlayerEvents = {}
            function RegisterPlayerEvent(event_id, callback)
                RegisteredPlayerEvents[event_id] = callback
            end
            function RegisterServerEvent(_, _) end
            function RegisterClientRequests(_) end

            Paragon = {
                GetLevel = function(_) return 1 end,
                GetPoints = function(_) return 0 end,
                GetExperience = function(_) return 0 end,
                GetExperienceForNextLevel = function(_) return 30000 end,
            }
            Player = {
                GetData = function(_, key)
                    if key == "Paragon" then return Paragon end
                    return nil
                end,
                SetData = function(_, _, _) end,
                GetLevel = function(_) return 80 end,
                IsPlayerBot = function(_) return false end,
                SendServerResponse = function(_, _, _, _) end,
            }

            function ResetAwards()
                AwardedXP = {}
            end
            function SetSkillExperienceOverride(value)
                SkillExperienceOverride = value
            end
            function AwardCount()
                return #AwardedXP
            end
            function AwardAmount(index)
                return AwardedXP[index]
            end
            """
        )
        with open(HOOK, encoding="utf-8") as handle:
            self.hook = self.lua.execute(handle.read())

    def award_for(self, skill_id, old_value, new_value, step=6):
        self.lua.globals().ResetAwards()
        self.hook.OnPlayerSkillUpdate(
            62,
            self.lua.globals().Player,
            skill_id,
            old_value,
            450,
            step,
            new_value,
        )
        count = self.lua.globals().AwardCount()
        return [self.lua.globals().AwardAmount(i) for i in range(1, count + 1)]

    def test_exact_wotlk_profession_allowlist_awards_one_tick_each(self):
        # The WotLK SkillLine IDs fit below 1000. Exercising the full ID space
        # proves this is an allowlist, rather than sampling only a few rejected
        # weapon/riding skills.
        for skill_id in range(1, 1001):
            with self.subTest(skill_id=skill_id):
                expected = [50] if skill_id in PROFESSION_SKILLS else []
                self.assertEqual(expected, self.award_for(skill_id, 100, 101))

    def test_weapon_defense_riding_and_lockpicking_never_award(self):
        excluded = {
            43: "Swords",
            95: "Defense",
            633: "Lockpicking",
            762: "Riding",
        }
        for skill_id, name in excluded.items():
            with self.subTest(skill=name):
                self.assertEqual([], self.award_for(skill_id, 100, 105))

    def test_multi_point_profession_gain_scales_base_xp_by_delta(self):
        self.assertEqual([250], self.award_for(164, 100, 105))

    def test_per_skill_override_also_scales_by_actual_points(self):
        self.lua.globals().SetSkillExperienceOverride(80)
        self.assertEqual([160], self.award_for(164, 100, 102))

    def test_non_increase_events_do_not_award(self):
        self.assertEqual([], self.award_for(164, 100, 100))
        self.assertEqual([], self.award_for(164, 105, 100))


class ParagonProfessionXPConfigContractTests(unittest.TestCase):
    def read(self, path):
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def test_shipped_configs_set_fifty_base_xp(self):
        pattern = re.compile(
            r"\('UNIVERSAL_SKILL_EXPERIENCE',\s*'(?P<value>\d+)'\)"
        )
        for path in (
            CONSTANT,
            DEFAULT_CONFIG,
            ANNIVERSARY_CONFIG,
            EXAMPLE_CONFIG,
        ):
            with self.subTest(path=os.path.relpath(path, ROOT)):
                match = pattern.search(self.read(path))
                self.assertIsNotNone(match)
                self.assertEqual("50", match.group("value"))

    def test_skill_override_schema_defaults_to_fifty(self):
        table_pattern = re.compile(
            r"paragon_config_experience_skill.*?"
            r"`experience`\s+INT(?:\(11\))?\s+NOT NULL\s+DEFAULT\s+50",
            re.DOTALL | re.IGNORECASE,
        )
        for path in (CONSTANT, SCHEMA):
            with self.subTest(path=os.path.relpath(path, ROOT)):
                self.assertRegex(self.read(path), table_pattern)

    def test_anniversary_upgrade_changes_existing_schema_default(self):
        upgrade_pattern = re.compile(
            r"ALTER\s+TABLE\s+`acore_ale`\."
            r"`paragon_config_experience_skill`.*?"
            r"MODIFY\s+COLUMN\s+`experience`.*?DEFAULT\s+50",
            re.DOTALL | re.IGNORECASE,
        )
        self.assertRegex(self.read(ANNIVERSARY_CONFIG), upgrade_pattern)


if __name__ == "__main__":
    unittest.main()
