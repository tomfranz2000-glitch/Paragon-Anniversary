-- Paragon milestone 600 (Empowered Spirit): the character sheet's actual
-- Mana Regen numbers come from server-sent fields and already show the
-- tripled values — but the Spirit stat's tooltip flavor line is computed
-- CLIENT-side from the stock formula (GetUnit*RegenRateFromSpirit) and
-- would understate by 3x. This rebuilds that one tooltip line, exactly the
-- way FrameXML's PaperDollFrame_SetStat builds it (statIndex 5 branch),
-- with the milestone multiplier applied. Keep MULT in sync with spell
-- 1900036 (+200% = x3) in Tools/paragon_client_patch.py.

local MULT = 3

-- Same live reward-track scan the dual-enchant popup uses: a later change
-- to the milestone level needs no addon edit.
local function SpiritRegenUnlocked()
    local data = ParagonRewardTrackData
    if not data or type(data.milestones) ~= "table" then
        return false
    end
    for _, milestone in ipairs(data.milestones) do
        if type(milestone) == "table" and type(milestone.rewards) == "table" then
            for _, reward in ipairs(milestone.rewards) do
                if type(reward) == "table" and reward.value == "SPIRIT_REGEN" then
                    return (tonumber(data.currentLevel) or 0)
                        >= (tonumber(milestone.level) or math.huge)
                end
            end
        end
    end
    return false
end

-- Effective-level scaling (reward track plus Timeless Body): spirit->MANA
-- regen converts by the gtRegenMPPerSpt ratio for the SUMMED reduction
-- ParagonScalingUnlocked() reports. Timeless Body unlocks before Empowered
-- Spirit, so scaling must also work with a plain x1 spirit multiplier. The
-- health tables are flat from levels 72 through 80 and need no level factor.
local SCALING_MANA_FACTOR = {
    [1] = 1.0529148,
    [2] = 1.1085202,
    [3] = 1.1668161,
    [4] = 1.2286995,
    [5] = 1.2935725,
    [6] = 1.3617339,
    [7] = 1.4337818,
    [8] = 1.5094170,
}

hooksecurefunc("PaperDollFrame_SetStat", function(statFrame, statIndex)
    if statIndex ~= 5 then
        return
    end
    local spiritMult = SpiritRegenUnlocked() and MULT or 1
    local reduction = ParagonScalingUnlocked and ParagonScalingUnlocked()
    local manaFactor = reduction and SCALING_MANA_FACTOR[reduction] or 1
    if spiritMult == 1 and manaFactor == 1 then
        return
    end

    -- DEFAULT_STAT5_TOOLTIP uses %d, matching stock positive-number
    -- truncation. Do not round up here.
    local health = floor(GetUnitHealthRegenRateFromSpirit("player") * spiritMult)
    statFrame.tooltip2 = format(_G["DEFAULT_STAT5_TOOLTIP"], health)
    if UnitHasMana("player") then
        local manaMult = spiritMult * manaFactor
        local regen = floor(GetUnitManaRegenRateFromSpirit("player")
            * manaMult * 5.0)
        statFrame.tooltip2 = statFrame.tooltip2 .. "\n" .. format(MANA_REGEN_FROM_SPIRIT, regen)
    end
end)
