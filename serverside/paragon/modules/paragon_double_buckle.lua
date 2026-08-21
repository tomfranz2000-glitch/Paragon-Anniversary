--[[
    Paragon Rework: double belt buckle (milestone 1100)

    A second Eternal Belt Buckle used on an already-buckled belt opens one
    more prismatic socket. The 3.3.5 client hardcodes "template sockets +
    one prismatic" in the socketing UI, so the second socket can never
    appear there: the server owns it, and the Paragon addon
    (Paragon_DoubleBuckle.lua, prefix "ParagonBuckle") renders it and
    provides the gem interaction — drag a gem (or a buckle) onto the
    equipped belt in the character frame.

    WHY ITEM_EVENT_ON_USE AND NOT THE CAST HOOK: Spell::CheckItems rejects
    any prismatic enchant on an item whose PRISMATIC slot is occupied
    (SPELL_FAILED_MAX_SOCKETS, Spell.cpp:7544) — and that runs in
    prepare(), before the cast exists, so PLAYER_EVENT_ON_SPELL_CAST never
    fires for a second buckle. ITEM_EVENT_ON_USE fires from the use-item
    packet BEFORE the spell is created (ALE::OnUse -> OnItemUse,
    ItemHooks.cpp:81; a handler returning false suppresses the cast with a
    silent inventory packet). The second buckle therefore happens entirely
    in this hook: we consume a buckle ourselves and open the socket. The
    first buckle (empty prismatic slot) passes through untouched.

    STORAGE: the second socket lives in PROP_ENCHANTMENT_SLOT_4 (11) on
    EVERY belt — while empty it holds enchant 3729 (the buckle's own
    prismatic-socket enchant, stat-less); a socketed gem's enchant
    replaces it. Slot 11 was chosen over the spare SOCK slots because the
    client renders PROP-slot enchant names as a tooltip line positioned
    directly under the socket rows (that is how random-suffix stats
    display), which the addon restyles into a proper socket row — SOCK
    slots render nothing at all. Two constraints follow:

    - Random-stat belts are REFUSED: Item::SetItemRandomProperties writes
      ALL FIVE prop slots (7-11) from the roll (Item.cpp:684), so slot 11
      is only safe on belts with template RandomProperty = RandomSuffix =
      0. Level-80 gear is all fixed-stat; the exclusion only touches
      leveling greens.
    - Core unique-gem scans (GetGemCountWithID, GetGemCountWithLimitCategory,
      CanEquipUniqueItem) originally read enchant slots 2-6 only; the §1o
      core patch extends all three to slot 11 (random-property enchants
      carry GemID 0, so the wider range adds no false matches). Stock
      socketing and equipping therefore see the overflow gem. This
      handler's own path (which bypasses HandleSocketOpcode) enforces the
      same rules in Lua below: cross-item unique-equipped scan and the
      ItemLimitCategory cap (ParagonGemLimitMax, jeweler's gems 3).

    Why stats just work: PROP-slot enchants have no socket-shape check in
    Player::ApplyEnchantment — login/equip blindly apply all 12 slots, and
    HandleSocketOpcode never touches slots it received no gem for. The ALE
    §1o patch (ItemMethods.h) opens slots 7-11 to Lua.

    Entitlement: marker spell 1900107 (LEARNED_SPELL_SPECIALS.DOUBLE_BUCKLE,
    milestone 1100) gates the ACT — attaching the second buckle, socketing
    its gem — not ownership: opened sockets and their gems survive a
    milestone strip (the §1e dual-enchant stance).

    Accepted exemption: stock socket paths end with RemoveTradeableItem /
    ClearSoulboundTradeable (the 2h BoP-trade window); this path does not —
    a freshly looted belt stays raid-tradeable after buckling/gemming.
    Solo test realm, no trade economy to protect.
]]

local PREFIX = "ParagonBuckle"
local BUCKLE_ITEM = 41611
local BUCKLE_ENCHANT = 3729  -- ITEM_ENCHANTMENT_TYPE_PRISMATIC_SOCKET, stat-less
local MARKER_SPELL = 1900107 -- milestone 1100 marker (hidden passive)
local PRISMATIC_SLOT = 6
local SOCKET_SLOT = 11       -- PROP_ENCHANTMENT_SLOT_4: the second socket
local WAIST_SLOT = 5
local ITEM_CLASS_GEM = 3
local UNIQUE_EQUIPPABLE_FLAG = 0x80000
local PUSHED_KEY = "ParagonBucklePushed"
local TICK_MS = 10000
-- every slot that can hold a gem enchant (BONUS 5 skipped, mirroring core)
local GEM_SCAN_SLOTS = { 2, 3, 4, 6, 11 }

local function Deny(player, text)
    player:SendBroadcastMessage("|cffff4444[Paragon]|r " .. text)
end

local function Note(player, text)
    player:SendBroadcastMessage("|cff44ff44[Paragon]|r " .. text)
end

local function IsBot(player)
    return player.IsPlayerBot and player:IsPlayerBot()
end

-- item entry -> eligibility (fixed-stat belts only; cached for the Lua
-- state's lifetime). Random-stat items rewrite prop slots 7-11 from the
-- roll, which would clobber the second socket.
local eligible = {}

local function BeltEligible(item)
    local entry = item:GetEntry()
    local cached = eligible[entry]
    if cached ~= nil then
        return cached
    end
    local q = WorldDBQuery(
        "SELECT RandomProperty, RandomSuffix FROM item_template WHERE entry = " .. entry)
    if not q then
        return false -- transient DB failure: fail closed but do NOT cache
    end
    local ok = q:GetUInt32(0) == 0 and q:GetUInt32(1) == 0
    eligible[entry] = ok
    return ok
end

-- enchant ids this module may legitimately find in slot 11 (the parked
-- buckle marker or any real gem enchant). Anything else in that slot is
-- foreign (e.g. a custom random-property roll) and must not be treated —
-- or advertised — as an open second socket.
local known_socket_ench = nil

local function KnownSocketEnchant(ench)
    if ench == BUCKLE_ENCHANT then
        return true
    end
    if not known_socket_ench then
        known_socket_ench = {}
        for _, def in pairs(ParagonGemProps or {}) do
            known_socket_ench[def.ench] = true
        end
    end
    return known_socket_ench[ench] or false
end

--- currentEnchant in the second socket (0 = not opened or foreign content)
local function SocketState(item)
    local ench = item:GetEnchantmentId(SOCKET_SLOT) or 0
    if ench > 0 and not KnownSocketEnchant(ench) then
        return 0
    end
    return ench
end

-- ============================================================================
-- GEM DATA HELPERS (ParagonGemProps / ParagonGemLimitMax from gen_gem_data)
-- ============================================================================

-- enchant id -> a representative gem item entry (for the client's icon)
local ench_to_item = {}

local function GemItemForEnchant(ench)
    local cached = ench_to_item[ench]
    if cached ~= nil then
        return cached
    end
    local entry = 0
    for prop, def in pairs(ParagonGemProps or {}) do
        if def.ench == ench then
            local q = WorldDBQuery(
                "SELECT entry FROM item_template WHERE class = 3 AND GemProperties = "
                .. prop .. " ORDER BY entry LIMIT 1")
            if q then
                entry = q:GetUInt32(0)
                if entry > 0 then
                    break
                end
            end
        end
    end
    ench_to_item[ench] = entry
    return entry
end

-- limit category -> set of enchant ids belonging to it (for cap counting)
local limit_ench_sets = {}

local function LimitEnchSet(cat)
    local cached = limit_ench_sets[cat]
    if cached then
        return cached
    end
    local set = {}
    local q = WorldDBQuery(
        "SELECT GemProperties FROM item_template WHERE class = 3 AND ItemLimitCategory = " .. cat)
    if q then
        repeat
            local props = ParagonGemProps and ParagonGemProps[q:GetUInt32(0)]
            if props then
                set[props.ench] = true
            end
        until not q:NextRow()
    end
    limit_ench_sets[cat] = set
    return set
end

--- Iterate every gem enchant on every equipped item, skipping the target
--- socket itself. cb(enchId) -> truthy to stop and return that value.
local function ScanEquippedGems(player, skip_guid, skip_slot, cb)
    for eq = 0, 18 do
        local it = player:GetEquippedItemBySlot(eq)
        if it then
            local it_guid = it:GetGUIDLow()
            for _, s in ipairs(GEM_SCAN_SLOTS) do
                if not (it_guid == skip_guid and s == skip_slot) then
                    local ench = it:GetEnchantmentId(s) or 0
                    if ench > 0 then
                        local hit = cb(ench)
                        if hit then
                            return hit
                        end
                    end
                end
            end
        end
    end
end

-- ============================================================================
-- STATE PUSH (equipped belt only; delta-suppressed like paragon_dual_enchant)
-- ============================================================================

local function Push(player, force)
    local state = { open = 0, ench = 0, item = 0 }
    local belt = player:GetEquippedItemBySlot(WAIST_SLOT)
    if belt then
        local current = SocketState(belt)
        if current > 0 then
            state.open = 1
            if current ~= BUCKLE_ENCHANT then
                state.ench = current
                state.item = GemItemForEnchant(current)
            end
        end
    end
    local prev = player:GetData(PUSHED_KEY)
    if not force and prev and prev.open == state.open and prev.ench == state.ench then
        return
    end
    player:SetData(PUSHED_KEY, state)
    player:SendServerResponse(PREFIX, 1, state)
end

-- delta tick: covers unequips (no ALE unequip event). Tickers are only
-- registered from the client load request, so bots never pay for one.
local tickers = {}

local function OnTick(eventId, delay, repeats, player)
    local ok, err = pcall(Push, player)
    if not ok then
        print("[Paragon] double buckle tick error: " .. tostring(err))
    end
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
    local ok, err = pcall(function()
        if player then
            Push(player, true)
            EnsureTicker(player)
        end
    end)
    if not ok then
        print("[Paragon] double buckle load-push error: " .. tostring(err))
    end
end)

RegisterPlayerEvent(29, function(event, player, item, bag, slot)
    local ok, err = pcall(Push, player)
    if not ok then
        print("[Paragon] double buckle equip error: " .. tostring(err))
    end
end)

-- ============================================================================
-- OPENING THE SECOND SOCKET
-- ============================================================================

--- Shared by the ON_USE hook and the addon's drag path. Consumes one
--- Eternal Belt Buckle from the player's bags on success. Carries the
--- account-gate contract itself so both entry roads enforce it.
local function OpenSecondSocket(player, belt)
    if IsBot(player) or player:GetLevel() < 80 then
        return
    end
    if player:IsInCombat() then
        Deny(player, "Not while you are in combat.")
        return
    end
    if (belt:GetEnchantmentId(PRISMATIC_SLOT) or 0) == 0 then
        Deny(player, "Attach the first Eternal Belt Buckle normally before doubling it.")
        return
    end
    if not BeltEligible(belt) then
        Deny(player, "This belt's shifting enchantments cannot hold a second buckle.")
        return
    end
    if SocketState(belt) > 0 then
        Deny(player, "This belt already carries its second buckle.")
        return
    end
    if player:GetItemCount(BUCKLE_ITEM) < 1 then
        Deny(player, "You need an Eternal Belt Buckle in your bags.")
        return
    end
    player:RemoveItem(BUCKLE_ITEM, 1)
    belt:SetEnchantment(BUCKLE_ENCHANT, SOCKET_SLOT)
    belt:SaveToDB()
    Push(player, true)
    Note(player, "A second prismatic socket opens on your belt. Drag a gem onto your equipped belt to fill it.")
end

-- ITEM_EVENT_ON_USE (2) for the buckle: fires BEFORE the spell is created
-- (see header). Return false = suppress the cast; return nothing = stock.
RegisterItemEvent(BUCKLE_ITEM, 2, function(event, player, item, target)
    local ok, result = pcall(function()
        if not target or not target.GetEnchantmentId then
            return -- no item target: stock flow (client targeting error)
        end
        if (target:GetEnchantmentId(PRISMATIC_SLOT) or 0) == 0 then
            return -- first buckle: stock flow, untouched
        end
        -- already buckled: the stock cast dies in CheckItems with "maximum
        -- sockets" — this hook is the only road to a second buckle
        if not player:HasSpell(MARKER_SPELL) then
            return -- let the stock error explain the refusal
        end
        if SocketState(target) > 0 then
            return -- already doubled: the stock error fits here too
        end
        -- OpenSecondSocket consumes a buckle itself; suppress the cast
        -- regardless of outcome so the stock error never double-fires
        OpenSecondSocket(player, target)
        return false
    end)
    if not ok then
        print("[Paragon] double buckle use error: " .. tostring(result))
        return
    end
    return result
end)

-- ============================================================================
-- CLIENT REQUESTS (addon drag interaction)
-- ============================================================================

local function HandleGemRequest(player, belt, gemId)
    local current = SocketState(belt)
    if current == 0 or not BeltEligible(belt) then
        Deny(player, "Attach a second Eternal Belt Buckle to this belt first.")
        return
    end
    if gemId <= 0 or player:GetItemCount(gemId) < 1 then
        Deny(player, "You no longer have that gem.")
        return
    end
    local q = WorldDBQuery(
        "SELECT GemProperties, Flags, RequiredSkill, name, class, ItemLimitCategory FROM item_template WHERE entry = " .. gemId)
    local prop = q and q:GetUInt32(0) or 0
    local props = prop > 0 and ParagonGemProps and ParagonGemProps[prop]
    if not props or q:GetUInt32(4) ~= ITEM_CLASS_GEM then
        Deny(player, "That item is not a socketable gem.")
        return
    end
    if props.color % 2 == 1 then
        Deny(player, "Meta gems will not fit a prismatic socket.")
        return
    end
    local req_skill = q:GetUInt32(2)
    if req_skill > 0 and not player:HasSkill(req_skill) then
        Deny(player, "That gem requires a profession you do not know.")
        return
    end
    local belt_guid = belt:GetGUIDLow()
    -- unique-equipped: our path bypasses HandleSocketOpcode, so mirror its
    -- rule here (the §1o core patch handles the stock directions)
    if math.floor(q:GetUInt32(1) / UNIQUE_EQUIPPABLE_FLAG) % 2 == 1 then
        local dup = ScanEquippedGems(player, belt_guid, SOCKET_SLOT, function(ench)
            return ench == props.ench
        end)
        if dup then
            Deny(player, "You already have that unique gem socketed.")
            return
        end
    end
    -- limit-category cap ("Unique-Equipped: Jeweler's Gems (3)" etc.)
    local cat = q:GetUInt32(5)
    local cap = cat > 0 and ParagonGemLimitMax and ParagonGemLimitMax[cat] or 0
    if cap > 0 then
        local set = LimitEnchSet(cat)
        local count = 0
        ScanEquippedGems(player, belt_guid, SOCKET_SLOT, function(ench)
            if set[ench] then
                count = count + 1
            end
        end)
        if count + 1 > cap then
            Deny(player, "You cannot equip more than " .. cap .. " of those gems.")
            return
        end
    end

    -- consume first, then socket: an error between the two wastes a gem
    -- rather than duplicating one
    player:RemoveItem(gemId, 1)
    local replaced = current ~= BUCKLE_ENCHANT
    belt:SetEnchantment(props.ench, SOCKET_SLOT)
    belt:SaveToDB()
    Push(player, true)
    if ParagonGemDouble_Poke then
        ParagonGemDouble_Poke(player) -- milestone 1150 recount, instant
    end
    local name = q:GetString(3) or "gem"
    if replaced then
        Note(player, "The old gem shatters as the " .. name .. " is set into the second buckle socket.")
    else
        Note(player, name .. " set into the second buckle socket.")
    end
end

function OnParagonBuckleClientRequest(player, arg_table)
    local ok, err = pcall(function()
        local data = arg_table and arg_table[1]
        if not player or type(data) ~= "table" then
            return
        end
        -- account-gate contract: inline bot/level gates (handler fires
        -- outside the statistics apply chain)
        if IsBot(player) or player:GetLevel() < 80 then
            return
        end
        if player:IsInCombat() then
            Deny(player, "Not while you are in combat.")
            return
        end
        if not player:HasSpell(MARKER_SPELL) then
            Deny(player, "Working the second buckle socket needs milestone 1100.")
            return
        end
        local belt = player:GetEquippedItemBySlot(WAIST_SLOT)
        if not belt then
            Deny(player, "No belt equipped.")
            return
        end
        if data.action == "buckle" then
            OpenSecondSocket(player, belt)
        elseif data.action == "gem" then
            HandleGemRequest(player, belt, tonumber(data.item) or 0)
        end
    end)
    if not ok then
        print("[Paragon] double buckle request error: " .. tostring(err))
    end
end

RegisterClientRequests({
    Prefix = PREFIX,
    Functions = { [1] = "OnParagonBuckleClientRequest" },
})

print("[Paragon] Rework: double buckle module loaded")
