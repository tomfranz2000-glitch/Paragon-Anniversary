--[[
    Paragon: "Sudden Light" proc feedback (milestone 1325).

    WotLK has NO spell-activation overlay. The glowing action button and the
    screen-edge flare people remember from Art of War are a Cataclysm feature:
    SpellActivationOverlay.dbc does not exist in any 3.3.5 client archive, and
    Wow.exe contains none of the SPELL_ACTIVATION_OVERLAY_* event names, so
    FrameXML could not register them even if we shipped the DBC. This file
    rebuilds that feedback in Lua, out of art the client already ships.

    What the game gives us for free (from the buff's own spell data):
      - a 15s buff icon with countdown
      - green floating combat text on proc (AttributesEx6 0x40 — without that
        bit nothing shows, because the aura-text cvar defaults OFF)
      - a holy chest flash + HolyProtection.wav (SpellVisualID 11955)
      - no cast bar at all on the next Holy Light (server-authoritative)

    What this file adds:
      - a pulsing gold glow on every action button holding ANY rank of Holy
        Light, for as long as the buff is up
      - a soft full-screen edge flare, alpha-pulsed, mimicking the Cataclysm
        spell alert

    Taint: action buttons ARE protected (they inherit SecureActionButtonTemplate),
    so this file never calls SetScript on one, never sets an attribute, and never
    calls a protected function. It only parents plain textures to them and reads
    GetActionInfo — both insecure operations that cannot taint the secure path.
]]

local BUFF_SPELL = 1900129

-- Every Holy Light rank, stock plus the project's custom ranks 14/15. Matched
-- by NAME (localised via GetSpellInfo) rather than id, because UnitAura in
-- 3.3.5 does not return a spell id and action slots may hold any rank.
local HOLY_LIGHT_ANY_RANK_ID = 635

-- Client art, all verified present in locale-enUS.MPQ
local TEX_GLOW = "Interface\\Cooldown\\star4"
local TEX_RING = "Interface\\Buttons\\IconBorder-GlowRing"

local buffName = nil        -- resolved lazily; GetSpellInfo needs the DBC row
local holyLightName = nil
local active = false
local glows = {}            -- [button] = glow frame

-- ============================================================================
-- GLOW FRAMES
-- ============================================================================

local function BuildGlow(button)
    local glow = CreateFrame("Frame", nil, button)
    glow:SetFrameLevel((button:GetFrameLevel() or 0) + 8)
    glow:SetPoint("TOPLEFT", button, "TOPLEFT", -6, 6)
    glow:SetPoint("BOTTOMRIGHT", button, "BOTTOMRIGHT", 6, -6)

    local ring = glow:CreateTexture(nil, "OVERLAY")
    ring:SetTexture(TEX_RING)
    ring:SetBlendMode("ADD")
    ring:SetVertexColor(1.0, 0.9, 0.4)
    ring:SetAllPoints(glow)

    local star = glow:CreateTexture(nil, "OVERLAY")
    star:SetTexture(TEX_GLOW)
    star:SetBlendMode("ADD")
    star:SetVertexColor(1.0, 0.95, 0.6)
    star:SetPoint("CENTER", glow, "CENTER")
    star:SetWidth(52)
    star:SetHeight(52)

    -- AnimationGroups are real in 3.3.5: AlertFrames.xml and BNet.xml declare
    -- <AnimationGroup> with <Alpha change=... duration=.../>, and
    -- Blizzard_Calendar.xml uses looping="BOUNCE". Alpha uses SetChange(delta)
    -- here, NOT the later SetFromAlpha/SetToAlpha pair.
    local ag = glow:CreateAnimationGroup()
    ag:SetLooping("BOUNCE")
    local a = ag:CreateAnimation("Alpha")
    a:SetChange(-0.55)
    a:SetDuration(0.6)
    a:SetSmoothing("IN_OUT")
    glow.pulse = ag

    glow:Hide()
    return glow
end

--- Every action button frame name the default UI can show.
local BAR_PREFIXES = {
    "ActionButton", "MultiBarBottomLeftButton", "MultiBarBottomRightButton",
    "MultiBarLeftButton", "MultiBarRightButton", "BonusActionButton",
}

local function ForEachHolyLightButton(fn)
    if not holyLightName then
        return
    end
    for _, prefix in ipairs(BAR_PREFIXES) do
        for i = 1, 12 do
            local button = _G[prefix .. i]
            if button and button.action then
                -- 3.3.5 returns (type, id, subType, spellID). For "spell" the
                -- SECOND return is a SPELLBOOK INDEX, not a spell id — passing
                -- it to GetSpellInfo resolves a completely unrelated spell.
                -- The FOURTH return is the real spell id.
                local kind, id, _subType, spellID = GetActionInfo(button.action)
                local name
                if kind == "spell" and spellID then
                    name = GetSpellInfo(spellID)
                elseif kind == "macro" and id and GetMacroSpell then
                    local macroSpell = GetMacroSpell(id)
                    if macroSpell then
                        name = GetSpellInfo(macroSpell)
                    end
                end
                if name and name == holyLightName then
                    fn(button)
                end
            end
        end
    end
end

local function ShowGlows()
    ForEachHolyLightButton(function(button)
        local glow = glows[button]
        if not glow then
            glow = BuildGlow(button)
            glows[button] = glow
        end
        glow:Show()
        if glow.pulse then
            glow.pulse:Play()
        end
    end)
end

local function HideGlows()
    for _, glow in pairs(glows) do
        if glow.pulse then
            glow.pulse:Stop()
        end
        glow:Hide()
    end
end

-- ============================================================================
-- SCREEN FLARE
-- ============================================================================

local flare = nil

local function BuildFlare()
    local f = CreateFrame("Frame", "ParagonSuddenLightFlare", UIParent)
    f:SetFrameStrata("BACKGROUND")
    f:SetAllPoints(UIParent)

    -- two soft edge flares (left/right), the closest 3.3.5 art to the
    -- Cataclysm spell-alert wings
    for _, side in ipairs({ "LEFT", "RIGHT" }) do
        local t = f:CreateTexture(nil, "BACKGROUND")
        t:SetTexture(TEX_GLOW)
        t:SetBlendMode("ADD")
        t:SetVertexColor(1.0, 0.9, 0.45, 0.7)
        t:SetPoint(side, f, side, 0, 0)
        t:SetWidth(220)
        t:SetHeight(UIParent:GetHeight() * 0.8)
    end

    local ag = f:CreateAnimationGroup()
    ag:SetLooping("BOUNCE")
    local a = ag:CreateAnimation("Alpha")
    a:SetChange(-0.7)
    a:SetDuration(0.8)
    a:SetSmoothing("IN_OUT")
    f.pulse = ag

    f:Hide()
    return f
end

-- ============================================================================
-- STATE
-- ============================================================================

local function HasBuff()
    if not buffName then
        return false
    end
    for i = 1, 40 do
        local name = UnitBuff("player", i)
        if not name then
            return false
        end
        if name == buffName then
            return true
        end
    end
    return false
end

local function Refresh()
    if not buffName then
        buffName = GetSpellInfo(BUFF_SPELL)
        holyLightName = holyLightName or GetSpellInfo(HOLY_LIGHT_ANY_RANK_ID)
        if not buffName then
            return -- spell data not available (older client patch)
        end
    end

    local now = HasBuff()
    if now == active then
        return
    end
    active = now

    if active then
        ShowGlows()
        flare = flare or BuildFlare()
        flare:Show()
        if flare.pulse then
            flare.pulse:Play()
        end
    else
        HideGlows()
        if flare then
            if flare.pulse then
                flare.pulse:Stop()
            end
            flare:Hide()
        end
    end
end

local watcher = CreateFrame("Frame")
watcher:RegisterEvent("UNIT_AURA")
watcher:RegisterEvent("PLAYER_ENTERING_WORLD")
watcher:RegisterEvent("ACTIONBAR_SLOT_CHANGED")
watcher:RegisterEvent("ACTIONBAR_PAGE_CHANGED")
watcher:SetScript("OnEvent", function(self, event, unit)
    if event == "UNIT_AURA" and unit ~= "player" then
        return
    end
    if event == "ACTIONBAR_SLOT_CHANGED" or event == "ACTIONBAR_PAGE_CHANGED" then
        -- Bar layout moved: hide every cached glow and force Refresh() back
        -- through the activate path so ShowGlows() re-evaluates which buttons
        -- still hold Holy Light. Do NOT wipe `glows` — action button frames
        -- are permanent, so the cache is keyed on stable objects; clearing it
        -- would orphan the old glow frames (they cannot be destroyed in 3.3.5)
        -- and build a fresh set on every bar swap.
        HideGlows()
        active = false
    end
    Refresh()
end)
