-- Effective stat-scaling presentation for the stock Wrath character sheet.
--
-- The server can evaluate gt-table conversions at a lower effective level,
-- but the 3.3.5 client still evaluates UnitDefense(), GetCombatRatingBonus(),
-- GetArmorPenetration(), and the attribute contribution helpers at the
-- character's real level. Final values such as dodge, parry, crit, mana
-- regeneration, and attack speed are server fields and must NOT be adjusted
-- here. This file only rebuilds the client-local display values after the
-- unmodified Blizzard functions have run.
--
-- Do not replace GetCombatRatingBonus (or any other Blizzard global). The
-- original implementation did that and tainted protected Blizzard paths.

local TIMELESS_BODY_NODE_ID = 51
local TIMELESS_BODY_MAX_RANK = 3

-- gtCombatRatings ratio(80) / ratio(80 - reduction). Every combat rating
-- shares this level curve; the tiny per-rating float differences disappear
-- far below the character sheet's precision. Current reward markers total
-- 0/2/4/5 and Timeless Body adds 0..3, so every total from 0 through 8 is
-- reachable. Values were read from the active patched 3.3.5a DBC set.
local RATING_FACTOR = {
    [1] = 1.0759524,
    [2] = 1.1576737,
    [3] = 1.2456018,
    [4] = 1.3402085,
    [5] = 1.4420001,
    [6] = 1.5515238,
    [7] = 1.6693660,
    [8] = 1.7961585,
}

local function CodexScalingReduction()
    local data = ParagonCodexData
    local ranks = type(data) == "table" and data.ranks
    if type(ranks) ~= "table" then
        return 0
    end

    -- Definitions make this automatically include a future scaling-kind node.
    -- State can arrive first during login, so retain node 51 as the fallback.
    if type(data.defs) == "table" then
        local total = 0
        local found = false
        for _, def in ipairs(data.defs) do
            if type(def) == "table" and def.kind == "scaling" then
                found = true
                local rank = tonumber(ranks[def.id] or ranks[tostring(def.id)])
                    or 0
                rank = math.max(0, math.floor(rank))
                local cap = tonumber(def.cap) or 0
                if cap > 0 then
                    rank = math.min(cap, rank)
                end
                total = total + rank * (tonumber(def.per) or 0)
            end
        end
        if found then
            return total
        end
    end

    local rank = tonumber(ranks[TIMELESS_BODY_NODE_ID]
            or ranks[tostring(TIMELESS_BODY_NODE_ID)])
        or 0
    rank = math.floor(rank or 0)
    return math.max(0, math.min(TIMELESS_BODY_MAX_RANK, rank))
end

--- Return the summed effective-level reduction currently owned by the player.
--- Reward-track markers and Timeless Body are additive on the server, so the
--- client must use the same sum. The number doubles as the old truthy return;
--- false preserves compatibility for callers when no reduction is active.
function ParagonScalingUnlocked()
    local total = CodexScalingReduction()
    local data = ParagonRewardTrackData
    if type(data) == "table" and type(data.milestones) == "table" then
        local level = tonumber(data.currentLevel) or 0
        for _, milestone in ipairs(data.milestones) do
            if type(milestone) == "table"
                and type(milestone.rewards) == "table"
                and level >= (tonumber(milestone.level) or math.huge) then
                for _, reward in ipairs(milestone.rewards) do
                    if type(reward) == "table"
                        and type(reward.value) == "string"
                        and reward.value:find("^SCALING_LEVEL") then
                        total = total + (tonumber(reward.amount) or 0)
                    end
                end
            end
        end
    end
    return total > 0 and total or false
end

local function ActiveRatingFactor()
    local reduction = ParagonScalingUnlocked()
    return reduction and RATING_FACTOR[reduction] or nil
end

local function CorrectedRatingBonus(ratingIndex, factor)
    local stock = GetCombatRatingBonus(ratingIndex)
    return factor and stock * factor or stock
end

-- UnitDefense returns an integer modifier. Preserve non-rating skill buffs
-- and replace only the stock client's truncated rating contribution.
local function CorrectedSkillModifier(modifier, factor, ...)
    local corrected = modifier
    for i = 1, select("#", ...) do
        local ratingIndex = select(i, ...)
        local stock = GetCombatRatingBonus(ratingIndex)
        corrected = corrected - math.floor(stock)
            + math.floor(stock * factor)
    end
    return corrected
end

-- GetArmorPenetration may include non-rating modifiers. Add only the delta
-- caused by rescaling CR_ARMOR_PENETRATION and retain those other modifiers.
local function CorrectedArmorPenetration(factor)
    local stockRating = GetCombatRatingBonus(CR_ARMOR_PENETRATION)
    local corrected = GetArmorPenetration() + stockRating * factor - stockRating
    return math.max(0, math.min(100, corrected))
end

local function Hook(name, callback)
    if type(_G[name]) == "function" then
        hooksecurefunc(name, callback)
    end
end

-- The only active callers of the generic rating renderer are the three Hit
-- rows. Rebuild them explicitly so the rating-derived Armor Penetration
-- percentage is corrected while flat Spell Penetration remains untouched.
Hook("PaperDollFrame_SetRating", function(statFrame, ratingIndex)
    local factor = ActiveRatingFactor()
    if not factor or not statFrame then
        return
    end
    local ratingBonus = CorrectedRatingBonus(ratingIndex, factor)
    if ratingIndex == CR_HIT_MELEE then
        statFrame.tooltip2 = format(CR_HIT_MELEE_TOOLTIP, UnitLevel("player"),
            ratingBonus, GetCombatRating(CR_ARMOR_PENETRATION),
            CorrectedArmorPenetration(factor))
    elseif ratingIndex == CR_HIT_RANGED then
        statFrame.tooltip2 = format(CR_HIT_RANGED_TOOLTIP, UnitLevel("player"),
            ratingBonus, GetCombatRating(CR_ARMOR_PENETRATION),
            CorrectedArmorPenetration(factor))
    elseif ratingIndex == CR_HIT_SPELL then
        local spellPenetration = GetSpellPenetration()
        statFrame.tooltip2 = format(CR_HIT_SPELL_TOOLTIP, UnitLevel("player"),
            ratingBonus, spellPenetration, spellPenetration)
    end
end)

-- Defense is the one active main PaperDoll value which is client-local.
-- Rebuild the row with the corrected integer rating contribution, while
-- retaining ordinary positive or negative Defense skill modifiers.
Hook("PaperDollFrame_SetDefense", function(statFrame, unit)
    local factor = ActiveRatingFactor()
    unit = unit or "player"
    if not factor or not statFrame or unit ~= "player" then
        return
    end

    local base, modifier = UnitDefense(unit)
    modifier = CorrectedSkillModifier(modifier, factor, CR_DEFENSE_SKILL)
    local posBuff = modifier > 0 and modifier or 0
    local negBuff = modifier < 0 and modifier or 0
    local text = _G[statFrame:GetName() .. "StatText"]
    PaperDollFormatStat(DEFENSE, base, posBuff, negBuff, statFrame, text)

    local ratingBonus = CorrectedRatingBonus(CR_DEFENSE_SKILL, factor)
    local defensePercent = DODGE_PARRY_BLOCK_PERCENT_PER_DEFENSE
        * ((base + modifier) - UnitLevel("player") * 5)
    defensePercent = math.max(defensePercent, 0)
    statFrame.tooltip2 = format(DEFAULT_STATDEFENSE_TOOLTIP,
        GetCombatRating(CR_DEFENSE_SKILL), math.floor(ratingBonus),
        defensePercent, defensePercent)
end)

Hook("PaperDollFrame_SetDodge", function(statFrame)
    local factor = ActiveRatingFactor()
    if factor and statFrame then
        statFrame.tooltip2 = format(CR_DODGE_TOOLTIP,
            GetCombatRating(CR_DODGE), CorrectedRatingBonus(CR_DODGE, factor))
    end
end)

Hook("PaperDollFrame_SetParry", function(statFrame)
    local factor = ActiveRatingFactor()
    if factor and statFrame then
        statFrame.tooltip2 = format(CR_PARRY_TOOLTIP,
            GetCombatRating(CR_PARRY), CorrectedRatingBonus(CR_PARRY, factor))
    end
end)

Hook("PaperDollFrame_SetBlock", function(statFrame)
    local factor = ActiveRatingFactor()
    if factor and statFrame then
        statFrame.tooltip2 = format(CR_BLOCK_TOOLTIP,
            GetCombatRating(CR_BLOCK), CorrectedRatingBonus(CR_BLOCK, factor),
            GetShieldBlock())
    end
end)

Hook("PaperDollFrame_SetResilience", function(statFrame)
    local factor = ActiveRatingFactor()
    if not factor or not statFrame then
        return
    end

    local melee = GetCombatRating(CR_CRIT_TAKEN_MELEE)
    local ranged = GetCombatRating(CR_CRIT_TAKEN_RANGED)
    local spell = GetCombatRating(CR_CRIT_TAKEN_SPELL)
    local lowestRating = CR_CRIT_TAKEN_SPELL
    if melee <= ranged and melee <= spell then
        lowestRating = CR_CRIT_TAKEN_MELEE
    elseif ranged <= spell then
        lowestRating = CR_CRIT_TAKEN_RANGED
    end

    local bonus = CorrectedRatingBonus(lowestRating, factor)
    local maxBonus = GetMaxCombatRatingBonus(lowestRating)
    statFrame.tooltip2 = format(RESILIENCE_TOOLTIP, bonus,
        math.min(
            bonus * RESILIENCE_CRIT_CHANCE_TO_DAMAGE_REDUCTION_MULTIPLIER,
            maxBonus),
        bonus * RESILIENCE_CRIT_CHANCE_TO_CONSTANT_DAMAGE_REDUCTION_MULTIPLIER)
end)

Hook("PaperDollFrame_SetAttackSpeed", function(statFrame)
    local factor = ActiveRatingFactor()
    if factor and statFrame then
        statFrame.tooltip2 = format(CR_HASTE_RATING_TOOLTIP,
            GetCombatRating(CR_HASTE_MELEE),
            CorrectedRatingBonus(CR_HASTE_MELEE, factor))
    end
end)

Hook("PaperDollFrame_SetRangedAttackSpeed", function(statFrame)
    local factor = ActiveRatingFactor()
    if factor and statFrame then
        statFrame.tooltip2 = format(CR_HASTE_RATING_TOOLTIP,
            GetCombatRating(CR_HASTE_RANGED),
            CorrectedRatingBonus(CR_HASTE_RANGED, factor))
    end
end)

Hook("PaperDollFrame_SetSpellHaste", function(statFrame)
    local factor = ActiveRatingFactor()
    if factor and statFrame then
        statFrame.tooltip2 = format(SPELL_HASTE_TOOLTIP,
            CorrectedRatingBonus(CR_HASTE_SPELL, factor))
    end
end)

Hook("PaperDollFrame_SetMeleeCritChance", function(statFrame)
    local factor = ActiveRatingFactor()
    if factor and statFrame then
        statFrame.tooltip2 = format(CR_CRIT_MELEE_TOOLTIP,
            GetCombatRating(CR_CRIT_MELEE),
            CorrectedRatingBonus(CR_CRIT_MELEE, factor))
    end
end)

Hook("PaperDollFrame_SetRangedCritChance", function(statFrame)
    local factor = ActiveRatingFactor()
    if factor and statFrame then
        statFrame.tooltip2 = format(CR_CRIT_RANGED_TOOLTIP,
            GetCombatRating(CR_CRIT_RANGED),
            CorrectedRatingBonus(CR_CRIT_RANGED, factor))
    end
end)

Hook("PaperDollFrame_SetExpertise", function(statFrame, unit)
    local factor = ActiveRatingFactor()
    if not factor or not statFrame then
        return
    end

    unit = unit or "player"
    local expertisePercent, offhandExpertisePercent = GetExpertisePercent()
    local _, offhandSpeed = UnitAttackSpeed(unit)
    local reductionText = format("%.2f", expertisePercent) .. "%"
    if offhandSpeed then
        reductionText = reductionText .. " / "
            .. format("%.2f", offhandExpertisePercent) .. "%"
    end
    statFrame.tooltip2 = format(CR_EXPERTISE_TOOLTIP, reductionText,
        GetCombatRating(CR_EXPERTISE),
        CorrectedRatingBonus(CR_EXPERTISE, factor))
end)

-- Attribute tooltips are also calculated locally. Unlike combat ratings,
-- their gt rows are class-specific. These are percentage-point deltas per
-- stat point between level 80 and level 80-reduction, extracted from the
-- active patched gtChanceToMeleeCrit / gtChanceToSpellCrit DBCs.
local AGI_WARRIOR_DK = {
    [1] = 0.0012, [2] = 0.0025, [3] = 0.0039, [4] = 0.0056,
    [5] = 0.0072, [6] = 0.0088, [7] = 0.0106, [8] = 0.0127,
}
local AGI_PALADIN = {
    [1] = 0.0014, [2] = 0.0030, [3] = 0.0048, [4] = 0.0066,
    [5] = 0.0083, [6] = 0.0107, [7] = 0.0129, [8] = 0.0154,
}
local AGI_HUNTER_ROGUE_SHAMAN_DRUID = {
    [1] = 0.0009, [2] = 0.0019, [3] = 0.0030, [4] = 0.0041,
    [5] = 0.0053, [6] = 0.0067, [7] = 0.0081, [8] = 0.0096,
}
local AGI_PRIEST = {
    [1] = 0.0015, [2] = 0.0030, [3] = 0.0048, [4] = 0.0065,
    [5] = 0.0084, [6] = 0.0107, [7] = 0.0128, [8] = 0.0152,
}
local AGI_MAGE = {
    [1] = 0.0013, [2] = 0.0031, [3] = 0.0046, [4] = 0.0066,
    [5] = 0.0085, [6] = 0.0107, [7] = 0.0133, [8] = 0.0155,
}
local AGI_WARLOCK = {
    [1] = 0.0014, [2] = 0.0031, [3] = 0.0047, [4] = 0.0066,
    [5] = 0.0089, [6] = 0.0111, [7] = 0.0132, [8] = 0.0157,
}
local AGI_CRIT_DELTA = {
    WARRIOR = AGI_WARRIOR_DK,
    DEATHKNIGHT = AGI_WARRIOR_DK,
    PALADIN = AGI_PALADIN,
    HUNTER = AGI_HUNTER_ROGUE_SHAMAN_DRUID,
    ROGUE = AGI_HUNTER_ROGUE_SHAMAN_DRUID,
    SHAMAN = AGI_HUNTER_ROGUE_SHAMAN_DRUID,
    DRUID = AGI_HUNTER_ROGUE_SHAMAN_DRUID,
    PRIEST = AGI_PRIEST,
    MAGE = AGI_MAGE,
    WARLOCK = AGI_WARLOCK,
}
local INT_CRIT_DELTA = {
    [1] = 0.0005, [2] = 0.0010, [3] = 0.0015, [4] = 0.0021,
    [5] = 0.0027, [6] = 0.0033, [7] = 0.0041, [8] = 0.0048,
}

Hook("PaperDollFrame_SetStat", function(statFrame, statIndex)
    if not statFrame or (statIndex ~= 2 and statIndex ~= 4) then
        return
    end
    local reduction = ParagonScalingUnlocked()
    if not reduction then
        return
    end

    local _, class = UnitClass("player")
    class = class and strupper(class)
    local _, effectiveStat = UnitStat("player", statIndex)
    local tooltip = _G["DEFAULT_STAT" .. statIndex .. "_TOOLTIP"]

    if statIndex == 2 then
        local classDeltas = class and AGI_CRIT_DELTA[class]
        local delta = classDeltas and classDeltas[reduction]
        if not delta then
            return
        end
        local crit = GetCritChanceFromAgility("player") + effectiveStat * delta
        local attackPower = GetAttackPowerForStat(statIndex, effectiveStat)
        if attackPower > 0 then
            statFrame.tooltip2 = format(STAT_ATTACK_POWER, attackPower)
                .. format(tooltip, crit, effectiveStat * ARMOR_PER_AGILITY)
        else
            statFrame.tooltip2 = format(tooltip, crit,
                effectiveStat * ARMOR_PER_AGILITY)
        end
    elseif UnitHasMana("player") then
        local delta = INT_CRIT_DELTA[reduction]
        if not delta then
            return
        end
        local baseInt = math.min(20, effectiveStat)
        local moreInt = effectiveStat - baseInt
        local crit = GetSpellCritChanceFromIntellect("player")
            + effectiveStat * delta
        statFrame.tooltip2 = format(tooltip,
            baseInt + moreInt * MANA_PER_INTELLECT, crit)

        -- Preserve the stock Hunter/Warlock pet contribution line; the old
        -- correction rebuilt the tooltip without it.
        local petInt = ComputePetBonus("PET_BONUS_INT", effectiveStat)
        if petInt > 0 then
            statFrame.tooltip2 = statFrame.tooltip2 .. "\r\n"
                .. format(PET_BONUS_TOOLTIP_INTELLECT, petInt)
        end
    end
end)

-- Network state can arrive while the character sheet is already open. Give
-- RewardTrack and Codex one safe redraw entrypoint so the newly known total is
-- reflected immediately without /reload or closing the window.
function ParagonScalingRefreshPaperDoll()
    if PaperDollFrame and PaperDollFrame.IsShown and PaperDollFrame:IsShown()
        and type(PaperDollFrame_UpdateStats) == "function" then
        PaperDollFrame_UpdateStats()
    end
end

Hook("UIParagon_OnReceiveRewardTrack", ParagonScalingRefreshPaperDoll)
Hook("UIParagon_OnClientReceiveLevel", ParagonScalingRefreshPaperDoll)
