import os
import unittest

try:
    from lupa.lua52 import LuaRuntime
except ImportError:  # pragma: no cover - reported as skipped by unittest
    LuaRuntime = None


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCES = os.path.join(
    ROOT, "serverside", "paragon", "modules", "paragon_rework_sources.lua")
POINTS = os.path.join(
    ROOT,
    "serverside",
    "paragon",
    "modules",
    "paragon_rework_data_achievement_points.lua",
)


@unittest.skipUnless(LuaRuntime, "lupa.lua52 is required for Lua behavior tests")
class ParagonAchievementXPTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute(
            """
            Config = {
                experience = {
                    creature = {},
                    quest = {},
                    achievement = { [19002] = 424242 },
                }
            }
            function Config:GetByField(field)
                if field == "PARAGON_ACHIEVEMENT_POINT_XP" then return "1000" end
                return nil
            end
            package.preload["paragon_config"] = function() return Config end

            function RegisterMediatorEvent(_, _) end

            WorldAchievementPoints = {
                [19001] = 10,
                [19002] = 10,
                [19301] = 25,
                [19304] = 50,
                [19999] = 0,
            }
            WorldQueryCounts = {}
            function WorldDBQuery(sql)
                local id = tonumber(string.match(sql, "ID%s*=%s*(%d+)"))
                assert(id, "achievement query did not contain a numeric ID: " .. sql)
                WorldQueryCounts[id] = (WorldQueryCounts[id] or 0) + 1
                local points = WorldAchievementPoints[id]
                if points == nil then
                    return nil
                end
                return {
                    GetUInt32 = function(_, column)
                        assert(column == 0)
                        return points
                    end,
                }
            end

            ParagonReworkData_QuestXP = {}
            """
        )
        with open(POINTS, encoding="utf-8") as handle:
            self.lua.execute(handle.read())
        with open(SOURCES, encoding="utf-8") as handle:
            self.lua.execute(handle.read())

    def value(self, achievement_id):
        return self.lua.globals().ParagonRework_AchievementValue(achievement_id)

    def query_count(self, achievement_id):
        return self.lua.globals().WorldQueryCounts[achievement_id] or 0

    def test_committed_stock_points_map_wins_without_query(self):
        self.assertEqual(10000, self.value(6))
        self.assertEqual(0, self.query_count(6))

    def test_custom_dungeon_uses_world_dbc_points(self):
        self.assertEqual(10000, self.value(19001))

    def test_custom_meta_uses_world_dbc_points(self):
        self.assertEqual(25000, self.value(19301))

    def test_custom_capstone_uses_world_dbc_points(self):
        self.assertEqual(50000, self.value(19304))

    def test_zero_point_and_unknown_rows_award_zero(self):
        self.assertEqual(0, self.value(19999))
        self.assertEqual(0, self.value(19998))

    def test_world_dbc_hits_and_misses_are_cached(self):
        for achievement_id, expected in ((19001, 10000), (19999, 0), (19998, 0)):
            self.assertEqual(expected, self.value(achievement_id))
            self.assertEqual(expected, self.value(achievement_id))
            self.assertEqual(1, self.query_count(achievement_id))

    def test_explicit_xp_override_wins_without_world_query(self):
        self.assertEqual(424242, self.value(19002))
        self.assertEqual(0, self.query_count(19002))


if __name__ == "__main__":
    unittest.main()
