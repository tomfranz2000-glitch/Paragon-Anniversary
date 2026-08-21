--[[
    Paragon Reward Track: talent-frame masking for extended talent ranks.

    Talents extended past the retail 5-rank cap exist in the (static, shared)
    client DBCs, so every character of the class would see e.g. "5/9". This
    file hides the extra ranks until the owning milestone is earned:

      - rank badge recolored to the "maxed" gold instead of learnable green
      - tooltip "Rank x/9" rewritten to "Rank x/5", and the next-rank block
        stripped once the visible cap is reached
      - left-clicks past the visible cap are eaten client-side
        (the server refuses them anyway; this is presentation)

    Inspecting other players masks UNCONDITIONALLY (v1): their milestone
    state is unknown, so their talents always display at the base cap.

    Which talents are extended comes from the reward-track payload
    (ParagonRewardTrackData.milestones): rewards carrying a `talent` table of
    { tab, tier, column, base }. Unlock state = ParagonRewardTrackData
    .currentLevel >= that milestone's level. Everything degrades to "no
    masking" when data is absent.

    A `talent` table may also carry `hidden = true` (with base = 0), which
    marks a BRAND-NEW milestone talent rather than extra ranks on a retail
    one. Those rows also live in the static client DBC, so the whole button
    is hidden while the milestone is locked instead of merely recolored.
    The server refuses every rank of them anyway (EXTENDED_TALENTS base = 0);
    this is presentation, and it is the only way to hide a talent at all —
    the server never transmits the tree, only the talents you have learned.
]]

-- ============================================================================
-- EXTENDED-TALENT LOOKUP (derived live from the reward-track payload)
-- ============================================================================

--- Returns (def, milestoneLevel) for the extended talent at the given pane
-- coordinates, or nil if that position is not an extended talent.
-- @param tab number Selected talent tab (1-based)
-- @param tier number Talent row (1-based)
-- @param column number Talent column (1-based)
local function ParagonTalentMask_DefAt(tab, tier, column)
    local data = ParagonRewardTrackData
    if type(data) ~= "table" or type(data.milestones) ~= "table" then
        return nil
    end

    for _, milestone in ipairs(data.milestones) do
        if type(milestone) == "table" and type(milestone.rewards) == "table" then
            for _, reward in ipairs(milestone.rewards) do
                local t = type(reward) == "table" and reward.talent
                if type(t) == "table" and t.tab == tab and t.tier == tier
                    and t.column == column and type(t.base) == "number" then
                    return t, tonumber(milestone.level) or 0
                end
            end
        end
    end

    return nil
end

--- True once the player's paragon level has reached the milestone
local function ParagonTalentMask_Unlocked(milestoneLevel)
    local data = ParagonRewardTrackData
    local level = data and tonumber(data.currentLevel) or 0
    return level >= milestoneLevel
end

--- Masking applies when the talent is extended and either this is an inspect
-- view (v1: always mask others) or the milestone is still locked.
-- Returns the def when masking applies.
local function ParagonTalentMask_Active(tab, tier, column, maxRank, inspect)
    local def, milestoneLevel = ParagonTalentMask_DefAt(tab, tier, column)
    if not def or maxRank <= def.base then
        return nil
    end
    if inspect or not ParagonTalentMask_Unlocked(milestoneLevel) then
        return def
    end
    return nil
end

-- ============================================================================
-- TALENT PANE: rank badge recolor
-- ============================================================================

-- A masked talent sitting at its visible cap shows the green "more ranks
-- available" state; repaint it to the gold maxed state. Mirrors the stock
-- visuals exactly: gold rank number + gold Slot ring (TalentFrameBase.lua's
-- displayRank < maxRank branch paints both green). The badge shows only the
-- rank number, so no text change is needed.
local function ParagonTalentMask_Repaint(talentFrame)
    if not talentFrame or talentFrame.pet or not talentFrame:IsShown() then return end

    local frameName = talentFrame:GetName()
    if not frameName then return end

    local tab = PanelTemplates_GetSelectedTab(talentFrame)
    if not tab then return end

    local inspect = talentFrame.inspect
    local preview = not inspect and GetCVarBool and GetCVarBool("previewTalents")
    local numTalents = GetNumTalents(tab, inspect, talentFrame.pet) or 0

    for i = 1, numTalents do
        local _, _, tier, column, rank, maxRank, _, _, previewRank =
            GetTalentInfo(tab, i, inspect, talentFrame.pet, talentFrame.talentGroup)
        local def = maxRank and ParagonTalentMask_Active(tab, tier, column, maxRank, inspect)
        if def and def.hidden then
            -- Brand-new milestone talents ship in the static client DBC, so
            -- every character of the class would see them. Hide the button
            -- outright while locked; TalentFrame_Update always Show()s it, so
            -- this runs after (hooksecurefunc + the deferred pass).
            --
            -- Only ever hide an EMPTY talent: on the inspect frame we mask
            -- unconditionally, and hiding a talent someone has spent points in
            -- would make their tab total stop adding up against the visible
            -- rows (GetTalentTabInfo still counts the hidden ranks).
            local shown = (preview and previewRank) or rank or 0
            local button = _G[frameName .. "Talent" .. i]
            if button and shown == 0 then
                button:Hide()
            end
        elseif def then
            local shown = (preview and previewRank) or rank or 0
            if shown >= def.base then
                local rankText = _G[frameName .. "Talent" .. i .. "Rank"]
                if rankText then
                    rankText:SetTextColor(NORMAL_FONT_COLOR.r, NORMAL_FONT_COLOR.g, NORMAL_FONT_COLOR.b)
                end
                local slot = _G[frameName .. "Talent" .. i .. "Slot"]
                if slot then
                    slot:SetVertexColor(1.0, 0.82, 0)
                end
            end
        end
    end
end

--- Repaint now and once more next frame — some paint paths on this custom
-- client run after the hook that triggered us, so the deferred pass wins
-- any ordering race.
local function ParagonTalentMask_RepaintSoon(talentFrame)
    ParagonTalentMask_Repaint(talentFrame)
    if C_Timer and C_Timer.After then
        C_Timer.After(0, function()
            ParagonTalentMask_Repaint(talentFrame)
        end)
    end
end

-- Trigger 1: the shared update function (covers player + inspect frames
-- whenever the paint goes through the Lua global)
hooksecurefunc("TalentFrame_Update", function(talentFrame)
    ParagonTalentMask_RepaintSoon(talentFrame)
end)

-- ============================================================================
-- TOOLTIP: "Rank x/9" -> "Rank x/5", next-rank block stripped at the cap
-- ============================================================================

hooksecurefunc(GameTooltip, "SetTalent", function(tip, tab, id, inspect, pet, group, preview)
    if pet or not tab or not id then return end

    local _, _, tier, column, rank, maxRank, _, _, previewRank =
        GetTalentInfo(tab, id, inspect, pet, group)
    local def = maxRank and ParagonTalentMask_Active(tab, tier, column, maxRank, inspect)
    if not def or def.hidden then
        return -- hidden talents have no visible button, so nothing to rewrite
    end

    local shown = (preview and not inspect and previewRank) or rank or 0
    local capped = shown >= def.base
    local nextRankLabel = TOOLTIP_TALENT_NEXT_RANK or "Next rank:"
    local stripping = false
    local changed = false

    for j = 1, tip:NumLines() do
        local left = _G["GameTooltipTextLeft" .. j]
        local text = left and left:GetText()
        if text then
            if stripping then
                left:SetText(nil)
                left:Hide()
                local right = _G["GameTooltipTextRight" .. j]
                if right then
                    right:SetText(nil)
                    right:Hide()
                end
                changed = true
            elseif text:find("%d+%s*/%s*" .. maxRank) then
                -- the "Rank x/9" line: show the masked cap instead
                left:SetText((text:gsub("(%d+%s*/%s*)" .. maxRank, "%1" .. def.base, 1)))
                changed = true
            elseif capped and text == nextRankLabel then
                -- at the visible cap there is no "next rank": strip the rest
                left:SetText(nil)
                left:Hide()
                stripping = true
                changed = true
            end
        end
    end

    if changed then
        tip:Show()
    end
end)

-- ============================================================================
-- CLICK GUARD: eat learn/preview clicks past the visible cap
-- ============================================================================

--- True when a left-click on the player frame's talent `id` must be eaten
local function ParagonTalentMask_BlocksClick(id)
    if not PlayerTalentFrame or PlayerTalentFrame.pet then
        return false
    end

    local tab = PanelTemplates_GetSelectedTab(PlayerTalentFrame)
    if not tab then return false end

    local _, _, tier, column, rank, maxRank, _, _, previewRank =
        GetTalentInfo(tab, id, false, PlayerTalentFrame.pet, PlayerTalentFrame.talentGroup)
    local def = maxRank and ParagonTalentMask_Active(tab, tier, column, maxRank, false)
    if not def then return false end

    local preview = GetCVarBool and GetCVarBool("previewTalents")
    local shown = (preview and previewRank) or rank or 0
    return shown >= def.base
end

--- Wraps the OnClick of every player talent button (bound once in
-- Blizzard_TalentUI's OnLoad, so rebinding here sticks) and adds repaint
-- triggers on every entry point that exists once Blizzard_TalentUI is in.
local function ParagonTalentMask_WrapButtons()
    for i = 1, (MAX_NUM_TALENTS or 40) do
        local button = _G["PlayerTalentFrameTalent" .. i]
        if button and not button.paragonMaskWrapped then
            local original = button:GetScript("OnClick")
            if original then
                button:SetScript("OnClick", function(self, mouseButton, ...)
                    if mouseButton == "LeftButton" and not IsModifiedClick("CHATLINK")
                        and ParagonTalentMask_BlocksClick(self:GetID()) then
                        return
                    end
                    original(self, mouseButton, ...)
                    -- a spent point may have just reached the visible cap
                    ParagonTalentMask_RepaintSoon(PlayerTalentFrame)
                end)
                button.paragonMaskWrapped = true
            end
        end
    end

    -- Trigger 2/3/4: LoD globals + frame show. These references are captured
    -- AFTER Blizzard_TalentUI loaded, so they fire even if some paint path
    -- bypasses the TalentFrame_Update global this client runs.
    if type(PlayerTalentFrame_Refresh) == "function" then
        hooksecurefunc("PlayerTalentFrame_Refresh", function()
            ParagonTalentMask_RepaintSoon(PlayerTalentFrame)
        end)
    end
    if type(PlayerTalentTab_OnClick) == "function" then
        hooksecurefunc("PlayerTalentTab_OnClick", function()
            ParagonTalentMask_RepaintSoon(PlayerTalentFrame)
        end)
    end
    if PlayerTalentFrame and PlayerTalentFrame.HookScript then
        PlayerTalentFrame:HookScript("OnShow", function()
            ParagonTalentMask_RepaintSoon(PlayerTalentFrame)
        end)
    end
end

-- Blizzard_TalentUI is load-on-demand: wrap now if it is already in, else
-- wait for it
if IsAddOnLoaded("Blizzard_TalentUI") then
    ParagonTalentMask_WrapButtons()
else
    local watcher = CreateFrame("Frame")
    watcher:RegisterEvent("ADDON_LOADED")
    watcher:SetScript("OnEvent", function(self, event, addonName)
        if addonName == "Blizzard_TalentUI" then
            ParagonTalentMask_WrapButtons()
            self:UnregisterEvent("ADDON_LOADED")
        end
    end)
end
