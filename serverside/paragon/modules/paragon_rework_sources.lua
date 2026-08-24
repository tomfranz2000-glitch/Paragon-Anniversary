--[[
    Paragon Rework: at-level experience sources (local module, see
    "Paragon Progression Design.md")

    Replaces the flat universal XP values with content-worth values:
      - Creatures: the core-equivalent at-level reward (including creature
        template, health, elite, no-XP and realm-rate modifiers), split using
        the participating group size before Paragon eligibility is applied.
      - Quests: the quest's own at-level XP (quest_template.RewardXPDifficulty
        indexed into the client's QuestXP table).
      - Achievements: achievement points x PARAGON_ACHIEVEMENT_POINT_XP.

    Mechanism: upstream's UpdatePlayerExperience prefers a per-entry value from
    Config.experience[source] over the universal fallback. This module computes
    values inside the OnBefore*Experience events (which fire just before that
    lookup) and writes them into the cache. Hand-authored quest and achievement
    overrides are snapshotted at load and always win. Creature overrides are
    deliberately ignored: kill XP must always reflect the creature's actual
    native reward characteristics.

    Data dependencies:
      - generated client-DBC maps for quest XP and stock achievement points;
      - acore_world.achievement_dbc for custom achievement points not present
        in the stock map.
]]

local Config = require("paragon_config")

-- Pristine DB-authored overrides, snapshotted before this module starts
-- writing computed values into the live cache.
local db_overrides = {
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

--- Standard group-size bonus. The divisor is the number of participating
--- members, not the number who may receive Paragon XP. Thus one eligible real
--- player in a five-member party still receives 1.4 / 5 = 28%.
local GROUP_BONUS = { [3] = 1.166, [4] = 1.3 }
local missing_xp_api_reported = false

local function StandardGroupShare(participant_count, is_raid)
    participant_count = tonumber(participant_count) or 0
    if participant_count <= 1 then
        return 1.0
    end

    local bonus = 1.0
    if not is_raid then
        bonus = GROUP_BONUS[participant_count]
            or (participant_count >= 5 and 1.4 or 1.0)
    end

    return bonus / participant_count
end

local function GroupShare(recipient, creature)
    local group = recipient:GetGroup()
    if not group then
        return 1.0
    end

    local share_range = tonumber(Config:GetByField("PARAGON_GROUP_XP_DISTANCE")) or 74
    local map = creature:GetMap()
    local is_dungeon = map and map:IsDungeon()
    local is_raid = map and map:IsRaid() and group:IsRaidGroup()

    local participant_count = 0
    for _, member in pairs(group:GetMembers()) do
        if member:IsAlive()
            and member:GetMapId() == creature:GetMapId()
            and (is_dungeon or member:GetDistance(creature) <= share_range) then
            participant_count = participant_count + 1
        end
    end

    return StandardGroupShare(participant_count, is_raid)
end

-- Unknown achievement ids are custom/server DBC rows. Cache both hits and
-- misses so their authoritative world-DB points cost at most one query per
-- achievement id for the lifetime of the Lua state.
local achievement_points_cache = {}

local function AchievementPoints(achievement_id)
    local points = ParagonReworkData_AchievementPoints
        and ParagonReworkData_AchievementPoints[achievement_id]
    if points ~= nil then
        return points
    end

    points = achievement_points_cache[achievement_id]
    if points ~= nil then
        return points
    end

    local result = WorldDBQuery(
        "SELECT Points FROM achievement_dbc WHERE ID = " .. achievement_id .. " LIMIT 1")
    points = result and result:GetUInt32(0) or 0
    achievement_points_cache[achievement_id] = points
    return points
end

--- Achievement value: explicit XP override wins, then the committed stock
--- points map, then a cached lookup of custom achievement DBC rows.
--- Global: also used by the banking module.
function ParagonRework_AchievementValue(achievement_id)
    local override = db_overrides.achievement[achievement_id]
    if override then
        return override
    end

    local points = AchievementPoints(achievement_id)
    local per_point = tonumber(Config:GetByField("PARAGON_ACHIEVEMENT_POINT_XP")) or 2000
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

--- Full per-recipient kill-share computation.
---
--- GetAtLevelXPReward is supplied by the required ALE patch and mirrors the
--- core's Acore::XP::Gain plus KillRewarder's low-health adjustment, but fixes
--- the virtual player level to the creature level. Gray status remains a
--- recipient property and applies a flat 50% reduction.
function ParagonRework_ComputeKillShare(recipient, creature, participant_count, is_raid)
    if not creature.GetAtLevelXPReward then
        if not missing_xp_api_reported then
            print("[Paragon] GetAtLevelXPReward missing; creature XP disabled until the ALE patch is installed")
            missing_xp_api_reported = true
        end
        return 0
    end

    local pool = creature:GetAtLevelXPReward()
    if pool <= 0 then
        return 0
    end

    -- Custom Paragon boundary: mobs zero through nine levels below the
    -- recipient pay in full; ten or more levels below pay half.
    if recipient:GetLevel() - creature:GetLevel() >= 10 then
        pool = pool * 0.5
    end

    local share_mult
    if tonumber(participant_count) and tonumber(participant_count) > 0 then
        -- ALE sends an integer count instead of a pre-divided C++ float. This
        -- avoids losing exact boundaries such as 1000 * 1.3 / 4 = 325 when
        -- the float crosses into Lua as 0.324999988.
        share_mult = StandardGroupShare(participant_count, is_raid)
    else
        share_mult = GroupShare(recipient, creature)
    end
    -- Match KillRewarder's uint32 conversion: truncate after group sharing.
    -- The tiny epsilon only neutralizes binary representation below an exact
    -- integer boundary (for example 1000 * 1.4 / 5 = 279.99999999999994).
    local share = math.floor(pool * share_mult + 1e-7)
    return share
end

-- ============================================================================
-- EVENT SUBSCRIBERS (mutate the config cache; return nothing)
-- ============================================================================

RegisterMediatorEvent("OnBeforeCreatureExperience", function(player, creature, paragon, participant_count, is_raid)
    local share = ParagonRework_ComputeKillShare(player, creature, participant_count, is_raid)
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
