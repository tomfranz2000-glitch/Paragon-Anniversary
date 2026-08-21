--[[
    Paragon_DoubleBuckle.lua

    Milestone 1100: the server opens a second prismatic socket on a belt
    carrying two Eternal Belt Buckles (paragon_double_buckle.lua). The
    socket lives in the item's PROP_4 enchantment slot, which the 3.3.5
    client renders as a raw enchant-name text line directly under the
    socket rows (that is how random-suffix stats display) — "Socket Belt"
    while empty (enchant 3729's name), the gem's text once filled. This
    file RESTYLES that line in place into a proper socket row:

        empty:  [grey socket icon] Prismatic Socket   (grey)
        filled: [gem icon] +30 Stamina                (unchanged color)

    via inline |T...|t texture escapes — one SetText on the line's own
    FontString, no lines added, moved or removed (the dual-enchant module
    documents why redistributing tooltip lines desyncs socket icons).
    3.3.5 has no prismatic socket texture; the generic UI-EmptySocket IS
    what the client uses for native prismatic rows.

    Server state (equipped belt only, pushed on login, equip, delta tick
    and every socket change):

        prefix "ParagonBuckle", fn 1: { open, ench, item }

    `item` is a representative gem entry for GetItemIcon. Bagged belts:
    the empty restyle still works (the "Socket Belt" text needs no state);
    a bagged FILLED belt keeps the plain white gem text (no state for it,
    still informative).

    Drop interaction: an overlay button covers the waist slot ONLY while
    the cursor holds a gem or a buckle (CURSOR_UPDATE watcher). The stock
    CharacterWaistSlot scripts are NEVER touched — the right-click branch
    is UseInventoryItem, PROTECTED in 3.3.5 (belt tinkers), and a
    SetScript wrapper would taint the whole path.

    Gem detection compares GetItemInfo's localized itemType to "Gem" —
    enUS realm only, by design of this whole installation. The server
    revalidates everything anyway.

    Dependencies: Paragon_EnchantText.lua (gem enchants are
    SpellItemEnchantment rows, so the generated table covers them too)
]]

local PREFIX = "ParagonBuckle"
local BUCKLE_ITEM = 41611
local INVSLOT_WAIST = 6 -- server EQUIPMENT_SLOT_WAIST (5) + 1
local EMPTY_TEXT = "Socket Belt" -- enchant 3729's name (the parked marker)
local SOCKET_ICON = "Interface\\ItemSocketingFrame\\UI-EmptySocket"

local state = { open = 0, ench = 0, item = 0 }

RegisterServerResponses({
    Prefix = PREFIX,
    Functions = { [1] = "ParagonDoubleBuckle_OnState" },
})

function ParagonDoubleBuckle_OnState(player, arg_table)
    local t = arg_table and arg_table[1]
    state = (type(t) == "table") and t or { open = 0, ench = 0, item = 0 }
end

-- ============================================================================
-- TOOLTIP RESTYLE
-- ============================================================================

-- Match BOTTOM-UP: the PROP-slot line renders below the socket rows, and a
-- native socket filled with the SAME gem produces an identical text line
-- above ours (with its own anchored icon) — top-down matching would restyle
-- the wrong line and double its icon. Containment matching (plain find)
-- tolerates any color escapes the client may wrap the line in.
local function RestyleLine(tip, match, text, r, g, b)
    local name = tip:GetName()
    if not name then
        return
    end
    for i = tip:NumLines(), 2, -1 do
        local fs = _G[name .. "TextLeft" .. i]
        local line = fs and fs:GetText()
        if line and (line == match or line:find(match, 1, true)) then
            fs:SetText(text)
            if r then
                fs:SetTextColor(r, g, b)
            end
            tip:Show()
            return true
        end
    end
end

local EMPTY_STYLED = "|T" .. SOCKET_ICON .. ":14:14:0:0|t Prismatic Socket"

local function RestyleEmpty(tip)
    return RestyleLine(tip, EMPTY_TEXT, EMPTY_STYLED, 0.6, 0.6, 0.6)
end

local function FilledIconText()
    local text = ParagonEnchantText and ParagonEnchantText[state.ench]
    local icon = (state.item and state.item ~= 0 and GetItemIcon(state.item)) or SOCKET_ICON
    return text, icon
end

-- equipped belt with a gem: the raw enchant text line gets the gem's icon
local function RestyleFilled(tip)
    if type(state) ~= "table" or state.open ~= 1 or not state.ench or state.ench == 0 then
        return
    end
    local text, icon = FilledIconText()
    if not text then
        return
    end
    return RestyleLine(tip, text, "|T" .. icon .. ":14:14:0:0|t " .. text)
end

hooksecurefunc(GameTooltip, "SetInventoryItem", function(self, unit, slot)
    if unit ~= "player" or slot ~= INVSLOT_WAIST then
        return
    end
    local styled = RestyleEmpty(self)
    styled = RestyleFilled(self) or styled
    -- fallback: the server says the socket exists but no raw line was
    -- found to restyle (client built the tooltip differently than
    -- expected) — append the styled row so the socket always shows
    if not styled and type(state) == "table" and state.open == 1 then
        if state.ench and state.ench ~= 0 then
            local text, icon = FilledIconText()
            self:AddLine("|T" .. icon .. ":14:14:0:0|t " .. (text or "socketed gem"), 1, 1, 1)
        else
            self:AddLine(EMPTY_STYLED, 0.6, 0.6, 0.6)
        end
        self:Show()
    end
end)

-- ground-truth probe: prints the pushed state and every tooltip line of
-- the equipped belt, so a display failure is diagnosable from a paste
SLASH_PARAGONBUCKLE1 = "/buckledebug"
SlashCmdList["PARAGONBUCKLE"] = function()
    GameTooltip:SetOwner(UIParent, "ANCHOR_NONE")
    GameTooltip:SetInventoryItem("player", INVSLOT_WAIST)
    DEFAULT_CHAT_FRAME:AddMessage(string.format(
        "|cff44ff44[Paragon]|r buckle state: open=%s ench=%s item=%s",
        tostring(type(state) == "table" and state.open), tostring(type(state) == "table" and state.ench),
        tostring(type(state) == "table" and state.item)))
    for i = 1, GameTooltip:NumLines() do
        local fs = _G["GameTooltipTextLeft" .. i]
        DEFAULT_CHAT_FRAME:AddMessage(i .. ": [" .. tostring(fs and fs:GetText()) .. "]")
    end
    GameTooltip:Hide()
end

-- bagged belts render the same PROP-slot line; the empty case needs no
-- server state (unique text), the filled case stays plain by design
hooksecurefunc(GameTooltip, "SetBagItem", function(self)
    RestyleEmpty(self)
end)

-- ============================================================================
-- DROP TARGET (overlay over the waist slot, shown only when relevant)
-- ============================================================================

local function CursorPayload()
    local kind, itemId = GetCursorInfo()
    if kind ~= "item" or not itemId then
        return nil
    end
    if itemId == BUCKLE_ITEM then
        return "buckle", itemId
    end
    if select(6, GetItemInfo(itemId)) == "Gem" then
        return "gem", itemId
    end
    return nil
end

local function HandleDrop()
    local what, itemId = CursorPayload()
    if not what then
        return
    end
    if what == "gem" and (type(state) ~= "table" or state.open ~= 1) then
        DEFAULT_CHAT_FRAME:AddMessage(
            "|cffff4444[Paragon]|r This belt has no second buckle socket — attach a second Eternal Belt Buckle first.")
        return -- keep the gem on the cursor
    end
    SendClientRequest(PREFIX, 1, { action = what, item = itemId })
    ClearCursor()
end

local overlay = CreateFrame("Button", "ParagonDoubleBuckleOverlay", CharacterWaistSlot)
overlay:SetAllPoints(CharacterWaistSlot)
overlay:SetFrameLevel(CharacterWaistSlot:GetFrameLevel() + 5)
overlay:RegisterForClicks("LeftButtonUp", "RightButtonUp")
overlay:Hide()

local glow = overlay:CreateTexture(nil, "OVERLAY")
glow:SetAllPoints()
glow:SetTexture("Interface\\Buttons\\ButtonHilight-Square")
glow:SetBlendMode("ADD")

overlay:SetScript("OnClick", function(self, button)
    if button == "RightButton" then
        ClearCursor() -- native pickup-cancel feel
        return
    end
    HandleDrop()
end)
overlay:SetScript("OnReceiveDrag", HandleDrop)

local watcher = CreateFrame("Frame")
watcher:RegisterEvent("CURSOR_UPDATE")
watcher:SetScript("OnEvent", function()
    if CursorPayload() then
        overlay:Show()
    else
        overlay:Hide()
    end
end)
