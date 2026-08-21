-- Paragon milestone 375 (Empowered Spirit): the character sheet's actual
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

-- Milestones 500/800 (scaling level): spirit->MANA regen converts by the
-- gtRegenMPPerSpt ratio for the SUMMED reduction ParagonScalingUnlocked()
-- reports, on top of the x3 (the health table is flat at these levels, so
-- the health line keeps plain MULT). Keyed like the rating factors.
local SCALING_MANA_FACTOR = { [2] = 1.1085, [4] = 1.2287, [5] = 1.293572 }

hooksecurefunc("PaperDollFrame_SetStat", function(statFrame, statIndex)
    if statIndex ~= 5 or not SpiritRegenUnlocked() then
        return
    end
    local health = floor(GetUnitHealthRegenRateFromSpirit("player") * MULT + 0.5)
    statFrame.tooltip2 = format(_G["DEFAULT_STAT5_TOOLTIP"], health)
    if UnitHasMana("player") then
        local manaMult = MULT
        local reduction = ParagonScalingUnlocked and ParagonScalingUnlocked()
        if reduction and SCALING_MANA_FACTOR[reduction] then
            manaMult = MULT * SCALING_MANA_FACTOR[reduction]
        end
        local regen = floor(GetUnitManaRegenRateFromSpirit("player") * manaMult * 5.0)
        statFrame.tooltip2 = statFrame.tooltip2 .. "\n" .. format(MANA_REGEN_FROM_SPIRIT, regen)
    end
end)
