--[[
    Paragon Rework: at-level experience sources (local module, see
    "Paragon Progression Design.md")

    Replaces the flat universal XP values with content-worth values:
      - Creatures: at-level base XP formula (content-tier constants, elite x2),
        split across the killer's group like regular kill XP.
      - Quests: the quest's own at-level XP (quest_template.RewardXPDifficulty
        indexed into the client's QuestXP table).
      - Achievements: achievement points x PARAGON_ACHIEVEMENT_POINT_XP.

    Mechanism: upstream's UpdatePlayerExperience prefers a per-entry value from
    Config.experience[source] over the universal fallback. This module computes
    values inside the OnBefore*Experience events (which fire just before that
    lookup) and writes them into the cache. Hand-authored DB override rows are
    snapshotted at load and always win as the base value; for creatures they
    act as the pre-split XP pool.

    Data dependencies (generated from client DBCs):
      ParagonReworkData_QuestXP, ParagonReworkData_AchievementPoints
]]

local Config = require("paragon_config")

-- Pristine DB-authored overrides, snapshotted before this module starts
-- writing computed values into the live cache.
local db_overrides = {
    creature = {},
    quest = {},
    achievement = {},
}
for source, snapshot in pairs(db_overrides) do
    local live = Config.experience and Config.experience[source]
    if live then
        for entry, value in pairs(live) do
            snapshot[entry] = value
        end
    end
end

-- ============================================================================
-- VALUE CALCULATION
-- ============================================================================

--- At-level base XP for a mob: what it is worth to a same-level player.
--- Constants mirror the core's per-content-tier base gain.
local function CreatureAtLevelXP(creature)
    local level = creature:GetLevel()
    local base
    if level <= 60 then
        base = 5 * level + 45
    elseif level <= 70 then
        base = 5 * level + 235
    else
        base = 5 * level + 580
    end

    if creature:IsElite() then
        base = base * 2
    end

    return base
end

--- Group-share factor mirroring regular kill XP: split across eligible
--- members, with the standard group-size bonus for parties (raids: none).
--- Returns share_multiplier (applied to the pool value) and the list of
--- eligible members other than the killer.
local MIN_LEVEL = function()
    return tonumber(Config:GetByField("MINIMUM_LEVEL_FOR_PARAGON_XP")) or 80
end

local GROUP_BONUS = { [3] = 1.166, [4] = 1.3 }

local function GroupShare(killer, creature)
    local group = killer:GetGroup()
    if not group then
        return 1.0, nil
    end

    local share_range = tonumber(Config:GetByField("PARAGON_GROUP_XP_DISTANCE")) or 74
    local min_level = MIN_LEVEL()
    local is_raid = group:IsRaidGroup()

    local eligible_count = 0
    local others = {}
    for _, member in pairs(group:GetMembers()) do
        if member:IsAlive()
            and member:GetLevel() >= min_level
            and member:GetMapId() == killer:GetMapId()
            and member:GetDistance(killer) <= share_range then
            eligible_count = eligible_count + 1
            if member:GetGUIDLow() ~= killer:GetGUIDLow() then
                table.insert(others, member)
            end
        end
    end

    if eligible_count <= 1 then
        return 1.0, nil
    end

    local bonus = 1.0
    if not is_raid then
        bonus = GROUP_BONUS[eligible_count] or (eligible_count >= 5 and 1.4 or 1.0)
    end

    return bonus / eligible_count, others
end

--- Achievement value: DB override wins, else points x multiplier.
--- Global: also used by the banking module.
function ParagonRework_AchievementValue(achievement_id)
    local override = db_overrides.achievement[achievement_id]
    if override then
        return override
    end

    local points = ParagonReworkData_AchievementPoints
        and ParagonReworkData_AchievementPoints[achievement_id] or 0
    local per_point = tonumber(Config:GetByField("PARAGON_ACHIEVEMENT_POINT_XP")) or 1000
    return points * per_point
end

--- Quest value: DB override wins, else at-level XP from quest data.
local function QuestValue(player, quest)
    local quest_id = quest:GetId()
    local override = db_overrides.quest[quest_id]
    if override then
        return override
    end

    local level = quest:GetLevel()
    if not level or level < 1 then
        level = player:GetLevel()
    end
    if level > 100 then
        level = 100
    end

    local difficulty = 0
    local result = WorldDBQuery("SELECT RewardXPDifficulty FROM quest_template WHERE ID = " .. quest_id)
    if result then
        difficulty = result:GetUInt32(0)
    end

    local row = ParagonReworkData_QuestXP and ParagonReworkData_QuestXP[level]
    if not row then
        return nil
    end

    return row[difficulty + 1] or 0
end

--- Gray threshold, mirroring the core (Formulas.h GetGrayLevel).
local function GrayLevel(pl_level)
    if pl_level <= 5 then
        return 0
    elseif pl_level <= 39 then
        return pl_level - 5 - math.floor(pl_level / 10)
    elseif pl_level <= 59 then
        return pl_level - 1 - math.floor(pl_level / 5)
    end
    return pl_level - 9
end

--- Full kill-share computation. Global: the party-credit module calls this
--- independently so the numbers always match regardless of handler order.
--- Returns per-member share and the eligible members other than the killer.
--- Nerf 2026-08-19: mobs gray to the KILLER pay half — trivially farming
--- old content stays worthwhile but no longer competes with at-level play;
--- the moment a mob cons green or better it pays in full again.
function ParagonRework_ComputeKillShare(killer, creature)
    local pool = db_overrides.creature[creature:GetEntry()] or CreatureAtLevelXP(creature)
    if creature:GetLevel() <= GrayLevel(killer:GetLevel()) then
        pool = pool * 0.5
    end
    local share_mult, others = GroupShare(killer, creature)
    local share = math.floor(pool * share_mult + 0.5)
    if share < 1 then
        share = 1
    end
    return share, others
end

-- ============================================================================
-- EVENT SUBSCRIBERS (mutate the config cache; return nothing)
-- ============================================================================

RegisterMediatorEvent("OnBeforeCreatureExperience", function(player, creature, paragon)
    local share = ParagonRework_ComputeKillShare(player, creature)
    Config.experience.creature[creature:GetEntry()] = share
end)

RegisterMediatorEvent("OnBeforeQuestExperience", function(player, quest, paragon)
    -- Write 0 too: a quest whose at-level XP is zero should pay zero,
    -- not fall back to the universal flat value.
    local value = QuestValue(player, quest)
    if value ~= nil then
        Config.experience.quest[quest:GetId()] = value
    end
end)

RegisterMediatorEvent("OnBeforeAchievementExperience", function(player, achievement, paragon)
    -- Write 0 too: 0-point achievements (feats of strength) pay nothing.
    Config.experience.achievement[achievement:GetId()] = ParagonRework_AchievementValue(achievement:GetId())
end)

print("[Paragon] Rework: at-level experience sources module loaded")
