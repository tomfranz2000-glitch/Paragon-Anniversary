--[[
    ParagonExpBar.lua
    Paragon Experience Bar display on MainMenuBar

    This module manages a Paragon XP bar positioned above the player's main XP bar.
    It handles positioning relative to ReputationWatchBar (if visible) and updates
    the display based on server data.

    Positioning rules:
    - If ReputationWatchBar is NOT visible: Replaces it (BOTTOM->TOP of MainMenuBar, offset -3)
    - If ReputationWatchBar IS visible: Positioned above it (BOTTOM->TOP of ReputationWatchBar, offset -2)

    @module ParagonExpBar
    @author Paragon Team
]]

--- Current Paragon XP values
-- @field currentXP number Current experience points
-- @field maxXP number Maximum experience points needed for next level
ParagonExpData = {
    currentXP = 0,
    maxXP = 150  -- Default max XP
}

--- Initialize ParagonExpBar on load
-- Sets up initial state and registers for text display
-- @param self Frame The ParagonExpBar frame
function ParagonExpBar_OnLoad(self)
    self.textLocked = false

    if ParagonExpBarOverlayFrameText then
        ParagonExpBarOverlayFrameText:Hide()
    end

    -- Initial update
    ParagonExpBar_Update()
end

--- Handle events for ParagonExpBar
-- @param self Frame The ParagonExpBar frame
-- @param event string Event name
-- @param ... any Event arguments
function ParagonExpBar_OnEvent(self, event, ...)
    if (event == "PLAYER_ENTERING_WORLD") then
        ParagonExpBar_Update()
    elseif (event == "PLAYER_LEVEL_UP") then
        ParagonExpBar_Update()
    elseif (event == "UPDATE_FACTION") then
        ParagonExpBar_UpdatePosition()
    end
end

--- Update the ParagonExpBar position based on ReputationWatchBar visibility
-- - If ReputationWatchBar is visible: stack above it
-- - If ReputationWatchBar is hidden: take its place above MainMenuBar
--
-- !! DO NOT "FIX" THE OVERLAP WITH THE PALADIN AURA BAR HERE !!
-- This bar is 1024 wide and anchored across the whole band that Blizzard
-- stacks its secondary bars into, so on a paladin it runs underneath
-- ShapeshiftBarFrame (paladin auras live on the stance bar in 3.3.5, not in
-- the buff frame; BonusActionBarFrame.xml:206). That overlap is known and
-- deliberate, because both alternatives are worse:
--
--   * Lifting THIS bar above the cluster was tried and reverted. It clears the
--     aura bar by floating the whole 1024px bar ~40px up, detached from the
--     main bar it belongs to, with the aura buttons poking through it. Worse
--     than the overlap it fixed.
--   * Moving the AURA BAR up cannot be done cleanly.
--     UIParent_ManageFramePositions (UIParent.lua:1949) does not position
--     anything itself, it dispatches to secure code via
--     FramePositionDelegate:SetAttribute("uiparent-manage", true), so any
--     SetPoint we apply is overwritten the next time the secure handler runs.
--     The supported hooks are `frame.ignoreFramePositionManager = true`
--     (UIParent.lua:1229), which means owning every layout case Blizzard
--     handles, or mutating UIPARENT_MANAGED_FRAME_POSITIONS
--     (UIParent.lua:1189), which the secure handler reads and which risks
--     taint and "action blocked" errors in combat.
--
-- If this is ever revisited, the cheap option is to narrow the bar so it stops
-- short of the aura bar horizontally, rather than moving anything vertically.
function ParagonExpBar_UpdatePosition()
    if (not ParagonExpBar) then return end

    ParagonExpBar:ClearAllPoints()

    if (ReputationWatchBar and ReputationWatchBar:IsShown()) then
        ParagonExpBar:SetPoint("BOTTOM", "ReputationWatchBar", "TOP", 0, -2)
    else
        ParagonExpBar:SetPoint("BOTTOM", "MainMenuBar", "TOP", 0, -2)
    end
end

--- Update the ParagonExpBar display with current XP values
-- Updates the StatusBar value and text display
-- Also checks if the bar should be visible based on player level and user settings
function ParagonExpBar_Update()
    if (not ParagonExpBar or not ParagonExpBar.StatusBar) then return end

    local currentXP = ParagonExpData.currentXP
    local maxXP = ParagonExpData.maxXP

    -- Update StatusBar range and value
    ParagonExpBar.StatusBar:SetMinMaxValues(0, maxXP)
    ParagonExpBar.StatusBar:SetValue(currentXP)

    ParagonExpBar_UpdateText()
    ParagonExpBar_UpdatePosition()

    local cvarValue = GetCVar("paragonShowMainMenuXP")
    if (cvarValue == nil) then
        SetCVar("paragonShowMainMenuXP", "0")
        cvarValue = "0"
    end

    local isEnabled = (cvarValue == "1")

    if (not isEnabled) then
        if (ParagonExpBar:IsShown()) then
            ParagonExpBar:Hide()
        end
    else
        ParagonExpBar:Show()
    end
end

--- Update the text display on the Paragon XP bar
-- Shows current/max XP and percentage
function ParagonExpBar_UpdateText()
    if (not ParagonExpBarOverlayFrameText) then return end

    local currentXP = ParagonExpData.currentXP
    local maxXP = ParagonExpData.maxXP

    -- Get localized text
    local Locales = GetLocaleTable()
    local experienceText = Locales and Locales.PARAGON_EXPERIENCE_TEXT or "Paragon %d / %d (%d%%)"

    -- Format: "Paragon 75 / 150 (50%)"
    local percentage = 0
    if (maxXP > 0) then
        percentage = math.floor((currentXP / maxXP) * 100)
    end

    local text = string.format(experienceText, currentXP, maxXP, percentage)
    ParagonExpBarOverlayFrameText:SetText(text)
end

--- Set Paragon experience values and update display
-- This function is called by the network layer when server sends XP data
-- @param current number Current Paragon XP
-- @param max number Maximum Paragon XP for current level
function ParagonExpBar_SetExperience(current, max)
    ParagonExpData.currentXP = current or 0
    ParagonExpData.maxXP = max or 150

    ParagonExpBar_Update()
end

--- Show the Paragon XP bar text
-- Can be locked to always show, or unlocked to only show on hover
-- @param lock string If "lock", text will always show; otherwise unlocked
function ShowParagonExpBarText(lock)
    if (not ParagonExpBar) then return end

    if (lock) then
        ParagonExpBar.textLocked = true
    end

    if (ParagonExpBarOverlayFrameText) then
        ParagonExpBarOverlayFrameText:Show()
    end
end

--- Hide the Paragon XP bar text
-- @param unlock string If "unlock", removes the lock; text will hide on mouse leave
function HideParagonExpBarText(unlock)
    if (not ParagonExpBar) then return end

    if (unlock) then
        ParagonExpBar.textLocked = false
    end

    if (ParagonExpBarOverlayFrameText and not ParagonExpBar.textLocked) then
        ParagonExpBarOverlayFrameText:Hide()
    end
end
