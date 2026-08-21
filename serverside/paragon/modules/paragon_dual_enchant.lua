--[[
    Paragon Rework: dual weapon enchant client display (milestone 275)

    The core patch (Paragon Core Patches.md §1e) stores the second permanent
    weapon enchant in the item's prismatic enchantment slot. The client
    APPLIES that slot's effects but renders neither a tooltip line nor a
    link gem for it (verified in-game: self-built links carry gem4 = 0 — the
    client's link builder maps the 4th gem field to the BONUS slot, same as
    ALE's GetItemLink). So the addon gets the data pushed instead:

        server -> client [1]: { [clientInvSlot] = enchantId, ... }

    over CSMH prefix "ParagonEnch", for the equipped weapon slots only
    (client INVSLOT = server EQUIPMENT_SLOT + 1). The addon side
    (Paragon_DualEnchant.lua) appends the enchant's text line to
    SetInventoryItem tooltips.

    Push triggers: client load request (forced), equip event, milestone
    crossing, and a 10s delta tick (enchant casts have no ALE event; the
    tick only sends when the state actually changed).

    Deliberately NOT gated on the milestone marker: the extra enchant lives
    ON THE ITEM ("gates the act, not ownership" — same stance as trainer
    ranks), so any character holding a dual-enchanted weapon has both
    effects and the tooltip must say so. The ENTITLEMENT gate lives where
    the act happens: the core patch (HasSpell on the item owner; its §1e
    eager-shift addendum also keeps the primary slot empty, so the client's
    replace popup only fires on real overwrites — no addon involvement).

    !! BOTS DO REACH THIS -- the old header claimed otherwise !!
    The ticker is registered from OnAfterClientLoadRequest, and that fires
    from OnParagonClientLoadRequest, which Hook.OnPlayerStatLoad calls on
    EVERY login -- it is not only the addon's request handler. So every
    playerbot was registering a 10s ticker whose body walked 13 equipment
    slots x 4 enchantment slots with nothing to gate it, forever. At the
    configured 2500 concurrent bots that is roughly 19,500 ALE calls per
    second plus a fresh Lua table per bot per tick, all of it discarded.
    Both halves are now gated the way the sibling modules already were
    (paragon_double_buckle:286, paragon_gem_double's `eligible` fast path).
]]

local PREFIX = "ParagonEnch"
-- §1e placement chain: prismatic first, then gem slots without a template
-- socket (read in the same order the core fills them)
local CHAIN_SLOTS = { 6, 4, 3, 2 }
-- every §1e-eligible equipment slot (milestone 725 extended the ladder to
-- armor/rings/shield; weapon slots were the milestone-275 original)
local ENCHANT_SLOTS = { 0, 2, 4, 6, 7, 8, 9, 10, 11, 14, 15, 16, 17 }
local PUSHED_KEY = "ParagonDualEnchPushed"
local TICK_MS = 10000

-- item entry -> template socket count (one world-DB lookup per entry,
-- cached for the Lua state's lifetime)
local socket_counts = {}

local function SocketCount(item)
    local entry = item:GetEntry()
    local cached = socket_counts[entry]
    if cached ~= nil then
        return cached
    end
    local count = 0
    local q = WorldDBQuery(
        "SELECT socketColor_1, socketColor_2, socketColor_3 FROM item_template WHERE entry = " .. entry)
    if q then
        for i = 0, 2 do
            if q:GetUInt32(i) > 0 then
                count = count + 1
            end
        end
    end
    socket_counts[entry] = count
    return count
end

--- Extra enchant ids per client inv slot, colon-joined in chain order.
--- Real template sockets are skipped (their slots hold gems); the addon
--- additionally renders only ids present in its enchant-text table, which
--- filters any stray gem/socket-enchant ids client-side.
local function CurrentState(player)
    local state = {}
    for _, slot in ipairs(ENCHANT_SLOTS) do
        local item = player:GetEquippedItemBySlot(slot)
        if item then
            local sockets = SocketCount(item)
            local ids = nil
            for _, ench_slot in ipairs(CHAIN_SLOTS) do
                if ench_slot == 6 or (ench_slot - 2) >= sockets then
                    local ench = item:GetEnchantmentId(ench_slot)
                    if ench and ench > 0 then
                        ids = ids and (ids .. ":" .. ench) or tostring(ench)
                    end
                end
            end
            if ids then
                state[slot + 1] = ids -- client INVSLOT numbering
            end
        end
    end
    return state
end

local function SameState(a, b)
    if not a or not b then
        return false
    end
    for k, v in pairs(a) do
        if b[k] ~= v then
            return false
        end
    end
    for k, v in pairs(b) do
        if a[k] ~= v then
            return false
        end
    end
    return true
end

local function IsBot(player)
    return player.IsPlayerBot and player:IsPlayerBot()
end

--- Account-gate contract: a bot or a sub-80 alt can never hold the milestone,
--- so there is nothing to compute and nothing that could have been applied.
--- Checked BEFORE CurrentState because the scan is the whole cost.
local function Eligible(player)
    return not IsBot(player) and player:GetLevel() >= 80
end

local function Push(player, force)
    if not Eligible(player) then
        return
    end
    local state = CurrentState(player)
    if not force and SameState(player:GetData(PUSHED_KEY), state) then
        return
    end
    player:SetData(PUSHED_KEY, state)
    player:SendServerResponse(PREFIX, 1, state)
end

-- ============================================================================
-- DELTA TICK (module-local registry; object events die with the player)
-- ============================================================================

local tickers = {}

local function OnTick(eventId, delay, repeats, player)
    local ok, err = pcall(Push, player)
    if not ok then
        print("[Paragon] dual enchant tick error: " .. tostring(err))
    end
end

--- Gated on IsBot ONLY, not on level: a character can ding 80 mid-session and
--- EnsureTicker is reached just once, at login, so a level test here would
--- leave that character without a ticker for the rest of the session. A bot
--- never stops being a bot, so that test is safe to make permanent.
local function EnsureTicker(player)
    if IsBot(player) then
        return
    end
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

RegisterMediatorEvent("OnAfterClientLoadRequest", function(player, paragon)
    local ok, err = pcall(function()
        if player then
            Push(player, true)
            EnsureTicker(player)
        end
    end)
    if not ok then
        print("[Paragon] dual enchant load-push error: " .. tostring(err))
    end
end)

RegisterPlayerEvent(29, function(event, player, item, bag, slot)
    local ok, err = pcall(Push, player)
    if not ok then
        print("[Paragon] dual enchant equip error: " .. tostring(err))
    end
end)

RegisterMediatorEvent("OnParagonLevelChanged", function(player, paragon)
    local ok, err = pcall(function()
        if player then
            Push(player)
        end
    end)
    if not ok then
        print("[Paragon] dual enchant level-change error: " .. tostring(err))
    end
end)

print("[Paragon] Rework: dual enchant display module loaded")
