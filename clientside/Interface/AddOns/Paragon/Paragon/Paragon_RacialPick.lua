--[[
    Paragon_RacialPick.lua
    Milestone 1400 "Racially Ambiguous" — picker for one ACTIVE racial
    ability belonging to another race.

    The server sends nothing but ids: every option is a REAL stock spell
    (originals are granted, not clones — see the server module header for
    why), so the client resolves name and icon with GetSpellInfo and the
    tooltip with SetHyperlink straight out of its own DBC. Nothing here
    needs an MPQ patch, and no name or icon text ever crosses the wire.

    Protocol (prefix "ParagonRacial"):
      S->C [1] state { unlocked, milestone, pick, spell,
                       options = { { key, race, raceName, spell }, ... } }
      C->S [1] { action = "pick", key = "..." } | { action = "clear" }

    The picker is opened by clicking the level-1400 node on the reward
    track (Paragon_RewardTrack.lua calls ParagonRacial_Toggle).

    @module Paragon_RacialPick
]]

local PREFIX = "ParagonRacial"

--- Live state, replaced wholesale by each state push
-- @table ParagonRacialData
ParagonRacialData = {
    unlocked = false,
    milestone = 1400,
    pick = nil,       -- option key, or nil while nothing is chosen
    spell = nil,      -- class-resolved spell id of the current pick
    options = {},
}

local COLUMNS = 4
local CELL_W, CELL_H = 104, 88
local MARGIN_X, MARGIN_TOP = 24, 62

local panel
local buttons = {}

local function Send(data)
    SendClientRequest(PREFIX, 1, data)
end

-- ============================================================================
-- OPTION WIDGETS
-- ============================================================================

local function Option_OnEnter(self)
    local opt = self.option
    if not opt then return end
    GameTooltip:SetOwner(self, "ANCHOR_RIGHT")
    -- The real client tooltip for the real spell: cooldown, cost, range
    -- and description all come from the client's own DBC, so it stays
    -- correct without shipping a word of text from the server.
    GameTooltip:SetHyperlink("spell:" .. opt.spell)
    GameTooltip:AddLine(" ")
    GameTooltip:AddLine((opt.raceName or "?") .. " racial ability", 0.6, 0.6, 0.6)
    if ParagonRacialData.pick == opt.key then
        GameTooltip:AddLine("Currently active", 0, 1, 0)
        GameTooltip:AddLine("Right-click: unlearn", 0.5, 0.5, 0.5)
    else
        GameTooltip:AddLine("Click to learn this ability", 1, 0.82, 0)
    end
    GameTooltip:AddLine("Changes are free, but only out of combat and only "
        .. "while your current ability is off cooldown.", 0.5, 0.5, 0.5, true)
    GameTooltip:Show()
end

local function Option_OnClick(self, button)
    local opt = self.option
    if not opt then return end
    if button == "RightButton" then
        if ParagonRacialData.pick == opt.key then
            Send({ action = "clear" })
        end
        return
    end
    if ParagonRacialData.pick == opt.key then
        return -- already active; the server would no-op anyway
    end
    Send({ action = "pick", key = opt.key })
end

local function CreateOption(parent, index)
    local btn = CreateFrame("Frame", "ParagonRacialOption" .. index, parent,
        "ParagonTrackNodeTemplate")
    btn:EnableMouse(true)
    btn:SetScript("OnEnter", Option_OnEnter)
    btn:SetScript("OnLeave", function() GameTooltip:Hide() end)
    btn:SetScript("OnMouseUp", Option_OnClick)

    -- The template's Level fontstring sits ABOVE the node; this panel wants
    -- the ability name below it instead (same move the codex makes).
    if btn.Level then
        btn.Level:ClearAllPoints()
        btn.Level:SetPoint("TOP", btn, "BOTTOM", 0, -1)
        btn.Level:SetWidth(CELL_W - 8)
        -- 3.3.5 SetFontObject takes the font OBJECT, not its name
        btn.Level:SetFontObject(GameFontHighlightSmall)
    end
    return btn
end

-- ============================================================================
-- PANEL
-- ============================================================================

local function Refresh()
    if not panel then return end

    local options = ParagonRacialData.options or {}
    for i, opt in ipairs(options) do
        local btn = buttons[i]
        if not btn then
            btn = CreateOption(panel, i)
            buttons[i] = btn
        end
        local col, row = (i - 1) % COLUMNS, math.floor((i - 1) / COLUMNS)
        btn:ClearAllPoints()
        btn:SetPoint("TOPLEFT", panel, "TOPLEFT",
            MARGIN_X + col * CELL_W, -(MARGIN_TOP + row * CELL_H))
        btn.option = opt

        local name, _, icon = GetSpellInfo(opt.spell)
        if btn.Icon then
            SetPortraitToTexture(btn.Icon,
                icon or "Interface\\Icons\\INV_Misc_QuestionMark")
            -- Dim everything that is not the active choice, so the current
            -- ability reads at a glance
            btn.Icon:SetDesaturated(ParagonRacialData.pick == opt.key and nil or 1)
        end
        if btn.Level then
            btn.Level:SetText(name or opt.key)
            if ParagonRacialData.pick == opt.key then
                btn.Level:SetTextColor(0, 1, 0)
            else
                btn.Level:SetTextColor(0.75, 0.75, 0.75)
            end
        end
        btn:Show()
    end
    for i = #options + 1, #buttons do
        buttons[i]:Hide()
    end

    if panel.Status then
        if ParagonRacialData.pick and ParagonRacialData.spell then
            local name = GetSpellInfo(ParagonRacialData.spell)
            panel.Status:SetText("Active: |cff00ff00" .. (name or "?") .. "|r")
        else
            panel.Status:SetText("|cffffd100No ability chosen.|r Pick one below.")
        end
    end

    -- Size to the number of rows actually shown
    local rows = math.max(1, math.ceil(#options / COLUMNS))
    panel:SetHeight(MARGIN_TOP + rows * CELL_H + 44)
end

local function BuildPanel()
    if panel then return end

    panel = CreateFrame("Frame", "ParagonRacialPickFrame", UIParent)
    panel:SetFrameStrata("DIALOG")
    panel:SetWidth(MARGIN_X * 2 + COLUMNS * CELL_W)
    panel:SetHeight(320)
    panel:SetPoint("CENTER", UIParent, "CENTER", 0, 0)
    panel:EnableMouse(true)
    panel:SetMovable(true)
    panel:RegisterForDrag("LeftButton")
    panel:SetScript("OnDragStart", panel.StartMoving)
    panel:SetScript("OnDragStop", panel.StopMovingOrSizing)
    panel:SetBackdrop({
        bgFile = "Interface/Tooltips/UI-Tooltip-Background",
        edgeFile = "Interface/Tooltips/UI-Tooltip-Border",
        tile = true, tileSize = 16, edgeSize = 16,
        insets = { left = 4, right = 4, top = 4, bottom = 4 },
    })
    panel:SetBackdropColor(0.06, 0.05, 0.03, 0.95)
    panel:SetBackdropBorderColor(0.90, 0.80, 0.50, 1)

    local title = panel:CreateFontString(nil, "OVERLAY", "GameFontNormalLarge")
    title:SetPoint("TOP", panel, "TOP", 0, -14)
    title:SetText("Racially Ambiguous")
    panel.Title = title

    local status = panel:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    status:SetPoint("TOP", title, "BOTTOM", 0, -6)
    panel.Status = status

    local close = CreateFrame("Button", nil, panel, "UIPanelCloseButton")
    close:SetPoint("TOPRIGHT", panel, "TOPRIGHT", -2, -2)
    close:SetScript("OnClick", function() panel:Hide() end)

    local hint = panel:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
    hint:SetPoint("BOTTOM", panel, "BOTTOM", 0, 14)
    hint:SetWidth(COLUMNS * CELL_W - 10)
    hint:SetText("Your own race's abilities are not listed. Changing is free, "
        .. "out of combat, once your current ability is off cooldown.")

    panel:SetScript("OnShow", Refresh)
    panel:Hide()
end

--- Opens or closes the picker. Called by the reward track's level-1400 node.
function ParagonRacial_Toggle()
    if not ParagonRacialData.unlocked then
        return false
    end
    BuildPanel()
    if panel:IsShown() then
        panel:Hide()
    else
        panel:Show()
        Refresh()
    end
    return true
end

--- True when the milestone is reached and there is something to pick.
function ParagonRacial_IsUnlocked()
    return ParagonRacialData.unlocked and true or false
end

--- Display name of the current pick, or nil — used by the milestone tooltip.
function ParagonRacial_CurrentName()
    if not ParagonRacialData.spell then return nil end
    return (GetSpellInfo(ParagonRacialData.spell))
end

--- Icon path of the current pick, or nil — the reward-track node swaps to
--- it so the milestone shows the chosen ability at a glance.
function ParagonRacial_CurrentIcon()
    if not ParagonRacialData.spell then return nil end
    local _, _, icon = GetSpellInfo(ParagonRacialData.spell)
    return icon
end

-- ============================================================================
-- NETWORK
-- ============================================================================

RegisterServerResponses({
    Prefix = PREFIX,
    Functions = { [1] = "ParagonRacial_OnState" },
})

function ParagonRacial_OnState(player, arg_table)
    local data = arg_table and arg_table[1]
    if type(data) ~= "table" then return end
    ParagonRacialData.unlocked = data.unlocked and true or false
    ParagonRacialData.milestone = tonumber(data.milestone) or 1400
    ParagonRacialData.pick = data.pick
    ParagonRacialData.spell = tonumber(data.spell)
    ParagonRacialData.options = type(data.options) == "table" and data.options or {}

    if panel and panel:IsShown() then
        Refresh()
    end
    -- Repaint the milestone node + any open milestone tooltip
    if UIParagon_RefreshRacialMilestone then
        UIParagon_RefreshRacialMilestone()
    end
end
