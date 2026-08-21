--[[
    Paragon Rework: Loremaster's Ledger (milestone 600)

    Nine bonuses keyed to the character's REWARDED QUEST COUNT (native
    Player:GetRewardedQuestCount — live in-memory set, non-repeatable
    quests only, so dailies cannot farm the counter). Per-character by
    design: each alt writes its own ledger. Tier value:

        value = rate * max(0, count - threshold)

    Structural twin of paragon_achievement_bonus.lua — same reconcile,
    same suspense contract (only unlocked tiers are pushed), same
    single-XP-modifier integration (ParagonQuestXP_Percent consumed by
    paragon_collection_xp.lua; party shares inherit automatically).

    Count timing (adversarial review finding, fixed): the count is read
    LIVE on every use — GetRewardedQuestCount is m_RewardedQuests.size(),
    O(1), so unlike the achievement module there is deliberately NO cache
    (a cache goes stale here: event 54 fires at OBJECTIVES-complete,
    minutes before the hub turn-in that actually inserts into the set,
    and RewardQuest itself fires no hookable event — even the paragon
    hook's own quest XP rides event 54). Live reads make the XP factor
    always-exact by construction; the applied stats and tooltip catch up
    within one 10s safety tick of any turn-in (ilvl-module ticker
    pattern), with event 54, level changes and login as fast paths.

    Client: pushes { count, tiers = { {label, amount, xp?}... }, next }
    over CSMH prefix "ParagonQuest"; locked tiers never leave the server.
]]

local Constant = require("paragon_constant")

local MILESTONE_LEVEL = 400 -- track reorder 2026-08-18 (was 600)
local PREFIX = "ParagonQuest"
local APPLIED_KEY = "ParagonQuestApplied"
local TICK_MS = 10000

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

-- The whole design in one table; rates are the user's final tuning
-- (2026-08-18): XP tier untouched, stats halved from the first draft,
-- primary nerfed to 0.1, stamina capstone at 2000 instead of a second
-- XP tier (too many quests exist beyond 2000 for +%XP to stay sane).
local TIERS = {
    { threshold = 0,    xp = true, rate = 0.05,
      label = "Paragon Experience earned" },
    { threshold = 250,  primary = true, rate = 0.1 },
    { threshold = 500,  mods = { "ARMOR" }, rate = 1,
      label = "Armor" },
    { threshold = 750,  ratings = { "DODGE" }, rate = 0.05,
      label = "Dodge Rating" },
    { threshold = 1000, ratings = { "PARRY" }, rate = 0.05,
      label = "Parry Rating" },
    { threshold = 1250, ratings = { "DEFENSE_SKILL" }, rate = 0.075,
      label = "Defense Rating" },
    { threshold = 1500, ratings = { "ARMOR_PENETRATION" }, rate = 0.1,
      label = "Armor Penetration Rating" },
    { threshold = 1750, ratings = { "BLOCK" }, rate = 0.075,
      label = "Block Rating" },
    { threshold = 2000, mods = { "STAT_STAMINA" }, rate = 0.2,
      label = "Stamina" },
}

-- always live: O(1) set size, never cached (see header). NOTE the Lua
-- binding name: ALE registers the core's GetRewardedQuestCount as
-- Player:GetCompletedQuestsCount (LuaFunctions.cpp:493)
local function Count(player)
    return player:GetCompletedQuestsCount()
end

local function Owed(player, paragon)
    -- account-wide paragon: benefits require character level 80
    return paragon and paragon:GetLevel() >= MILESTONE_LEVEL
        and player:GetLevel() >= 80
end

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
function ParagonQuestXP_Percent(player, paragon)
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
        -- stat mods unchanged but the XP tier's display moves with every
        -- quest: refresh bookkeeping + tooltip without touching stats
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
-- SAFETY TICK (turn-ins fire no event; ilvl-module ticker pattern —
-- defined BEFORE the lifecycle handlers that close over EnsureTicker)
-- ============================================================================

local tickers = {}

local function OnTick(eventId, delay, repeats, player)
    local ok, err = pcall(function()
        Reconcile(player, player:GetData("Paragon"), true)
    end)
    if not ok then
        print("[Paragon] quest bonus tick error: " .. tostring(err))
    end
end

local function EnsureTicker(player)
    local guid = player:GetGUIDLow()
    if not tickers[guid] then
        tickers[guid] = player:RegisterEvent(OnTick, TICK_MS, 0)
    end
end

RegisterPlayerEvent(4, function(event, player)
    tickers[player:GetGUIDLow()] = nil
end)

-- ============================================================================
-- LIFECYCLE
-- ============================================================================

RegisterMediatorEvent("OnAfterUpdatePlayerStatistics", function(player, paragon, apply)
    local ok, err = pcall(function()
        if player and paragon then
            Reconcile(player, paragon, apply)
            if apply and Owed(player, paragon) then
                EnsureTicker(player)
            end
        end
    end)
    if not ok then
        print("[Paragon] quest bonus statistics error: " .. tostring(err))
    end
end)

RegisterMediatorEvent("OnParagonLevelChanged", function(player, paragon)
    local ok, err = pcall(function()
        if player and paragon then
            Reconcile(player, paragon, true)
            if Owed(player, paragon) then
                EnsureTicker(player)
            end
        end
    end)
    if not ok then
        print("[Paragon] quest bonus level-change error: " .. tostring(err))
    end
end)

-- Fast path only (fires at objectives-complete, which for auto-complete
-- quests is the same beat as the turn-in); the safety tick above is what
-- guarantees hub turn-ins land within 10s
RegisterPlayerEvent(54, function(event, player, quest)
    local ok, err = pcall(function()
        Reconcile(player, player:GetData("Paragon"), true)
    end)
    if not ok then
        print("[Paragon] quest bonus complete-event error: " .. tostring(err))
    end
end)

RegisterMediatorEvent("OnAfterClientLoadRequest", function(player, paragon)
    local ok, err = pcall(function()
        if player then
            PushState(player, player:GetData(APPLIED_KEY))
        end
    end)
    if not ok then
        print("[Paragon] quest bonus state-push error: " .. tostring(err))
    end
end)

print("[Paragon] Rework: quest bonus module loaded")
