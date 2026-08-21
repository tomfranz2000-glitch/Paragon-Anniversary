--[[
    Paragon Rework: milestone ITEM payouts (2026-08-20)

    The first reward type on the track that hands over a real item. Items
    are MAILED rather than pushed into bags: mail cannot fail on a full
    inventory, it survives a disconnect mid-cascade, and it gives the
    payout a place to carry flavour text.

    DELIBERATELY NOT LEDGERED, per user decision. Every other one-way
    reward here (titles, learned spells) is reconciled from state, but an
    item cannot be. The alternatives were a per-account "already paid"
    mirror (like paragon_rewarded_collectible_spell) or nothing at all,
    and nothing wins on this server:
      * the crossing test `old < level <= new` matches EXACTLY ONCE per
        real progression, and a lump XP grant still produces one event
        whose old/new span the threshold -- so no cascade double-pays;
      * only one character per account can be online, so the shared
        account-wide paragon level cannot be crossed twice at once;
      * a real player never de-levels, and a ledger would actively BREAK
        the intended re-test loop below.
    If idempotency is ever wanted, the mirror-table pattern in
    paragon_collection_rewards.lua drops straight in here.

    !! RE-TEST LOOP -- `.paragon setlevel` RAISES NO EVENT !!
    setlevel writes the level straight onto the object (paragon_admin.lua)
    and never raises OnParagonLevelChanged, so it can neither pay nor
    un-pay. Only addxp/addlevel go through GrantExperience, which does.
    To retrigger a payout:
        .paragon setlevel 1499      -- silent, no crossing
        .paragon addlevel 1         -- real crossing -> mail arrives
    Setting the level straight to 1500+ pays NOTHING, which is the single
    easiest way to think this module is broken when it is not.

    Item creation goes through the core (Item::CreateItem inside ALE's
    SendMail), so the template's own spellcharges are copied -- which is
    why this cannot repeat the "no charges remain" trap from the
    hand-written mail rows.
]]

-- ALE builds against Lua 5.2 (mod-ale/CMakeLists.txt LUA_VERSION "lua52"),
-- where the global `unpack` was moved to `table.unpack`. Same alias
-- lib/Mediator/mediator.lua:18 uses. Calling the bare global is a nil-call
-- that only surfaces when a payout actually fires -- which cost one live
-- crossing to find.
local unpack = unpack or table.unpack

-- MailStationery: 61 = MAIL_STATIONERY_GM, the parchment/GM envelope.
-- Sender guid stays 0 (ALE's SendMail hardcodes MAIL_NORMAL, so a
-- creature sender is not expressible); the client renders no sender name
-- for guid 0, which the GM stationery makes read as official rather than
-- broken. A named sender would need a dedicated character to mail from.
local STATIONERY_GM = 61
local SENDER_GUID = 0

--- THE single source of truth for item payouts. The TRACK row for the same
--- level is informational only -- exactly like GEM_DOUBLE, SOLO_DUNGEON and
--- RACIAL_PICK, whose milestones are also enforced outside the track module
--- (which has no DB or mail layer of its own).
--- Adding a payout is a data change here: any level, up to 12 items
--- (MAX_MAIL_ITEMS), each one a real item_template entry.
local PAYOUTS = {
    [1500] = {
        subject = "The Heavens Take Notice",
        body = "Mortal limits fell away from you a long while ago.\n\n"
            .. "Something beyond this world has been watching your ascent, "
            .. "and it sends you a mount cut from the same stuff as the "
            .. "night sky. It will carry you over Azeroth and beyond it.\n\n"
            .. "Ride well.",
        announce = "Celestial Steed",
        items = {
            -- 54811 Celestial Steed: teaches 75614, which the core's
            -- spell_celestial_steed (spell_gen_mount) scales by riding
            -- skill -- 60/100 on the ground, 150/280/310 in the air.
            -- BoP, requires Riding 75 to use, MaxCount 0 so there is no
            -- unique cap to collide with.
            { entry = 54811, count = 1 },
        },
    },
}

local function IsBot(player)
    return player.IsPlayerBot and player:IsPlayerBot()
end

local function Pay(player, level, payout)
    -- SendMail(subject, text, receiver, sender, stationery, delay, money,
    --          cod, entry1, amount1, ... up to 12 pairs)
    local args = { payout.subject, payout.body, player:GetGUIDLow(),
                   SENDER_GUID, STATIONERY_GM, 0, 0, 0 }
    for _, item in ipairs(payout.items) do
        args[#args + 1] = item.entry
        args[#args + 1] = item.count
    end
    SendMail(unpack(args))

    player:SendBroadcastMessage(string.format(
        "|cff00ff00[Paragon]|r Paragon %d reward sent to your mailbox: |cffa335ee%s|r",
        level, payout.announce or "a reward"))
end

-- Crossing only. INLINE BOT/LEVEL GATE per the account-gate contract:
-- this handler fires outside the statistics apply chain, so the sub-80
-- protection never runs for it. Bots cannot earn paragon XP and so never
-- cross, but the gate keeps that a guarantee rather than a coincidence.
RegisterMediatorEvent("OnParagonLevelChanged", function(player, paragon, old_level, new_level)
    local ok, err = pcall(function()
        if not player or not old_level or not new_level then
            return
        end
        if IsBot(player) or player:GetLevel() < 80 then
            return
        end
        for level, payout in pairs(PAYOUTS) do
            if old_level < level and level <= new_level then
                Pay(player, level, payout)
            end
        end
    end)
    if not ok then
        print("[Paragon] milestone item payout error: " .. tostring(err))
        -- A swallowed error here is invisible: the crossing does not repeat,
        -- so the player would simply never receive the reward and have no
        -- reason to suspect anything. Say so in-game. (Learned the hard way:
        -- a nil `unpack` ate a live crossing and only the log knew.)
        if player then
            player:SendBroadcastMessage("|cffff4040[Paragon]|r A milestone reward "
                .. "failed to send. Nothing was lost - report this so it can be "
                .. "re-issued.")
        end
    end
end)

-- A payout naming an item that does not exist makes ALE's SendMail raise a
-- Lua error at the moment of the crossing -- the worst possible time to
-- discover it, since the crossing does not repeat. Verify at load instead.
do
    local missing = {}
    for level, payout in pairs(PAYOUTS) do
        for _, item in ipairs(payout.items) do
            local q = WorldDBQuery(string.format(
                "SELECT COUNT(*) FROM item_template WHERE entry = %d;", item.entry))
            if not q or q:GetUInt32(0) == 0 then
                missing[#missing + 1] = string.format("%d (milestone %d)", item.entry, level)
            end
        end
    end
    if #missing > 0 then
        print(string.format(
            "[Paragon] !! MILESTONE ITEM PAYOUT IS BROKEN: item_template has no row for: %s. "
            .. "The crossing will raise a Lua error and pay NOTHING, and the crossing does "
            .. "not repeat. Fix the entry or remove the payout, then restart.",
            table.concat(missing, ", ")))
    end
end

local count = 0
for _ in pairs(PAYOUTS) do count = count + 1 end
print(string.format("[Paragon] Rework: milestone item payouts loaded (%d milestone%s)",
    count, count == 1 and "" or "s"))
