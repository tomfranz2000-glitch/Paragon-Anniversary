--[[
    Paragon_Tutorial.lua
    Guided tour of the Paragon window, reached from the "?" button.

    Walks the panel top to bottom -- level, experience, the reward track, the
    Codex -- dimming everything except the element being described and
    blocking interaction until the tour ends.

    !! THE STEP LIST IS BUILT ONCE, AT START, AND FILTERED BY VISIBILITY !!
    STEP_DEFS is a superset; a step whose frame does not resolve, or resolves
    to something hidden, is dropped before the tour begins rather than skipped
    while running. Two reasons:

      * The old version skipped at display time by calling
        Paragon_TutorialNext() from inside Paragon_TutorialShowStep(). That
        only skips FORWARD, so Previous lands on the unavailable step and is
        bounced forward again -- Back stops working. That was latent while
        every step targeted an XML frame that always exists; it goes live the
        moment the list includes frames the server builds on demand, which is
        exactly what the track and Codex steps are.
      * The step counter read #steps including any that would be skipped, so
        the tour could announce "Step 3/6" and then finish.

    And the case that was not hypothetical at all: the Codex calls
    HideLegacySpender(), which hides Body.TopSpacer, Body.StatisticsList and
    ApplyButton -- exactly what the old steps 4-6 pointed at. Those frames
    still EXIST, so the nil check above never fired and they were not even
    skipped: the tour highlighted empty space and explained a point-spender
    that no longer ships.

    Compatible with WoW 3.3.5 API (2008-2010 era)

    @module Paragon_Tutorial
]]

-- ============================================================================
-- TUTORIAL STATE
-- ============================================================================

--- @field active     boolean Whether the tour is running
--- @field currentStep number  Index into `steps` (1-based)
--- @field steps      table   The filtered step list for THIS run
local TutorialState = {
    active = false,
    currentStep = 1,
    steps = {}
}

-- ============================================================================
-- STEP DEFINITIONS
-- ============================================================================

--- Resolvers are functions because most of these frames are built in Lua when
--- the server's first payload arrives (the reward track and the whole Codex),
--- so they do not exist at file-load time and may not exist at all yet.
local STEP_DEFS = {
    -- ---- the banner -------------------------------------------------------
    {
        key = "TUTORIAL_LEVEL",
        frame = function()
            return UIParagon and UIParagon.TopBanner and UIParagon.TopBanner.Level
        end,
        position = "TOP", xOffset = 0, yOffset = -16
    },
    {
        key = "TUTORIAL_XP_BAR",
        frame = function()
            return UIParagon and UIParagon.TopBanner and UIParagon.TopBanner.ExperienceBar
        end,
        position = "TOP", xOffset = 0, yOffset = -16
    },
    {
        key = "TUTORIAL_MAINBAR_XP",
        frame = function() return UIParagon and UIParagon.ShowMainMenuXP end,
        position = "TOP", xOffset = 120, yOffset = -16
    },

    -- ---- the reward track -------------------------------------------------
    {
        key = "TUTORIAL_TRACK",
        noDim = true,
        frame = function() return UIParagonRewardTrack end,
        position = "TOP", xOffset = 0, yOffset = -16
    },
    {
        key = "TUTORIAL_TRACK_NODES",
        noDim = true,
        frame = function() return UIParagonRewardTrackClipper end,
        position = "TOP", xOffset = 0, yOffset = -16
    },
    {
        -- The racial pick is the ONLY node that responds to a click, and
        -- nothing on screen says so -- see UIParagonTrackNode_OnMouseUp.
        -- The milestone level comes from the server payload, so it is
        -- formatted in rather than hardcoded.
        key = "TUTORIAL_TRACK_CLICK",
        noDim = true,
        frame = function() return UIParagonRewardTrackClipper end,
        textArgs = function()
            return (ParagonRacialData and ParagonRacialData.milestone) or 1400
        end,
        available = function()
            return ParagonRacialData ~= nil and ParagonRacialData.milestone ~= nil
        end,
        position = "TOP", xOffset = 0, yOffset = -16
    },
    {
        -- No UI element of its own: class milestones ARE track nodes, and
        -- what they grant shows up in the talent pane and at the class
        -- trainer instead. Anchored to the track for that reason.
        key = "TUTORIAL_CLASS",
        noDim = true,
        frame = function() return UIParagonRewardTrack end,
        position = "TOP", xOffset = 0, yOffset = -16
    },

    -- ---- the codex --------------------------------------------------------
    {
        key = "TUTORIAL_CODEX",
        frame = function() return UIParagonCodexTitle end,
        position = "BOTTOM", xOffset = 0, yOffset = 16
    },
    {
        key = "TUTORIAL_CODEX_POINTS",
        frame = function() return UIParagonCodexRespec end,
        position = "BOTTOM", xOffset = 0, yOffset = 16
    },

    -- ---- wrap up on the button they just pressed --------------------------
    {
        key = "TUTORIAL_HELP_BUTTON",
        frame = function() return UIParagon and UIParagon.HelpButton end,
        position = "RIGHT", xOffset = -16, yOffset = 0
    },
}

--- Builds the step list for one run: resolve every frame, keep the ones that
--- are actually on screen, and cache the resolved frame so nothing has to be
--- looked up again mid-tour.
--- @return table Array of { frame, key, position, xOffset, yOffset, textArgs }
local function BuildSteps()
    local steps = {}
    for _, def in ipairs(STEP_DEFS) do
        local ok, frame = pcall(def.frame)
        if ok and frame and frame.IsVisible and frame:IsVisible()
                and (not def.available or def.available()) then
            table.insert(steps, {
                frame = frame,
                key = def.key,
                position = def.position,
                xOffset = def.xOffset,
                yOffset = def.yOffset,
                textArgs = def.textArgs,
                noDim = def.noDim,
            })
        end
    end
    return steps
end

-- ============================================================================
-- ALPHA BOOKKEEPING
-- ============================================================================

--- Dim everything the tour talks about, remembering what it looked like.
---
--- !! RESTORE THE CAPTURED VALUE, NEVER A HARDCODED 1 !!
--- UIParagon.HelpButton ships at alpha 0.5 by design (UIParagon.xml), so the
--- old "set everything back to 1" restore quietly brightened it for the rest
--- of the session every time the tour was run.
---
--- !! AND NEVER DIM A FRAME WHOSE ALPHA ALREADY MEANS SOMETHING !!
--- The reward track says "locked" by desaturating the icon and greying the
--- border and level label (UIParagon_RefreshRewardTrackLocks). Dimming the
--- whole strip to 0.5 lands on top of that and makes EARNED milestones read
--- as unearned -- field-reported. Steps flagged noDim keep their own alpha
--- for the whole tour and rely on the pulsing highlight border instead.
local function DimSteps()
    for _, step in ipairs(TutorialState.steps) do
        if not step.noDim then
            if step.originalAlpha == nil then
                step.originalAlpha = step.frame:GetAlpha()
            end
            step.frame:SetAlpha(0.5)
        end
    end
end

local function RestoreSteps()
    for _, step in ipairs(TutorialState.steps) do
        if step.originalAlpha ~= nil then
            step.frame:SetAlpha(step.originalAlpha)
        end
    end
end

-- ============================================================================
-- TUTORIAL UI FRAMES
-- ============================================================================

--- Invisible click/scroll blocker covering the panel for the duration.
local function CreateTutorialOverlay()
    local overlay = CreateFrame("Frame", "ParagonTutorialOverlay", UIParagon)
    overlay:SetFrameStrata("FULLSCREEN_DIALOG")
    overlay:SetFrameLevel(100)
    overlay:SetAllPoints(UIParagon)
    overlay:EnableMouse(true)
    overlay:EnableMouseWheel(true)
    overlay:Hide()
    return overlay
end

--- Pulsing gold border around the element under discussion.
--- OnUpdate rather than an AnimationGroup: 3.3.5 has no animation system.
local function CreateTutorialHighlight()
    local highlight = CreateFrame("Frame", "ParagonTutorialHighlight", UIParent)
    highlight:SetFrameStrata("FULLSCREEN_DIALOG")
    highlight:SetFrameLevel(101)
    highlight:Hide()

    local border = highlight:CreateTexture(nil, "OVERLAY")
    border:SetAllPoints()
    border:SetTexture("Interface\\QuestFrame\\UI-QuestLogTitleHighlight")
    border:SetBlendMode("ADD")
    border:SetVertexColor(1, 0.82, 0, 0.8)
    highlight.border = border

    highlight.pulseAlpha = 0.5
    highlight.pulseDirection = 1
    highlight.pulseSpeed = 1.0

    highlight:SetScript("OnUpdate", function(self, elapsed)
        if not self:IsShown() then return end

        self.pulseAlpha = self.pulseAlpha + ((elapsed / self.pulseSpeed) * self.pulseDirection)
        if self.pulseAlpha >= 1.0 then
            self.pulseAlpha = 1.0
            self.pulseDirection = -1
        elseif self.pulseAlpha <= 0.5 then
            self.pulseAlpha = 0.5
            self.pulseDirection = 1
        end

        self.border:SetVertexColor(1, 0.82, 0, self.pulseAlpha)
    end)

    return highlight
end

-- Vertical space the box needs around the wrapped description. Top: title row
-- plus divider. Bottom: the tallest thing down there is stepIcon, whose top
-- edge sits at y=58 (16 high, anchored BOTTOMLEFT at y=42), so 62 clears it
-- with 4px to spare -- the old 78 plus a literal "+ 12" was reserving a 32px
-- dead band under the text at every single step.
local TOOLTIP_CHROME_TOP    = 48
local TOOLTIP_CHROME_BOTTOM = 62
local TOOLTIP_TEXT_PAD      = 10
local TOOLTIP_MIN_HEIGHT    = 170
-- Pure runaway stop. The tallest step computes to roughly 300 and UIParagon is
-- 710 tall, so nothing real approaches this; it exists only so a garbage
-- measurement cannot produce a screen-filling frame.
local TOOLTIP_MAX_HEIGHT    = 600
local TOOLTIP_WIDTH         = 420
-- !! THE WRAP WIDTH IS A CONSTANT, NOT TWO OPPOSING ANCHORS !!
-- Every wrapping body FontString in FrameXML is one anchor + an explicit width
-- + height 0 (QuestInfo.xml:279-283 x=285 y=0, StaticPopup.xml:53-63 x=290
-- y=0). Where Blizzard DOES use two horizontal anchors it pairs them with a
-- fixed height and maxLines -- i.e. exactly where an ellipsis is wanted
-- (InterfaceOptionsPanels.xml:64-80). This addon's own non-truncating wrapped
-- text already follows the good pattern: Paragon_RacialPick.lua:197-199.
local TOOLTIP_TEXT_WIDTH    = TOOLTIP_WIDTH - 32   -- 16px inset each side
local TOOLTIP_TEXT_SPACING  = 3

--- Step text, navigation buttons and the step counter.
local function CreateTutorialTooltip()
    local tooltip = CreateFrame("Frame", "ParagonTutorialTooltip", UIParent)
    tooltip:SetFrameStrata("FULLSCREEN_DIALOG")
    tooltip:SetFrameLevel(102)
    tooltip:SetWidth(TOOLTIP_WIDTH)
    tooltip:SetHeight(TOOLTIP_MIN_HEIGHT)
    tooltip:Hide()

    tooltip:SetBackdrop({
        bgFile = "Interface\\DialogFrame\\UI-DialogBox-Background-Dark",
        edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border",
        tile = true,
        tileSize = 16,
        edgeSize = 16,
        insets = { left = 4, right = 4, top = 4, bottom = 4 }
    })
    tooltip:SetBackdropColor(0.05, 0.05, 0.1, 1)
    tooltip:SetBackdropBorderColor(0.90, 0.80, 0.50, 1)

    local title = tooltip:CreateFontString(nil, "OVERLAY", "GameFontNormalLarge")
    title:SetPoint("TOPLEFT", 12, -12)
    title:SetTextColor(1, 0.82, 0)
    tooltip.Title = title

    local divider = tooltip:CreateTexture(nil, "ARTWORK")
    divider:SetPoint("TOPLEFT", 12, -38)
    divider:SetPoint("TOPRIGHT", -12, -38)
    divider:SetHeight(1)
    divider:SetTexture(1, 1, 1, 0.1)

    -- One anchor, explicit width, height 0 -- see TOOLTIP_TEXT_WIDTH above for
    -- why the two-anchor form is the wrong shape for text meant to grow.
    local description = tooltip:CreateFontString(nil, "OVERLAY", "GameFontHighlight")
    description:SetPoint("TOPLEFT", 16, -TOOLTIP_CHROME_TOP)
    description:SetWidth(TOOLTIP_TEXT_WIDTH)
    description:SetHeight(0)                -- 0 means auto-grow, never cap
    description:SetJustifyH("LEFT")
    description:SetJustifyV("TOP")
    description:SetSpacing(TOOLTIP_TEXT_SPACING)
    -- Both are real FontString methods in 3.3.5 -- verified in Wow.exe, where
    -- they sit in the same method-name table as GetStringHeight and SetSpacing
    -- (0x5e9254 / 0x5e926c). Guarded anyway so an odd client cannot error.
    if description.SetWordWrap then description:SetWordWrap(true) end
    if description.SetNonSpaceWrap then description:SetNonSpaceWrap(false) end
    tooltip.Description = description

    local stepIcon = tooltip:CreateTexture(nil, "OVERLAY")
    stepIcon:SetWidth(16)
    stepIcon:SetHeight(16)
    stepIcon:SetPoint("BOTTOMLEFT", 15, 42)
    stepIcon:SetTexture("Interface\\Buttons\\UI-GuildButton-PublicNote-Up")

    local stepCounter = tooltip:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    stepCounter:SetPoint("LEFT", stepIcon, "RIGHT", 5, 0)
    stepCounter:SetTextColor(0.9, 0.85, 0.6)
    tooltip.StepCounter = stepCounter

    local buttonDivider = tooltip:CreateTexture(nil, "ARTWORK")
    buttonDivider:SetPoint("BOTTOMLEFT", 12, 38)
    buttonDivider:SetPoint("BOTTOMRIGHT", -12, 38)
    buttonDivider:SetHeight(1)
    buttonDivider:SetTexture(1, 1, 1, 0.1)

    local prevButton = CreateFrame("Button", nil, tooltip, "UIPanelButtonTemplate")
    prevButton:SetWidth(110)
    prevButton:SetHeight(24)
    prevButton:SetPoint("BOTTOMLEFT", 15, 8)
    prevButton:SetScript("OnClick", function() Paragon_TutorialPrevious() end)
    tooltip.PrevButton = prevButton

    local nextButton = CreateFrame("Button", nil, tooltip, "UIPanelButtonTemplate")
    nextButton:SetWidth(110)
    nextButton:SetHeight(24)
    nextButton:SetPoint("BOTTOM", 0, 8)
    nextButton:SetScript("OnClick", function() Paragon_TutorialNext() end)
    tooltip.NextButton = nextButton

    local closeButton = CreateFrame("Button", nil, tooltip, "UIPanelButtonTemplate")
    closeButton:SetWidth(110)
    closeButton:SetHeight(24)
    closeButton:SetPoint("BOTTOMRIGHT", -15, 8)
    closeButton:SetScript("OnClick", function() Paragon_TutorialEnd() end)
    tooltip.CloseButton = closeButton

    return tooltip
end

-- ============================================================================
-- CONTROL
-- ============================================================================

--- Starts the tour. Safe to call while it is already running (no-op).
function Paragon_TutorialStart()
    if TutorialState.active then return end

    if not ParagonTutorialOverlay then CreateTutorialOverlay() end
    if not ParagonTutorialHighlight then CreateTutorialHighlight() end
    if not ParagonTutorialTooltip then CreateTutorialTooltip() end

    TutorialState.steps = BuildSteps()
    if #TutorialState.steps == 0 then
        -- Nothing on screen to describe (the panel's contents arrive from the
        -- server); saying nothing beats an empty highlighted rectangle.
        return
    end

    TutorialState.active = true
    TutorialState.currentStep = 1

    ParagonTutorialOverlay:Show()
    DimSteps()
    Paragon_TutorialShowStep(1)
end

--- Ends the tour and puts every alpha back the way it was.
function Paragon_TutorialEnd()
    if not TutorialState.active then return end

    RestoreSteps()

    TutorialState.active = false
    TutorialState.currentStep = 1
    TutorialState.steps = {}

    if ParagonTutorialOverlay then ParagonTutorialOverlay:Hide() end
    if ParagonTutorialHighlight then ParagonTutorialHighlight:Hide() end
    if ParagonTutorialTooltip then
        ParagonTutorialTooltip:SetScript("OnUpdate", nil)
        ParagonTutorialTooltip:Hide()
    end
end

function Paragon_TutorialNext()
    if not TutorialState.active then return end

    local nextStep = TutorialState.currentStep + 1
    if nextStep > #TutorialState.steps then
        Paragon_TutorialEnd()
        return
    end
    Paragon_TutorialShowStep(nextStep)
end

function Paragon_TutorialPrevious()
    if not TutorialState.active then return end

    local prevStep = TutorialState.currentStep - 1
    if prevStep < 1 then return end
    Paragon_TutorialShowStep(prevStep)
end

-- ============================================================================
-- TOOLTIP PLACEMENT AND SIZING
-- ============================================================================

local TUTORIAL_ANCHOR_MAP = {
    ["TOP"]         = "BOTTOM",
    ["BOTTOM"]      = "TOP",
    ["LEFT"]        = "RIGHT",
    ["RIGHT"]       = "LEFT",
    ["TOPLEFT"]     = "BOTTOMRIGHT",
    ["TOPRIGHT"]    = "BOTTOMLEFT",
    ["BOTTOMLEFT"]  = "TOPRIGHT",
    ["BOTTOMRIGHT"] = "TOPLEFT"
}

--- Anchors the box to the step's element, then pulls it back inside UIParagon
--- if that put it over an edge. Split out of ShowStep because the height is
--- now decided AFTER the box is placed, so the clamp has to run again.
local function PositionTutorialTooltip(step, targetFrame)
    local tip = ParagonTutorialTooltip
    if not (tip and step and targetFrame) then return end

    local position = step.position or "BOTTOM"
    local xOffset = step.xOffset or 0
    local yOffset = step.yOffset or -15
    local targetAnchor = TUTORIAL_ANCHOR_MAP[position] or position

    tip:ClearAllPoints()
    tip:SetPoint(position, targetFrame, targetAnchor, xOffset, yOffset)

    -- Clamp to UIParagon, not to the screen: the tour is about this panel and
    -- a box floating off beside it reads as a bug.
    local panelLeft, panelRight = UIParagon:GetLeft(), UIParagon:GetRight()
    local panelTop, panelBottom = UIParagon:GetTop(), UIParagon:GetBottom()
    local tipLeft, tipRight = tip:GetLeft(), tip:GetRight()
    local tipTop, tipBottom = tip:GetTop(), tip:GetBottom()

    local dx, dy = 0, 0
    if tipLeft and panelLeft and tipLeft < panelLeft then
        dx = panelLeft - tipLeft + 10
    elseif tipRight and panelRight and tipRight > panelRight then
        dx = panelRight - tipRight - 10
    end
    if tipTop and panelTop and tipTop > panelTop then
        dy = panelTop - tipTop - 10
    elseif tipBottom and panelBottom and tipBottom < panelBottom then
        dy = panelBottom - tipBottom + 10
    end

    -- One re-anchor carrying BOTH corrections. Doing the horizontal and
    -- vertical fixes as two ClearAllPoints/SetPoint pairs makes the second
    -- discard the first's offset, so a box overflowing a corner only ever got
    -- pushed back on one axis.
    if dx ~= 0 or dy ~= 0 then
        tip:ClearAllPoints()
        tip:SetPoint(position, targetFrame, targetAnchor, xOffset + dx, yOffset + dy)
    end
end

--- Reads the height the wrapped text ended up needing.
--- !! GetHeight IS THE CALL BLIZZARD ACTUALLY USES -- GetStringHeight APPEARS
--- IN ZERO OF THE 293 FrameXML FILES !! (StaticPopup.lua:2922
--- `32 + text:GetHeight() + 8 + button1:GetHeight()`, WatchFrame.lua:604,
--- GossipFrame.lua:172.) GetStringHeight is kept as a floor via max() because
--- it costs nothing and the two are independent.
local function MeasureDescription(desc)
    local h = desc:GetHeight() or 0
    local alt = (desc.GetStringHeight and desc:GetStringHeight()) or 0
    if alt > h then h = alt end
    return h
end

local function ClampTooltipHeight(h)
    if h < TOOLTIP_MIN_HEIGHT then return TOOLTIP_MIN_HEIGHT end
    if h > TOOLTIP_MAX_HEIGHT then return TOOLTIP_MAX_HEIGHT end
    return h
end

--- Sets the text and sizes the box around it.
--- !! ONLY VALID WITH THE TOOLTIP ALREADY SHOWN !!
--- Blizzard never measures a hidden frame. StaticPopup_Show anchors, calls
--- `dialog:Show()` (StaticPopup.lua:3271) and only THEN calls
--- StaticPopup_Resize (:3273) -- under its own comment "Finally size and show
--- the dialog". GossipFrame.lua:21 does ShowUIPanel before GossipFrameUpdate
--- at :27; LFDFrame.xml:683-685 drives its measuring update from <OnShow>.
--- The previous version of this file did the exact opposite: it measured
--- while the tooltip was still hidden and called Show() forty lines later.
local function SizeTutorialTooltip(text)
    local tip = ParagonTutorialTooltip
    local desc = tip.Description

    -- Height 0 before SetText. A non-zero height on a FontString is 3.3.5's
    -- truncation mechanism, so clearing it each time means nothing -- a
    -- template, another addon, an earlier step -- can leave a cap behind.
    desc:SetHeight(0)
    desc:SetWidth(TOOLTIP_TEXT_WIDTH)
    desc:SetText(text)

    local height = ClampTooltipHeight(
        TOOLTIP_CHROME_TOP + MeasureDescription(desc)
        + TOOLTIP_TEXT_PAD + TOOLTIP_CHROME_BOTTOM)
    tip:SetHeight(height)
    return height
end

--- Second pass, one frame after the box became visible. GROWS ONLY.
---
--- This is the insurance, and it is what makes the fix hold on the FIRST step
--- of a cold /reload: it depends on nothing from a previous step, only on the
--- frame having been rendered once. Growing only -- the same principle as
--- StaticPopup's maxHeightSoFar clamp -- means a bad reading here can never
--- collapse the box, and it breaks the self-locking failure the old code had,
--- where one short measurement sized the box small and it never recovered.
---
--- Self-cancelling, so it costs exactly one OnUpdate tick per step. It claims
--- the tooltip's OnUpdate slot; nothing else in the addon uses it (only the
--- highlight frame has one), and Paragon_TutorialEnd clears it.
local function TutorialTooltip_DeferredResize(self)
    self:SetScript("OnUpdate", nil)

    local desc = self.Description
    if not desc then return end

    local want = ClampTooltipHeight(
        TOOLTIP_CHROME_TOP + MeasureDescription(desc)
        + TOOLTIP_TEXT_PAD + TOOLTIP_CHROME_BOTTOM)

    if want > (self:GetHeight() or 0) + 0.5 then
        self:SetHeight(want)
        PositionTutorialTooltip(self.tutorialStep, self.tutorialTarget)
    end
end

--- Moves the highlight and the text box to one step.
--- Every step in TutorialState.steps is known-good (BuildSteps filtered them),
--- so there is no skip path here and Previous always works.
--- @param stepIndex number 1-based index into TutorialState.steps
function Paragon_TutorialShowStep(stepIndex)
    if not TutorialState.active then return end
    if stepIndex < 1 or stepIndex > #TutorialState.steps then return end

    local L = GetLocaleTable()
    local step = TutorialState.steps[stepIndex]
    local targetFrame = step.frame
    TutorialState.currentStep = stepIndex

    -- Undim only the current step, leaving noDim frames alone entirely
    for index, data in ipairs(TutorialState.steps) do
        if not data.noDim then
            data.frame:SetAlpha(index == stepIndex and 1 or 0.5)
        end
    end

    ParagonTutorialHighlight:ClearAllPoints()
    ParagonTutorialHighlight:SetPoint("TOPLEFT", targetFrame, "TOPLEFT", -5, 5)
    ParagonTutorialHighlight:SetPoint("BOTTOMRIGHT", targetFrame, "BOTTOMRIGHT", 5, -5)
    ParagonTutorialHighlight:Show()

    local text = L[step.key] or step.key
    if step.textArgs then
        text = string.format(text, step.textArgs())
    end

    local tip = ParagonTutorialTooltip
    tip.Title:SetText(L.TUTORIAL_TITLE or "Help - Paragon Interface")
    tip.StepCounter:SetText(string.format(
        L.TUTORIAL_STEP_COUNTER or "Step %d/%d",
        stepIndex,
        #TutorialState.steps
    ))

    if stepIndex > 1 then
        tip.PrevButton:Enable()
    else
        tip.PrevButton:Disable()
    end
    tip.PrevButton:SetText(L.TUTORIAL_BUTTON_PREVIOUS or "Previous")

    if stepIndex == #TutorialState.steps then
        tip.NextButton:SetText(L.TUTORIAL_BUTTON_FINISH or "Finish")
    else
        tip.NextButton:SetText(L.TUTORIAL_BUTTON_NEXT or "Next")
    end
    tip.CloseButton:SetText(L.TUTORIAL_BUTTON_CLOSE or "Close")

    -- Place, SHOW, then set the text and size the box. That order is the
    -- point of this function -- see SizeTutorialTooltip.
    tip.tutorialStep = step
    tip.tutorialTarget = targetFrame
    PositionTutorialTooltip(step, targetFrame)
    tip:Show()

    SizeTutorialTooltip(text)
    PositionTutorialTooltip(step, targetFrame)   -- height changed, re-clamp

    tip:SetScript("OnUpdate", TutorialTooltip_DeferredResize)
end

-- ============================================================================
-- PUBLIC API
-- ============================================================================

--- @return boolean True while the tour is running
function Paragon_IsTutorialActive()
    return TutorialState.active
end

--- Hard stop used by UIParagon_OnHide: closing the panel out from under the
--- tour must not leave frames dimmed for the rest of the session.
--- (The old body assigned an undeclared global `bool` here. Harmless in
--- practice -- nil is falsy and TutorialEnd had already run -- but it read as
--- a deliberate flag, so it is spelled out.)
function Paragon_RemoveActivateTutorial()
    if Paragon_IsTutorialActive() then
        Paragon_TutorialEnd()
    end
    TutorialState.active = false
end
