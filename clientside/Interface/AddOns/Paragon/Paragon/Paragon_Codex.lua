--[[
    Paragon_Codex.lua
    The Codex: tiered point-spending panel (2026-08-18 redesign, layout v3)

    Replaces the flat statistics spender inside UIParagon with four node
    families (server: paragon_codex.lua, prefix "ParagonCodex"), laid out
    in two bands on a WHEEL-SCROLLED content frame under a fixed header
    (the panel is shorter than the node set — user spec, layout v4):

      band 1:  ATTRIBUTES (Greater->chain->Endless rows, left)
               WARFARE (5x2 rating grid with labels, right of it)
      band 2:  WARDS (Prismatic center + six petals, left)
               MASTERY (exotic-cost plaques, right of it)

    COORDINATE DOCTRINE (the hard-won lesson of layouts v1/v2, both of
    which scattered): UIParagon carries an inherited frame scale, and
    SetPoint offsets are interpreted in the POSITIONED element's own
    effective space — so mixing SetScale'd nodes with unscaled labels
    puts them in different coordinate systems and no single offset
    convention can line them up. Therefore NOTHING in this panel uses
    SetScale. Node size differences come from SetSize on the node frame
    plus an explicit Icon resize (the template's Bg/Border textures have
    no fixed size and auto-fill the frame). Every offset below is plain
    codex-frame units, and the whole panel inherits the panel's scale
    uniformly. Keep it that way.

    All content sits at x < 740 and two vertical bands, so it cannot
    overflow the panel whatever its actual width.

    States: gold ring = invested, green = maxed, gray + desaturated =
    gate locked, white = untouched. Chain bars light gold when the
    Endless gate opens. Left-click buy 1, shift-click buy 10,
    right-click refund 1, Respec (confirm) clears all — the server is
    the authority, every response repaints.

    The legacy spender (TopSpacer header, StatisticsList, and
    UIParagon.ApplyButton — a child of the TOP frame, not Body) is
    hidden here and re-hidden after every stock rebuild.
]]

local Locale = GetLocaleTable()

ParagonCodexData = {
    defs = nil,          -- ordered definition array from the server
    byId = {},           -- id -> def
    ranks = {},          -- id -> rank
    available = 0,
    nodes = {},          -- id -> node frame
    plaques = {},        -- id -> mastery plaque frame
    chains = {},         -- endless-node id -> chain bar texture
}

local PREFIX = "ParagonCodex"
local built = false

local ATTR_LABEL = {
    [1] = "Might", [3] = "Grace", [5] = "Fortitude",
    [7] = "Brilliance", [9] = "Serenity",
}
local WAR_LABEL = {
    [20] = "Crit", [21] = "Haste", [22] = "Hit", [23] = "Expertise",
    [24] = "Armor Pen", [25] = "Defense", [26] = "Dodge", [27] = "Parry",
    [28] = "Block", [29] = "Resilience",
    [38] = "Health", [39] = "Mana/5s",
}

-- ============================================================================
-- HELPERS
-- ============================================================================

local function RankOf(id)
    return ParagonCodexData.ranks[id] or 0
end

local function RankCost(def, rank)
    return def.cost + (def.step or 0) * (rank - 1)
end

local function GateOpen(def)
    if not def.requires then
        return true
    end
    return RankOf(def.requires.node) >= def.requires.rank
end

--- True when the server will refuse this node outright for the player's
--- class (node 57 is dead content for mages, who train the teleports).
--- Pushed as data so the client never offers an irreversible purchase that
--- the server is going to deny after the player has already confirmed it.
local function ClassDenied(def)
    if not def.deniedClass then
        return false
    end
    local _, _, classId = UnitClass("player")
    return classId == def.deniedClass
end

local function IsMaxed(def)
    return def.cap > 0 and RankOf(def.id) >= def.cap
end

local function BonusText(def)
    -- server-computed magnitudes: collection nodes (52/53) scale with the
    -- live mount/companion count, classlevel (58) with the paragon level and
    -- the player's class. Both arrive ready-made in the state push, and the
    -- per*rank fallback below would misstate them -- never fall through.
    if def.kind == "collection" or def.kind == "classlevel" then
        local text = ParagonCodexData.bonus and ParagonCodexData.bonus[def.id]
        if text then
            return (RankOf(def.id) > 0 and "Current bonus: " or "At rank 1: ") .. text
        end
        return def.kind == "classlevel" and "Scales with your Paragon level"
            or "Scales with your collection"
    end
    -- bundle nodes (54): the desc line already lists every grant — the
    -- green line just reports whether the package is active
    if def.kind == "bundle" then
        return RankOf(def.id) > 0 and "Currently active" or "Currently inactive"
    end
    -- skill nodes (56): binary grant, and the live skill value lives in the
    -- Skills pane rather than here — the per*rank fallback would be meaningless
    if def.kind == "skill" then
        if def.cap > 1 then
            -- multi-rank grant node (each rank teaches a different spell):
            -- a flat "Learned" would read identically at 1/6 and 6/6
            return string.format("Learned %d of %d", RankOf(def.id), def.cap)
        end
        return RankOf(def.id) > 0 and "Learned" or "Not learned"
    end
    local total = def.per * RankOf(def.id)
    if def.kind == "scaling" then
        return string.format("Current bonus: -%d effective levels", total)
    end
    -- mitigation (60): a percentage, and it has no NODE_UNIT entry — the
    -- generic "+N <unit>" fallback below would render "+3 " with a blank
    -- unit and the wrong sign
    if def.kind == "mitigation" then
        return string.format("Current bonus: %d%% less damage taken", total)
    end
    -- movespeed (61): same reason as mitigation — a percentage with no
    -- NODE_UNIT entry
    if def.kind == "movespeed" then
        return string.format("Current bonus: +%d%% movement speed", total)
    end
    local unit = def.unit or ""
    return string.format("Current bonus: +%d %s", total, unit)
end

local function SendCodex(data)
    SendClientRequest(PREFIX, 1, data)
end

-- ============================================================================
-- NODE WIDGETS
-- ============================================================================

local function Node_OnEnter(self)
    local def = self.codexDef
    if not def then return end
    GameTooltip:SetOwner(self, "ANCHOR_RIGHT")
    GameTooltip:ClearLines()
    GameTooltip:AddLine(def.name, 1, 0.82, 0, false)
    local rank = RankOf(def.id)
    if def.cap > 0 then
        GameTooltip:AddLine(string.format("Rank %d / %d", rank, def.cap), 0.7, 0.7, 0.7, false)
    else
        GameTooltip:AddLine(string.format("Rank %d (no cap)", rank), 0.7, 0.7, 0.7, false)
    end
    if def.desc then
        GameTooltip:AddLine(def.desc, 1, 1, 1, true)
    end
    GameTooltip:AddLine(BonusText(def), 0, 1, 0, false)
    if ClassDenied(def) then
        GameTooltip:AddLine(def.deniedText or "Your class cannot use this.", 1, 0.3, 0.3, true)
        GameTooltip:Show()
        return
    end
    if not GateOpen(def) then
        local gate = ParagonCodexData.byId[def.requires.node]
        GameTooltip:AddLine(string.format("Requires %s at rank %d",
            gate and gate.name or "?", def.requires.rank), 1, 0.3, 0.3, true)
    elseif not IsMaxed(def) then
        local cost = RankCost(def, rank + 1)
        GameTooltip:AddLine(string.format("Next rank: %d point%s", cost,
            cost == 1 and "" or "s"), 1, 0.82, 0, false)
        -- never offer a refund on a permanent node: the server refuses it
        -- (HandleRefund) and a respec keeps it, so the stock hint would lie
        if def.permanent then
            GameTooltip:AddLine("Left-click: buy   |cffff4040(permanent — no refund)|r", 0.5, 0.5, 0.5, false)
        else
            GameTooltip:AddLine("Left-click: +1   Shift: +10   Right-click: refund", 0.5, 0.5, 0.5, false)
        end
    else
        GameTooltip:AddLine("Maxed", 0, 1, 0, false)
        if def.permanent then
            GameTooltip:AddLine("|cffff4040Permanent — cannot be refunded|r", 0.5, 0.5, 0.5, false)
        else
            GameTooltip:AddLine("Right-click: refund", 0.5, 0.5, 0.5, false)
        end
    end
    GameTooltip:Show()
end

local function Node_OnClick(self, button)
    local def = self.codexDef
    if not def then return end
    if ClassDenied(def) then
        return -- the server always refuses; the tooltip explains why
    end
    if button == "RightButton" then
        if def.permanent then
            return -- the server always denies; the tooltip already says so
        end
        SendCodex({ action = "refund", node = def.id })
    elseif def.permanent then
        if IsMaxed(def) then
            -- HandleBuy breaks at cap with bought == 0, so it does not even
            -- Deny -- prompting here would be a silent no-op
            return
        end
        -- Every other node is right-click refundable, so a misclick has
        -- always been free. A permanent node cannot be undone by refund OR
        -- respec, so one stray click would commit the points forever --
        -- confirm first.
        local cost = RankCost(def, RankOf(def.id) + 1)
        StaticPopup_Show("PARAGON_CODEX_PERMANENT", def.name, cost,
            { node = def.id })
    else
        SendCodex({ action = "buy", node = def.id, count = IsShiftKeyDown() and 10 or 1 })
    end
end

local nodeCount = 0

--- diameter is the ring size in codex units — NO SetScale (see header)
local function CreateNode(parent, def, diameter)
    nodeCount = nodeCount + 1
    local node = CreateFrame("Frame", "ParagonCodexNode_" .. nodeCount, parent, "ParagonTrackNodeTemplate")
    node:SetSize(diameter, diameter)
    if node.Icon then
        node.Icon:SetSize(diameter * 0.76, diameter * 0.74)
    end
    node.codexDef = def
    node:EnableMouse(true)
    node:SetScript("OnEnter", Node_OnEnter)
    node:SetScript("OnLeave", function() GameTooltip:Hide() end)
    node:SetScript("OnMouseUp", Node_OnClick)
    SetPortraitToTexture(node.Icon, def.icon or "Interface\\Icons\\INV_Misc_QuestionMark")
    if node.Level then
        node.Level:ClearAllPoints()
        node.Level:SetPoint("TOP", node, "BOTTOM", 0, 2)
        node.Level:SetText("0")
    end
    ParagonCodexData.nodes[def.id] = node
    return node
end

local function PaintNode(node)
    local def = node.codexDef
    local rank = RankOf(def.id)
    if node.Level then
        if def.cap > 0 then
            node.Level:SetText(string.format("%d/%d", rank, def.cap))
        else
            node.Level:SetText(tostring(rank))
        end
    end
    local locked = not GateOpen(def)
    if node.Icon then
        node.Icon:SetDesaturated(locked and 1 or nil)
    end
    if node.Border then
        if locked then
            node.Border:SetVertexColor(0.45, 0.45, 0.45)
        elseif IsMaxed(def) then
            node.Border:SetVertexColor(0.2, 0.9, 0.2)
        elseif rank > 0 then
            node.Border:SetVertexColor(1, 0.82, 0)
        else
            node.Border:SetVertexColor(1, 1, 1)
        end
    end
    if node.Level then
        if locked then
            node.Level:SetTextColor(0.5, 0.5, 0.5)
        else
            node.Level:SetTextColor(1, 0.82, 0)
        end
    end
end

-- ============================================================================
-- PANEL CONSTRUCTION (single coordinate space — see header doctrine)
-- ============================================================================

local function SectionLabel(codex, text, x, y)
    local label = codex:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    label:SetPoint("TOPLEFT", codex, "TOPLEFT", x, y)
    label:SetTextColor(0.90, 0.80, 0.50, 1)
    label:SetText(text)
    return label
end

local function HideLegacySpender()
    if UIParagon and UIParagon.Body then
        if UIParagon.Body.TopSpacer then
            UIParagon.Body.TopSpacer:Hide()
        end
        if UIParagon.Body.StatisticsList then
            UIParagon.Body.StatisticsList:Hide()
        end
    end
    if UIParagon and UIParagon.ApplyButton then
        UIParagon.ApplyButton:Hide()
    end
end

local function BuildPanel()
    if built or not UIParagon or not UIParagon.Body or not ParagonCodexData.defs then
        return
    end
    built = true

    HideLegacySpender()
    if UIParagon_RebuildStatistics then
        hooksecurefunc("UIParagon_RebuildStatistics", HideLegacySpender)
    end

    local codex = CreateFrame("Frame", "UIParagonCodex", UIParagon.Body)
    if UIParagonRewardTrack then
        codex:SetPoint("TOPLEFT", UIParagonRewardTrack, "BOTTOMLEFT", 0, -10)
        codex:SetPoint("TOPRIGHT", UIParagonRewardTrack, "BOTTOMRIGHT", 0, -10)
    else
        codex:SetPoint("TOPLEFT", UIParagon.Body, "TOPLEFT", 0, -260)
        codex:SetPoint("TOPRIGHT", UIParagon.Body, "TOPRIGHT", 0, -260)
    end
    -- bottom anchors to the PANEL, not Body: Body ends where the old
    -- statistics list did, ~150px above the visible panel edge, and the
    -- scroll viewport would clip there (field-proven)
    codex:SetPoint("BOTTOM", UIParagon, "BOTTOM", 0, 24)

    local title = codex:CreateFontString("UIParagonCodexTitle", "OVERLAY", "GameFontNormalHuge")
    title:SetPoint("TOPLEFT", codex, "TOPLEFT", 20, 0)
    title:SetTextColor(0.90, 0.80, 0.50, 1)
    title:SetText("THE CODEX")

    local respec = CreateFrame("Button", "UIParagonCodexRespec", codex, "UIPanelButtonTemplate")
    respec:SetSize(80, 22)
    respec:SetPoint("TOPRIGHT", codex, "TOPRIGHT", -20, 0)
    respec:SetText("Respec")
    respec:SetScript("OnClick", function()
        StaticPopup_Show("PARAGON_CODEX_RESPEC")
    end)

    local points = codex:CreateFontString("UIParagonCodexPoints", "OVERLAY", "GameFontNormal")
    points:SetPoint("RIGHT", respec, "LEFT", -12, 0)
    points:SetText("0 points")

    StaticPopupDialogs["PARAGON_CODEX_RESPEC"] = {
        text = "Refund ALL Codex points?\n\n|cffff4040Permanent nodes are kept, and their points are not returned.|r",
        button1 = YES,
        button2 = NO,
        OnAccept = function() SendCodex({ action = "respec" }) end,
        timeout = 0, whileDead = 1, hideOnEscape = 1,
    }

    -- Confirmation for permanent nodes (see Node_OnClick). data carries the
    -- node id; 3.3.5 passes it to OnAccept as the second argument.
    StaticPopupDialogs["PARAGON_CODEX_PERMANENT"] = {
        text = "Buy %s for %d Paragon points?\n\n|cffff4040This node is PERMANENT. It can never be refunded, and a Respec will not return its points.|r",
        button1 = YES,
        button2 = NO,
        OnAccept = function(self, data)
            if data and data.node then
                SendCodex({ action = "buy", node = data.node, count = 1 })
            end
        end,
        timeout = 0, whileDead = 1, hideOnEscape = 1,
    }

    local divider = CreateFrame("Frame", "UIParagonCodexDivider", codex, "ParagonDivider")
    divider:SetPoint("TOP", codex, "TOP", 0, -24)

    -- Scrollable content region (user spec: the panel is shorter than the
    -- node set — the header/points/respec stay fixed, everything below
    -- the divider lives on a wheel-scrolled child)
    local scroll = CreateFrame("ScrollFrame", "UIParagonCodexScroll", codex)
    scroll:SetPoint("TOPLEFT", codex, "TOPLEFT", 0, -34)
    scroll:SetPoint("BOTTOMRIGHT", codex, "BOTTOMRIGHT", 0, 0)

    local content = CreateFrame("Frame", "UIParagonCodexContent", scroll)
    content:SetSize(760, 680)
    scroll:SetScrollChild(content)

    scroll:EnableMouseWheel(true)
    scroll:SetScript("OnMouseWheel", function(self, delta)
        local max = self:GetVerticalScrollRange()
        local target = self:GetVerticalScroll() - delta * 45
        if target < 0 then target = 0 end
        if target > max then target = max end
        self:SetVerticalScroll(target)
    end)

    local defs = ParagonCodexData.defs
    local byFamily = { attr = {}, war = {}, ward = {}, mastery = {} }
    for _, def in ipairs(defs) do
        if byFamily[def.family] then
            table.insert(byFamily[def.family], def)
        end
    end

    -- ======== band 1: attributes (x 30..270) | warfare (x 300..610) ========
    SectionLabel(content, "Attributes", 34, -8)
    for i = 0, 4 do
        local greater = byFamily.attr[i * 2 + 1]
        local endless = byFamily.attr[i * 2 + 2]
        local rowTop = -34 - i * 58
        if greater then
            local g = CreateNode(content, greater, 40)
            g:SetPoint("TOPLEFT", content, "TOPLEFT", 34, rowTop)
        end
        if endless then
            local chain = content:CreateTexture(nil, "ARTWORK")
            chain:SetSize(24, 3)
            chain:SetTexture(1, 1, 1, 1)
            chain:SetVertexColor(0.4, 0.4, 0.4)
            chain:SetPoint("TOPLEFT", content, "TOPLEFT", 76, rowTop - 19)
            ParagonCodexData.chains[endless.id] = chain

            local e = CreateNode(content, endless, 30)
            e:SetPoint("TOPLEFT", content, "TOPLEFT", 102, rowTop - 5)
        end
        if greater then
            local label = content:CreateFontString(nil, "OVERLAY", "GameFontNormal")
            label:SetPoint("TOPLEFT", content, "TOPLEFT", 142, rowTop - 12)
            label:SetText(ATTR_LABEL[greater.id] or greater.name)
        end
    end

    SectionLabel(content, "Warfare", 272, -8)
    for i, def in ipairs(byFamily.war) do
        -- ids 20-29 fill the 5x2 grid; later additions (38/39) stack as a
        -- sixth column to the right of Armor Pen / Resilience
        local col, row
        if i <= 10 then
            col, row = (i - 1) % 5, math.floor((i - 1) / 5)
        else
            col, row = 5, i - 11
        end
        local n = CreateNode(content, def, 40)
        n:SetPoint("TOPLEFT", content, "TOPLEFT", 272 + col * 64, -34 - row * 96)

        local label = content:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
        label:SetPoint("TOP", n, "BOTTOM", 0, -13)
        label:SetTextColor(0.75, 0.75, 0.75)
        label:SetText(WAR_LABEL[def.id] or "")
    end

    -- ======== mastery: slim plaques under the warfare grid (band 1) ========
    SectionLabel(content, "Mastery", 272, -224)
    for i, def in ipairs(byFamily.mastery) do
        local plaque = CreateFrame("Button", "ParagonCodexPlaque_" .. def.id, content)
        plaque:SetSize(185, 44)
        -- 2-per-row grid (52 = plaque height 44 + 8 gap), ARRAY ORDER:
        -- row 0 = 54/55, row 1 = 52/53, row 2 = 58/59, row 3 = 56/57,
        -- row 4 = 50/51. Row 4 sits at y -454..-498 in the x 272+ column,
        -- which is clear of the Wards flower (x <= 230) -- no band-2 shift
        -- needed. Node 59 needs NO other client change: its kind is "skill",
        -- so BonusText's cap-1 branch already renders Learned/Not learned,
        -- and the whole permanent-purchase path (tooltip swap, right-click
        -- swallow, PARAGON_CODEX_PERMANENT popup) keys purely off
        -- def.permanent.
        local col, row = (i - 1) % 2, math.floor((i - 1) / 2)
        plaque:SetPoint("TOPLEFT", content, "TOPLEFT", 272 + col * 192, -246 - row * 52)
        plaque:SetBackdrop({
            bgFile = "Interface/Tooltips/UI-Tooltip-Background",
            edgeFile = "Interface/Tooltips/UI-Tooltip-Border",
            tile = true, tileSize = 16, edgeSize = 12,
            insets = { left = 3, right = 3, top = 3, bottom = 3 },
        })
        plaque:SetBackdropColor(0.08, 0.06, 0.03, 0.9)

        local icon = plaque:CreateTexture(nil, "ARTWORK")
        icon:SetSize(24, 24)
        icon:SetPoint("LEFT", 7, 0)
        SetPortraitToTexture(icon, def.icon)

        local name = plaque:CreateFontString(nil, "OVERLAY", "GameFontNormal")
        name:SetPoint("TOPLEFT", 38, -6)
        name:SetText(def.name)

        local info = plaque:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
        info:SetPoint("BOTTOMLEFT", 38, 6)
        plaque.Info = info

        plaque.codexDef = def
        plaque:RegisterForClicks("LeftButtonUp", "RightButtonUp")
        plaque:SetScript("OnEnter", Node_OnEnter)
        plaque:SetScript("OnLeave", function() GameTooltip:Hide() end)
        plaque:SetScript("OnClick", Node_OnClick)
        ParagonCodexData.plaques[def.id] = plaque
    end

    -- ======== band 2: the wards flower, enlarged (x 60..280) ===============
    -- (-382: pushed down 52px when the mastery block grew its second row)
    local band2 = -382
    SectionLabel(content, "Wards", 34, band2)
    local cx, cy = 134, band2 - 110
    for _, def in ipairs(byFamily.ward) do
        if def.id == 30 then
            local n = CreateNode(content, def, 56)
            n:SetPoint("CENTER", content, "TOPLEFT", cx, cy)
        end
    end
    local petalAngles = { 90, 30, -30, -90, -150, 150 }
    local petalIndex = 0
    for _, def in ipairs(byFamily.ward) do
        if def.id ~= 30 then
            petalIndex = petalIndex + 1
            local a = math.rad(petalAngles[petalIndex] or 0)
            local n = CreateNode(content, def, 38)
            n:SetPoint("CENTER", content, "TOPLEFT",
                cx + math.cos(a) * 76, cy + math.sin(a) * 76)
        end
    end
end

-- ============================================================================
-- REFRESH
-- ============================================================================

local function Refresh()
    if not built then
        return
    end
    for _, node in pairs(ParagonCodexData.nodes) do
        PaintNode(node)
    end
    for id, chain in pairs(ParagonCodexData.chains) do
        local def = ParagonCodexData.byId[id]
        if def and GateOpen(def) then
            chain:SetVertexColor(1, 0.82, 0)
        else
            chain:SetVertexColor(0.4, 0.4, 0.4)
        end
    end
    for _, plaque in pairs(ParagonCodexData.plaques) do
        local def = plaque.codexDef
        local rank = RankOf(def.id)
        local capText = def.cap > 0 and string.format("%d/%d", rank, def.cap) or tostring(rank)
        if IsMaxed(def) then
            plaque.Info:SetText(string.format("Rank %s \226\128\148 maxed", capText))
        else
            plaque.Info:SetText(string.format("Rank %s \226\128\148 next: %d pts",
                capText, RankCost(def, rank + 1)))
        end
    end
    if UIParagonCodexPoints then
        UIParagonCodexPoints:SetText(string.format("|cffffd100%d|r unspent point%s",
            ParagonCodexData.available, ParagonCodexData.available == 1 and "" or "s"))
    end
end

--- The micro-button notification keys off ParagonData.availablePoints;
--- codex-available IS the spendable number now.
local function SyncStockPoints()
    ParagonData.availablePoints = ParagonCodexData.available
    if ParagonMicroButton_UpdateNotification then
        ParagonMicroButton_UpdateNotification()
    end
end

-- ============================================================================
-- NETWORK
-- ============================================================================

RegisterServerResponses({
    Prefix = PREFIX,
    Functions = {
        [1] = "ParagonCodex_OnState",
        [2] = "ParagonCodex_OnDefinitions",
    },
})

function ParagonCodex_OnState(player, arg_table)
    local data = arg_table and arg_table[1]
    if type(data) ~= "table" then return end
    ParagonCodexData.ranks = type(data.ranks) == "table" and data.ranks or {}
    ParagonCodexData.available = tonumber(data.available) or 0
    ParagonCodexData.bonus = type(data.bonus) == "table" and data.bonus or {}
    Refresh()
    SyncStockPoints()
end

function ParagonCodex_OnDefinitions(player, arg_table)
    local defs = arg_table and arg_table[1]
    if type(defs) ~= "table" then return end
    ParagonCodexData.defs = defs
    ParagonCodexData.byId = {}
    for _, def in ipairs(defs) do
        ParagonCodexData.byId[def.id] = def
    end
    BuildPanel()
    Refresh()
end

-- ============================================================================
-- /RELOAD RESILIENCE
-- ============================================================================

-- The stock load request fires from UIParagon's XML OnLoad — DURING addon
-- loading, before the handler files (Network/RewardTrack/this one) have
-- registered. At a fresh login the timing works out; after a /reload the
-- server's reply races into a half-loaded addon and the entire dataset is
-- dropped (field-proven: empty track, empty codex, resurrected legacy
-- header until the next relog). Re-issue the handshake once the world
-- (re)enters, when every handler exists — the server-side load push is
-- idempotent, so the doubled request at fresh login is harmless.
local reloadGuard = CreateFrame("Frame")
reloadGuard:RegisterEvent("PLAYER_ENTERING_WORLD")
reloadGuard:SetScript("OnEvent", function()
    if C_Timer and C_Timer.After then
        C_Timer.After(1.5, function()
            SendClientRequest("ParagonAnniversary", 1)
        end)
    else
        SendClientRequest("ParagonAnniversary", 1)
    end
end)
