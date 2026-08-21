--[[
    Paragon_Interface.lua
    UI building and event handlers for Paragon Anniversary system

    This module handles all UI construction, frame management, and user interactions
    for the Paragon Anniversary interface. It implements a frame recycling system
    to efficiently reuse existing frames and prevent duplication on server reloads.

    Key responsibilities:
    - Build and manage the main UIParagon frame
    - Dynamically create and recycle category lines and stat items
    - Handle experience bar display and animations
    - Manage stat item interactions (hover, click, mouse wheel)
    - Display tooltips with localized information

    Dependencies:
    - Paragon_Locales.lua: GetLocaleTable() for translations
    - Paragon_Network.lua: ParagonData, UIParagon_ModifyStatValue()
    - Paragon_Animations.lua: Animation functions for smooth UI effects

    @module Paragon_Interface
    @author Paragon Team
]]

-- ============================================================================
-- CVAR REGISTRATION
-- ============================================================================

-- Register custom CVar for Paragon MainMenuBar XP visibility
-- Parameters: name, defaultValue, characterSpecific (true/false)
RegisterCVar("paragonShowMainMenuXP", "0", true)

-- ============================================================================
-- STATIC POPUPS (Dialogues)
-- ============================================================================

--- Popup for choosing between Add or Remove points (Middle click)
StaticPopupDialogs["PARAGON_STAT_CHOOSE_ACTION"] = {
    text = "",  -- Will be set dynamically with locale
    button1 = "",  -- Will be set dynamically (Add)
    button2 = "",  -- Will be set dynamically (Cancel)
    button3 = "",  -- Will be set dynamically (Remove)
    OnShow = function(self)
        local L = GetLocaleTable()
        self.text:SetText(L.POPUP_CHOOSE_ACTION)
        self.button1:SetText(L.POPUP_BUTTON_ADD)
        self.button2:SetText(L.POPUP_BUTTON_CANCEL)
        self.button3:SetText(L.POPUP_BUTTON_REMOVE)
    end,
    OnAccept = function(self)
        -- User chose "Add" (button1) - open the amount dialog with positive mode
        local data = self.data
        StaticPopup_Show("PARAGON_STAT_ENTER_AMOUNT", nil, nil, {
            categoryId = data.categoryId,
            statId = data.statId,
            statName = data.statName,
            mode = "add"
        })
    end,
    OnCancel = function(self)
        -- User chose "Cancel" (button2) - just close the dialog
        -- Nothing to do, dialog will close automatically
    end,
    OnAlt = function(self)
        -- User chose "Remove" (button3) - open the amount dialog with negative mode
        local data = self.data
        StaticPopup_Show("PARAGON_STAT_ENTER_AMOUNT", nil, nil, {
            categoryId = data.categoryId,
            statId = data.statId,
            statName = data.statName,
            mode = "remove"
        })
    end,
    timeout = 0,
    whileDead = true,
    hideOnEscape = true,
    preferredIndex = 3,
}

--- Popup for entering the amount of points to add/remove
StaticPopupDialogs["PARAGON_STAT_ENTER_AMOUNT"] = {
    text = "",  -- Will be set dynamically with locale
    button1 = "",  -- Will be set dynamically
    button2 = "",  -- Will be set dynamically
    hasEditBox = true,
    maxLetters = 6,
    OnShow = function(self)
        local L = GetLocaleTable()
        local data = self.data
        -- Format the text with action (add/remove) and stat name
        local actionText = (data.mode == "add") and L.POPUP_ACTION_ADD or L.POPUP_ACTION_REMOVE
        local formattedText = string.format(L.POPUP_ENTER_AMOUNT, actionText, data.statName or "???")
        self.text:SetText(formattedText)
        -- Set button texts
        self.button1:SetText(L.POPUP_BUTTON_CONFIRM)
        self.button2:SetText(L.POPUP_BUTTON_CANCEL)
        -- Focus the edit box
        self.editBox:SetFocus()
        self.editBox:SetNumber(0)
    end,
    OnAccept = function(self)
        local amount = self.editBox:GetNumber()
        if amount and amount > 0 then
            local data = self.data
            -- Apply the amount as positive or negative based on mode
            local delta = (data.mode == "add") and amount or -amount
            UIParagon_ModifyStatValue(data.categoryId, data.statId, delta)
        end
    end,
    EditBoxOnEnterPressed = function(self)
        local parent = self:GetParent()
        local amount = self:GetNumber()
        if amount and amount > 0 then
            local data = parent.data
            local delta = (data.mode == "add") and amount or -amount
            UIParagon_ModifyStatValue(data.categoryId, data.statId, delta)
        end
        parent:Hide()
    end,
    EditBoxOnEscapePressed = function(self)
        self:GetParent():Hide()
    end,
    timeout = 0,
    whileDead = true,
    hideOnEscape = true,
    preferredIndex = 3,
}


-- ============================================================================
-- MAIN FRAME
-- ============================================================================

--- Initializes the main UIParagon frame on load
-- Sets up frame references, localized text, portrait icon, and requests initial
-- data from the server
-- @param self Frame The UIParagon frame being initialized
-- @usage Called automatically by XML OnLoad script
function UIParagon_OnLoad(self)
    -- Cache locale table for this frame
    self.Locales = GetLocaleTable()

    -- Cache references to child frames for quick access
    self.experienceBar = self.TopBanner.ExperienceBar
    self.level = self.TopBanner.Level
    self.body = self.Body

    -- Set localized title text
    self.body.TopSpacer.Title:SetText(self.Locales.STATISTICS_TEXT)

    -- Set portrait icon (holy power infusion spell icon)
    SetPortraitToTexture(self.PortraitFrame.Portrait, "Interface\\Icons\\spell_holy_powerinfusion")

    -- Initialize points display with 0 points
    self.body.TopSpacer.Points:SetText(string.format(self.Locales.POINTS_TO_SPEND, 0, self.Locales.POINTS_SINGULAR))

    -- Enable closing the frame with Escape key
    tinsert(UISpecialFrames, "UIParagon")

    -- Request initial data from server (Hook ID: 1)
    SendClientRequest("ParagonAnniversary", 1)

    -- Update notification badge on load (will show if player has unspent points)
    if ParagonMicroButton_UpdateNotification then
        ParagonMicroButton_UpdateNotification()
    end
end

--- Called when UIParagon frame is shown
-- Closes other micro button frames to avoid overlapping
function UIParagon_OnShow()
    -- Close character frame
    if CharacterFrame and CharacterFrame:IsShown() then
        HideUIPanel(CharacterFrame)
    end

    -- Close spellbook
    if SpellBookFrame and SpellBookFrame:IsShown() then
        HideUIPanel(SpellBookFrame)
    end

    -- Close talent frame
    if PlayerTalentFrame and PlayerTalentFrame:IsShown() then
        HideUIPanel(PlayerTalentFrame)
    end

    -- Close achievement frame
    if AchievementFrame and AchievementFrame:IsShown() then
        HideUIPanel(AchievementFrame)
    end

    -- Close quest log
    if QuestLogFrame and QuestLogFrame:IsShown() then
        HideUIPanel(QuestLogFrame)
    end

    -- Close friends frame
    if FriendsFrame and FriendsFrame:IsShown() then
        HideUIPanel(FriendsFrame)
    end

    -- Close PVP frame
    if PVPParentFrame and PVPParentFrame:IsShown() then
        HideUIPanel(PVPParentFrame)
    end

    -- Close LFD frame
    if LFDParentFrame and LFDParentFrame:IsShown() then
        HideUIPanel(LFDParentFrame)
    end

    -- Update micro buttons state to reflect Paragon frame is shown
    if UpdateMicroButtons then
        UpdateMicroButtons()
    end
end

function UIParagon_OnHide()
    if (ParagonTutorialOverlay) then
        ParagonTutorialOverlay:Hide()
    end

    if (ParagonTutorialHighlight) then
        ParagonTutorialHighlight:Hide()
    end

    if (ParagonTutorialTooltip) then
        ParagonTutorialTooltip:Hide()
    end

    if (Paragon_IsTutorialActive()) then
        Paragon_RemoveActivateTutorial()
    end

    -- Update micro buttons state to reflect Paragon frame is hidden
    if UpdateMicroButtons then
        UpdateMicroButtons()
    end
end

-- ============================================================================
-- STATISTICS UI BUILDING
-- ============================================================================

--- Legacy function for clearing statistics display
-- No longer needed as frames are now recycled instead of hidden/cleared
-- @deprecated Kept for backwards compatibility with Paragon_Network.lua
-- @usage This function does nothing - frame recycling system handles cleanup
function UIParagon_ClearStatistics()
    -- This function is kept for compatibility but does nothing
    -- Frames are now recycled based on their unique IDs
end

--- Rebuilds the entire statistics UI from ParagonData
-- Implements frame recycling system using unique IDs to prevent duplication
-- Creates new frames only when needed, reuses existing frames based on categoryId/statId
-- Only positions frames on initial creation, not on subsequent rebuilds
-- @usage Called by network handlers when server sends category/stat data
function UIParagon_RebuildStatistics()
    local statisticsList = UIParagon.Body.StatisticsList
    if not statisticsList then
        return
    end

    local Locales = GetLocaleTable()
    local yOffset = 0

    -- Process each category from ParagonData
    for _, category in ipairs(ParagonData.categories) do
        local categoryId = category.id
        local categoryStats = ParagonData.stats[categoryId]

        if categoryStats and #categoryStats > 0 then
            -- Get or create category line using unique ID (frame recycling)
            local lineName = "ParagonCategory_" .. categoryId
            local line = _G[lineName]

            if not line then
                -- Create new category line frame
                line = CreateFrame("Frame", lineName, statisticsList, "ParagonStatLineTemplate")
                -- Only position on first creation, not on subsequent rebuilds
                line:SetPoint("TOP", 0, yOffset)
            end

            line:Show()

            -- Update category title with localized text
            local categoryName = Locales[category.nameKey] or category.nameKey
            if line.Title and line.Title.Text then
                line.Title.Text:SetText(categoryName)
            end

            -- Process stats for this category
            local xOffset = 220  -- Start position after title section (185 + spacing)

            for _, stat in ipairs(categoryStats) do
                -- Generate unique frame name using categoryId_statId
                local statFrameName = "ParagonStat_" .. categoryId .. "_" .. stat.id
                local statFrame = _G[statFrameName]
                local isNewFrame = (statFrame == nil)

                if not statFrame then
                    -- Create new stat frame if it doesn't exist
                    statFrame = CreateFrame("Frame", statFrameName, line, "ParagonStatItemTemplate")
                    -- Only position on first creation
                    statFrame:SetPoint("LEFT", xOffset, 0)
                end

                statFrame:Show()

                -- Store IDs and limit for event handlers
                statFrame.categoryId = categoryId
                statFrame.statId = stat.id
                statFrame.statLimit = stat.limit

                -- Get localized stat name and description
                local statName = Locales[stat.nameKey] or stat.nameKey
                local statDesc = Locales[stat.descKey] or stat.descKey

                -- Store for tooltip display
                statFrame.statTitle = statName
                statFrame.statDescription = statDesc

                -- Set stat icon texture
                SetPortraitToTexture(statFrame.Icon, stat.icon)

                -- Check if this stat has pending changes and use that value instead
                local displayValue = stat.value
                local key = categoryId .. "_" .. stat.id
                if PendingChanges and PendingChanges.stats and PendingChanges.stats[key] then
                    displayValue = PendingChanges.stats[key].value
                end

                -- Update value badge with current or pending stat value
                if statFrame.Value and statFrame.Value.Text then
                    local valueText = tostring(displayValue)
                    statFrame.Value.Text:SetText(valueText)

                    -- Dynamic horizontal offset based on digit count for proper centering
                    local numDigits = string.len(valueText)
                    local xStatOffset = -1
                    if (numDigits == 1) then
                        xStatOffset = -0.5  -- Single digit offset
                    elseif (numDigits == 2) then
                        xStatOffset = -1    -- Double digit offset
                    end

                    statFrame.Value.Text:ClearAllPoints()
                    statFrame.Value.Text:SetPoint("CENTER", xStatOffset, 1)
                end

                -- Set description text (displayed on hover animation)
                if statFrame.Description then
                    statFrame.Description:SetText(statName)
                end

                -- Only setup event handlers for newly created frames (not on rebuild)
                if isNewFrame then
                    -- Enable mouse wheel scrolling
                    statFrame:EnableMouseWheel(true)
                    statFrame:SetScript("OnMouseWheel", function(self, delta)
                        if delta > 0 then
                            -- If statLimit is 0 (unlimited) or value + 5 is within limit
                            if (self.statLimit == 0) or ((stat.value + 5) <= (self.statLimit)) then
                                UIParagon_ModifyStatValue(self.categoryId, self.statId, 5)  -- Scroll up: +5
                            end
                        else
                            UIParagon_ModifyStatValue(self.categoryId, self.statId, -5) -- Scroll down: -5
                        end
                    end)

                    -- Mouse button interactions
                    statFrame:SetScript("OnMouseDown", function(self, button)
                        if button == "LeftButton" and ((self.statLimit == 0) or ((stat.value + 1) <= self.statLimit)) then
                            UIParagon_ModifyStatValue(self.categoryId, self.statId, 1)   -- Left click: +1
                        elseif button == "RightButton" then
                            UIParagon_ModifyStatValue(self.categoryId, self.statId, -1)  -- Right click: -1
                        elseif button == "MiddleButton" then
                            -- Middle click: Open dialog to choose Add/Remove then enter amount
                            StaticPopup_Show("PARAGON_STAT_CHOOSE_ACTION", nil, nil, {
                                categoryId = self.categoryId,
                                statId = self.statId,
                                statName = self.statTitle or "Stat"
                            })
                        end
                    end)
                end

                -- Reapply visual modification marker if this stat has pending changes
                if PendingChanges and PendingChanges.stats and PendingChanges.stats[key] then
                    UIParagon_MarkStatAsModified(categoryId, stat.id, true)
                end

                xOffset = xOffset + 75  -- Move right for next stat (55 width + 20 spacing)
            end

            yOffset = yOffset - 80  -- Space between category lines (50 height + 30 spacing)
        end
    end
end

-- ============================================================================
-- EXPERIENCE BAR
-- ============================================================================

--- Initializes the experience bar on load
-- Sets up child frame references, animation parameters, and default XP values
-- @param self Frame The experience bar frame
-- @usage Called automatically by XML OnLoad script
function UIParagonExperienceBar_OnLoad(self)
    -- Cache child frame references via parentKey
    self.statusbar = self.StatusBar
    self.hover_text = self.OverlayFrame.HoverText
    self.text = self.OverlayFrame.Text

    -- Initialize text visibility (hover text hidden by default)
    self.hover_text:SetAlpha(0)
    self.hover_text:SetText("")
    self.text:SetAlpha(1)
    self.text:SetText("0%")

    -- Animation parameters
    self.hoverStartY = 5       -- Starting Y position for hover text animation
    self.textY = 3             -- Normal text Y position
    self.isAnimating = false   -- Animation state flag

    -- Cache locale table
    self.Locales = GetLocaleTable()

    -- Initialize default XP values
    self.currentXP = 0
    self.maxXP = 150

    -- Set initial position for hover text
    self.hover_text:ClearAllPoints()
    self.hover_text:SetPoint("CENTER", 0, self.hoverStartY)
end

--- OnShow handler for experience bar
-- Currently unused but required by XML OnShow script
-- @param self Frame The experience bar frame
-- @deprecated Reserved for future functionality
function UIParagonExperienceBar_OnShow(self)
    -- Currently unused
end

--- Sets the experience bar values and updates display
-- Updates the StatusBar value and sets both text displays
-- @param self Frame The experience bar frame
-- @param current number Current XP amount
-- @param max number Maximum XP amount (default: 150)
-- @usage Called by UIParagon_OnClientReceiveXP from network layer
function UIParagonExperienceBar_SetExperience(self, current, max)
    self.currentXP = current or 0
    self.maxXP = max or 150

    -- Calculate percentage (0.0 to 1.0)
    local percentage = self.currentXP / self.maxXP

    -- Update StatusBar
    self.statusbar:SetMinMaxValues(0, self.maxXP)
    self.statusbar:SetValue(self.currentXP)

    -- Update percentage text (always visible)
    self.text:SetText(string.format("%d%%", percentage * 100))

    -- Update hover text (shows current/max format)
    self.hover_text:SetText(string.format(self.Locales.EXPERIENCE_TEXT, self.currentXP, self.maxXP))
end

--- Handles mouse enter event on experience bar
-- Initiates cross-fade animation from percentage to detailed XP display
-- @param self Frame The experience bar frame
-- @usage Called automatically by XML OnEnter script
function UIParagonExperienceBar_OnEnter(self)
    self.animStart = GetTime()
    self.animDuration = 0.3
    self.animType = "in"
    self.isAnimating = true
end

--- Handles mouse leave event on experience bar
-- Initiates cross-fade animation from detailed XP display back to percentage
-- @param self Frame The experience bar frame
-- @usage Called automatically by XML OnLeave script
function UIParagonExperienceBar_OnLeave(self)
    self.animStart = GetTime()
    self.animDuration = 0.3
    self.animType = "out"
    self.isAnimating = true
end

-- ============================================================================
-- STAT ITEMS
-- ============================================================================

--- Initializes a stat item frame on load
-- Sets up default icon, value text, and animation parameters
-- @param self Frame The stat item frame being initialized
-- @usage Called automatically by XML OnLoad script in ParagonStatItemTemplate
function UIParagonStatItem_OnLoad(self)
    -- Cache reference to description text
    self.description = self.Description

    -- Set default icon (question mark)
    SetPortraitToTexture(self.Icon, "Interface\\Icons\\INV_Misc_QuestionMark")

    -- Initialize value badge with 0
    if self.Value and self.Value.Text then
        self.Value.Text:SetText("0")
    end

    -- Initialize animation parameters
    self.description:SetAlpha(0)  -- Hidden by default
    self.descStartY = -45         -- Starting Y position for animation
    self.isAnimating = false      -- Animation state flag
end

--- Helper function to initialize a stat item with localized data
-- Sets icon, value, and description text using locale keys
-- @param self Frame The stat item frame to initialize
-- @param iconPath string Path to the icon texture
-- @param value number The stat value to display
-- @param localeKey string Locale key for stat name
-- @param descriptionKey string Locale key for stat description
-- @deprecated This function is not currently used by the dynamic system
-- @usage Legacy function kept for backwards compatibility
function UIParagonStatItem_Initialize(self, iconPath, value, localeKey, descriptionKey)
    local Locales = GetLocaleTable()

    -- Set icon texture
    SetPortraitToTexture(self.Icon, iconPath)

    -- Set value badge text
    if self.Value and self.Value.Text then
        self.Value.Text:SetText(tostring(value))
    end

    -- Set description text
    if self.Description and Locales[localeKey] then
        self.Description:SetText(Locales[localeKey])
    end

    -- Store for tooltip display
    self.statTitle = Locales[localeKey] or "Stat"
    self.statDescription = Locales[descriptionKey] or "Increase this statistic to improve your character."
end

--- Handles mouse enter event on stat item
-- Starts description fade-in animation, enables icon zoom, and shows tooltip
-- @param self Frame The stat item frame
-- @usage Called automatically by XML OnEnter script
function UIParagonStatItem_OnEnter(self)
    self.animStart = GetTime()
    self.animDuration = 0.3
    self.animType = "in"
    self.isAnimating = true
    self.zoomEnabled = true

    local Locales = GetLocaleTable()

    GameTooltip:SetOwner(self, "ANCHOR_BOTTOMRIGHT")
    GameTooltip:ClearLines()
    GameTooltip:SetMinimumWidth(400)

    if self.statTitle then
        GameTooltip:AddLine(self.statTitle, 1, 0.82, 0, 1, false)
    end

    if self.statDescription then
        GameTooltip:AddLine(self.statDescription, 1, 1, 1, 1, false)
    end

    -- Add limit information in gray color
    if self.statLimit and self.statLimit < 999999 then
        local limitText = string.format(Locales.TOOLTIP_LIMIT, self.statLimit)
        GameTooltip:AddLine(limitText, 0.7, 0.7, 0.7, 1, false)
    end

    GameTooltip:AddLine(" ")

    if Locales.TOOLTIP_INSTRUCTIONS then
        GameTooltip:AddLine(Locales.TOOLTIP_INSTRUCTIONS, 0, 0.8, 1, 1, true)
    end

    GameTooltip:SetPadding(16, 0)
    GameTooltip:Show()
end

--- Handles mouse leave event on stat item
-- Starts description fade-out animation, disables icon zoom, and hides tooltip
-- @param self Frame The stat item frame
-- @usage Called automatically by XML OnLeave script
function UIParagonStatItem_OnLeave(self)
    -- Start description fade-out animation
    self.animStart = GetTime()
    self.animDuration = 0.3
    self.animType = "out"
    self.isAnimating = true

    -- Disable zoom effect (animated in OnUpdate)
    self.zoomEnabled = false

    -- Hide tooltip
    GameTooltip:Hide()
end

-- ============================================================================
-- SHOW MAINMENU XP CHECKBOX
-- ============================================================================

--- Initialize the checkbox for showing Paragon XP on MainMenuBar
-- Sets the initial state from CVar and configures the label
-- @param self CheckButton The checkbox frame
function UIParagon_ShowMainMenuXP_OnLoad(self)
    -- Get saved setting from CVar, initialize if it doesn't exist
    local cvarValue = GetCVar("paragonShowMainMenuXP")
    if (cvarValue == nil) then
        -- CVar doesn't exist yet, create it with default value (0 = disabled)
        SetCVar("paragonShowMainMenuXP", "0")
        cvarValue = "0"
    end

    local isEnabled = (cvarValue == "1")

    self:SetChecked(isEnabled)

    local Locales = GetLocaleTable()
    self.Text:SetText(Locales.SHOW_MAINMENU_XP_LABEL or "Show XP bar on main interface")

    UIParagon_UpdateMainMenuXPVisibility()
end

--- Handle checkbox click event
-- Toggles the visibility of ParagonExpBar on MainMenuBar
-- @param self CheckButton The checkbox frame
function UIParagon_ShowMainMenuXP_OnClick(self)
    local isChecked = self:GetChecked()

    SetCVar("paragonShowMainMenuXP", isChecked and "1" or "0")

    UIParagon_UpdateMainMenuXPVisibility()

    if isChecked then
        PlaySound("igMainMenuOptionCheckBoxOn")
    else
        PlaySound("igMainMenuOptionCheckBoxOff")
    end
end

--- Handle checkbox hover event
-- Shows tooltip with description
-- @param self CheckButton The checkbox frame
function UIParagon_ShowMainMenuXP_OnEnter(self)
    local Locales = GetLocaleTable()
    GameTooltip:SetOwner(self, "ANCHOR_RIGHT")
    GameTooltip:SetText(Locales.SHOW_MAINMENU_XP_LABEL or "Show XP bar on main interface", 1, 1, 1)
    GameTooltip:AddLine(Locales.SHOW_MAINMENU_XP_TOOLTIP or "If checked, displays the Paragon experience bar above your character's XP bar at the bottom of the screen.", nil, nil, nil, true)
    GameTooltip:Show()
end

--- Update the visibility of ParagonExpBar based on checkbox state
-- This function is called when the checkbox is toggled or on load
function UIParagon_UpdateMainMenuXPVisibility()
    if not ParagonExpBar then return end

    local cvarValue = GetCVar("paragonShowMainMenuXP")
    if (cvarValue == nil) then
        SetCVar("paragonShowMainMenuXP", "0")
        cvarValue = "0"
    end

    local isEnabled = (cvarValue == "1")

    if isEnabled then
        ParagonExpBar_Update()
    else
        ParagonExpBar:Hide()
    end
end

-- ============================================================================
-- APPLY BUTTON
-- ============================================================================

--- Initializes the Apply button on load
-- Sets the localized button text
-- @param self Button The ApplyButton frame
function UIParagon_ApplyButton_OnLoad(self)
    local Locales = GetLocaleTable()
    self.Text:SetText(Locales.APPLY_BUTTON_TEXT)
end

--- Handles the Apply button click event
-- Sends all pending stat changes to the server
-- @param self Button The ApplyButton frame
function UIParagon_ApplyButton_OnClick(self)
    UIParagon_SendPendingChanges()
    PlaySound("igMainMenuOptionCheckBoxOn")
end

--- Marks a stat frame as modified or unmodified in the UI
-- Adds/removes a visual indicator (yellow border) to show pending changes
-- @param categoryId number The category ID containing the stat
-- @param statId number The stat ID to mark
-- @param isModified boolean true to mark as modified, false to remove marking
function UIParagon_MarkStatAsModified(categoryId, statId, isModified)
    local statisticsList = UIParagon.Body.StatisticsList
    if not statisticsList then return end

    -- Find the stat frame
    local statFrameName = "ParagonStat_" .. categoryId .. "_" .. statId
    local statFrame = _G[statFrameName]

    if not statFrame then return end

    -- Apply or remove visual indicator
    if isModified then
        -- Add yellow border to indicate modification
        if statFrame.Border then
            statFrame.Border:SetVertexColor(1, 0.82, 0, 1)  -- Golden/yellow color
        end
    else
        -- Reset to default white color
        if statFrame.Border then
            statFrame.Border:SetVertexColor(1, 1, 1, 1)
        end
    end
end

-- ============================================================================
-- TUTORIAL SYSTEM
-- ============================================================================

--- Shows the tutorial overlay
-- Starts the interactive step-by-step guide through the Paragon interface
function UIParagon_ShowTutorial()
    Paragon_TutorialStart()
end