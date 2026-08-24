--[[
    Paragon Rework: native XP drops

    Sends the real SMSG_LOG_XPGAIN (0x1D0) packet for paragon experience
    gains, so the CLIENT ENGINE renders them exactly like stock XP: the
    yellow float over the slain mob (kill gains) and the native chat line.
    At max level the core never sends this packet, so the channel is
    exclusively ours. The client side only rewords the chat line
    ("experience" -> "paragon experience", Paragon_ExpToast.lua).

    Packet layout (Player::SendLogXPGain, Player.cpp): uint64 victim guid
    (0 = none), uint32 total xp, uint8 type (0 kill / 1 non-kill), kill
    only: uint32 raw xp + float group rate, uint8 RAF flag.

    The shared award boundary publishes the exact applied amount and source.
    Creature awards briefly remain pending until
    OnAfterCreatureExperienceAwarded anchors the native packet to the victim.
    Every non-creature gain sends addon response 8 immediately, which draws the
    floating number client-side (the stock non-kill packet never does). Sources
    without a detailed message also receive the victimless native chat packet.
]]

local Hook = require("paragon_hook")

local OPCODE_LOG_XPGAIN = 0x1D0
local SOURCE_CREATURE = Hook.ExperienceSource.CREATURE
local SOURCE_ACHIEVEMENT = Hook.ExperienceSource.ACHIEVEMENT
local SOURCE_COLLECTIBLE = Hook.ExperienceSource.COLLECTIBLE
local FLOAT_RESPONSE = 8

-- guidLow -> { gained = n }  (consumed by the kill handler or the flush timer)
local pending = {}

local function SendDrop(player, gained, victimGuid)
    local pkt = CreatePacket(OPCODE_LOG_XPGAIN, 22)
    if victimGuid then
        pkt:WriteGUID(victimGuid)
        pkt:WriteULong(gained)
        pkt:WriteUByte(0)          -- kill xp: engine floats it over the victim
        pkt:WriteULong(gained)
        pkt:WriteFloat(1)
    else
        pkt:WriteULong(0)
        pkt:WriteULong(0)          -- empty uint64 victim guid
        pkt:WriteULong(gained)
        pkt:WriteUByte(1)          -- non-kill xp: chat line only
    end
    pkt:WriteUByte(0)              -- no recruit-a-friend bonus
    player:SendPacket(pkt)
end

RegisterMediatorEvent("OnAfterUpdatePlayerExperience", function(player, paragon, gained, sourceType, sourceEntry)
    local ok, err = pcall(function()
        if not player or not paragon then
            return
        end
        gained = math.floor(tonumber(gained) or 0)
        if gained <= 0 then
            return
        end

        if tonumber(sourceType) ~= SOURCE_CREATURE then
            -- Collection rewards and the entry-0 bank payout already emit a
            -- richer source-specific chat line. They still get the immediate
            -- float, but avoid a redundant generic "You gain XP" line.
            local has_contextual_chat = tonumber(sourceType) == SOURCE_COLLECTIBLE
                or (tonumber(sourceType) == SOURCE_ACHIEVEMENT
                    and tonumber(sourceEntry) == 0)
            if not has_contextual_chat then
                SendDrop(player, gained, nil)
            end
            player:SendServerResponse(Hook.Addon.Prefix, FLOAT_RESPONSE, gained)
            return
        end

        local guidLow = player:GetGUIDLow()
        pending[guidLow] = { gained = gained }
        -- Defensive fallback for a future creature caller which does not raise
        -- OnAfterCreatureExperienceAwarded. Normal kill rewards consume the
        -- entry synchronously and this timer becomes a no-op.
        CreateLuaEvent(function()
            local pend = pending[guidLow]
            if not pend then
                return
            end
            pending[guidLow] = nil
            local guid = GetPlayerGUID(guidLow)
            local p = guid and GetPlayerByGUID(guid)
            if p then
                SendDrop(p, pend.gained, nil)
                p:SendServerResponse(Hook.Addon.Prefix, FLOAT_RESPONSE, pend.gained)
            end
        end, 50, 1)
    end)
    if not ok then
        print("[Paragon] xp drop error: " .. tostring(err))
    end
end)

RegisterMediatorEvent("OnAfterCreatureExperienceAwarded", function(player, creature)
    pcall(function()
        if not player or not creature then
            return
        end
        local pend = pending[player:GetGUIDLow()]
        if not pend then
            return
        end
        pending[player:GetGUIDLow()] = nil
        SendDrop(player, pend.gained, creature:GetGUID())
    end)
end)

print("[Paragon] Rework: native xp drops module loaded")
