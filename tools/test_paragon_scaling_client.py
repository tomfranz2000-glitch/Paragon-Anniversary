import math
import os
import re
import unittest

try:
    from lupa.lua51 import LuaRuntime
except ImportError:  # pragma: no cover - reported as skipped by unittest
    LuaRuntime = None


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON = os.path.join(
    ROOT, "clientside", "Interface", "AddOns", "Paragon", "Paragon")
SPIRIT_TOOLTIP = os.path.join(ADDON, "Paragon_SpiritTooltip.lua")
SCALING_LEVEL = os.path.join(ADDON, "Paragon_ScalingLevel.lua")
CODEX = os.path.join(ADDON, "Paragon_Codex.lua")


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


RATING_FACTORS = {
    1: 1.0759524,
    2: 1.1576737,
    3: 1.2456018,
    4: 1.3402085,
    5: 1.4420001,
    6: 1.5515238,
    7: 1.6693660,
    8: 1.7961585,
}

MANA_FACTORS = {
    1: 1.0529148,
    2: 1.1085202,
    3: 1.1668161,
    4: 1.2286995,
    5: 1.2935725,
    6: 1.3617339,
    7: 1.4337818,
    8: 1.5094170,
}

AGI_DELTAS = {
    "WARRIOR": (.0012, .0025, .0039, .0056, .0072, .0088, .0106, .0127),
    "DEATHKNIGHT": (
        .0012, .0025, .0039, .0056, .0072, .0088, .0106, .0127),
    "PALADIN": (.0014, .0030, .0048, .0066, .0083, .0107, .0129, .0154),
    "HUNTER": (.0009, .0019, .0030, .0041, .0053, .0067, .0081, .0096),
    "ROGUE": (.0009, .0019, .0030, .0041, .0053, .0067, .0081, .0096),
    "SHAMAN": (.0009, .0019, .0030, .0041, .0053, .0067, .0081, .0096),
    "DRUID": (.0009, .0019, .0030, .0041, .0053, .0067, .0081, .0096),
    "PRIEST": (.0015, .0030, .0048, .0065, .0084, .0107, .0128, .0152),
    "MAGE": (.0013, .0031, .0046, .0066, .0085, .0107, .0133, .0155),
    "WARLOCK": (.0014, .0031, .0047, .0066, .0089, .0111, .0132, .0157),
}

INT_DELTAS = (.0005, .0010, .0015, .0021, .0027, .0033, .0041, .0048)

# One reachable reward-track/Timeless combination for every total reduction.
TOTAL_TO_SOURCES = {
    1: (0, 1),
    2: (0, 2),
    3: (0, 3),
    4: (4, 0),
    5: (5, 0),
    6: (5, 1),
    7: (5, 2),
    8: (5, 3),
}


MOCK_FRAME_XML = r'''
format = string.format
floor = math.floor
strupper = string.upper

CR_DEFENSE_SKILL = 2
CR_DODGE = 3
CR_PARRY = 4
CR_BLOCK = 5
CR_HIT_MELEE = 6
CR_HIT_RANGED = 7
CR_HIT_SPELL = 8
CR_CRIT_MELEE = 9
CR_CRIT_RANGED = 10
CR_CRIT_SPELL = 11
CR_CRIT_TAKEN_MELEE = 15
CR_CRIT_TAKEN_RANGED = 16
CR_CRIT_TAKEN_SPELL = 17
CR_HASTE_MELEE = 18
CR_HASTE_RANGED = 19
CR_HASTE_SPELL = 20
CR_EXPERTISE = 24
CR_ARMOR_PENETRATION = 25

DODGE_PARRY_BLOCK_PERCENT_PER_DEFENSE = 0.04
RESILIENCE_CRIT_CHANCE_TO_DAMAGE_REDUCTION_MULTIPLIER = 2.2
RESILIENCE_CRIT_CHANCE_TO_CONSTANT_DAMAGE_REDUCTION_MULTIPLIER = 2.0
ARMOR_PER_AGILITY = 2
MANA_PER_INTELLECT = 15

DEFENSE = "Defense"
DEFAULT_STATDEFENSE_TOOLTIP =
    "DEF raw=%d skill=%d avoid=%.6f hit=%.6f"
CR_DODGE_TOOLTIP = "DODGE raw=%d bonus=%.7f"
CR_PARRY_TOOLTIP = "PARRY raw=%d bonus=%.7f"
CR_BLOCK_TOOLTIP = "BLOCK raw=%d bonus=%.7f value=%d"
RESILIENCE_TOOLTIP = "RES crit=%.7f damage=%.7f constant=%.7f"
CR_HASTE_RATING_TOOLTIP = "HASTE raw=%d bonus=%.7f"
SPELL_HASTE_TOOLTIP = "SPELL_HASTE bonus=%.7f"
CR_CRIT_MELEE_TOOLTIP = "MELEE_CRIT raw=%d bonus=%.7f"
CR_CRIT_RANGED_TOOLTIP = "RANGED_CRIT raw=%d bonus=%.7f"
CR_EXPERTISE_TOOLTIP = "EXPERTISE reduction=%s raw=%d skill=%d"
CR_HIT_MELEE_TOOLTIP = "MELEE_HIT level=%d bonus=%.7f arp=%d pct=%.7f"
CR_HIT_RANGED_TOOLTIP = "RANGED_HIT level=%d bonus=%.7f arp=%d pct=%.7f"
CR_HIT_SPELL_TOOLTIP = "SPELL_HIT level=%d bonus=%.7f pen=%d/%d"
DEFAULT_STAT2_TOOLTIP = "AGI crit=%.6f armor=%d"
DEFAULT_STAT4_TOOLTIP = "INT mana=%d crit=%.6f"
DEFAULT_STAT5_TOOLTIP = "SPI health=%d"
MANA_REGEN_FROM_SPIRIT = "SPI mana=%d"
STAT_ATTACK_POWER = "AP %d"
PET_BONUS_TOOLTIP_INTELLECT = "PET %d"

Hooks = {}
function hooksecurefunc(name, callback)
    if not Hooks[name] then
        Hooks[name] = {}
    end
    table.insert(Hooks[name], callback)
end

local hookedFunctions = {
    "PaperDollFrame_SetRating",
    "PaperDollFrame_SetDefense",
    "PaperDollFrame_SetDodge",
    "PaperDollFrame_SetParry",
    "PaperDollFrame_SetBlock",
    "PaperDollFrame_SetResilience",
    "PaperDollFrame_SetAttackSpeed",
    "PaperDollFrame_SetRangedAttackSpeed",
    "PaperDollFrame_SetSpellHaste",
    "PaperDollFrame_SetMeleeCritChance",
    "PaperDollFrame_SetRangedCritChance",
    "PaperDollFrame_SetExpertise",
    "PaperDollFrame_SetStat",
    "UIParagon_OnReceiveRewardTrack",
    "UIParagon_OnClientReceiveLevel",
}
for _, name in ipairs(hookedFunctions) do
    _G[name] = function() end
end

function RunHooks(name, ...)
    for _, callback in ipairs(Hooks[name] or {}) do
        callback(...)
    end
end

function MakeFrame(name)
    local frame = { name = name, tooltip2 = "stock" }
    function frame:GetName()
        return self.name
    end
    local text = { value = nil }
    function text:SetText(value)
        self.value = value
    end
    frame.statText = text
    _G[name .. "StatText"] = text
    return frame
end

MockRatingBonus = {}
MockRating = {}
MockDefaultRatingBonus = 10.0
MockDefaultRating = 73
MockDefenseBase = 400
MockDefenseModifier = 0
MockArmorPenetration = 7.0
MockMaxRatingBonus = 33.0
MockOffhandSpeed = nil
MockClass = "PALADIN"
MockStat = 100
MockAttackPower = 0
MockHasMana = true
MockPetBonus = 0
MockHealthRegenFromSpirit = 2.9
MockManaRegenFromSpirit = 1.2

function GetCombatRatingBonus(index)
    return MockRatingBonus[index] or MockDefaultRatingBonus
end
function GetCombatRating(index)
    return MockRating[index] or MockDefaultRating
end
function UnitDefense()
    return MockDefenseBase, MockDefenseModifier
end
OriginalGetCombatRatingBonus = GetCombatRatingBonus
OriginalUnitDefense = UnitDefense

function GetArmorPenetration()
    return MockArmorPenetration
end
function GetSpellPenetration()
    return 20
end
function UnitLevel()
    return 80
end
function PaperDollFormatStat(_, base, positive, negative, frame, text)
    frame.formattedBase = base
    frame.formattedPositive = positive
    frame.formattedNegative = negative
    text:SetText(math.max(0, base + positive + negative))
end
function GetShieldBlock()
    return 100
end
function GetMaxCombatRatingBonus()
    return MockMaxRatingBonus
end
function UnitAttackSpeed()
    return 2.0, MockOffhandSpeed
end
function GetExpertisePercent()
    return 4.25, 3.50
end
function UnitClass()
    return "Localized Class", MockClass
end
function UnitStat()
    return MockStat, MockStat, 0, 0
end
function GetCritChanceFromAgility()
    return 5.0
end
function GetAttackPowerForStat()
    return MockAttackPower
end
function UnitHasMana()
    return MockHasMana
end
function GetSpellCritChanceFromIntellect()
    return 3.0
end
function ComputePetBonus()
    return MockPetBonus
end
function GetUnitHealthRegenRateFromSpirit()
    return MockHealthRegenFromSpirit
end
function GetUnitManaRegenRateFromSpirit()
    return MockManaRegenFromSpirit
end

RefreshCount = 0
PaperDollFrame = { shown = false }
function PaperDollFrame:IsShown()
    return self.shown
end
function PaperDollFrame_UpdateStats()
    RefreshCount = RefreshCount + 1
end

function SetScaling(trackReduction, timelessRank, empowered, includeDefinitions)
    ParagonCodexData = { ranks = { [51] = timelessRank } }
    if includeDefinitions ~= false then
        ParagonCodexData.defs = {
            { id = 51, kind = "scaling", per = 1, cap = 3 },
        }
    end

    ParagonRewardTrackData = { currentLevel = 2000, milestones = {} }
    if trackReduction >= 2 then
        table.insert(ParagonRewardTrackData.milestones, {
            level = 700,
            rewards = { { value = "SCALING_LEVEL", amount = 2 } },
        })
    end
    if trackReduction >= 4 then
        table.insert(ParagonRewardTrackData.milestones, {
            level = 925,
            rewards = { { value = "SCALING_LEVEL_2", amount = 2 } },
        })
    end
    if trackReduction >= 5 then
        table.insert(ParagonRewardTrackData.milestones, {
            level = 1350,
            rewards = { { value = "SCALING_LEVEL_3", amount = 1 } },
        })
    end
    if empowered then
        table.insert(ParagonRewardTrackData.milestones, {
            level = 600,
            rewards = { { value = "SPIRIT_REGEN", amount = 3 } },
        })
    end
end
'''


@unittest.skipUnless(LuaRuntime, "lupa.lua51 is required for client Lua tests")
class ParagonScalingClientTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute(MOCK_FRAME_XML)
        # Preserve the real TOC order: Spirit registers its stat hook before
        # ScalingLevel defines the shared reduction helper and its own hook.
        self.lua.execute(read(SPIRIT_TOOLTIP))
        self.lua.execute(read(SCALING_LEVEL))
        self.globals = self.lua.globals()

    def set_scaling(self, track, rank, empowered=False, definitions=True):
        self.globals.SetScaling(track, rank, empowered, definitions)

    def set_total(self, reduction, empowered=False):
        track, rank = TOTAL_TO_SOURCES[reduction]
        self.set_scaling(track, rank, empowered)

    def frame(self, name="TestFrame"):
        return self.globals.MakeFrame(name)

    def run_hooks(self, name, frame=None, *args):
        if frame is None:
            self.globals.RunHooks(name, *args)
        else:
            self.globals.RunHooks(name, frame, *args)

    def test_all_reward_track_and_timeless_combinations_are_summed(self):
        for track in (0, 2, 4, 5):
            for rank in range(4):
                with self.subTest(track=track, timeless_rank=rank):
                    self.set_scaling(track, rank)
                    actual = self.globals.ParagonScalingUnlocked()
                    expected = track + rank
                    if expected == 0:
                        self.assertIs(actual, False)
                    else:
                        self.assertEqual(expected, actual)

        # State can precede definitions during login; node 51 remains usable.
        self.set_scaling(2, 3, definitions=False)
        self.assertEqual(5, self.globals.ParagonScalingUnlocked())

        # In Codex definitions cap=0 means uncapped, not a zero-rank node.
        self.lua.execute(r'''
            ParagonRewardTrackData = { currentLevel = 0, milestones = {} }
            ParagonCodexData = {
                ranks = { [99] = 4 },
                defs = {
                    { id = 99, kind = "scaling", per = 2, cap = 0 },
                },
            }
        ''')
        self.assertEqual(8, self.globals.ParagonScalingUnlocked())

    def test_every_active_paperdoll_surface_is_post_hooked(self):
        expected = {
            "PaperDollFrame_SetRating",
            "PaperDollFrame_SetDefense",
            "PaperDollFrame_SetDodge",
            "PaperDollFrame_SetParry",
            "PaperDollFrame_SetBlock",
            "PaperDollFrame_SetResilience",
            "PaperDollFrame_SetAttackSpeed",
            "PaperDollFrame_SetRangedAttackSpeed",
            "PaperDollFrame_SetSpellHaste",
            "PaperDollFrame_SetMeleeCritChance",
            "PaperDollFrame_SetRangedCritChance",
            "PaperDollFrame_SetExpertise",
            "PaperDollFrame_SetStat",
            "UIParagon_OnReceiveRewardTrack",
            "UIParagon_OnClientReceiveLevel",
        }
        hooks = self.globals.Hooks
        self.assertEqual(expected, set(hooks.keys()))
        for name in expected - {"PaperDollFrame_SetStat"}:
            self.assertEqual(1, len(hooks[name]), name)
        self.assertEqual(2, len(hooks["PaperDollFrame_SetStat"]))

    def test_rating_factor_exists_for_every_reachable_total(self):
        for reduction, factor in RATING_FACTORS.items():
            with self.subTest(reduction=reduction):
                self.set_total(reduction)
                frame = self.frame("Dodge%d" % reduction)
                self.run_hooks("PaperDollFrame_SetDodge", frame)
                match = re.search(r"bonus=([0-9.]+)", frame.tooltip2)
                self.assertIsNotNone(match)
                self.assertAlmostEqual(
                    10.0 * factor, float(match.group(1)), places=6)

    def test_all_active_rating_hooks_rebuild_their_local_values(self):
        self.set_total(2)
        factor = RATING_FACTORS[2]

        for index, prefix in (
                (self.globals.CR_HIT_MELEE, "MELEE_HIT"),
                (self.globals.CR_HIT_RANGED, "RANGED_HIT"),
                (self.globals.CR_HIT_SPELL, "SPELL_HIT")):
            with self.subTest(rating_index=index):
                frame = self.frame("Hit%d" % index)
                self.run_hooks("PaperDollFrame_SetRating", frame, index)
                self.assertTrue(frame.tooltip2.startswith(prefix))
                self.assertIn("bonus=%.7f" % (10.0 * factor), frame.tooltip2)
        self.assertIn("pen=20/20", frame.tooltip2)

        expected = {
            "PaperDollFrame_SetDodge": "DODGE",
            "PaperDollFrame_SetParry": "PARRY",
            "PaperDollFrame_SetBlock": "BLOCK",
            "PaperDollFrame_SetResilience": "RES",
            "PaperDollFrame_SetAttackSpeed": "HASTE",
            "PaperDollFrame_SetRangedAttackSpeed": "HASTE",
            "PaperDollFrame_SetSpellHaste": "SPELL_HASTE",
            "PaperDollFrame_SetMeleeCritChance": "MELEE_CRIT",
            "PaperDollFrame_SetRangedCritChance": "RANGED_CRIT",
        }
        for hook, prefix in expected.items():
            with self.subTest(hook=hook):
                frame = self.frame(hook)
                self.run_hooks(hook, frame)
                self.assertTrue(frame.tooltip2.startswith(prefix))

        self.globals.MockOffhandSpeed = 1.8
        frame = self.frame("Expertise")
        self.run_hooks("PaperDollFrame_SetExpertise", frame)
        self.assertIn("reduction=4.25% / 3.50%", frame.tooltip2)
        self.assertIn("raw=73", frame.tooltip2)
        self.assertIn("skill=%d" % math.floor(10.0 * factor), frame.tooltip2)

    def test_defense_preserves_non_rating_modifier_and_floors_each_bonus(self):
        self.set_total(2)
        stock_bonus = 14.841929267333
        self.globals.MockRatingBonus[self.globals.CR_DEFENSE_SKILL] = stock_bonus
        # UnitDefense contains floor(stock bonus) plus two non-rating skill.
        self.globals.MockDefenseModifier = math.floor(stock_bonus) + 2

        frame = self.frame("Defense")
        self.run_hooks("PaperDollFrame_SetDefense", frame)

        corrected_rating = stock_bonus * RATING_FACTORS[2]
        expected_modifier = (
            math.floor(stock_bonus) + 2
            - math.floor(stock_bonus)
            + math.floor(corrected_rating)
        )
        self.assertEqual(400, frame.formattedBase)
        self.assertEqual(expected_modifier, frame.formattedPositive)
        self.assertEqual(0, frame.formattedNegative)
        self.assertEqual(400 + expected_modifier, frame.statText.value)
        self.assertIn("raw=73", frame.tooltip2)
        self.assertIn("skill=%d" % math.floor(corrected_rating), frame.tooltip2)
        expected_avoidance = expected_modifier * 0.04
        self.assertIn("avoid=%.6f" % expected_avoidance, frame.tooltip2)
        self.assertIn("hit=%.6f" % expected_avoidance, frame.tooltip2)

    def test_all_six_agility_delta_groups_cover_all_totals(self):
        self.assertEqual(6, len(set(AGI_DELTAS.values())))
        self.globals.MockStat = 1000
        for class_name, deltas in AGI_DELTAS.items():
            self.globals.MockClass = class_name
            for reduction, delta in enumerate(deltas, 1):
                with self.subTest(class_name=class_name, reduction=reduction):
                    self.set_total(reduction)
                    frame = self.frame("Agility")
                    self.run_hooks("PaperDollFrame_SetStat", frame, 2)
                    expected = 5.0 + 1000 * delta
                    self.assertEqual(
                        "AGI crit=%.6f armor=2000" % expected,
                        frame.tooltip2,
                    )

    def test_intellect_deltas_and_pet_line_cover_all_totals(self):
        self.globals.MockStat = 1000
        self.globals.MockPetBonus = 12
        for reduction, delta in enumerate(INT_DELTAS, 1):
            with self.subTest(reduction=reduction):
                self.set_total(reduction)
                frame = self.frame("Intellect")
                self.run_hooks("PaperDollFrame_SetStat", frame, 4)
                expected_crit = 3.0 + 1000 * delta
                self.assertEqual(
                    ["INT mana=14720 crit=%.6f" % expected_crit, "PET 12"],
                    frame.tooltip2.splitlines(),
                )

    def test_spirit_scaling_works_with_and_without_empowered_spirit(self):
        for reduction, factor in MANA_FACTORS.items():
            for empowered, multiplier in ((False, 1), (True, 3)):
                with self.subTest(
                        reduction=reduction, empowered=empowered):
                    self.set_total(reduction, empowered)
                    frame = self.frame("Spirit")
                    self.run_hooks("PaperDollFrame_SetStat", frame, 5)
                    expected_health = math.floor(2.9 * multiplier)
                    expected_mana = math.floor(1.2 * factor * multiplier * 5)
                    self.assertEqual(
                        "SPI health=%d\nSPI mana=%d"
                        % (expected_health, expected_mana),
                        frame.tooltip2,
                    )

        self.set_scaling(0, 0, empowered=False)
        frame = self.frame("UnscaledSpirit")
        self.run_hooks("PaperDollFrame_SetStat", frame, 5)
        self.assertEqual("stock", frame.tooltip2)

    def test_redraw_occurs_only_for_a_visible_paperdoll(self):
        self.assertEqual(0, self.globals.RefreshCount)
        self.globals.ParagonScalingRefreshPaperDoll()
        self.assertEqual(0, self.globals.RefreshCount)

        self.globals.PaperDollFrame.shown = True
        self.globals.ParagonScalingRefreshPaperDoll()
        self.assertEqual(1, self.globals.RefreshCount)
        self.run_hooks("UIParagon_OnReceiveRewardTrack")
        self.run_hooks("UIParagon_OnClientReceiveLevel")
        self.assertEqual(3, self.globals.RefreshCount)

        self.globals.PaperDollFrame = None
        self.globals.ParagonScalingRefreshPaperDoll()
        self.assertEqual(3, self.globals.RefreshCount)

        codex = read(CODEX)
        for function_name in (
                "ParagonCodex_OnState", "ParagonCodex_OnDefinitions"):
            match = re.search(
                r"function\s+%s\b(.*?)(?=\nfunction\s+|\Z)"
                % function_name,
                codex,
                flags=re.DOTALL,
            )
            self.assertIsNotNone(match)
            self.assertIn("ParagonScalingRefreshPaperDoll()", match.group(1))

    def test_codex_refreshes_hovered_tooltip_and_exposes_holy_resistance(self):
        codex = read(CODEX)
        on_enter = re.search(
            r"local function Node_OnEnter\b(.*?)(?=\nlocal function)",
            codex,
            flags=re.DOTALL,
        )
        refresh = re.search(
            r"local function Refresh\(\)(.*?)(?=\nend\n\n---)",
            codex,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(on_enter)
        self.assertIsNotNone(refresh)
        self.assertIn('UnitResistance("player", 1)', on_enter.group(1))
        self.assertIn("def.id == 30 or def.id == 31", on_enter.group(1))
        self.assertIn("RefreshOpenTooltip()", refresh.group(1))
        self.assertIn('SetScript("OnLeave", Node_OnLeave)', codex)

        # Execute the actual tooltip helpers in isolation so this verifies
        # ownership/leave behavior and the second UnitResistance return,
        # rather than only matching source text.
        helper_start = codex.index("local function RankOf")
        helper_end = codex.index("\nlocal function Node_OnClick", helper_start)
        helper_source = codex[helper_start:helper_end]
        tooltip_lua = LuaRuntime(unpack_returned_tuples=True)
        tooltip_lua.execute(r'''
            ParagonCodexData = {
                ranks = { [30] = 1, [31] = 3 },
                byId = {},
            }
            function UnitClass() return "Paladin", "PALADIN", 2 end
            function UnitResistance() return 7, 42, 35, 0 end
            function SendClientRequest() end
            RESISTANCE1_NAME = "Holy Resistance"
            GameTooltip = { lines = {}, renders = 0, shown = false }
            function GameTooltip:SetOwner(owner) self.owner = owner end
            function GameTooltip:GetOwner() return self.owner end
            function GameTooltip:ClearLines() self.lines = {} end
            function GameTooltip:AddLine(value)
                table.insert(self.lines, tostring(value))
            end
            function GameTooltip:Show()
                self.shown = true
                self.renders = self.renders + 1
            end
            function GameTooltip:Hide() self.shown = false end
            function GameTooltip:IsShown() return self.shown end
        ''' + helper_source + r'''
            TestNodeOnEnter = Node_OnEnter
            TestNodeOnLeave = Node_OnLeave
            TestRefreshOpenTooltip = RefreshOpenTooltip
            TestFrame = {
                codexDef = {
                    id = 30, name = "Prismatic Ward", kind = "resist",
                    per = 1, cap = 25, cost = 5,
                    desc = "+1 all resistances per rank.",
                },
            }
        ''')
        tg = tooltip_lua.globals()

        tg.TestNodeOnEnter(tg.TestFrame)
        self.assertTrue(tooltip_lua.eval(
            "GameTooltip:GetOwner() == TestFrame and GameTooltip:IsShown()"))
        lines = tooltip_lua.eval("table.concat(GameTooltip.lines, '\\n')")
        self.assertIn("Rank 1 / 25", lines)
        self.assertIn("Current Holy Resistance: 42", lines)
        self.assertNotIn("Current Holy Resistance: 7", lines)

        tooltip_lua.execute("ParagonCodexData.ranks[30] = 2")
        tg.TestRefreshOpenTooltip()
        lines = tooltip_lua.eval("table.concat(GameTooltip.lines, '\\n')")
        self.assertIn("Rank 2 / 25", lines)
        self.assertEqual(2, tg.GameTooltip.renders)

        # Another tooltip owner blocks the redraw.
        tooltip_lua.execute(
            "GameTooltip.owner = {}; ParagonCodexData.ranks[30] = 3")
        tg.TestRefreshOpenTooltip()
        self.assertEqual(2, tg.GameTooltip.renders)

        # Leaving clears the tracked node, not just the visible tooltip.
        tooltip_lua.execute("GameTooltip.owner = TestFrame")
        tg.TestNodeOnLeave(tg.TestFrame)
        tooltip_lua.execute("GameTooltip.shown = true")
        tg.TestRefreshOpenTooltip()
        self.assertEqual(2, tg.GameTooltip.renders)

    def test_blizzard_conversion_apis_keep_their_original_identity(self):
        self.assertTrue(self.lua.eval(
            "GetCombatRatingBonus == OriginalGetCombatRatingBonus"))
        self.assertTrue(self.lua.eval("UnitDefense == OriginalUnitDefense"))


if __name__ == "__main__":
    unittest.main()
