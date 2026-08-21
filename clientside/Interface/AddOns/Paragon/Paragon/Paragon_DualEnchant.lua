--[[
    Paragon_DualEnchant.lua

    Milestone 275: the server stores a second permanent weapon enchant in the
    item's prismatic enchantment slot (core patch, Paragon Core Patches.md
    §1e). The 3.3.5 client applies that slot's effects but never renders its
    text — and it cannot be recovered from item links either (the client's
    link builder maps the 4th gem field to the BONUS slot, so links carry 0;
    verified in-game). The server therefore pushes the state over CSMH:

        prefix "ParagonEnch", fn 1: { [invSlot] = enchantId, ... }

    for the player's equipped weapon slots (client INVSLOT numbering, pushed
    on login, equip, milestone crossing and a 10s server delta tick). This
    file stores it and appends the enchant's green text line to equipped-item
    tooltips. Chat-linked items cannot show the line (no data in links).

    Dependencies: Paragon_EnchantText.lua (Tools/gen_enchant_text.py)
]]

local PREFIX = "ParagonEnch"

local state = {}

RegisterServerResponses({
    Prefix = PREFIX,
    Functions = { [1] = "ParagonDualEnchant_OnState" },
})

function ParagonDualEnchant_OnState(player, arg_table)
    state = (arg_table and arg_table[1]) or {}
end

-- ============================================================================
-- MID-TOOLTIP INSERTION
-- ============================================================================

-- The tooltip API only appends, and REDISTRIBUTING text across lines is
-- unsafe: socket icons and the money frame are separate objects anchored to
-- specific line FontStrings, so they stay behind when texts shift (verified
-- in-game — icons desynced one line, sell price overlapped). Instead, grow
-- the primary enchant's OWN FontString to two lines by appending "\n<text>".
-- No object moves relative to its anchor: every TextLeft(i) hangs from
-- TextLeft(i-1)'s BOTTOMLEFT (GameTooltipTemplate.xml), so the whole chain
-- below — sockets, icons, money frame — reflows automatically. Both enchant
-- lines share the same green, so the shared FontString color just works.

local function FindLine(tip, text)
    local name = tip:GetName()
    for i = 2, tip:NumLines() do
        local fs = _G[name .. "TextLeft" .. i]
        if fs and fs:GetText() == text then
            return fs
        end
    end
end

hooksecurefunc(GameTooltip, "SetInventoryItem", function(self, unit, slot)
    if unit ~= "player" then
        return
    end
    -- milestone 725 generalized the push: the value is now one or more
    -- colon-joined enchant ids (chain order). Only ids present in the
    -- enchant-text table render — that filters stray gem ids server-side
    -- heuristics may pass through.
    local ids = state[slot]
    if not ids or not ParagonEnchantText then
        return
    end
    local extra = nil
    for id in string.gmatch(tostring(ids), "[^:]+") do
        local text = ParagonEnchantText[tonumber(id)]
        if text then
            extra = extra and (extra .. "\n" .. text) or text
        end
    end
    if not extra then
        return
    end

    -- anchor: the primary enchant's line (its id rides the item link).
    -- Every SetInventoryItem call rebuilds the lines, so the append never
    -- doubles up.
    local permId = tonumber(select(2, string.match(
        GetInventoryItemLink(unit, slot) or "", "item:(%-?%d*):(%-?%d*)")))
    local permText = permId and permId ~= 0 and ParagonEnchantText[permId]
    local line = permText and FindLine(self, permText)
    if line then
        line:SetText(permText .. "\n" .. extra)
    else
        self:AddLine(extra, 0, 1, 0)
    end
    self:Show()
end)

-- ============================================================================
-- REPLACE POPUP: no addon involvement (deliberate)
-- ============================================================================

-- History: this file used to auto-accept the REPLACE_ENCHANT popup. That
-- called the protected ReplaceEnchant() from insecure hook code — it worked
-- for a while purely by taint-state accident, then started firing
-- ADDON_ACTION_FORBIDDEN. The suppression now lives SERVER-side (§1e eager
-- shift): the primary enchant slot is kept empty while extra capacity
-- remains, so the client never has a reason to show the popup — and when it
-- does show one, accepting really would overwrite an enchant. Do not
-- reintroduce a ReplaceEnchant()/ReplaceTradeEnchant() call here.
