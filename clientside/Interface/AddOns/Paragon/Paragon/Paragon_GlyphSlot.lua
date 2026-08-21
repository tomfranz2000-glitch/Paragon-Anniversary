--[[
    Paragon_GlyphSlot.lua

    Seventh (major-only) glyph socket in the center of the glyph flower,
    unlocked by paragon milestone 225. The stock UI caps at six protocol
    slots, so this socket is entirely addon-driven: state comes from the
    server over CSMH prefix "ParagonGlyph" and applying sends the dragged
    glyph item's id back over the same prefix. The server owns validation,
    item consumption and the glyph's passive aura.

    Visuals reuse the stock virtual GlyphTemplate + Blizzard_GlyphUI helpers
    (GlyphFrameGlyph_SetGlyphType), so the socket looks native. The stock
    template scripts are overridden — they would query GetGlyphSocketInfo
    with this socket's fake id and error.
]]

local PREFIX = "ParagonGlyph"
local MILESTONE_LEVEL = 225

local state = { aura = 0, rune = nil }
local socket  -- created once Blizzard_GlyphUI loads

-- ============================================================================
-- SERVER STATE
-- ============================================================================

RegisterServerResponses({
    Prefix = PREFIX,
    Functions = { [1] = "ParagonGlyphSlot_OnState" },
})

function ParagonGlyphSlot_OnState(player, arg_table)
    local data = arg_table and arg_table[1] or {}
    if data.error then
        UIErrorsFrame:AddMessage(data.error, 1.0, 0.1, 0.1, 1.0)
    end

    local newAura = tonumber(data.aura) or 0
    local changed = newAura ~= state.aura and newAura > 0 and not data.error
    state.aura = newAura
    state.rune = data.rune
    ParagonGlyphSlot_Refresh()

    -- native feedback on a successful socket
    if changed then
        PlaySound("Glyph_MajorCreate")
        if GlyphFrame and GlyphFrame:IsVisible() and GlyphFrame_PulseGlow then
            GlyphFrame_PulseGlow()
        end
    end
end

-- ============================================================================
-- SOCKET VISUALS
-- ============================================================================

function ParagonGlyphSlot_Refresh()
    if not socket then
        return
    end

    local level = (ParagonRewardTrackData and ParagonRewardTrackData.currentLevel) or 0
    if level < MILESTONE_LEVEL then
        socket:Hide()
        return
    end
    socket:Show()

    socket.setting:SetAlpha(0.6)
    socket.shine:Show()
    socket.ring:Show()
    socket.background:Show()
    socket.background:SetAlpha(1)

    if state.aura > 0 then
        socket.spell = state.aura
        socket.background:SetTexCoord(GLYPH_SLOTS[1].left, GLYPH_SLOTS[1].right,
                                      GLYPH_SLOTS[1].top, GLYPH_SLOTS[1].bottom)
        -- stock sockets show the glyph property's round rune art, not the
        -- aura spell's (square) icon — the server resolves it from the DBCs
        socket.glyph:SetTexture(state.rune or "Interface\\Spellbook\\UI-Glyph-Rune1")
        socket.glyph:Show()
    else
        socket.spell = nil
        socket.background:SetTexCoord(GLYPH_SLOTS[0].left, GLYPH_SLOTS[0].right,
                                      GLYPH_SLOTS[0].top, GLYPH_SLOTS[0].bottom)
        socket.glyph:Hide()
    end
end

-- ============================================================================
-- INTERACTION
-- ============================================================================

local function TryApplyCursorItem()
    local kind, itemId = GetCursorInfo()
    if kind ~= "item" then
        return false
    end
    ClearCursor()
    SendClientRequest(PREFIX, 1, { item = itemId })
    return true
end

local function OnClick(self, button)
    TryApplyCursorItem()
end

local function OnEnter(self)
    self.highlight:Show()
    GameTooltip:SetOwner(self, "ANCHOR_RIGHT")
    if state.aura > 0 then
        GameTooltip:SetHyperlink("spell:" .. state.aura)
        GameTooltip:AddLine(" ")
        GameTooltip:AddLine("Paragon glyph slot: drag another major glyph here to replace this one.", 1, 1, 1, true)
    else
        GameTooltip:SetText("Paragon Major Glyph Slot", 1, 0.82, 0)
        GameTooltip:AddLine("Unlocked at Paragon level " .. MILESTONE_LEVEL .. ".", 1, 1, 1, true)
        GameTooltip:AddLine("Drag a major glyph from your bags onto this socket to activate it.", 1, 1, 1, true)
    end
    GameTooltip:Show()
end

local function OnLeave(self)
    self.highlight:Hide()
    GameTooltip:Hide()
end

-- ============================================================================
-- CREATION (after Blizzard_GlyphUI is available)
-- ============================================================================

local function InitSocket()
    if socket or not GlyphFrame then
        return
    end

    socket = CreateFrame("Button", "ParagonGlyphSocket", GlyphFrame, "GlyphTemplate")
    socket:SetWidth(90)
    socket:SetHeight(90)
    -- flower center; matches the sparkle animations' shared origin (-13, 17)
    socket:SetPoint("CENTER", GlyphFrame, "CENTER", -13, 17)

    -- stock scripts assume a real socket id — replace them wholesale
    socket:SetScript("OnShow", nil)
    socket:SetScript("OnUpdate", nil)
    socket:SetScript("OnClick", OnClick)
    socket:SetScript("OnReceiveDrag", OnClick)
    socket:SetScript("OnEnter", OnEnter)
    socket:SetScript("OnLeave", OnLeave)

    GlyphFrameGlyph_SetGlyphType(socket, GLYPHTYPE_MAJOR)

    -- keep the socket in sync whenever the stock frame refreshes
    hooksecurefunc("GlyphFrame_Update", ParagonGlyphSlot_Refresh)

    ParagonGlyphSlot_Refresh()
end

local watcher = CreateFrame("Frame")
watcher:RegisterEvent("ADDON_LOADED")
watcher:SetScript("OnEvent", function(self, event, name)
    if name == "Blizzard_GlyphUI" then
        InitSocket()
        self:UnregisterEvent("ADDON_LOADED")
    end
end)

if IsAddOnLoaded("Blizzard_GlyphUI") then
    InitSocket()
end
