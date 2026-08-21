--[[
    Paragon Rework: gem doubling (milestone 1150, universal)

    Every gem socketed in the player's equipment counts twice: this module
    sums the flat stats of all socketed gems and applies the same amounts
    AGAIN through the standard apply chain — the "track the stats and
    output them a second time" design. The gems themselves are untouched
    (no enchant edits, no tooltip lies): the doubled half lives as paragon
    statistics, exactly like codex bonuses.

    Gem detection: equipped items' enchant slots {2,3,4,6,11} (native
    sockets, prismatic, and the milestone-1100 second buckle socket),
    filtered through the generated ParagonGemStats table
    (Tools/gen_gem_data.py) — an enchant absent from that table is not a
    stat gem (perm enchants parked in sock slots by the §1e system, the
    3729 buckle marker, meta specials) and contributes nothing.

    Coverage (from the full 613-enchant DBC census):
      - all 17 flat ItemModTypes gems use (stats, ratings, AP, SP, mp5)
      - "+N All Stats" gems -> five stat mods
      - "+N Resist All" gems -> six resistance mods
      - "+N Armor" gems -> armor mod
      NOT doubled (by design): meta-gem special effects (procs, %-effects,
      stun-duration etc.) and spell-penetration gems — those grant their
      effect via embedded spells, not flat stats. Socket bonuses are not
      gems and are untouched.

    Application primitives are the codex's: HandleStatFlatModifier /
    ApplyRatingMod, plus player:SetSpellPower for spell power and the mp5
    carrier aura 1900110 (own clone of the Petting Zoo aura 1900099 — a
    reconcile-owning module needs its OWN aura id) cast with
    CastCustomSpell bp override. Rating triples follow the codex
    convention (hit/crit/haste/resilience apply all three sub-ratings);
    attack power applies melee AND ranged (WotLK item-AP convention).

    Reconcile triggers: client load request (plus a 10s delta ticker —
    stock re-gemming has no ALE event), equip (29), paragon level change
    (milestone gain AND strip), and a direct poke from
    paragon_double_buckle.lua after custom socketing. Bots and sub-80
    characters compute nothing (account-gate contract).

    Tooltip: prefix "ParagonGems" fn 1 pushes { stats = { {label, amount},
    ... } }; Paragon_RewardTrack.lua appends "Currently doubled:" +
    green lines to the milestone-1150 track tooltip (ILVL_ATTUNEMENT
    pattern).
]]

local Constant = require("paragon_constant")

local PREFIX = "ParagonGems"
local MILESTONE = 1150
local REGEN_SPELL = 1900110
local APPLIED_KEY = "ParagonGemDoubleApplied"
local REGEN_KEY = "ParagonGemDoubleRegen"
local PUSHED_KEY = "ParagonGemDoublePushed"
local TICK_MS = 10000
local GEM_SLOTS = { 2, 3, 4, 6, 11 }

-- ItemModType -> apply-chain mods (um = UNIT_MODS key, cr = COMBAT_RATING
-- key). Special keys: 45 spell power and 43 mp5 are handled out of band.
local STAT_MODS = {
    [3]  = { { um = "STAT_AGILITY" } },
    [4]  = { { um = "STAT_STRENGTH" } },
    [5]  = { { um = "STAT_INTELLECT" } },
    [6]  = { { um = "STAT_SPIRIT" } },
    [7]  = { { um = "STAT_STAMINA" } },
    [12] = { { cr = "DEFENSE_SKILL" } },
    [13] = { { cr = "DODGE" } },
    [14] = { { cr = "PARRY" } },
    [31] = { { cr = "HIT_MELEE" }, { cr = "HIT_RANGED" }, { cr = "HIT_SPELL" } },
    [32] = { { cr = "CRIT_MELEE" }, { cr = "CRIT_RANGED" }, { cr = "CRIT_SPELL" } },
    [35] = { { cr = "CRIT_TAKEN_MELEE" }, { cr = "CRIT_TAKEN_RANGED" }, { cr = "CRIT_TAKEN_SPELL" } },
    [36] = { { cr = "HASTE_MELEE" }, { cr = "HASTE_RANGED" }, { cr = "HASTE_SPELL" } },
    [37] = { { cr = "EXPERTISE" } },
    [38] = { { um = "ATTACK_POWER" }, { um = "ATTACK_POWER_RANGED" } },
    [44] = { { cr = "ARMOR_PENETRATION" } },
    as   = { { um = "STAT_STRENGTH" }, { um = "STAT_AGILITY" }, { um = "STAT_STAMINA" },
             { um = "STAT_INTELLECT" }, { um = "STAT_SPIRIT" } },
    ra   = { { um = "RESISTANCE_HOLY" }, { um = "RESISTANCE_FIRE" }, { um = "RESISTANCE_NATURE" },
             { um = "RESISTANCE_FROST" }, { um = "RESISTANCE_SHADOW" }, { um = "RESISTANCE_ARCANE" } },
    ar   = { { um = "ARMOR" } },
}

local LABELS = {
    [3] = "Agility", [4] = "Strength", [5] = "Intellect", [6] = "Spirit",
    [7] = "Stamina", [12] = "Defense Rating", [13] = "Dodge Rating",
    [14] = "Parry Rating", [31] = "Hit Rating", [32] = "Critical Strike Rating",
    [35] = "Resilience Rating", [36] = "Haste Rating", [37] = "Expertise Rating",
    [38] = "Attack Power", [43] = "Mana per 5 sec.", [44] = "Armor Penetration Rating",
    [45] = "Spell Power", as = "All Stats", ra = "All Resistances", ar = "Armor",
}

-- tooltip display order
local DISPLAY = { 4, 3, 7, 5, 6, "as", 38, 45, 32, 31, 36, 37, 44, 12, 13, 14, 35, 43, "ra", "ar" }

local function IsBot(player)
    return player.IsPlayerBot and player:IsPlayerBot()
end

-- ============================================================================
-- COMPUTE
-- ============================================================================

-- enchant id -> gem color mask, built lazily from ParagonGemProps (needed
-- for meta-gem activation: color counts feed the condition comparators)
local ench_color = nil

local function EnchColor(ench)
    if not ench_color then
        ench_color = {}
        for _, def in pairs(ParagonGemProps or {}) do
            ench_color[def.ench] = def.color
        end
    end
    return ench_color[ench]
end

--- Mirror of Player::EnchantmentFitsRequirements (Player.cpp:11323, called
--- with slot -1 from ApplyEnchantment): counts index 1=meta 2=red 3=yellow
--- 4=blue; comparator 2 '<', 3 '>', 5 '>='; rows AND together.
local function ConditionMet(cond, counts)
    local rows = ParagonGemConditions and ParagonGemConditions[cond]
    if not rows then
        return true
    end
    for _, r in ipairs(rows) do
        local cur = counts[r.c] or 0
        local cmp = r.cc > 0 and (counts[r.cc] or 0) or r.v
        if r.op == 2 and cur >= cmp then
            return false
        elseif r.op == 3 and cur <= cmp then
            return false
        elseif r.op == 5 and cur < cmp then
            return false
        end
    end
    return true
end

--- Sum of socketed gem stats across all equipment: map of STAT_MODS key
--- (plus 43/45) -> amount. Mirrors core application rules: broken items
--- grant nothing; a meta gem with an unmet activation condition grants
--- nothing (the core's color count reads enchant slots 2-6 of unbroken
--- equipped items only — the slot-11 buckle gem does NOT feed the count,
--- exactly like stock).
local function GemTotals(player)
    local counts = { 0, 0, 0, 0 }
    local found = {}
    for eq = 0, 18 do
        local it = player:GetEquippedItemBySlot(eq)
        if it and not it:IsBroken() then
            for _, s in ipairs(GEM_SLOTS) do
                local ench = it:GetEnchantmentId(s) or 0
                if ench > 0 then
                    if s ~= 11 then
                        local color = EnchColor(ench)
                        if color then
                            for b = 1, 4 do
                                if math.floor(color / 2 ^ (b - 1)) % 2 == 1 then
                                    counts[b] = counts[b] + 1
                                end
                            end
                        end
                    end
                    found[#found + 1] = ench
                end
            end
        end
    end
    local totals = {}
    for _, ench in ipairs(found) do
        local fx = ParagonGemStats and ParagonGemStats[ench]
        if fx and (not fx.cond or ConditionMet(fx.cond, counts)) then
            for _, e in ipairs(fx) do
                local key = e.t or e.k
                totals[key] = (totals[key] or 0) + e.a
            end
        end
    end
    return totals
end

local function SameTotals(a, b)
    if not a or not b then
        return false
    end
    for k, v in pairs(a) do
        if b[k] ~= v then
            return false
        end
    end
    for k, v in pairs(b) do
        if a[k] ~= v then
            return false
        end
    end
    return true
end

-- ============================================================================
-- APPLY
-- ============================================================================

local function ApplyTotals(player, totals, apply)
    for key, amount in pairs(totals) do
        local mods = STAT_MODS[key]
        if mods and amount > 0 then
            for _, mod in ipairs(mods) do
                if mod.cr then
                    player:ApplyRatingMod(Constant.STATISTICS.COMBAT_RATING[mod.cr], amount, apply)
                else
                    player:HandleStatFlatModifier(Constant.STATISTICS.UNIT_MODS[mod.um], 0, amount, apply)
                end
            end
        elseif key == 45 and amount > 0 then
            player:SetSpellPower(amount, apply)
        end
        -- key 43 (mp5) is the regen aura's job, below
    end
end

local function ReconcileRegen(player, want)
    local have = player:GetData(REGEN_KEY) or 0
    local present = player:HasAura(REGEN_SPELL)
    if want == have and present == (want > 0) then
        return
    end
    if present then
        player:RemoveAura(REGEN_SPELL)
    end
    if want > 0 then
        player:CastCustomSpell(player, REGEN_SPELL, true, want)
    end
    player:SetData(REGEN_KEY, want)
end

-- ============================================================================
-- PUSH (track tooltip, delta-suppressed)
-- ============================================================================

local function Push(player, totals, force)
    local stats = {}
    for _, key in ipairs(DISPLAY) do
        local amount = totals[key]
        if amount and amount > 0 then
            stats[#stats + 1] = { label = LABELS[key], amount = amount }
        end
    end
    local sig = {}
    for _, s in ipairs(stats) do
        sig[#sig + 1] = s.label .. "=" .. s.amount
    end
    sig = table.concat(sig, ";")
    if not force and player:GetData(PUSHED_KEY) == sig then
        return
    end
    player:SetData(PUSHED_KEY, sig)
    player:SendServerResponse(PREFIX, 1, { stats = stats })
end

-- ============================================================================
-- RECONCILE
-- ============================================================================

local function Reconcile(player, force)
    -- account-gate contract: bots and sub-80 alts compute nothing
    local eligible = not IsBot(player) and player:GetLevel() >= 80
    local paragon = eligible and player:GetData("Paragon") or nil
    local owed = paragon and paragon:GetLevel() >= MILESTONE
    local totals = owed and GemTotals(player) or {}

    local prev = player:GetData(APPLIED_KEY)
    if prev == nil and not next(totals) and not eligible then
        return -- fast path: nothing applied, nothing owed (every bot equip)
    end
    if not SameTotals(prev, totals) then
        -- swapping the set momentarily drops max health and the core
        -- clamps current health to the lowered max — preserve it
        local health = player:IsAlive() and player:GetHealth() or nil
        if prev then
            ApplyTotals(player, prev, false)
        end
        ApplyTotals(player, totals, true)
        player:SetData(APPLIED_KEY, totals)
        if health then
            player:SetHealth(math.min(health, player:GetMaxHealth()))
        end
    end
    -- always for eligible players: also clears a stale relog-restored
    -- aura when nothing is owed anymore (it is death-persistent)
    ReconcileRegen(player, totals[43] or 0)
    Push(player, totals, force)
end

local function SafeReconcile(player, force, label)
    local ok, err = pcall(Reconcile, player, force)
    if not ok then
        print("[Paragon] gem double " .. label .. " error: " .. tostring(err))
    end
end

--- Poke for sibling modules (paragon_double_buckle.lua calls this after
--- socketing through the custom path).
function ParagonGemDouble_Poke(player)
    SafeReconcile(player, false, "poke")
end

-- ============================================================================
-- LIFECYCLE
-- ============================================================================

-- delta tick: stock re-gemming (CMSG_SOCKET_GEMS) has no ALE event.
-- Tickers only ever start from the client load request — bots never pay.
local tickers = {}

local function OnTick(eventId, delay, repeats, player)
    SafeReconcile(player, false, "tick")
end

local function EnsureTicker(player)
    local guid = player:GetGUIDLow()
    if not tickers[guid] then
        tickers[guid] = player:RegisterEvent(OnTick, TICK_MS, 0)
    end
end

RegisterPlayerEvent(4, function(event, player)
    tickers[player:GetGUIDLow()] = nil
end)

RegisterMediatorEvent("OnAfterClientLoadRequest", function(player, paragon)
    if player then
        SafeReconcile(player, true, "load")
        EnsureTicker(player)
    end
end)

RegisterMediatorEvent("OnParagonLevelChanged", function(player, paragon)
    if player then
        SafeReconcile(player, false, "level")
    end
end)

RegisterPlayerEvent(29, function(event, player, item, bag, slot)
    SafeReconcile(player, false, "equip")
end)

print("[Paragon] Rework: gem double module loaded")
