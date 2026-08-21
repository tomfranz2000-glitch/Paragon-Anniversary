--[[
    Paragon Rework: enchant-slot ladder (milestone 725)

    Eleven extra permanent-enchant slots unlocked by AVERAGE ITEM LEVEL
    thresholds (the §1e capacity mechanism: each tier is a marker spell
    whose EquippedItem fit mask names one equipment slot; the core counts
    markers the OWNER knows per fitting item). This module is the GATE:
    it teaches/unteaches the markers as the character's live average item
    level (core GetAverageItemLevel: 15 slots, empty counts as zero)
    crosses tier thresholds, gated on the milestone + level 80.

    "Gates the act, not ownership" (§1e stance): dropping below a
    threshold later revokes the CAPACITY (no further extra enchants) but
    never strips enchants already on items.

    Client: pushes { ilvl, unlocked = {labels...}, next = threshold }
    over CSMH prefix "ParagonSlots" — suspense contract, locked tiers
    never leave the server.
]]

local MILESTONE_LEVEL = 850 -- track reorder 2026-08-18 (was 725)
local PREFIX = "ParagonSlots"
local PUSHED_KEY = "ParagonSlotsPushed"
local TICK_MS = 10000

-- threshold ladder (avg item level -> §1e marker). Weapon tier reuses the
-- reserved 1900033 (third weapon enchant on top of milestone 275's second).
-- Calibration (user spec 2026-08-18): first tier at 200, final tier at the
-- MAXIMUM ACHIEVABLE average — verified per-slot maxima over the 15
-- counted slots sum to 4225 (floor avg 281 with literal BiS incl. the 284
-- belt/bracers); 280 = functionally-BiS. Even 8-point steps between.
local TIERS = {
    { ilvl = 200, marker = 1900057, label = "Chest" },
    { ilvl = 208, marker = 1900058, label = "Legs" },
    { ilvl = 216, marker = 1900059, label = "Hands" },
    { ilvl = 224, marker = 1900060, label = "Feet" },
    { ilvl = 232, marker = 1900061, label = "Wrist" },
    { ilvl = 240, marker = 1900062, label = "Back" },
    { ilvl = 248, marker = 1900063, label = "Head" },
    { ilvl = 256, marker = 1900064, label = "Shoulder" },
    { ilvl = 264, marker = 1900065, label = "Rings" },
    { ilvl = 272, marker = 1900066, label = "Shield" },
    { ilvl = 280, marker = 1900033, label = "Weapon (third enchantment)" },
}

local function Owed(player, paragon)
    -- account-wide paragon: benefits require character level 80
    return paragon and paragon:GetLevel() >= MILESTONE_LEVEL
        and player:GetLevel() >= 80
end

--- Teaches/unteaches tier markers to match the live average item level,
--- then pushes the tooltip state when it changed.
local function Reconcile(player, paragon)
    local owed = Owed(player, paragon)
    local ilvl = math.floor(player:GetAverageItemLevel())
    local unlocked, nextThreshold = {}, nil

    for _, tier in ipairs(TIERS) do
        local entitled = owed and ilvl >= tier.ilvl
        if entitled then
            unlocked[#unlocked + 1] = tier.label
            if not player:HasSpell(tier.marker) then
                player:LearnSpell(tier.marker)
            end
        else
            if not nextThreshold and owed then
                nextThreshold = tier.ilvl
            end
            if player:HasSpell(tier.marker) then
                player:RemoveSpell(tier.marker)
            end
        end
    end

    local state = { ilvl = owed and ilvl or 0, unlocked = unlocked, next = nextThreshold }
    local pushed = player:GetData(PUSHED_KEY)
    if pushed and pushed.ilvl == state.ilvl and pushed.next == state.next
            and #pushed.unlocked == #state.unlocked then
        return
    end
    player:SetData(PUSHED_KEY, state)
    player:SendServerResponse(PREFIX, 1, state)
end

-- ============================================================================
-- SAFETY TICK (unequip-to-bag fires no event; ilvl-module pattern)
-- ============================================================================

local tickers = {}

local function OnTick(eventId, delay, repeats, player)
    local ok, err = pcall(function()
        Reconcile(player, player:GetData("Paragon"))
    end)
    if not ok then
        print("[Paragon] enchant slots tick error: " .. tostring(err))
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
        if player and paragon and apply then
            Reconcile(player, paragon)
            if Owed(player, paragon) then
                EnsureTicker(player)
            end
        end
    end)
    if not ok then
        print("[Paragon] enchant slots statistics error: " .. tostring(err))
    end
end)

RegisterMediatorEvent("OnParagonLevelChanged", function(player, paragon)
    local ok, err = pcall(function()
        if player and paragon then
            Reconcile(player, paragon)
            if Owed(player, paragon) then
                EnsureTicker(player)
            end
        end
    end)
    if not ok then
        print("[Paragon] enchant slots level-change error: " .. tostring(err))
    end
end)

RegisterPlayerEvent(29, function(event, player, item, bag, slot)
    local ok, err = pcall(function()
        Reconcile(player, player:GetData("Paragon"))
    end)
    if not ok then
        print("[Paragon] enchant slots equip error: " .. tostring(err))
    end
end)

RegisterMediatorEvent("OnAfterClientLoadRequest", function(player, paragon)
    local ok, err = pcall(function()
        if player then
            player:SetData(PUSHED_KEY, nil)
            Reconcile(player, player:GetData("Paragon"))
        end
    end)
    if not ok then
        print("[Paragon] enchant slots state-push error: " .. tostring(err))
    end
end)

print("[Paragon] Rework: enchant slots module loaded")
