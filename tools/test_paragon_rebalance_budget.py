import importlib.util
import json
import math
import os
import sys
import unittest

try:
    from lupa.lua52 import LuaRuntime
except ImportError:  # pragma: no cover - optional local test dependency
    LuaRuntime = None


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(TOOLS, filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class ParagonRebalanceBudgetTests(unittest.TestCase):
    """Lock the approved curve and one-time reward-pool rebalance."""

    CURVE_BASE = 100_000
    CURVE_R0 = 0.029552484
    CURVE_K = 20
    TARGET_LEVEL = 2_000

    ELIGIBLE_ACHIEVEMENT_POINTS = 12_720
    ACHIEVEMENT_XP_PER_POINT = 10_000
    SKILL_MASTERY_POINTS = 12_700
    PROFESSION_XP_PER_POINT = 5_000

    @classmethod
    def setUpClass(cls):
        cls.collections = load_module(
            "paragon_collectible_budget", "paragon_collectible_xp.py")
        cls.recipes = load_module(
            "paragon_recipe_budget", "gen_recipe_rewards.py")

    @staticmethod
    def curve_costs(base, r0, k, last_level):
        costs = [base]
        for level in range(2, last_level + 1):
            costs.append(math.floor(
                costs[-1] * (1 + r0 / (1 + level / k)) + 0.5
            ))
        return costs

    def test_curve_starts_at_100k_and_preserves_level_2000_endpoint(self):
        costs = self.curve_costs(
            self.CURVE_BASE, self.CURVE_R0, self.CURVE_K,
            self.TARGET_LEVEL)
        old_costs = self.curve_costs(
            30_000, 0.0429, 20, self.TARGET_LEVEL)

        self.assertEqual(100_000, costs[0])
        self.assertEqual(1_845_119_090, sum(costs[:-1]))
        self.assertEqual(1_454_342, costs[-1])
        self.assertEqual(1_454_339, old_costs[-1])
        self.assertEqual(3, costs[-1] - old_costs[-1])

    def test_eight_catalog_pools_reflect_the_approved_rebalance(self):
        collection = {
            name: rule["budget"]
            for name, rule in self.collections.CATEGORY_RULES.items()
        }
        pools = {
            "achievements": (
                self.ELIGIBLE_ACHIEVEMENT_POINTS
                * self.ACHIEVEMENT_XP_PER_POINT
            ),
            "appearances": collection["appearance"],
            "mounts": collection["mount"],
            "companions": collection["companion"],
            "toys": collection["toy"],
            "heirlooms": 38 * self.collections.HEIRLOOM_XP,
            "skill_mastery": (
                self.SKILL_MASTERY_POINTS * self.PROFESSION_XP_PER_POINT
            ),
            "profession_recipes": self.recipes.BUDGET,
        }
        self.assertEqual(
            {
                "achievements": 127_200_000,
                "appearances": 354_984_000,
                "mounts": 187_933_000,
                "companions": 83_525_000,
                "toys": 43_500_000,
                "heirlooms": 3_800_000,
                "skill_mastery": 63_500_000,
                "profession_recipes": 140_000_000,
            },
            pools,
        )
        self.assertEqual(1_004_442_000, sum(pools.values()))

    def test_generated_recipe_audit_matches_generator_contract(self):
        path = os.path.join(TOOLS, "generated", "recipe_reward_audit.json")
        with open(path, encoding="utf-8") as handle:
            audit = json.load(handle)
        totals = audit["totals"]
        self.assertEqual(self.recipes.EXPECTED_DISCOVERED, totals["discovered"])
        self.assertEqual(self.recipes.EXPECTED_REWARDABLE, totals["rewardable"])
        self.assertEqual(self.recipes.EXPECTED_QUARANTINED, totals["quarantined"])
        self.assertEqual(self.recipes.BUDGET, totals["xp"])


@unittest.skipUnless(LuaRuntime, "lupa.lua52 is required for Lua curve tests")
class ParagonCurveRuntimeTests(unittest.TestCase):
    """Exercise the shipped Lua recurrence rather than a Python copy alone."""

    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute("""
            CurveConfig = {
                BASE_MAX_EXPERIENCE = "100000",
                PARAGON_CURVE_R0 = "0.029552484",
                PARAGON_CURVE_K = "20",
            }
            Config = {}
            function Config:GetByField(field)
                return CurveConfig[field]
            end
            package.preload["paragon_config"] = function()
                return Config
            end
            CurveHandlers = {}
            function RegisterMediatorEvent(event, callback)
                CurveHandlers[event] = callback
            end
        """)
        path = os.path.join(
            ROOT, "serverside", "paragon", "modules",
            "paragon_rework_curve.lua")
        with open(path, encoding="utf-8") as handle:
            self.lua.execute(handle.read())
        self.lua.execute("""
            function CurveTotal(last_level)
                local total = 0
                for level = 1, last_level do
                    total = total + ParagonRework_CurveCost(level)
                end
                return total
            end
        """)

    def test_runtime_curve_anchors_and_rounding(self):
        curve_cost = self.lua.globals().ParagonRework_CurveCost
        curve_total = self.lua.globals().CurveTotal

        self.assertEqual(100_000, curve_cost(1))
        self.assertEqual(1_454_342, curve_cost(2_000))
        self.assertEqual(1_845_119_090, curve_total(1_999))

    def test_runtime_curve_cache_tracks_configuration_changes(self):
        curve_cost = self.lua.globals().ParagonRework_CurveCost
        self.assertEqual(1_454_342, curve_cost(2_000))

        config = self.lua.globals().CurveConfig
        config.BASE_MAX_EXPERIENCE = "30000"
        config.PARAGON_CURVE_R0 = "0.0429"

        self.assertEqual(30_000, curve_cost(1))
        self.assertEqual(1_454_339, curve_cost(2_000))


if __name__ == "__main__":
    unittest.main()
