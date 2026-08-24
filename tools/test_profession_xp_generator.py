import importlib.util
import json
import os
import pathlib
import re
import sys
import unittest

try:
    from lupa.lua52 import LuaRuntime
except ImportError:  # pragma: no cover - reported as skipped by unittest
    LuaRuntime = None


ROOT = pathlib.Path(__file__).resolve().parents[1]
GENERATOR_PATH = ROOT / "tools" / "gen_profession_xp.py"
AUDIT_PATH = ROOT / "tools" / "generated" / "profession_xp_audit.json"
LUA_PATH = (
    ROOT
    / "serverside"
    / "paragon"
    / "modules"
    / "paragon_profession_data.lua"
)


def load_generator():
    spec = importlib.util.spec_from_file_location("paragon_profession_generator", GENERATOR_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


generator = load_generator()


class ProfessionXPModelTests(unittest.TestCase):
    def test_rank_tiers_and_starting_weights_are_stable(self):
        boundaries = {
            0: 1,
            75: 1,
            76: 2,
            150: 2,
            151: 3,
            225: 3,
            226: 4,
            300: 4,
            301: 5,
            375: 5,
            376: 6,
            450: 6,
        }
        for rank, expected in boundaries.items():
            with self.subTest(rank=rank):
                self.assertEqual(expected, generator.rank_tier(rank))
        self.assertEqual((1.0, 1.5, 2.0, 2.5, 3.0, 4.0), generator.TIER_WEIGHTS)

    def test_scarcity_and_cooldown_multipliers_are_bounded(self):
        self.assertEqual(1.0, generator.bounded_cooldown_multiplier(0))
        self.assertLessEqual(generator.bounded_cooldown_multiplier(10**12), 2.5)
        self.assertEqual(1.0, generator.bounded_spawn_multiplier(0))
        self.assertLessEqual(generator.bounded_spawn_multiplier(1), 1.5)
        self.assertGreaterEqual(generator.bounded_spawn_multiplier(10**9), 1.0)

    def test_vendor_material_value_is_positive_and_conservatively_bounded(self):
        item = generator.Item(
            1, "Vendor reagent", 7, 0, 1, 0, 1, 10000, 0, 1, 1,
            0, 0, 0, 0, 20, (0, 0, 0, 0, 0), 0, 0, 0, 0, 0, 0, 0,
        )
        intrinsic = generator.intrinsic_item_value(item)
        value = generator.vendor_material_value(item)
        self.assertGreaterEqual(value, intrinsic * 0.25)
        self.assertLessEqual(value, intrinsic * 0.50)

    def test_loot_resolver_models_independent_groups_references_and_filters(self):
        row = generator.LootRow
        stores = {
            key: {} for key in generator.LOOT_TABLES
        }
        stores["reference"] = {
            900: [row(900, 30, 0, 100, 0, 1, 0, 2, 2)]
        }
        stores["gameobject"] = {
            100: [
                row(100, 10, 0, 50, 0, 1, 0, 1, 1),
                row(100, 20, 0, 25, 0, 1, 1, 1, 1),
                row(100, 21, 0, 0, 0, 1, 1, 1, 1),
                row(100, 0, 900, 100, 0, 1, 0, 1, 1),
                row(100, 40, 0, 100, 1, 1, 0, 1, 1),
                row(100, 41, 0, 100, 0, 1, 0, 1, 1, conditioned=True),
                row(100, 42, 0, 100, 0, 2, 0, 1, 1),
            ]
        }
        result = generator.LootResolver(stores).expected("gameobject", 100)
        self.assertAlmostEqual(0.5, result[10])
        self.assertAlmostEqual(0.25, result[20])
        self.assertAlmostEqual(0.75, result[21])
        self.assertAlmostEqual(2.0, result[30])
        self.assertNotIn(40, result)
        self.assertNotIn(41, result)
        self.assertNotIn(42, result)

    def test_gather_tier_floor_survives_tiny_generic_quest_loot_mask(self):
        row = generator.LootRow
        stores = {key: {} for key in generator.LOOT_TABLES}
        stores["gameobject"] = {
            100: [
                # A tiny incidental generic drop must not bypass the floor.
                row(100, 10, 0, 0.01, 0, 1, 0, 1, 1),
                # Model the real failure mode: the primary item is restricted.
                row(100, 20, 0, 100, 1, 1, 0, 1, 1),
            ],
            101: [row(101, 30, 0, 100, 0, 1, 0, 1, 1)],
        }
        loot = generator.LootResolver(stores)

        details = {}
        value = generator.gather_material_value(
            loot, "gameobject", 100, {10: 1.0, 20: 20.0}, 4, details
        )
        self.assertEqual(generator.TIER_WEIGHTS[3], value)
        self.assertGreater(details["rawExpectedMaterialValue"], 0)
        self.assertLess(
            details["rawExpectedMaterialValue"],
            details["professionTierMaterialFloor"],
        )
        self.assertTrue(details["tierFloorApplied"])
        self.assertEqual("profession_tier_floor", details["fallback"])

        above_floor = {}
        value = generator.gather_material_value(
            loot, "gameobject", 101, {30: 10.0}, 4, above_floor
        )
        self.assertEqual(10.0, value)
        self.assertFalse(above_floor["tierFloorApplied"])
        self.assertNotIn("fallback", above_floor)

    def test_recipe_scc_detection_excludes_both_directions_and_self_loop(self):
        def spell(spell_id, reagent, output, external=()):
            return generator.Spell(
                spell_id,
                f"spell {spell_id}",
                [(reagent, 1), *external],
                [generator.EFFECT_CREATE_ITEM, 0, 0],
                [0, 0, 0],
                [0.0, 0.0, 0.0],
                [0, 0, 0],
                [output, 0, 0],
                [0, 0, 0],
                [0, 0, 0],
                0,
                0,
                0,
                0,
                0,
            )

        recipes = [
            generator.Recipe(
                spell(1, 10, 11, ((99, 2),)),
                171,
                1,
                {11: 1.0},
                False,
                0,
            ),
            generator.Recipe(spell(2, 11, 10), 171, 1, {10: 1.0}, False, 0),
            generator.Recipe(spell(3, 12, 12), 202, 1, {12: 1.0}, False, 0),
            generator.Recipe(spell(4, 20, 21), 164, 1, {21: 1.0}, False, 0),
        ]
        self.assertEqual({0, 1, 2}, generator.cyclic_recipe_indices(recipes))
        components = generator.cyclic_recipe_components(recipes)
        self.assertEqual(frozenset({10, 11}), components[0])
        self.assertNotIn(99, components[0])
        recipes[0].cyclic = True
        recipes[0].cyclic_items = components[0]
        returned, external = generator.split_cycle_reagents(recipes[0])
        self.assertEqual([(10, 1)], returned)
        self.assertEqual([(99, 2)], external)


class GeneratedProfessionXPContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.audit = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))
        cls.lua = LUA_PATH.read_text(encoding="utf-8")

    def test_every_discovered_action_is_positive_or_explicitly_excluded(self):
        self.assertEqual([], self.audit["silentGaps"])
        totals = self.audit["totals"]
        self.assertEqual(
            totals["discovered"], totals["valued"] + totals["excluded"]
        )
        seen = set()
        for action in self.audit["actions"]:
            self.assertNotIn(action["key"], seen)
            seen.add(action["key"])
            if action["xp"] is None:
                self.assertTrue(action["reason"], action["key"])
            else:
                self.assertGreater(action["xp"], 0, action["key"])
                self.assertIsNone(action["reason"], action["key"])

    def test_time_gated_recipe_cycles_use_finite_fallbacks(self):
        fallbacks = [
            action
            for action in self.audit["actions"]
            if action.get("details", {}).get("cycleFallback")
        ]
        self.assertGreater(len(fallbacks), 0)
        for action in fallbacks:
            self.assertEqual("craft", action["kind"])
            self.assertGreater(action["xp"], 0)
            self.assertGreater(action["details"]["cooldownSeconds"], 0)
            self.assertEqual(
                "intrinsic_input_value",
                action["details"]["valuationFallback"],
            )
        for exclusion in self.audit["exclusions"]:
            if exclusion["reason"] == "cyclic_recipe":
                self.assertEqual(0, exclusion["details"]["cooldownSeconds"])

    def test_awarded_base_xp_is_hard_capped_and_raw_values_are_audited(self):
        cap = self.audit["model"]["baseXpCap"]
        self.assertEqual(5000, cap)
        capped = []
        for action in self.audit["actions"]:
            if action["xp"] is None:
                continue
            self.assertLessEqual(action["xp"], cap, action["key"])
            self.assertGreaterEqual(action["rawXp"], action["xp"], action["key"])
            if action.get("details", {}).get("capped"):
                capped.append(action)
                self.assertEqual(cap, action["xp"])
                self.assertGreater(action["details"]["uncappedXp"], cap)
        self.assertEqual(self.audit["inputs"]["cappedActions"], len(capped))
        self.assertGreater(len(capped), 0)
        self.assertIn("statisticsByProfessionTierRaw", self.audit)

    def test_special_and_quest_gathers_receive_audited_fallback_xp(self):
        fallback_actions = [
            action
            for action in self.audit["actions"]
            if action.get("details", {}).get("restrictedLootFallback")
        ]
        self.assertEqual(
            self.audit["inputs"]["restrictedLootFallbackActions"],
            len(fallback_actions),
        )
        self.assertGreater(len(fallback_actions), 0)
        for action in fallback_actions:
            self.assertIn(
                action["kind"],
                {"gather_gameobject", "gather_creature", "fishing_area", "fishing_hole"},
            )
            self.assertGreater(action["xp"], 0, action["key"])
            self.assertIsNone(action["reason"], action["key"])
        self.assertNotIn(
            "no_unconditional_material_loot", self.audit["exclusionReasons"]
        )

    def test_every_gather_audits_and_applies_the_profession_tier_floor(self):
        gather_kinds = {
            "gather_gameobject",
            "gather_creature",
            "fishing_area",
            "fishing_hole",
        }
        gathers = [
            action
            for action in self.audit["actions"]
            if action["kind"] in gather_kinds and action["xp"] is not None
        ]
        applied = []
        for action in gathers:
            details = action["details"]
            self.assertIn("rawExpectedMaterialValue", details, action["key"])
            self.assertIn("tierFloorApplied", details, action["key"])
            floor = generator.TIER_WEIGHTS[action["tier"] - 1]
            self.assertEqual(floor, details["professionTierMaterialFloor"])
            self.assertGreaterEqual(action["xp"], round(50 * floor), action["key"])
            if details["tierFloorApplied"]:
                applied.append(action)
                self.assertLess(details["materialValueBeforeTierFloor"], floor)
        self.assertEqual(
            self.audit["inputs"]["tierFloorAppliedActions"], len(applied)
        )
        self.assertGreater(len(applied), 0)

    def test_only_lossless_cycles_are_excluded_and_external_cost_is_valued(self):
        exclusions = {row["key"]: row["reason"] for row in self.audit["exclusions"]}
        for key in ("craft:28022", "craft:42615"):
            self.assertEqual("cyclic_recipe", exclusions[key])
        self.assertNotIn("craft:13240", exclusions)
        actions = {row["key"]: row for row in self.audit["actions"]}
        mortar = actions["craft:13240"]
        self.assertGreater(mortar["xp"], 0)
        self.assertTrue(mortar["details"]["cycleExternalMaterial"])
        self.assertEqual(
            "external_consumed_material", mortar["details"]["valuationFallback"]
        )
        self.assertGreater(mortar["details"]["externalConsumedMaterialValue"], 0)
        self.assertGreater(len(mortar["details"]["cyclicReturnedReagents"]), 0)
        self.assertGreater(len(mortar["details"]["externalConsumedReagents"]), 0)
        external_cycles = [
            row
            for row in self.audit["actions"]
            if row.get("details", {}).get("cycleExternalMaterial")
        ]
        self.assertEqual(
            self.audit["inputs"]["cycleExternalMaterialActions"],
            len(external_cycles),
        )
        self.assertNotIn("vendor_only_zero_material", self.audit["exclusionReasons"])
        self.assertNotIn("zero_input_material_value", self.audit["exclusionReasons"])
        self.assertGreater(self.audit["inputs"]["vendorMaterialFallbackActions"], 0)
        vendor_fallbacks = [
            action
            for action in self.audit["actions"]
            if action.get("details", {}).get("vendorMaterialFallback")
        ]
        self.assertEqual(
            self.audit["inputs"]["vendorMaterialFallbackActions"],
            len(vendor_fallbacks),
        )
        for action in vendor_fallbacks:
            self.assertGreater(action["xp"], 0, action["key"])

    def test_all_required_action_classes_are_present(self):
        expected = set(generator.ACTION)
        self.assertEqual(expected, set(self.audit["coverage"]))
        for kind in expected:
            coverage = self.audit["coverage"][kind]
            self.assertGreater(coverage["discovered"], 0, kind)
            self.assertEqual([], coverage["silentGaps"], kind)

    def test_prismatic_socket_service_crafts_are_valued(self):
        actions = {row["key"]: row for row in self.audit["actions"]}
        for spell_id in (55628, 55641):
            action = actions[f"craft:{spell_id}"]
            self.assertEqual(164, action["skill"])
            self.assertGreater(action["xp"], 0)
            self.assertIsNone(action["reason"])

    def test_unreachable_disenchant_rows_are_not_action_contexts(self):
        keys = {row["key"] for row in self.audit["actions"]}
        for item_id in (3034, 8840, 23461):
            self.assertNotIn(f"disenchant:{item_id}", keys)

    def test_summary_statistics_are_ordered_and_nonempty(self):
        self.assertTrue(self.audit["statisticsByProfessionTier"])
        for profession, tiers in self.audit["statisticsByProfessionTier"].items():
            for tier, stats in tiers.items():
                with self.subTest(profession=profession, tier=tier):
                    self.assertGreater(stats["count"], 0)
                    self.assertLessEqual(stats["p50"], stats["p95"])
                    self.assertLessEqual(stats["p95"], stats["max"])

    def test_live_source_fingerprints_are_complete(self):
        fingerprints = self.audit["source"]["dbcSha256"]
        self.assertEqual(
            {"Spell", "SkillLineAbility", "SkillLine", "Lock", "AreaTable", "Map"},
            set(fingerprints),
        )
        for digest in fingerprints.values():
            self.assertRegex(digest, r"^[0-9a-f]{64}$")
        for key in (
            "databaseSnapshotSha256",
            "overridesSha256",
            "generatorSha256",
            "snapshotSha256",
        ):
            self.assertRegex(self.audit["source"][key], r"^[0-9a-f]{64}$")

    def test_lua_module_has_stable_consumer_api_and_global_compatibility(self):
        for name, action_id in generator.ACTION.items():
            enum_name = generator.ACTION_LUA_NAMES[name]
            self.assertRegex(self.lua, rf"\b{enum_name}\s*=\s*{action_id},")
        self.assertIn("function M.Resolve(actionKind, skillId, contextId, quantity)", self.lua)
        self.assertIn("M.MAX_PER_UNIT_QUANTITY = 4", self.lua)
        self.assertIn("M.BASE_XP_CAP = 5000", self.lua)
        self.assertIn(
            "math.min(M.MAX_PER_UNIT_QUANTITY, math.max(1, math.floor(tonumber(quantity) or 1)))",
            self.lua,
        )
        self.assertIn(
            'package.loaded["paragon.modules.paragon_profession_data"] = M',
            self.lua,
        )
        self.assertIn("ParagonProfessionData = M", self.lua)
        self.assertRegex(self.lua, r"return M\s*$")

    @unittest.skipUnless(LuaRuntime, "lupa.lua52 is required for Lua execution tests")
    def test_generated_lua_dotted_require_alias_is_the_returned_module(self):
        runtime = LuaRuntime(unpack_returned_tuples=True)
        module = runtime.execute(self.lua)
        required = runtime.eval('require("paragon.modules.paragon_profession_data")')
        same_table = runtime.eval("function(left, right) return rawequal(left, right) end")
        self.assertTrue(same_table(module, required))
        self.assertTrue(
            same_table(
                module,
                runtime.eval(
                    'package.loaded["paragon.modules.paragon_profession_data"]'
                ),
            )
        )

    @unittest.skipUnless(LuaRuntime, "lupa.lua52 is required for Lua execution tests")
    def test_generated_lua_executes_and_resolves_awards_exclusions_and_caps(self):
        runtime = LuaRuntime(unpack_returned_tuples=True)
        module = runtime.execute(self.lua)
        valued = next(action for action in self.audit["actions"] if action["xp"])
        xp, metadata = module.Resolve(
            valued["action"], valued["skill"], valued["context"], 999999
        )
        self.assertEqual(valued["xp"], xp)  # generated actions are per-action
        self.assertEqual(valued["rawXp"], metadata["uncappedXP"])
        excluded = next(iter(self.audit["exclusions"]))
        xp, reason = module.Resolve(
            generator.ACTION[excluded["kind"]],
            excluded["skill"],
            excluded["context"],
            1,
        )
        self.assertIsNone(xp)
        self.assertEqual(excluded["reason"], reason)
        capped = next(
            action
            for action in self.audit["actions"]
            if action.get("details", {}).get("capped")
        )
        xp, metadata = module.Resolve(
            capped["action"], capped["skill"], capped["context"], 1
        )
        self.assertEqual(5000, xp)
        self.assertTrue(metadata["capped"])
        self.assertEqual(capped["rawXp"], metadata["uncappedXP"])

    @unittest.skipUnless(LuaRuntime, "lupa.lua52 is required for Lua execution tests")
    def test_rendered_per_unit_rows_clamp_large_quantities(self):
        sample = generator.Result(
            "gather_gameobject",
            123,
            182,
            100,
            1,
            True,
            "Sample node",
        )
        module = LuaRuntime(unpack_returned_tuples=True).execute(
            generator.render_lua([sample], "0" * 64, 5000)
        )
        xp, metadata = module.Resolve(2, 182, 123, 999999)
        self.assertEqual(400, xp)
        self.assertEqual(4, metadata["quantity"])


if __name__ == "__main__":
    unittest.main()
