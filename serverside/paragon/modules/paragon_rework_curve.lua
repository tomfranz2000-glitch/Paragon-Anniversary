--[[
    Paragon Rework: decaying-growth experience curve (local module, see
    "Paragon Progression Design.md")

    Replaces upstream's linear cost (BASE_MAX_EXPERIENCE x level) with:

        cost(1) = BASE_MAX_EXPERIENCE            (30,000)
        cost(L) = cost(L-1) x (1 + r0 / (1 + L/k))

    Config fields (paragon_config): PARAGON_CURVE_R0, PARAGON_CURVE_K.
    Early levels compound noticeably, deep levels approach flat growth.

    Upstream still writes its linear value into exp.max on SetLevel before
    publishing OnParagonLevelChanged, so this module corrects it there, and
    self-heals on the pre-XP and pre-UI-load events (covers login/load, where
    no level-change event fires).
]]

local Config = require("paragon_config")

local cost_cache = {}
local cached_params = nil

local function CurveParams()
    return tonumber(Config:GetByField("BASE_MAX_EXPERIENCE")) or 30000,
        tonumber(Config:GetByField("PARAGON_CURVE_R0")) or 0.0429,
        tonumber(Config:GetByField("PARAGON_CURVE_K")) or 20
end

--- XP required to go from `level` to `level + 1`.
local function CurveCost(level)
    if level < 1 then
        level = 1
    end

    local base, r0, k = CurveParams()
    local key = base .. ":" .. r0 .. ":" .. k
    if cached_params ~= key then
        cost_cache = { [1] = base }
        cached_params = key
    end

    if cost_cache[level] then
        return cost_cache[level]
    end

    -- Fill the cache iteratively up to the requested level
    local last_known = 1
    for l = level, 2, -1 do
        if cost_cache[l] then
            last_known = l
            break
        end
    end

    local cost = cost_cache[last_known]
    for l = last_known + 1, level do
        cost = math.floor(cost * (1 + r0 / (1 + l / k)) + 0.5)
        cost_cache[l] = cost
    end

    return cost
end

-- Exposed for other modules / debugging
ParagonRework_CurveCost = CurveCost

local function HealMaxExperience(paragon)
    if not paragon or not paragon.GetLevel then
        return
    end
    local expected = CurveCost(paragon:GetLevel())
    if paragon:GetExperienceForNextLevel() ~= expected then
        paragon:SetExperienceForNextLevel(expected)
    end
end

-- After every level change (fires mid-cascade too, so multi-level gains use
-- the curved cost for each step).
RegisterMediatorEvent("OnParagonLevelChanged", function(player, paragon, old_level, new_level)
    HealMaxExperience(paragon)
end)

-- Self-heal points: before any XP is processed, and before state is sent to
-- the client UI. Catches freshly loaded characters whose max XP was computed
-- by the upstream linear formula.
RegisterMediatorEvent("OnBeforeUpdatePlayerExperience", function(player, paragon, source_type, entry)
    HealMaxExperience(paragon)
end)

RegisterMediatorEvent("OnBeforeClientLoadRequest", function(player, paragon)
    HealMaxExperience(paragon)
end)

print("[Paragon] Rework: experience curve module loaded")
