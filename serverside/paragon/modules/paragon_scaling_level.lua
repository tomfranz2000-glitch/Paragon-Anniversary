--[[
    Paragon Rework: stat-scaling level refresh (milestone 500)

    The §1g core patch makes every gt-table stat conversion read the level
    through Player::GetStatScalingLevel, lowered by marker aura 1900039's
    basepoints. The aura is a passive-attribute AddAura and is therefore
    NOT saved to character_aura — the track re-adds it each login AFTER
    the core has already computed stats, and a mid-session milestone
    crossing adds it with no stat recompute of its own. This module makes
    the change bite immediately whenever the aura's presence flips:

    - ratings: ApplyRatingMod(cr, 0, true) per rating — the core's own
      RecalculateRating idiom (0-amount still runs UpdateRating)
    - stat chains: +1/-1 flat mod on AGI / INT / SPI forces the full
      UpdateStats cascade (melee crit + dodge from agi, spell crit + mana
      regen from int, regen from spirit)

    Runs AFTER the track module's aura reconcile on the same mediator
    events (module load order: rework_track < scaling_level). The session
    flag dies with the player object, so each login pokes exactly once.
]]

local Constant = require("paragon_constant")

-- Milestone markers 1900039 (700), 1900072 (925) and 1900131 (1350), plus
-- the codex's three Timeless Body ranks. All summed by the §1g core patch;
-- this module only refreshes stats + client state when the SET changes.
--
-- !! THIRD REGISTRY FOR THE SAME MARKER !! A new scaling marker must be
-- listed in ALL of: Player::GetStatScalingLevel (the core sums it there),
-- SPECIAL_AURAS in paragon_rework_track.lua (grants it), and HERE. Omit it
-- here and State() never changes when the aura lands, so Sync() early-returns
-- and no stat recompute happens -- the milestone silently does nothing until
-- some unrelated event pokes stats.
local SPELLS = { 1900039, 1900072, 1900131, 1900096, 1900097, 1900098 }
local FLAG = "ParagonScalingPoked"

local POKE_STATS = { "STAT_AGILITY", "STAT_INTELLECT", "STAT_SPIRIT" }

local function Refresh(player)
    for _, cr in pairs(Constant.STATISTICS.COMBAT_RATING) do
        player:ApplyRatingMod(cr, 0, true)
    end
    for _, stat in ipairs(POKE_STATS) do
        local mod = Constant.STATISTICS.UNIT_MODS[stat]
        player:HandleStatFlatModifier(mod, 0, 1, true)
        player:HandleStatFlatModifier(mod, 0, 1, false)
    end
end

local function State(player)
    local state = ""
    for _, spell in ipairs(SPELLS) do
        state = state .. (player:HasAura(spell) and "1" or "0")
    end
    return state
end

local function Sync(player)
    local state = State(player)
    local prev = player:GetData(FLAG)
    if prev == state then
        return
    end
    player:SetData(FLAG, state)
    -- first sight with no markers at all: record without the (pointless)
    -- stat recompute — mirrors the old single-aura behavior
    if prev ~= nil or state ~= string.rep("0", #SPELLS) then
        Refresh(player)
    end
end

RegisterMediatorEvent("OnAfterUpdatePlayerStatistics", function(player, paragon, apply)
    local ok, err = pcall(function()
        if player and apply then
            Sync(player)
        end
    end)
    if not ok then
        print("[Paragon] scaling level statistics error: " .. tostring(err))
    end
end)

RegisterMediatorEvent("OnParagonLevelChanged", function(player, paragon)
    local ok, err = pcall(function()
        if player then
            Sync(player)
        end
    end)
    if not ok then
        print("[Paragon] scaling level level-change error: " .. tostring(err))
    end
end)

print("[Paragon] Rework: scaling level module loaded")
