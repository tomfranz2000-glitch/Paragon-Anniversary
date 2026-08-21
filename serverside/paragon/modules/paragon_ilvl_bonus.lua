--[[
    Paragon Rework: item-level attunement (milestone 375)

    Class-specific flat attributes scaling with the character's average item
    level (core Player::GetAverageItemLevel: empty slots count as zero and
    shirt/tabard/ranged/offhand are ignored, so stripping gear honestly drops
    the average). Quadratic curve so late gear jumps outweigh early ones:

        base = floor(ilvl^2 / 200)      -- 100 -> 50, 200 -> 200, 284 -> 403

    Each class entry weights that base per stat; EVERY class has one since
    2026-08-20, so this is a universal milestone. The bonus rides the
    same HandleStatFlatModifier path as the track's placeholder stats, with
    private bookkeeping (applied amounts live on the player object and die
    with the session):

    - statistics apply pass: compute + apply (remove pass strips — this also
      covers logout and the account gate's forced sub-80 remove)
    - PLAYER_EVENT_ON_EQUIP: instant recompute on any equip/swap
    - 10s per-player safety tick: unequip-to-bag fires no event
    - OnParagonLevelChanged: crossing the milestone mid-session

    Client: every change pushes { ilvl, stats } over CSMH prefix "ParagonIlvl"
    (also on the client load request) so the reward-track tooltip renders live
    numbers. Nothing is pushed as owed for sub-80/sub-250 characters, so the
    masked presentation stays consistent.
]]

local Constant = require("paragon_constant")

local MILESTONE_LEVEL = 375 -- track reorder 2026-08-18 (was 250)
local PREFIX = "ParagonIlvl"
local APPLIED_KEY = "ParagonIlvlApplied"
local TICK_MS = 10000

-- Weight of the quadratic base per stat, by class id. Every class carries the
-- same SHAPE -- primary 1.0 / Stamina 0.75 / secondary 0.5 -- so no class is
-- ahead of another on total budget; only the split differs. Stamina sits in
-- the middle for everyone because it is the one attribute all three specs of
-- every class want. The secondary is the attribute that class's OFF specs
-- build on, so a single entry serves all three (e.g. Druid: Intellect for
-- Balance/Restoration, Agility for Feral).
--
-- UNIVERSAL since 2026-08-20 (user call: "all classes have an item level, it
-- should never have been class specific in the first place"). Milestone 375
-- was the one class_rewards row whose mechanic was class-agnostic; its TRACK
-- entry is now a plain universal reward and MILESTONE_375 is gone.
--
-- Paladin keeps its original weighting: Ret and Prot build on Strength
-- (doubly so with the milestone-75 Divine Strength ranks multiplying it),
-- Intellect carries Holy (Holy Guidance converts it to spell power).
local CLASS_WEIGHTS = {
    [1]  = { -- Warrior: Arms/Fury/Prot all Strength; Agility = crit + armor
        { stat = "STAT_STRENGTH",  label = "Strength",  weight = 1.0 },
        { stat = "STAT_STAMINA",   label = "Stamina",   weight = 0.75 },
        { stat = "STAT_AGILITY",   label = "Agility",   weight = 0.5 },
    },
    [2]  = { -- Paladin
        { stat = "STAT_STRENGTH",  label = "Strength",  weight = 1.0 },
        { stat = "STAT_STAMINA",   label = "Stamina",   weight = 0.75 },
        { stat = "STAT_INTELLECT", label = "Intellect", weight = 0.5 },
    },
    [3]  = { -- Hunter: Agility for all three; Intellect = mana + Careful Aim
        { stat = "STAT_AGILITY",   label = "Agility",   weight = 1.0 },
        { stat = "STAT_STAMINA",   label = "Stamina",   weight = 0.75 },
        { stat = "STAT_INTELLECT", label = "Intellect", weight = 0.5 },
    },
    [4]  = { -- Rogue: Agility for all three; Strength = flat attack power
        { stat = "STAT_AGILITY",   label = "Agility",   weight = 1.0 },
        { stat = "STAT_STAMINA",   label = "Stamina",   weight = 0.75 },
        { stat = "STAT_STRENGTH",  label = "Strength",  weight = 0.5 },
    },
    [5]  = { -- Priest: Intellect for all three; Spirit = regen + Spiritual Guidance
        { stat = "STAT_INTELLECT", label = "Intellect", weight = 1.0 },
        { stat = "STAT_STAMINA",   label = "Stamina",   weight = 0.75 },
        { stat = "STAT_SPIRIT",    label = "Spirit",    weight = 0.5 },
    },
    [6]  = { -- Death Knight: Strength for all three; Agility = armor + dodge
        { stat = "STAT_STRENGTH",  label = "Strength",  weight = 1.0 },
        { stat = "STAT_STAMINA",   label = "Stamina",   weight = 0.75 },
        { stat = "STAT_AGILITY",   label = "Agility",   weight = 0.5 },
    },
    [7]  = { -- Shaman: Intellect for Elemental/Restoration, Agility for Enhancement
        { stat = "STAT_INTELLECT", label = "Intellect", weight = 1.0 },
        { stat = "STAT_STAMINA",   label = "Stamina",   weight = 0.75 },
        { stat = "STAT_AGILITY",   label = "Agility",   weight = 0.5 },
    },
    [8]  = { -- Mage: Intellect for all three; Spirit = regen + Arcane Meditation
        { stat = "STAT_INTELLECT", label = "Intellect", weight = 1.0 },
        { stat = "STAT_STAMINA",   label = "Stamina",   weight = 0.75 },
        { stat = "STAT_SPIRIT",    label = "Spirit",    weight = 0.5 },
    },
    [9]  = { -- Warlock: Intellect for all three; Spirit = regen + Fel Armor
        { stat = "STAT_INTELLECT", label = "Intellect", weight = 1.0 },
        { stat = "STAT_STAMINA",   label = "Stamina",   weight = 0.75 },
        { stat = "STAT_SPIRIT",    label = "Spirit",    weight = 0.5 },
    },
    [11] = { -- Druid: Intellect for Balance/Restoration, Agility for Feral
        { stat = "STAT_INTELLECT", label = "Intellect", weight = 1.0 },
        { stat = "STAT_STAMINA",   label = "Stamina",   weight = 0.75 },
        { stat = "STAT_AGILITY",   label = "Agility",   weight = 0.5 },
    },
}

local function CurveBase(ilvl)
    return math.floor((ilvl * ilvl) / 200)
end

local function Owed(player, paragon)
    -- account-wide paragon: benefits require character level 80
    return paragon and paragon:GetLevel() >= MILESTONE_LEVEL
        and player:GetLevel() >= 80
        and CLASS_WEIGHTS[player:GetClass()] ~= nil
end

--- Current entitlement as { ilvl = n, stats = { {stat, label, amount}... } }
local function ComputeBonuses(player)
    local weights = CLASS_WEIGHTS[player:GetClass()]
    local ilvl = math.floor(player:GetAverageItemLevel())
    local base = CurveBase(ilvl)
    local stats = {}
    for i, def in ipairs(weights) do
        stats[i] = { stat = def.stat, label = def.label,
                     amount = math.floor(base * def.weight) }
    end
    return { ilvl = ilvl, stats = stats }
end

local function SameBonuses(a, b)
    if not a or not b or #a.stats ~= #b.stats then
        return false
    end
    for i, entry in ipairs(a.stats) do
        if entry.stat ~= b.stats[i].stat or entry.amount ~= b.stats[i].amount then
            return false
        end
    end
    return true
end

-- Same application path as the track's placeholder stats (flat, application 0)
local function ApplyBonuses(player, bonuses, apply)
    for _, entry in ipairs(bonuses.stats) do
        if entry.amount ~= 0 then
            player:HandleStatFlatModifier(
                Constant.STATISTICS.UNIT_MODS[entry.stat], 0, entry.amount, apply)
        end
    end
end

local function PushState(player)
    local applied = player:GetData(APPLIED_KEY)
    local stats = {}
    if applied then
        for i, entry in ipairs(applied.stats) do
            stats[i] = { label = entry.label, amount = entry.amount }
        end
    end
    player:SendServerResponse(PREFIX, 1, {
        ilvl = applied and applied.ilvl or 0,
        stats = stats,
    })
end

--- Swaps the applied amounts to the current entitlement (no-op when equal).
local function Reconcile(player, paragon, apply)
    local want = nil
    if apply and Owed(player, paragon) then
        want = ComputeBonuses(player)
    end

    local applied = player:GetData(APPLIED_KEY)
    if applied == nil and want == nil then
        return
    end
    if SameBonuses(applied, want) then
        return
    end

    if applied then
        ApplyBonuses(player, applied, false)
    end
    if want then
        ApplyBonuses(player, want, true)
    end
    player:SetData(APPLIED_KEY, want)
    PushState(player)
end

-- ============================================================================
-- SAFETY TICK (module-local registry; object events die with the player)
-- ============================================================================

local tickers = {}

local function OnTick(eventId, delay, repeats, player)
    local ok, err = pcall(function()
        Reconcile(player, player:GetData("Paragon"), true)
    end)
    if not ok then
        print("[Paragon] ilvl bonus tick error: " .. tostring(err))
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
        if not player or not paragon then
            return
        end
        Reconcile(player, paragon, apply)
        if apply and Owed(player, paragon) then
            EnsureTicker(player)
        end
    end)
    if not ok then
        print("[Paragon] ilvl bonus statistics error: " .. tostring(err))
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
        print("[Paragon] ilvl bonus level-change error: " .. tostring(err))
    end
end)

-- Instant recompute on equip/swap; before the async paragon load this is a
-- no-op (Owed sees no paragon object and nothing is applied yet).
RegisterPlayerEvent(29, function(event, player, item, bag, slot)
    local ok, err = pcall(function()
        Reconcile(player, player:GetData("Paragon"), true)
    end)
    if not ok then
        print("[Paragon] ilvl bonus equip error: " .. tostring(err))
    end
end)

RegisterMediatorEvent("OnAfterClientLoadRequest", function(player, paragon)
    local ok, err = pcall(function()
        if player then
            PushState(player)
        end
    end)
    if not ok then
        print("[Paragon] ilvl bonus state-push error: " .. tostring(err))
    end
end)

print("[Paragon] Rework: ilvl bonus module loaded")
