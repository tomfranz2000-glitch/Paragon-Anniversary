--[[
    Paragon Rework: achievement ladder (milestone 475)

    Eleven bonuses unlocked by TOTAL achievement count (Feats of Strength
    excluded — GetCompletedAchievementsCount default), each scaling only
    with the achievements earned PAST its own unlock threshold:

        value = rate * max(0, count - threshold)

    Thresholds run 0..1000 in steps of 100. The two XP tiers (0: +0.1%/ach,
    1000: +1%/ach) are NOT applied here: the single-XP-modifier rule keeps
    every experience factor in paragon_collection_xp.lua, which folds in
    the global ParagonAchievementXP_Percent exposed below (party shares
    inherit it through ParagonCollectionXP_Factor automatically, and
    achievement-source XP itself stays flat as with the collection tiers).
    The nine stat tiers ride the ilvl-attunement application pattern
    (flat unit mods + combat ratings, session bookkeeping, diff-gated).

    Count updates live via PLAYER_EVENT_ON_ACHIEVEMENT_COMPLETE (45); no
    safety ticker is needed — the count changes through no other path.

    Client: pushes { count, tiers = { {label, amount, xp?}... }, next }
    over CSMH prefix "ParagonAchieve". ONLY UNLOCKED tiers are sent — the
    locked ones never leave the server, so the tooltip can tease "another
    scaling boost at N" without spoiling what it is (user spec).
]]

local Constant = require("paragon_constant")

local MILESTONE_LEVEL = 300 -- track reorder 2026-08-18 (was 475)
local PREFIX = "ParagonAchieve"
local APPLIED_KEY = "ParagonAchieveApplied"
local COUNT_KEY = "ParagonAchieveCount"

--- Cached achievement count: GetCompletedAchievementsCount walks the whole
--- completed-achievement store (Feats-of-Strength filter) and the XP factor
--- below runs on EVERY XP gain — per party member on kills — so the walk
--- must not. Invalidated by the event-45 handler, exactly like the
--- collection module's Counts().
local function Count(player)
    local cached = player:GetData(COUNT_KEY)
    if cached ~= nil then
        return cached
    end
    local count = player:GetCompletedAchievementsCount()
    player:SetData(COUNT_KEY, count)
    return count
end

-- Class primary stat for the threshold-100 tier (mirrors the track's
-- placeholder philosophy; hybrid classes get their placeholder stat)
local PRIMARY_STAT = {
    [1]  = { stat = "STAT_STRENGTH",  label = "Strength" },
    [2]  = { stat = "STAT_STRENGTH",  label = "Strength" },
    [3]  = { stat = "STAT_AGILITY",   label = "Agility" },
    [4]  = { stat = "STAT_AGILITY",   label = "Agility" },
    [5]  = { stat = "STAT_INTELLECT", label = "Intellect" },
    [6]  = { stat = "STAT_STRENGTH",  label = "Strength" },
    [7]  = { stat = "STAT_INTELLECT", label = "Intellect" },
    [8]  = { stat = "STAT_INTELLECT", label = "Intellect" },
    [9]  = { stat = "STAT_INTELLECT", label = "Intellect" },
    [11] = { stat = "STAT_INTELLECT", label = "Intellect" },
}

local ALL_RESISTANCES = {
    "RESISTANCE_HOLY", "RESISTANCE_FIRE", "RESISTANCE_NATURE",
    "RESISTANCE_FROST", "RESISTANCE_SHADOW", "RESISTANCE_ARCANE",
}

-- The whole design in one table. rate = per achievement past threshold.
-- xp tiers -> collection module factor; mods -> UNIT_MODS flat; ratings ->
-- combat ratings. Tune freely; restart-only.
local TIERS = {
    { threshold = 0,    xp = true, rate = 0.1,
      label = "Paragon Experience earned" },
    { threshold = 100,  primary = true, rate = 0.25 },
    { threshold = 200,  mods = { "STAT_STAMINA" }, rate = 0.25,
      label = "Stamina" },
    { threshold = 300,  mods = { "HEALTH" }, rate = 2.5,
      label = "Health" },
    { threshold = 400,  mods = { "ATTACK_POWER" }, rate = 0.75,
      label = "Attack Power" },
    { threshold = 500,  ratings = { "EXPERTISE" }, rate = 0.125,
      label = "Expertise Rating" },
    { threshold = 600,  ratings = { "HIT_MELEE", "HIT_RANGED", "HIT_SPELL" }, rate = 0.2,
      label = "Hit Rating" },
    { threshold = 700,  ratings = { "CRIT_MELEE", "CRIT_RANGED", "CRIT_SPELL" }, rate = 0.25,
      label = "Critical Strike Rating" },
    { threshold = 800,  ratings = { "HASTE_MELEE", "HASTE_RANGED", "HASTE_SPELL" }, rate = 0.375,
      label = "Haste Rating" },
    { threshold = 900,  mods = ALL_RESISTANCES, rate = 0.25,
      label = "Resistance to all magic schools" },
    { threshold = 1000, xp = true, rate = 1.0,
      label = "Paragon Experience earned beyond 1000" },
}

local function Owed(player, paragon)
    -- account-wide paragon: benefits require character level 80
    return paragon and paragon:GetLevel() >= MILESTONE_LEVEL
        and player:GetLevel() >= 80
end

--- Full current entitlement:
---   mods    flattened stat/rating applications (floored, non-zero only)
---   tiers   display rows for every UNLOCKED tier (XP rows unfloored)
---   next    the lowest still-locked threshold (nil when all 11 are open)
local function ComputeState(player)
    local count = Count(player)
    local mods, tiers, nextThreshold = {}, {}, nil
    for _, tier in ipairs(TIERS) do
        if count >= tier.threshold then
            local span = count - tier.threshold
            local label = tier.label
            if tier.xp then
                tiers[#tiers + 1] = { label = label, amount = tier.rate * span, xp = true }
            else
                local amount = math.floor(tier.rate * span)
                local keys, rating = tier.mods, false
                if tier.primary then
                    local primary = PRIMARY_STAT[player:GetClass()]
                    keys, label = { primary.stat }, primary.label
                elseif tier.ratings then
                    keys, rating = tier.ratings, true
                end
                for _, key in ipairs(keys) do
                    if amount ~= 0 then
                        mods[#mods + 1] = { key = key, amount = amount, rating = rating }
                    end
                end
                tiers[#tiers + 1] = { label = label, amount = amount }
            end
        elseif not nextThreshold then
            nextThreshold = tier.threshold
        end
    end
    return { count = count, mods = mods, tiers = tiers, next = nextThreshold }
end

--- Summed XP percent of the unlocked xp tiers; consumed by
--- paragon_collection_xp.lua inside ParagonCollectionXP_Factor.
function ParagonAchievementXP_Percent(player, paragon)
    if not Owed(player, paragon) then
        return 0
    end
    local count = Count(player)
    local percent = 0
    for _, tier in ipairs(TIERS) do
        if tier.xp and count >= tier.threshold then
            percent = percent + tier.rate * (count - tier.threshold)
        end
    end
    return percent
end

local function SameMods(a, b)
    if not a or not b or #a.mods ~= #b.mods then
        return false
    end
    for i, mod in ipairs(a.mods) do
        local other = b.mods[i]
        if mod.key ~= other.key or mod.amount ~= other.amount
                or mod.rating ~= other.rating then
            return false
        end
    end
    return true
end

-- Same application paths as the track's rewards (flat, application 0)
local function ApplyMods(player, state, apply)
    for _, mod in ipairs(state.mods) do
        if mod.rating then
            player:ApplyRatingMod(Constant.STATISTICS.COMBAT_RATING[mod.key], mod.amount, apply)
        else
            player:HandleStatFlatModifier(Constant.STATISTICS.UNIT_MODS[mod.key], 0, mod.amount, apply)
        end
    end
end

local function PushState(player, state)
    if state then
        player:SendServerResponse(PREFIX, 1, {
            count = state.count, tiers = state.tiers, next = state.next })
    else
        player:SendServerResponse(PREFIX, 1, { count = 0 })
    end
end

--- Swaps the applied mods to the current entitlement (no-op when equal).
local function Reconcile(player, paragon, apply)
    local want = nil
    if apply and Owed(player, paragon) then
        want = ComputeState(player)
    end

    local applied = player:GetData(APPLIED_KEY)
    if applied == nil and want == nil then
        return
    end
    if applied and want and SameMods(applied, want) then
        -- stat mods unchanged, but the display can still move (the XP
        -- tiers scale with EVERY achievement, stats only with floored
        -- steps): refresh bookkeeping + tooltip without touching stats
        if applied.count ~= want.count then
            player:SetData(APPLIED_KEY, want)
            PushState(player, want)
        end
        return
    end

    if applied then
        ApplyMods(player, applied, false)
    end
    if want then
        ApplyMods(player, want, true)
    end
    player:SetData(APPLIED_KEY, want)
    PushState(player, want)
end

-- ============================================================================
-- LIFECYCLE
-- ============================================================================

RegisterMediatorEvent("OnAfterUpdatePlayerStatistics", function(player, paragon, apply)
    local ok, err = pcall(function()
        if player and paragon then
            Reconcile(player, paragon, apply)
        end
    end)
    if not ok then
        print("[Paragon] achievement bonus statistics error: " .. tostring(err))
    end
end)

RegisterMediatorEvent("OnParagonLevelChanged", function(player, paragon)
    local ok, err = pcall(function()
        if player and paragon then
            Reconcile(player, paragon, true)
        end
    end)
    if not ok then
        print("[Paragon] achievement bonus level-change error: " .. tostring(err))
    end
end)

-- Live update the moment an achievement pops (also fires per achievement
-- during AccountBound's login sync — Reconcile's diff gate keeps that cheap)
RegisterPlayerEvent(45, function(event, player, achievement)
    local ok, err = pcall(function()
        player:SetData(COUNT_KEY, nil)
        Reconcile(player, player:GetData("Paragon"), true)
    end)
    if not ok then
        print("[Paragon] achievement bonus complete-event error: " .. tostring(err))
    end
end)

RegisterMediatorEvent("OnAfterClientLoadRequest", function(player, paragon)
    local ok, err = pcall(function()
        if player then
            PushState(player, player:GetData(APPLIED_KEY))
        end
    end)
    if not ok then
        print("[Paragon] achievement bonus state-push error: " .. tostring(err))
    end
end)

print("[Paragon] Rework: achievement bonus module loaded")
