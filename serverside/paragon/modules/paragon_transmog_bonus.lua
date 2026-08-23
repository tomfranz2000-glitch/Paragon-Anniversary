--[[
    Paragon Rework: Collector's Wardrobe (milestone 975)

    Nine bonuses keyed to the ACCOUNT-WIDE transmog appearance count
    (mod-transmog collection system — exactly the number the transmog
    NPC shows: distinct armor/weapon item templates, recolors counted
    separately, cross-class included via TrackUnusableItems). Tier value:

        value = rate * max(0, count - threshold)

    Structural twin of paragon_achievement_bonus.lua / _quest_bonus.lua:
    same reconcile, same suspense contract (only unlocked tiers are
    pushed; locked ones never leave the server), same single-XP-modifier
    integration (ParagonTransmogXP_Percent is consumed by
    paragon_collection_xp.lua; party shares inherit automatically).

    Count source (the one DB-backed ladder): mod-transmog keeps the
    collection in C++ memory (Transmogrification::collectionCache) with
    no Lua binding, but INSERTs custom_unlocked_appearances the moment
    an appearance unlocks (AddToDatabase) — the row exists by the time
    the "added appearance" chat line prints. Count() is therefore a
    per-account COUNT(*) over the (account_id, item_template_id)
    PRIMARY KEY: an index range scan, sub-millisecond.

    Update strategy (bots loot constantly — the query must NOT ride the
    item events): store-new-item (53: loot/mail/AH/vendor/trade/craft/
    quest turn-in all store) and equip (29: equip-direct paths) only set
    a DIRTY flag (cheap table write). The 10s safety ticker recounts
    ONLY while dirty; Reconcile then diff-gates the stat swap. Quest
    turn-ins unlock every reward CHOICE (even untaken ones), but the
    taken reward's store fires event 53 in the same beat — covered. The
    once-per-character retroactive login sweep bypasses both events; its
    rows are caught by the login count or the next dirty pickup.
]]

local Constant = require("paragon_constant")

local MILESTONE_LEVEL = 500 -- track reorder 2026-08-18 (was 975)
local PREFIX = "ParagonTransmog"
local APPLIED_KEY = "ParagonTransmogApplied"
local COUNT_KEY = "ParagonTransmogCount"
local DIRTY_KEY = "ParagonTransmogDirty"
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

local ALL_RESISTANCES = {
    "RESISTANCE_HOLY", "RESISTANCE_FIRE", "RESISTANCE_NATURE",
    "RESISTANCE_FROST", "RESISTANCE_SHADOW", "RESISTANCE_ARCANE",
}

-- Class-mapped power stat for the threshold-5000 tier (user spec: no
-- dead stats — melee classes get Attack Power, hunters Ranged Attack
-- Power, casters Spell Power). Spell power has no UNIT_MOD; it applies
-- through the dedicated ALE binding Player:SetSpellPower
-- (PlayerMethods.h -> ApplySpellPowerBonus, +damage AND +healing). Its
-- rate sits at roughly half the AP rate for point-parity (1 SP ~ 2 AP).
local CLASS_POWER = {
    [1]  = { mod = "ATTACK_POWER",        rate = 0.075, label = "Attack Power" },
    [2]  = { mod = "ATTACK_POWER",        rate = 0.075, label = "Attack Power" },
    [3]  = { mod = "ATTACK_POWER_RANGED", rate = 0.075, label = "Ranged Attack Power" },
    [4]  = { mod = "ATTACK_POWER",        rate = 0.075, label = "Attack Power" },
    [5]  = { spellpower = true,           rate = 0.04,  label = "Spell Power" },
    [6]  = { mod = "ATTACK_POWER",        rate = 0.075, label = "Attack Power" },
    [7]  = { spellpower = true,           rate = 0.04,  label = "Spell Power" },
    [8]  = { spellpower = true,           rate = 0.04,  label = "Spell Power" },
    [9]  = { spellpower = true,           rate = 0.04,  label = "Spell Power" },
    [11] = { spellpower = true,           rate = 0.04,  label = "Spell Power" },
}

-- Resilience = the three crit-taken ratings in wrath (the character
-- sheet sums them into the one displayed number)
local RESILIENCE = {
    "CRIT_TAKEN_MELEE", "CRIT_TAKEN_RANGED", "CRIT_TAKEN_SPELL",
}

-- The whole design in one table; user tuning 2026-08-18 (halved twice
-- from the first pitch, thresholds stretched to 15000 because the
-- appearance pool is ~30k templates, resistances capstone replacing the
-- second XP tier, 5000 tier class-mapped instead of flat Ranged AP).
-- MP5 / Block Value from the pitch are not appliable (no UNIT_MOD,
-- rating, or binding exists) — substituted with Spirit / Mana / Armor /
-- Health (Spirit is a real caster stat on this server: milestone 375's
-- 3x regen plus the scaling-level factors).
local TIERS = {
    { threshold = 0,     xp = true, rate = 0.01,
      label = "Paragon Experience earned" },
    { threshold = 500,   primary = true, rate = 0.025 },
    { threshold = 1500,  mods = { "STAT_SPIRIT" }, rate = 0.025,
      label = "Spirit" },
    { threshold = 3000,  mods = { "MANA" }, rate = 0.5,
      label = "Mana" },
    { threshold = 5000,  power = true },
    { threshold = 7500,  mods = { "ARMOR" }, rate = 0.25,
      label = "Armor" },
    { threshold = 10000, mods = { "HEALTH" }, rate = 0.5,
      label = "Health" },
    { threshold = 12500, ratings = RESILIENCE, rate = 0.025,
      label = "Resilience Rating" },
    { threshold = 15000, mods = ALL_RESISTANCES, rate = 0.01,
      label = "Resistance to all magic schools" },
}

--- Cached account-wide appearance count; the cache lives until an item
--- event marks it dirty and the ticker recounts (see header). The query
--- itself is the only DB touch in the module.
local function Count(player)
    local cached = player:GetData(COUNT_KEY)
    if cached ~= nil then
        return cached
    end
    local count = 0
    local result = CharDBQuery(string.format(
        "SELECT COUNT(*) FROM custom_unlocked_appearances WHERE account_id = %d",
        player:GetAccountId()))
    if result then
        count = result:GetUInt32(0)
    end
    player:SetData(COUNT_KEY, count)
    return count
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
                local rate, keys, rating, spellpower = tier.rate, tier.mods, false, false
                if tier.primary then
                    local primary = PRIMARY_STAT[player:GetClass()]
                    keys, label = { primary.stat }, primary.label
                elseif tier.power then
                    -- class-mapped power stat; the rate lives per class
                    local power = CLASS_POWER[player:GetClass()]
                    rate, label = power.rate, power.label
                    if power.spellpower then
                        keys, spellpower = { "SPELL_POWER" }, true
                    else
                        keys = { power.mod }
                    end
                elseif tier.ratings then
                    keys, rating = tier.ratings, true
                end
                local amount = math.floor(rate * span)
                for _, key in ipairs(keys) do
                    if amount ~= 0 then
                        mods[#mods + 1] = { key = key, amount = amount, rating = rating, power = spellpower }
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
function ParagonTransmogXP_Percent(player, paragon)
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
                or mod.rating ~= other.rating or mod.power ~= other.power then
            return false
        end
    end
    return true
end

local function ApplyMods(player, state, apply)
    for _, mod in ipairs(state.mods) do
        if mod.power then
            player:SetSpellPower(mod.amount, apply)
        elseif mod.rating then
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
        -- stat mods unchanged but the XP tier's display moves with
        -- every appearance: refresh bookkeeping + tooltip only
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
-- SAFETY TICK (recount only while dirty — see header; ilvl/quest-module
-- ticker pattern, defined before the lifecycle handlers that close over
-- EnsureTicker)
-- ============================================================================

local tickers = {}

local function OnTick(eventId, delay, repeats, player)
    local ok, err = pcall(function()
        if not player:GetData(DIRTY_KEY) then
            return
        end
        player:SetData(DIRTY_KEY, nil)
        player:SetData(COUNT_KEY, nil)
        Reconcile(player, player:GetData("Paragon"), true)
    end)
    if not ok then
        print("[Paragon] transmog bonus tick error: " .. tostring(err))
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
-- DIRTY MARKS (fire for every player incl. bots — keep them trivial)
-- ============================================================================

-- 53 = PLAYER_EVENT_ON_STORE_NEW_ITEM, 29 = PLAYER_EVENT_ON_EQUIP: the
-- same beats mod-transmog unlocks appearances on (the suite fork's
-- store-new-item patch makes 53 the catch-all acquisition path)
for _, eventId in ipairs({ 53, 29 }) do
    RegisterPlayerEvent(eventId, function(event, player)
        player:SetData(DIRTY_KEY, true)
    end)
end

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
        print("[Paragon] transmog bonus statistics error: " .. tostring(err))
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
        print("[Paragon] transmog bonus level-change error: " .. tostring(err))
    end
end)

RegisterMediatorEvent("OnAfterClientLoadRequest", function(player, paragon)
    local ok, err = pcall(function()
        if player then
            PushState(player, player:GetData(APPLIED_KEY))
        end
    end)
    if not ok then
        print("[Paragon] transmog bonus state-push error: " .. tostring(err))
    end
end)

print("[Paragon] Rework: transmog bonus module loaded")
