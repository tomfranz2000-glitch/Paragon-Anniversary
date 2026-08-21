-- Milestone 500 cosmetic correction: the character sheet's ACTUAL percent
-- values (crit, dodge, haste...) are server-computed fields and already
-- reflect the lowered scaling level — but the paperdoll's "X Rating
-- equals Y%" flavor lines come from the CLIENT's GetCombatRatingBonus,
-- computed from its own gt tables at the character's REAL level, so they
-- would understate. At level 80 the -2 correction is UNIFORM across every
-- combat rating (one shared table curve), so wrapping the global with one
-- factor fixes every FrameXML caller at once. Keep RATING_FACTOR in sync
-- with marker 1900039's bp (Tools/paragon_client_patch.py SERVER_SPELLS).

-- gtCombatRatings ratio(80) / ratio(80 - reduction), keyed by the summed
-- reduction of every unlocked SCALING_LEVEL* milestone (700: -2, 925: -4,
-- 1350: -5). Factors are gtCombatRatings ratio(80)/ratio(80-n) -- recompute
-- from the DBC if a further scaling milestone is ever added.
local RATING_FACTOR = { [2] = 1.157674, [4] = 1.340208, [5] = 1.441893 }

--- Live reward-track scan (same pattern as the dual-enchant popup): a
--- later milestone-level change needs no addon edit. Returns the SUMMED
--- level reduction of all unlocked SCALING_LEVEL* rewards (2, 4, ...) or
--- false when none — number doubles as the old boolean. Global — the
--- spirit tooltip hook reuses it for the mana-regen factor.
function ParagonScalingUnlocked()
    local data = ParagonRewardTrackData
    if not data or type(data.milestones) ~= "table" then
        return false
    end
    local level = tonumber(data.currentLevel) or 0
    local total = 0
    for _, milestone in ipairs(data.milestones) do
        if type(milestone) == "table" and type(milestone.rewards) == "table"
            and level >= (tonumber(milestone.level) or math.huge) then
            for _, reward in ipairs(milestone.rewards) do
                if type(reward) == "table" and type(reward.value) == "string"
                    and reward.value:find("^SCALING_LEVEL") then
                    total = total + (tonumber(reward.amount) or 0)
                end
            end
        end
    end
    return total > 0 and total or false
end

-- NOTE: this deliberately does NOT replace the GetCombatRatingBonus global
-- (the first version did, and replacing a Blizzard global taints Blizzard
-- execution paths — it manifested as ADDON_ACTION_FORBIDDEN on
-- ReplaceEnchant() in the enchant popup skip). Instead, mirror the
-- spirit-tooltip approach: let FrameXML build the rating tooltip, then
-- rewrite the percent numbers in the finished string.
local function ScaleRatingText(text, factor)
    if type(text) ~= "string" then
        return text
    end
    -- CR_*_TOOLTIP strings embed the converted percent as "%.2f%%"-style
    -- numbers; scale each one (rating VALUES stay integers and unscaled,
    -- percents carry decimals, so only x.xx patterns are touched)
    return (text:gsub("(%d+%.%d+)", function(num)
        return string.format("%.2f", tonumber(num) * factor)
    end))
end

local function ScaleTooltipHook(statFrame)
    local reduction = ParagonScalingUnlocked()
    local factor = reduction and RATING_FACTOR[reduction]
    if factor and statFrame and statFrame.tooltip2 then
        statFrame.tooltip2 = ScaleRatingText(statFrame.tooltip2, factor)
    end
end

for _, fn in ipairs({ "PaperDollFrame_SetRating", "PaperDollFrame_SetDefense",
                      "PaperDollFrame_SetDodge", "PaperDollFrame_SetParry",
                      "PaperDollFrame_SetBlock", "PaperDollFrame_SetResilience" }) do
    if type(_G[fn]) == "function" then
        hooksecurefunc(fn, ScaleTooltipHook)
    end
end

-- Attribute tooltips: the Agility / Intellect CRIT lines are also
-- client-computed at the REAL level and would understate (Strength and
-- Stamina convert level-flat and truly do not change — no hook needed).
-- Only the per-point gt ratio scales, the flat class base does not, so
-- the fix is an ADDITIVE delta per stat point, not a multiply. Paladin
-- deltas keyed by the summed reduction (gt ratio at 80-r minus at 80):
--   agi:  0.0192%%/agi -> +0.0030 at -2, +0.0066 at -4
--   int:  0.0060%%/int -> +0.0010 at -2, +0.0021 at -4
local AGI_CRIT_DELTA = { [2] = 0.0030, [4] = 0.0066 }
local INT_CRIT_DELTA = { [2] = 0.0010, [4] = 0.0021 }

hooksecurefunc("PaperDollFrame_SetStat", function(statFrame, statIndex)
    if statIndex ~= 2 and statIndex ~= 4 then
        return
    end
    local reduction = ParagonScalingUnlocked()
    local agiDelta = reduction and AGI_CRIT_DELTA[reduction]
    local intDelta = reduction and INT_CRIT_DELTA[reduction]
    if not agiDelta or not intDelta then
        return
    end
    local _, effectiveStat = UnitStat("player", statIndex)
    local tooltip = _G["DEFAULT_STAT" .. statIndex .. "_TOOLTIP"]
    if statIndex == 2 then
        -- mirror of FrameXML's agility branch with the corrected crit
        local crit = GetCritChanceFromAgility("player") + effectiveStat * agiDelta
        local attackPower = GetAttackPowerForStat(statIndex, effectiveStat)
        if attackPower > 0 then
            statFrame.tooltip2 = format(STAT_ATTACK_POWER, attackPower)
                .. format(tooltip, crit, effectiveStat * ARMOR_PER_AGILITY)
        else
            statFrame.tooltip2 = format(tooltip, crit, effectiveStat * ARMOR_PER_AGILITY)
        end
    elseif UnitHasMana("player") then
        -- mirror of FrameXML's intellect branch (mana-per-int is flat and
        -- stays stock); pet-bonus lines don't apply to a paladin
        local baseInt = math.min(20, effectiveStat)
        local moreInt = effectiveStat - baseInt
        local crit = GetSpellCritChanceFromIntellect("player") + effectiveStat * intDelta
        statFrame.tooltip2 = format(tooltip, baseInt + moreInt * MANA_PER_INTELLECT, crit)
    end
end)
