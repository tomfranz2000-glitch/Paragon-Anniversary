--[[
    Paragon_RewardTrack.lua
    Reward Track UI for Paragon Anniversary system

    Renders a horizontally drag-scrollable strip of circular milestone nodes
    between the XP-bar block and the Statistics section of UIParagon. Each node
    shows the required paragon level above it and the bonus icon inside it.
    Locked milestones (player level < required) are desaturated with a gray
    label; unlocked ones are full-color with a gold label.

    Data path:
    - The server PUSHES the track definitions during the client load request
      via opcode 7 on prefix "ParagonAnniversary" (see Paragon_Network.lua).
      The payload is one array table, sorted ascending by level:
      { [1] = { level = 25, icon = "Interface\\Icons\\...",
                rewards = { [1] = { type = "UNIT_MODS", value = "STAT_STRENGTH", amount = 1000 } } }, ... }
    - The current paragon level is captured by hooking
      UIParagon_OnClientReceiveLevel (it does not store the level anywhere).

    Scroll mechanics (3.3.5-correct):
    - A ScrollFrame is used as clipper (clips rendering AND mouse hit-testing;
      there is NO SetClipsChildren in 3.3.5) with a wide child Frame holding
      the nodes, moved via SetHorizontalScroll.
    - Dragging: the clipper (and every node, which must forward its mouse
      events because it is mouse-enabled for tooltips) records the cursor
      position and current scroll on mouse-down; OnUpdate applies the clamped
      delta while the button is held.

    Dependencies:
    - Paragon_Locales.lua: GetLocaleTable()
    - Paragon_Network.lua: opcode registration ([7] = "UIParagon_OnReceiveRewardTrack")
    - UIParagon.xml: ParagonDivider and ParagonTrackNodeTemplate virtual templates,
      UIParagon frame (must be loaded before this file)

    @module Paragon_RewardTrack
    @author Paragon Team
]]

local Locale = GetLocaleTable()

--- Global data store for the Reward Track
-- @table ParagonRewardTrackData
-- @field milestones table Array of milestone definitions pushed by the server (opcode 7)
-- @field currentLevel number Current paragon level (captured from UIParagon_OnClientReceiveLevel)
-- @field nodeCount number Number of node frames ever created (for named-frame recycling)
ParagonRewardTrackData = {
    milestones = {},
    currentLevel = 0,
    nodeCount = 0
}

-- ============================================================================
-- ITEM-LEVEL ATTUNEMENT STATE (milestone 250, prefix "ParagonIlvl")
-- ============================================================================

-- Live numbers for the ILVL_ATTUNEMENT reward tooltip, pushed by the server
-- on every change (equip, safety tick) and on the client load request:
--   { ilvl = <n>, stats = { { label = "Strength", amount = 403 }, ... } }
-- stats is empty while the bonus is not active on this character.
local ilvlState = { ilvl = 0, stats = nil }
local collectionState = { mounts = 0, companions = 0 }
--- Achievement ladder (milestone 475): the server sends ONLY the unlocked
--- tiers — locked ones are deliberately kept secret for the tooltip's
--- "another scaling boost at N" tease.
---   { count = <n>, tiers = { { label, amount, xp? }... }, next = <n|nil> }
local achieveState = { count = 0, tiers = nil, next = nil }
--- Loremaster's Ledger (milestone 600): same contract, quest count
local questState = { count = 0, tiers = nil, next = nil }
--- Collector's Wardrobe (milestone 975): same contract, account-wide
--- transmog appearance count
local transmogState = { count = 0, tiers = nil, next = nil }
--- Enchant-slot ladder (milestone 725): unlocked slot labels only,
--- next threshold teased without revealing which slot it opens.
---   { ilvl = <n>, unlocked = { "Chest", ... }, next = <n|nil> }
local slotsState = { ilvl = 0, unlocked = nil, next = nil }
local hoveredNode  -- track node currently owning the tooltip (live refresh)

--- Re-renders the milestone tooltip in place if it is open (state pushes
--- can arrive mid-hover: gear swaps, learning a mount)
local function RefreshOpenTooltip()
    if hoveredNode and GameTooltip:IsShown() and GameTooltip:GetOwner() == hoveredNode then
        UIParagonTrackNode_OnEnter(hoveredNode)
    end
end

RegisterServerResponses({
    Prefix = "ParagonIlvl",
    Functions = { [1] = "UIParagonTrack_OnIlvlState" },
})

function UIParagonTrack_OnIlvlState(player, arg_table)
    local data = arg_table and arg_table[1] or {}
    ilvlState.ilvl = tonumber(data.ilvl) or 0
    ilvlState.stats = type(data.stats) == "table" and data.stats or nil
    RefreshOpenTooltip()
end

RegisterServerResponses({
    Prefix = "ParagonCollection",
    Functions = { [1] = "UIParagonTrack_OnCollectionState" },
})

function UIParagonTrack_OnCollectionState(player, arg_table)
    local data = arg_table and arg_table[1] or {}
    collectionState.mounts = tonumber(data.mounts) or 0
    collectionState.companions = tonumber(data.companions) or 0
    RefreshOpenTooltip()
end

RegisterServerResponses({
    Prefix = "ParagonAchieve",
    Functions = { [1] = "UIParagonTrack_OnAchieveState" },
})

function UIParagonTrack_OnAchieveState(player, arg_table)
    local data = arg_table and arg_table[1] or {}
    achieveState.count = tonumber(data.count) or 0
    achieveState.tiers = type(data.tiers) == "table" and data.tiers or nil
    achieveState.next = tonumber(data.next)
    RefreshOpenTooltip()
end

RegisterServerResponses({
    Prefix = "ParagonSlots",
    Functions = { [1] = "UIParagonTrack_OnSlotsState" },
})

function UIParagonTrack_OnSlotsState(player, arg_table)
    local data = arg_table and arg_table[1] or {}
    slotsState.ilvl = tonumber(data.ilvl) or 0
    slotsState.unlocked = type(data.unlocked) == "table" and data.unlocked or nil
    slotsState.next = tonumber(data.next)
    RefreshOpenTooltip()
end

-- Live sums for the GEM_DOUBLE reward tooltip (milestone 1150), pushed by
-- paragon_gem_double.lua: { stats = { { label, amount }, ... } }
local gemState = { stats = nil }

RegisterServerResponses({
    Prefix = "ParagonGems",
    Functions = { [1] = "UIParagonTrack_OnGemState" },
})

function UIParagonTrack_OnGemState(player, arg_table)
    local data = arg_table and arg_table[1] or {}
    gemState.stats = type(data.stats) == "table" and data.stats or nil
    RefreshOpenTooltip()
end

-- Live progress for the SOLO_DUNGEON reward tooltip (milestone 1200),
-- pushed by paragon_solo_dungeon.lua:
--   { count, total, resil, crit, next_at }
local soloState = { count = 0, total = 92, resil = 0, crit = 0, next_at = nil }

RegisterServerResponses({
    Prefix = "ParagonSolo",
    Functions = { [1] = "UIParagonTrack_OnSoloState" },
})

function UIParagonTrack_OnSoloState(player, arg_table)
    local data = arg_table and arg_table[1] or {}
    soloState.count = tonumber(data.count) or 0
    soloState.total = tonumber(data.total) or 92
    soloState.resil = tonumber(data.resil) or 0
    soloState.crit = tonumber(data.crit) or 0
    soloState.next_at = tonumber(data.next_at)
    RefreshOpenTooltip()
end

-- Live progress for the RARE_HUNTER reward tooltip (milestone 1300),
-- pushed by paragon_rare_hunter.lua:
--   { count, total, armor, resil, haste, next_at }
local rareState = { count = 0, total = 0, armor = 0, resil = 0, haste = 0, next_at = nil }

RegisterServerResponses({
    Prefix = "ParagonRares",
    Functions = { [1] = "UIParagonTrack_OnRareState" },
})

function UIParagonTrack_OnRareState(player, arg_table)
    local data = arg_table and arg_table[1] or {}
    rareState.count = tonumber(data.count) or 0
    rareState.total = tonumber(data.total) or 0
    rareState.armor = tonumber(data.armor) or 0
    rareState.resil = tonumber(data.resil) or 0
    rareState.haste = tonumber(data.haste) or 0
    rareState.next_at = tonumber(data.next_at)
    RefreshOpenTooltip()
end

RegisterServerResponses({
    Prefix = "ParagonQuest",
    Functions = { [1] = "UIParagonTrack_OnQuestState" },
})

function UIParagonTrack_OnQuestState(player, arg_table)
    local data = arg_table and arg_table[1] or {}
    questState.count = tonumber(data.count) or 0
    questState.tiers = type(data.tiers) == "table" and data.tiers or nil
    questState.next = tonumber(data.next)
    RefreshOpenTooltip()
end

RegisterServerResponses({
    Prefix = "ParagonTransmog",
    Functions = { [1] = "UIParagonTrack_OnTransmogState" },
})

function UIParagonTrack_OnTransmogState(player, arg_table)
    local data = arg_table and arg_table[1] or {}
    transmogState.count = tonumber(data.count) or 0
    transmogState.tiers = type(data.tiers) == "table" and data.tiers or nil
    transmogState.next = tonumber(data.next)
    RefreshOpenTooltip()
end

-- ============================================================================
-- LAYOUT CONSTANTS
-- ============================================================================

local TRACK_SECTION_WIDTH   = 650   -- Same width as the dividers / TopBanner
local TRACK_SECTION_HEIGHT  = 122   -- 20 header + 16 divider + 4 gap + 82 strip
local TRACK_CLIPPER_HEIGHT  = 82    -- Level label (~14) + node (55) + slack
local TRACK_NODE_SPACING    = 75    -- 55 node width + 20 gap (matches stat rows)
local TRACK_NODE_PADDING    = 10    -- Left/right padding inside the strip
local TRACK_NODE_TOP_OFFSET = -22   -- Node top below strip top (room for the level label)
local TRACK_WHEEL_STEP      = 75    -- Horizontal scroll per mouse-wheel click
local TRACK_NODE_WIDTH      = 55    -- ParagonTrackNodeTemplate icon width
local TRACK_UPCOMING_COUNT  = 4     -- How far ahead you can see (earned ones all stay)

-- ============================================================================
-- HELPERS
-- ============================================================================

--- Prettifies a raw statistic value key for display when no locale entry exists
-- e.g. "STAT_STRENGTH" -> "Strength", "ATTACK_POWER" -> "Attack Power"
-- @param value string The raw value key
-- @return string Human-readable name
local function PrettifyValueKey(value)
    local text = tostring(value or "")
    text = string.gsub(text, "^STAT_", "")
    text = string.lower(text)
    text = string.gsub(text, "_", " ")
    text = string.gsub(text, "(%a)([%w]*)", function(first, rest)
        return string.upper(first) .. rest
    end)
    return text
end

--- Resolves the localized display name for a reward
-- Uses the same Locale.STATISTICS[type][value] shape as Paragon_Network.lua
-- (each entry is a table with .name and .description). NEVER errors on nil:
-- falls back to a prettified value key when the locale entry is missing.
-- @param reward table {type = "UNIT_MODS"|"COMBAT_RATING", value = string, amount = number}
-- @return string Localized reward name
local function ResolveRewardName(reward)
    local statistics = Locale and Locale.STATISTICS
    local group = statistics and statistics[reward.type]
    local entry = group and group[reward.value]
    if type(entry) == "table" and entry.name then
        return entry.name
    end
    return PrettifyValueKey(reward.value)
end

--- The slice of the track the panel actually shows.
---
--- Everything you have EARNED stays on the track for good -- it is a record of
--- what you have. What is capped is the view AHEAD: only the next
--- TRACK_UPCOMING_COUNT unearned milestones are rendered, so there is still
--- something left to find out. A fresh level 80 has earned nothing, so it sees
--- exactly four; at max level nothing is upcoming, so it sees all sixty.
---
--- The `break` is safe because `all` is sorted ascending by level, so once the
--- upcoming budget is spent every remaining entry is also upcoming.
---
--- @param all table Milestones, ascending by level
--- @param currentLevel number Current paragon level
--- @return table The milestones to render, ascending by level
local function VisibleMilestones(all, currentLevel)
    local shown, upcoming = {}, 0

    for _, milestone in ipairs(all) do
        local level = tonumber(milestone.level) or 0
        if level <= currentLevel then
            table.insert(shown, milestone)
        elseif upcoming < TRACK_UPCOMING_COUNT then
            table.insert(shown, milestone)
            upcoming = upcoming + 1
        else
            break
        end
    end

    return shown
end

-- ============================================================================
-- SCROLL / DRAG CONTROLLER
-- ============================================================================

--- Applies a horizontal scroll offset clamped to [0, childWidth - clipperWidth]
-- @param clipper ScrollFrame The strip clipper
-- @param offset number Desired horizontal scroll offset
function UIParagonRewardTrack_SetScroll(clipper, offset)
    if not clipper then return end

    local maxScroll = 0
    local strip = clipper.scrollChild
    if strip then
        maxScroll = strip:GetWidth() - clipper:GetWidth()
        if maxScroll < 0 then
            maxScroll = 0
        end
    end

    if offset < 0 then
        offset = 0
    elseif offset > maxScroll then
        offset = maxScroll
    end

    clipper:SetHorizontalScroll(offset)
end

--- Begins a drag-scroll: records cursor X (in the clipper's effective scale)
-- and the scroll offset at drag start
-- @param clipper ScrollFrame The strip clipper
function UIParagonRewardTrack_StartDrag(clipper)
    if not clipper then return end
    local cursorX = GetCursorPosition()
    clipper.isDragging = true
    clipper.dragStartX = cursorX / clipper:GetEffectiveScale()
    clipper.dragStartScroll = clipper:GetHorizontalScroll()
    GameTooltip:Hide()
end

--- Ends a drag-scroll
-- @param clipper ScrollFrame The strip clipper
function UIParagonRewardTrack_StopDrag(clipper)
    if not clipper then return end
    clipper.isDragging = false
end

--- Per-frame drag handler: while the left button is held, scrolls by the
-- cursor delta since drag start (clamped)
-- @param self ScrollFrame The strip clipper
-- @param elapsed number Seconds since last update (unused)
function UIParagonRewardTrack_OnUpdate(self, elapsed)
    if not self.isDragging then return end

    -- Safety: the button may have been released outside the frame
    if not IsMouseButtonDown("LeftButton") then
        self.isDragging = false
        return
    end

    local cursorX = GetCursorPosition()
    cursorX = cursorX / self:GetEffectiveScale()

    UIParagonRewardTrack_SetScroll(self, self.dragStartScroll + (self.dragStartX - cursorX))
end

--- Mouse-wheel handler shared by the clipper and the nodes
-- Wheel up scrolls left, wheel down scrolls right
-- @param self Frame The clipper or a node
-- @param delta number 1 (up) or -1 (down)
function UIParagonRewardTrack_OnMouseWheel(self, delta)
    local clipper = UIParagonRewardTrackClipper
    if not clipper then return end

    local offset = clipper:GetHorizontalScroll()
    if delta > 0 then
        offset = offset - TRACK_WHEEL_STEP
    else
        offset = offset + TRACK_WHEEL_STEP
    end

    UIParagonRewardTrack_SetScroll(clipper, offset)
end

-- ============================================================================
-- NODE EVENT HANDLERS
-- ============================================================================

--- Shows the milestone tooltip (same pattern as UIParagonStatItem_OnEnter)
-- @param self Frame The track node
function UIParagonTrackNode_OnEnter(self)
    -- No tooltips while drag-scrolling: nodes passing under the cursor
    -- would pop them in and out every frame
    local clipper = UIParagonRewardTrackClipper
    if clipper and clipper.isDragging then return end

    local milestone = self.milestone
    if not milestone then return end
    hoveredNode = self

    -- %d errors on non-numbers in Lua 5.1, so coerce the level defensively
    local milestoneLevel = tonumber(milestone.level) or 0

    GameTooltip:SetOwner(self, "ANCHOR_BOTTOMRIGHT")
    GameTooltip:ClearLines()

    -- Gold title line: the milestone's flavor title when the server sent
    -- one (2026-08-18 pass), with the level relegated to a soft sub-line;
    -- the legacy "Reward Track: Level X" format stays as the fallback
    local flavor = self.milestone and self.milestone.title
    if flavor then
        GameTooltip:AddLine(flavor, 1, 0.82, 0, false)
        GameTooltip:AddLine(string.format(
            Locale.REWARD_TRACK_TOOLTIP_LEVEL or "Paragon Level %d", milestoneLevel),
            0.7, 0.7, 0.7, false)
    else
        local title = string.format(Locale.REWARD_TRACK_TOOLTIP_TITLE or "Reward Track: Level %d", milestoneLevel)
        GameTooltip:AddLine(title, 1, 0.82, 0, false)
    end

    -- Status line: green when unlocked, gray when locked
    if self.unlocked then
        GameTooltip:AddLine(Locale.REWARD_TRACK_UNLOCKED or "Unlocked", 0, 1, 0, false)
    else
        local lockedText = string.format(Locale.REWARD_TRACK_LOCKED or "Unlocks at Paragon Level %d", milestoneLevel)
        GameTooltip:AddLine(lockedText, 0.5, 0.5, 0.5, false)
    end

    -- One white line per reward: a server-provided label wins verbatim
    -- (unique bonuses), otherwise "+<amount> <name>"
    if type(milestone.rewards) == "table" then
        for _, reward in ipairs(milestone.rewards) do
            if type(reward) == "table" then
                local line
                if type(reward.label) == "string" then
                    line = reward.label
                else
                    line = "+" .. tostring(reward.amount or 0) .. " " .. ResolveRewardName(reward)
                end
                GameTooltip:AddLine(line, 1, 1, 1, false)

                -- Item-level attunement: live numbers pushed by the server
                if reward.type == "SPECIAL" and reward.value == "ILVL_ATTUNEMENT"
                        and ilvlState.stats and #ilvlState.stats > 0 then
                    GameTooltip:AddLine(string.format(
                        Locale.ILVL_ATTUNEMENT_CURRENT or "Your average item level: %d",
                        ilvlState.ilvl), 1, 0.82, 0, false)
                    for _, stat in ipairs(ilvlState.stats) do
                        GameTooltip:AddLine(string.format("  +%d %s",
                            tonumber(stat.amount) or 0, tostring(stat.label)), 0, 1, 0, false)
                    end
                end

                -- Gem doubling: live sums pushed by the server
                if reward.type == "SPECIAL" and reward.value == "GEM_DOUBLE"
                        and gemState.stats and #gemState.stats > 0 then
                    GameTooltip:AddLine(
                        Locale.GEM_DOUBLE_CURRENT or "Currently doubled:", 1, 0.82, 0, false)
                    for _, stat in ipairs(gemState.stats) do
                        GameTooltip:AddLine(string.format("  +%d %s",
                            tonumber(stat.amount) or 0, tostring(stat.label)), 0, 1, 0, false)
                    end
                end

                -- Racially Ambiguous: the chosen racial ability. Resolved
                -- entirely client-side -- the picker holds a real stock
                -- spell id, so GetSpellInfo supplies the name.
                if reward.type == "SPECIAL" and reward.value == "RACIAL_PICK" then
                    local chosen = ParagonRacial_CurrentName and ParagonRacial_CurrentName()
                    if chosen then
                        GameTooltip:AddLine("Current ability: |cff00ff00" .. chosen .. "|r",
                            1, 0.82, 0, false)
                    elseif self.unlocked then
                        GameTooltip:AddLine("No ability chosen yet.", 1, 0.3, 0.3, false)
                    end
                    if self.unlocked then
                        GameTooltip:AddLine("Click this milestone to choose.",
                            0.5, 0.5, 0.5, false)
                    end
                end

                -- Solo dungeon clears: live progress pushed by the server
                if reward.type == "SPECIAL" and reward.value == "SOLO_DUNGEON" then
                    GameTooltip:AddLine(string.format(
                        Locale.SOLO_DUNGEON_CURRENT or "Dungeons cleared solo: %d/%d",
                        soloState.count, soloState.total), 1, 0.82, 0, false)
                    if soloState.resil > 0 then
                        GameTooltip:AddLine(string.format("  +%d Resilience Rating",
                            soloState.resil), 0, 1, 0, false)
                    end
                    if soloState.crit > 0 then
                        GameTooltip:AddLine(string.format("  +%d%% Critical Strike Damage",
                            soloState.crit), 0, 1, 0, false)
                    end
                    if soloState.next_at then
                        GameTooltip:AddLine(string.format(
                            Locale.SOLO_DUNGEON_NEXT or "Next +1%% Critical Strike Damage at %d clears",
                            soloState.next_at), 0.6, 0.6, 0.6, false)
                    end
                end

                -- Rare kills: live progress pushed by the server
                if reward.type == "SPECIAL" and reward.value == "RARE_HUNTER" then
                    GameTooltip:AddLine(string.format(
                        Locale.RARE_HUNTER_CURRENT or "Unique rares slain: %d/%d",
                        rareState.count, rareState.total), 1, 0.82, 0, false)
                    if rareState.armor > 0 then
                        GameTooltip:AddLine(string.format("  +%d Armor", rareState.armor), 0, 1, 0, false)
                    end
                    if rareState.resil > 0 then
                        GameTooltip:AddLine(string.format("  +%d Resilience Rating", rareState.resil), 0, 1, 0, false)
                        GameTooltip:AddLine(string.format("  +%d Haste Rating", rareState.haste), 0, 1, 0, false)
                    end
                    if rareState.next_at then
                        GameTooltip:AddLine(string.format(
                            Locale.RARE_HUNTER_NEXT or "Next +1 Resilience and Haste at %d rares",
                            rareState.next_at), 0.6, 0.6, 0.6, false)
                    end
                end

                -- Collection XP bonuses: live counts pushed by the server
                if reward.type == "SPECIAL" and reward.value == "MOUNT_XP"
                        and collectionState.mounts > 0 then
                    GameTooltip:AddLine(string.format(
                        Locale.MOUNT_XP_CURRENT or "Mounts collected: %d",
                        collectionState.mounts), 1, 0.82, 0, false)
                    GameTooltip:AddLine(string.format("  +%d%% Paragon Experience",
                        collectionState.mounts), 0, 1, 0, false)
                end

                if reward.type == "SPECIAL" and reward.value == "COMPANION_XP"
                        and collectionState.companions > 0 then
                    GameTooltip:AddLine(string.format(
                        Locale.COMPANION_XP_CURRENT or "Companions collected: %d",
                        collectionState.companions), 1, 0.82, 0, false)
                    GameTooltip:AddLine(string.format("  +%s%% Paragon Experience",
                        tostring(collectionState.companions * 0.5)), 0, 1, 0, false)
                end

                -- Achievement ladder: the server sends only what is
                -- unlocked; the closing line teases the next threshold
                -- without revealing the locked bonus (deliberate)
                if reward.type == "SPECIAL" and reward.value == "ACHIEVEMENT_BONUS"
                        and achieveState.tiers then
                    GameTooltip:AddLine(string.format(
                        Locale.ACHIEVE_COUNT or "Achievements earned: %d",
                        achieveState.count), 1, 0.82, 0, false)
                    for _, tier in ipairs(achieveState.tiers) do
                        if tier.xp then
                            GameTooltip:AddLine(string.format("  +%.1f%% %s",
                                tonumber(tier.amount) or 0, tostring(tier.label)), 0, 1, 0, false)
                        else
                            GameTooltip:AddLine(string.format("  +%d %s",
                                tonumber(tier.amount) or 0, tostring(tier.label)), 0, 1, 0, false)
                        end
                    end
                    GameTooltip:AddLine(
                        Locale.ACHIEVE_GROWTH or "Each bonus keeps growing with every Achievement you earn.",
                        0.6, 0.6, 0.6, true)
                    if achieveState.next then
                        GameTooltip:AddLine(string.format(
                            Locale.ACHIEVE_NEXT or "Another scaling boost will be unlocked upon reaching %d Achievements.",
                            achieveState.next), 1, 0.82, 0, true)
                    elseif #achieveState.tiers >= 11 then
                        GameTooltip:AddLine(
                            Locale.ACHIEVE_ALL or "All bonuses unlocked.",
                            1, 0.82, 0, false)
                    end
                end

                -- Enchant-slot ladder: unlocked slots + a spoiler-free
                -- tease of the next threshold
                if reward.type == "SPECIAL" and reward.value == "ENCHANT_SLOTS"
                        and slotsState.unlocked then
                    GameTooltip:AddLine(string.format(
                        Locale.SLOTS_ILVL or "Your average item level: %d",
                        slotsState.ilvl), 1, 0.82, 0, false)
                    if #slotsState.unlocked > 0 then
                        GameTooltip:AddLine(string.format(
                            Locale.SLOTS_UNLOCKED or "Extra enchantment slots: %s",
                            table.concat(slotsState.unlocked, ", ")), 0, 1, 0, true)
                    end
                    if slotsState.next then
                        GameTooltip:AddLine(string.format(
                            Locale.SLOTS_NEXT or "The next slot unlocks at average item level %d.",
                            slotsState.next), 1, 0.82, 0, true)
                    elseif #slotsState.unlocked >= 11 then
                        GameTooltip:AddLine(
                            Locale.SLOTS_ALL or "Every extra enchantment slot is unlocked.",
                            1, 0.82, 0, false)
                    end
                end

                -- Loremaster's Ledger: same suspense contract as the
                -- achievement ladder, quest count
                if reward.type == "SPECIAL" and reward.value == "QUEST_BONUS"
                        and questState.tiers then
                    GameTooltip:AddLine(string.format(
                        Locale.QUEST_COUNT or "Quests completed: %d",
                        questState.count), 1, 0.82, 0, false)
                    for _, tier in ipairs(questState.tiers) do
                        if tier.xp then
                            GameTooltip:AddLine(string.format("  +%.1f%% %s",
                                tonumber(tier.amount) or 0, tostring(tier.label)), 0, 1, 0, false)
                        else
                            GameTooltip:AddLine(string.format("  +%d %s",
                                tonumber(tier.amount) or 0, tostring(tier.label)), 0, 1, 0, false)
                        end
                    end
                    GameTooltip:AddLine(
                        Locale.QUEST_GROWTH or "Each bonus keeps growing with every quest you complete.",
                        0.6, 0.6, 0.6, true)
                    if questState.next then
                        GameTooltip:AddLine(string.format(
                            Locale.QUEST_NEXT or "Another entry will be inscribed upon completing %d quests.",
                            questState.next), 1, 0.82, 0, true)
                    elseif #questState.tiers >= 9 then
                        GameTooltip:AddLine(
                            Locale.QUEST_ALL or "The ledger is complete.",
                            1, 0.82, 0, false)
                    end
                end

                -- Collector's Wardrobe: same suspense contract, account-
                -- wide transmog appearance count
                if reward.type == "SPECIAL" and reward.value == "TRANSMOG_BONUS"
                        and transmogState.tiers then
                    GameTooltip:AddLine(string.format(
                        Locale.TRANSMOG_COUNT or "Appearances collected: %d",
                        transmogState.count), 1, 0.82, 0, false)
                    for _, tier in ipairs(transmogState.tiers) do
                        if tier.xp then
                            GameTooltip:AddLine(string.format("  +%.1f%% %s",
                                tonumber(tier.amount) or 0, tostring(tier.label)), 0, 1, 0, false)
                        else
                            GameTooltip:AddLine(string.format("  +%d %s",
                                tonumber(tier.amount) or 0, tostring(tier.label)), 0, 1, 0, false)
                        end
                    end
                    GameTooltip:AddLine(
                        Locale.TRANSMOG_GROWTH or "Each bonus keeps growing with every appearance you collect.",
                        0.6, 0.6, 0.6, true)
                    if transmogState.next then
                        GameTooltip:AddLine(string.format(
                            Locale.TRANSMOG_NEXT or "Another bonus unlocks at %d collected appearances.",
                            transmogState.next), 1, 0.82, 0, true)
                    elseif #transmogState.tiers >= 9 then
                        GameTooltip:AddLine(
                            Locale.TRANSMOG_ALL or "The wardrobe is complete.",
                            1, 0.82, 0, false)
                    end
                end

                -- Per-100 talent points: derived from the live track level,
                -- no server push needed
                if reward.type == "SPECIAL" and reward.value == "TALENT_POINTS_PER_100" then
                    local level = tonumber(ParagonRewardTrackData.currentLevel) or 0
                    local per = tonumber(reward.amount) or 1
                    GameTooltip:AddLine(string.format(
                        Locale.TALENT_PER_100_CURRENT or "Currently granted: +%d Talent Points",
                        math.floor(level / 100) * per), 1, 0.82, 0, false)
                    GameTooltip:AddLine(string.format(
                        Locale.TALENT_PER_100_NEXT or "Next point at Paragon level %d",
                        (math.floor(level / 100) + 1) * 100), 0.6, 0.6, 0.6, false)
                end
            end
        end
    end

    GameTooltip:Show()
end

--- Hides the milestone tooltip
-- @param self Frame The track node
function UIParagonTrackNode_OnLeave(self)
    hoveredNode = nil
    GameTooltip:Hide()
end

--- Forwards node mouse-down to the drag controller so drags starting on a
-- node work (nodes are mouse-enabled for tooltips and swallow clicks)
-- @param self Frame The track node
-- @param button string Mouse button name
function UIParagonTrackNode_OnMouseDown(self, button)
    if button == "LeftButton" then
        UIParagonRewardTrack_StartDrag(UIParagonRewardTrackClipper)
    end
end

--- Forwards node mouse-up to the drag controller
-- @param self Frame The track node
-- @param button string Mouse button name
function UIParagonTrackNode_OnMouseUp(self, button)
    if button ~= "LeftButton" then
        return
    end
    local clipper = UIParagonRewardTrackClipper

    -- Distinguish a real click from the end of a drag-scroll. Nodes forward
    -- mouse-DOWN to the drag controller (so a drag can start on a node),
    -- which means every click also opens a drag -- the cursor delta since
    -- StartDrag is the only thing that separates the two. Same scale
    -- normalisation StartDrag/OnUpdate use.
    local moved = 0
    if clipper and clipper.dragStartX then
        local cursorX = GetCursorPosition() / clipper:GetEffectiveScale()
        moved = math.abs(cursorX - clipper.dragStartX)
    end
    UIParagonRewardTrack_StopDrag(clipper)
    if moved > 4 then
        return
    end

    local milestone = self.milestone
    if not (milestone and self.unlocked) then
        return
    end
    -- Milestone 1400 is the only interactive node on the track
    if ParagonRacialData and ParagonRacial_Toggle
            and tonumber(milestone.level) == ParagonRacialData.milestone then
        ParagonRacial_Toggle()
    end
end

-- ============================================================================
-- LOCK STATE
-- ============================================================================

--- Refreshes the locked/unlocked visuals of every visible node
-- Locked: desaturated/dimmed icon, dim border, gray level label
-- Unlocked: full-color icon and border, gold level label
-- @usage Called after rebuilds and whenever the paragon level changes
function UIParagon_RefreshRewardTrackLocks()
    local currentLevel = ParagonRewardTrackData.currentLevel or 0

    for i = 1, ParagonRewardTrackData.nodeCount do
        local node = _G["ParagonTrackNode_" .. i]
        if node and node.milestone then
            local unlocked = currentLevel >= (tonumber(node.milestone.level) or 0)
            node.unlocked = unlocked

            if unlocked then
                if node.Icon then
                    node.Icon:SetDesaturated(nil)
                    node.Icon:SetVertexColor(1, 1, 1)
                end
                if node.Border then
                    node.Border:SetVertexColor(1, 1, 1)
                end
                if node.Level then
                    node.Level:SetTextColor(1, 0.82, 0)  -- Gold
                end
            else
                if node.Icon then
                    -- SetDesaturated returns nil when the hardware shader is
                    -- unsupported; fall back to a plain gray tint in that case
                    local shaderSupported = node.Icon:SetDesaturated(1)
                    if shaderSupported then
                        node.Icon:SetVertexColor(0.7, 0.7, 0.7)
                    else
                        node.Icon:SetVertexColor(0.4, 0.4, 0.4)
                    end
                end
                if node.Border then
                    node.Border:SetVertexColor(0.5, 0.5, 0.5)
                end
                if node.Level then
                    node.Level:SetTextColor(0.5, 0.5, 0.5)  -- Gray
                end
            end
        end
    end
end

-- ============================================================================
-- SECTION / STRIP BUILDING
-- ============================================================================

--- Builds the Reward Track section skeleton (header + divider + strip clipper)
-- Created once at file load; nodes are added later when the server pushes data
local function BuildRewardTrackSection()
    if UIParagonRewardTrack or not UIParagon then return end

    -- Section container, slotted between the XP-bar block and the Statistics
    -- body (the -40 offset clears the "Show XP bar" checkbox row)
    local section = CreateFrame("Frame", "UIParagonRewardTrack", UIParagon)
    section:SetWidth(TRACK_SECTION_WIDTH)
    section:SetHeight(TRACK_SECTION_HEIGHT)
    section:SetPoint("TOP", UIParagonTopBannerExperienceBar, "BOTTOM", 0, -40)

    -- Header FontString, styled like the STATISTICS one
    local title = section:CreateFontString("UIParagonRewardTrackTitle", "OVERLAY", "GameFontNormalHuge")
    title:SetPoint("TOPLEFT", section, "TOPLEFT", 20, 0)
    title:SetJustifyH("LEFT")
    title:SetTextColor(0.90, 0.80, 0.50, 1)
    title:SetText(Locale.REWARD_TRACK_TEXT or "REWARD TRACK")
    section.Title = title

    -- Divider under the header (same template as the Statistics divider)
    local divider = CreateFrame("Frame", "UIParagonRewardTrackDivider", section, "ParagonDivider")
    divider:SetPoint("TOP", section, "TOP", 0, -20)
    section.Divider = divider

    -- ScrollFrame clipper: clips node rendering and mouse hit-testing
    local clipper = CreateFrame("ScrollFrame", "UIParagonRewardTrackClipper", section)
    clipper:SetWidth(TRACK_SECTION_WIDTH)
    clipper:SetHeight(TRACK_CLIPPER_HEIGHT)
    clipper:SetPoint("TOPLEFT", section, "TOPLEFT", 0, -40)
    clipper:EnableMouse(true)
    clipper:EnableMouseWheel(true)
    section.Clipper = clipper

    -- Wide scroll child that carries the nodes (resized on rebuild)
    local strip = CreateFrame("Frame", "UIParagonRewardTrackStrip", clipper)
    strip:SetWidth(TRACK_SECTION_WIDTH)
    strip:SetHeight(TRACK_CLIPPER_HEIGHT)
    clipper:SetScrollChild(strip)
    clipper.scrollChild = strip
    section.Strip = strip

    -- Drag controller wiring
    clipper:SetScript("OnMouseDown", function(self, button)
        if button == "LeftButton" then
            UIParagonRewardTrack_StartDrag(self)
        end
    end)
    clipper:SetScript("OnMouseUp", function(self, button)
        if button == "LeftButton" then
            UIParagonRewardTrack_StopDrag(self)
        end
    end)
    clipper:SetScript("OnUpdate", UIParagonRewardTrack_OnUpdate)
    clipper:SetScript("OnMouseWheel", UIParagonRewardTrack_OnMouseWheel)
end

--- Rebuilds the milestone nodes from ParagonRewardTrackData.milestones
-- Uses named-frame recycling (like UIParagon_RebuildStatistics): frames are
-- looked up by unique global name and reused; extras are hidden. Nodes ARE
-- repositioned on every rebuild - safe here because ParagonTrackNodeTemplate
-- has no zoom animation caching first anchors.
-- @usage Called by UIParagon_OnReceiveRewardTrack when server data arrives
function UIParagon_RebuildRewardTrack()
    BuildRewardTrackSection()

    local clipper = UIParagonRewardTrackClipper
    local strip = UIParagonRewardTrackStrip
    if not clipper or not strip then return end

    local all = ParagonRewardTrackData.milestones or {}

    -- Contract says sorted ascending by level; enforce it defensively
    table.sort(all, function(a, b)
        return (tonumber(a.level) or 0) < (tonumber(b.level) or 0)
    end)

    local milestones = VisibleMilestones(all, ParagonRewardTrackData.currentLevel or 0)
    local count = #milestones

    -- !! FORCE THE STRIP BACK TO FULL ALPHA ON EVERY REBUILD !!
    -- Alpha multiplies down the parent chain, so a single stale 0.5 anywhere
    -- on section -> clipper -> strip makes every node underneath read as
    -- unearned, which is exactly what the "locked" styling looks like. The
    -- tutorial is the only thing in the addon that ever dimmed these (it no
    -- longer touches them -- see the noDim flag in Paragon_Tutorial.lua), but
    -- the point of resetting here is that it does not matter what did it: a
    -- rebuild runs on login and on every level change, so no dim from any
    -- source can outlive one.
    UIParagonRewardTrack:SetAlpha(1)
    clipper:SetAlpha(1)
    strip:SetAlpha(1)

    -- With a handful of nodes the strip is narrower than its clipper, so they
    -- would otherwise huddle against the left edge under a 650px header.
    local contentWidth = 0
    if count > 0 then
        contentWidth = (count - 1) * TRACK_NODE_SPACING + TRACK_NODE_WIDTH
    end
    local startX = TRACK_NODE_PADDING
    local centred = (clipper:GetWidth() - contentWidth) / 2
    if centred > startX then
        startX = centred
    end

    for i = 1, count do
        local milestone = milestones[i]
        local nodeName = "ParagonTrackNode_" .. i
        local node = _G[nodeName]

        if not node then
            node = CreateFrame("Frame", nodeName, strip, "ParagonTrackNodeTemplate")
            node:EnableMouseWheel(true)
            node:SetScript("OnEnter", UIParagonTrackNode_OnEnter)
            node:SetScript("OnLeave", UIParagonTrackNode_OnLeave)
            node:SetScript("OnMouseDown", UIParagonTrackNode_OnMouseDown)
            node:SetScript("OnMouseUp", UIParagonTrackNode_OnMouseUp)
            node:SetScript("OnMouseWheel", UIParagonRewardTrack_OnMouseWheel)

            if i > ParagonRewardTrackData.nodeCount then
                ParagonRewardTrackData.nodeCount = i
            end
        end

        node:ClearAllPoints()
        node:SetPoint("TOPLEFT", strip, "TOPLEFT",
            startX + (i - 1) * TRACK_NODE_SPACING, TRACK_NODE_TOP_OFFSET)

        node.milestone = milestone
        node:SetAlpha(1)          -- recycled frames must not carry a stale dim

        if node.Level then
            node.Level:SetText(tostring(milestone.level or "?"))
        end

        -- Circular icon crop, same call the stat items use
        SetPortraitToTexture(node.Icon, milestone.icon or "Interface\\Icons\\INV_Misc_QuestionMark")

        node:Show()
    end

    -- Hide recycled frames beyond the current milestone count
    for i = count + 1, ParagonRewardTrackData.nodeCount do
        local node = _G["ParagonTrackNode_" .. i]
        if node then
            node.milestone = nil
            node:Hide()
        end
    end

    -- Resize the scroll child, then re-clamp the scroll. The window always
    -- fits, so this normally equals the clipper width and maxScroll lands on
    -- 0 -- which also discards any leftover offset from a wider earlier strip.
    local stripWidth = startX + contentWidth + TRACK_NODE_PADDING
    if count == 0 or stripWidth < clipper:GetWidth() then
        stripWidth = clipper:GetWidth()
    end
    strip:SetWidth(stripWidth)

    -- Snap to the far end rather than preserving the old offset. A rebuild
    -- happens on login and on a level change, and at both of those moments the
    -- part worth looking at is the right-hand end: the milestone just earned
    -- and the few still ahead. Left at offset 0 the whole point of capping the
    -- view would sit off-screen behind a long tail of old milestones. Manual
    -- drags are untouched -- dragging does not rebuild.
    UIParagonRewardTrack_SetScroll(clipper, stripWidth)

    UIParagon_RefreshRewardTrackLocks()
    if UIParagon_RefreshRacialMilestone then
        UIParagon_RefreshRacialMilestone()
    end
end

-- ============================================================================
-- SERVER RECEIVE FUNCTION (Hook ID: 7)
-- ============================================================================

--- Handles the reward track definitions pushed by the server (Hook ID: 7)
-- The server sends ONE Smallfolk-serialized array table during the client
-- load request reply, so - mirroring UIParagon_OnReceiveAllData - the table
-- arrives as arg_table[1].
-- @param player table Player object (provided by server, unused)
-- @param arg_table table Arguments from server: { [1] = milestonesArray }
function UIParagon_OnReceiveRewardTrack(player, arg_table)
    local track = arg_table and arg_table[1]
    if type(track) ~= "table" then return end

    ParagonRewardTrackData.milestones = track
    UIParagon_RebuildRewardTrack()
end

-- ============================================================================
-- LEVEL CAPTURE
-- ============================================================================

-- UIParagon_OnClientReceiveLevel (Paragon_Network.lua) writes the level
-- straight into FontStrings and stores it nowhere, so hook it to capture
-- level changes and refresh node lock states live.
hooksecurefunc("UIParagon_OnClientReceiveLevel", function(player, arg_table)
    local level = tonumber(arg_table and arg_table[1]) or 0
    local previous = ParagonRewardTrackData.currentLevel or 0
    ParagonRewardTrackData.currentLevel = level

    -- A level change can move the WINDOW, not just the lock states, so the
    -- strip has to be rebuilt rather than repainted -- otherwise a milestone
    -- you just earned would sit there gold forever and the next one would
    -- never appear. Repaint only when the level did not actually move.
    if level ~= previous then
        UIParagon_RebuildRewardTrack()
    else
        UIParagon_RefreshRewardTrackLocks()
    end
end)

-- Build the section skeleton immediately: UIParagon.xml is loaded before this
-- file, so UIParagon and both virtual templates already exist
BuildRewardTrackSection()

-- ============================================================================
-- /soloach — solo-clear achievement diagnostic (temporary)
-- ============================================================================
-- Reports whether the CLIENT's own achievement DBC knows the custom rows and
-- whether the custom category is in its category tree, so a data problem can
-- be told apart from a UI-side rejection.
SLASH_SOLOACH1 = "/soloach"
SlashCmdList["SOLOACH"] = function()
    local function say(fmt, ...)
        DEFAULT_CHAT_FRAME:AddMessage("|cff66ccff[soloach]|r " .. string.format(fmt, ...))
    end

    for _, id in ipairs({ 19004, 19301, 19304 }) do
        local ok, aid, name, points, completed = pcall(GetAchievementInfo, id)
        if not ok then
            say("GetAchievementInfo(%d) ERROR: %s", id, tostring(aid))
        elseif aid then
            say("ach %d -> %q pts=%s completed=%s", id, tostring(name),
                tostring(points), tostring(completed))
        else
            say("ach %d -> NOT KNOWN TO CLIENT", id)
        end
    end

    local ok, n = pcall(GetCategoryNumAchievements, 15200)
    say("GetCategoryNumAchievements(15200) = %s%s", tostring(ok and n),
        ok and "" or " (error)")
    local okc, n2 = pcall(GetCategoryNumAchievements, 14821)
    say("control: category 14821 (Classic) = %s", tostring(okc and n2))

    local okl, list = pcall(GetCategoryList)
    if okl and type(list) == "table" then
        local found, dnr = false, 0
        for _, cid in ipairs(list) do
            if cid == 15200 then found = true end
            if cid == 14807 then dnr = dnr + 1 end
        end
        say("GetCategoryList: %d entries, contains 15200 = %s (14807 seen %d)",
            #list, tostring(found), dnr)
    else
        say("GetCategoryList failed: %s", tostring(list))
    end

    local okn, done, total = pcall(GetNumCompletedAchievements)
    say("GetNumCompletedAchievements -> %s / %s", tostring(okn and done),
        tostring(okn and total))
end

-- ============================================================================
-- RACIALLY AMBIGUOUS (milestone 1400)
-- ============================================================================

--- Repaints the Racially Ambiguous node so the track shows the CHOSEN
--- ability at a glance, and re-renders the tooltip if it is open on it.
--- Called from the track rebuild and from every ParagonRacial state push.
--- Defined at the bottom of the file deliberately: it closes over the
--- file-local RefreshOpenTooltip, which a Lua local only makes visible to
--- code parsed after it.
function UIParagon_RefreshRacialMilestone()
    if not ParagonRacialData then
        return
    end
    local target = ParagonRacialData.milestone
    local count = ParagonRewardTrackData and ParagonRewardTrackData.nodeCount or 0
    for i = 1, count do
        local node = _G["ParagonTrackNode_" .. i]
        if node and node.milestone and tonumber(node.milestone.level) == target then
            local icon = ParagonRacial_CurrentIcon and ParagonRacial_CurrentIcon()
            SetPortraitToTexture(node.Icon, icon or node.milestone.icon
                or "Interface\Icons\INV_Misc_QuestionMark")
            break
        end
    end
    RefreshOpenTooltip()
end
