import os
import unittest

try:
    from lupa.lua52 import LuaRuntime
except ImportError:  # pragma: no cover - reported as skipped by unittest
    LuaRuntime = None


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(
    ROOT, "serverside", "paragon", "modules", "paragon_exp_drops.lua")
CLIENT = os.path.join(
    ROOT,
    "clientside",
    "Interface",
    "AddOns",
    "Paragon",
    "Paragon",
    "Paragon_ExpToast.lua",
)
NETWORK = os.path.join(
    ROOT,
    "clientside",
    "Interface",
    "AddOns",
    "Paragon",
    "Paragon",
    "Paragon_Network.lua",
)
HOOK = os.path.join(ROOT, "serverside", "paragon", "paragon_hook.lua")


@unittest.skipUnless(LuaRuntime, "lupa.lua52 is required for Lua behavior tests")
class ParagonExperienceDropTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute(
            r"""
            Hook = {
                Addon = { Prefix = "ParagonAnniversary" },
                ExperienceSource = {
                    CREATURE = 1,
                    ACHIEVEMENT = 2,
                    COLLECTIBLE = 8,
                },
            }
            package.preload["paragon_hook"] = function() return Hook end

            Handlers = {}
            function RegisterMediatorEvent(name, callback)
                Handlers[name] = callback
            end

            Packets = {}
            Responses = {}
            Timers = {}
            LookedUpGUID = nil
            function CreatePacket(opcode, size)
                local packet = {
                    opcode = opcode,
                    size = size,
                    ulongs = {},
                    ubytes = {},
                    floats = {},
                    victim = nil,
                }
                function packet:WriteGUID(value) self.victim = value end
                function packet:WriteULong(value)
                    self.ulongs[#self.ulongs + 1] = value
                end
                function packet:WriteUByte(value)
                    self.ubytes[#self.ubytes + 1] = value
                end
                function packet:WriteFloat(value)
                    self.floats[#self.floats + 1] = value
                end
                return packet
            end

            Player = {}
            function Player:GetGUIDLow() return 77 end
            function Player:SendPacket(packet)
                Packets[#Packets + 1] = packet
            end
            function Player:SendServerResponse(prefix, response, amount)
                Responses[#Responses + 1] = {
                    prefix = prefix, response = response, amount = amount,
                }
            end
            Paragon = {}
            Creature = { GetGUID = function(_) return "creature-9001" end }

            function CreateLuaEvent(callback, delay, repeats)
                Timers[#Timers + 1] = callback
                return #Timers
            end
            function GetPlayerGUID(guidLow)
                return "player-guid-" .. tostring(guidLow)
            end
            function GetPlayerByGUID(guid)
                LookedUpGUID = guid
                if guid == "player-guid-77" then return Player end
                return nil
            end
            function RunTimer(index) Timers[index]() end
            function ResetHarness()
                Packets = {}
                Responses = {}
                Timers = {}
                LookedUpGUID = nil
            end
            function PacketGain(index)
                local packet = Packets[index]
                if packet.victim then return packet.ulongs[1] end
                return packet.ulongs[3]
            end
            """
        )
        with open(SERVER, encoding="utf-8") as handle:
            self.lua.execute(handle.read())

    @property
    def after_gain(self):
        return self.lua.globals().Handlers["OnAfterUpdatePlayerExperience"]

    @property
    def after_creature(self):
        return self.lua.globals().Handlers["OnAfterCreatureExperienceAwarded"]

    def test_collectible_sends_exact_float_without_duplicate_generic_chat(self):
        self.after_gain(
            self.lua.globals().Player,
            self.lua.globals().Paragon,
            2000,
            8,
            12345,
        )

        self.assertEqual(0, len(self.lua.globals().Packets))
        self.assertEqual(1, len(self.lua.globals().Responses))
        response = self.lua.globals().Responses[1]
        self.assertEqual("ParagonAnniversary", response["prefix"])
        self.assertEqual(8, response["response"])
        self.assertEqual(2000, response["amount"])
        self.assertEqual(0, len(self.lua.globals().Timers))

    def test_profession_skillup_keeps_native_chat_and_exact_float(self):
        self.after_gain(
            self.lua.globals().Player,
            self.lua.globals().Paragon,
            2000,
            3,
            164,
        )

        self.assertEqual(1, len(self.lua.globals().Packets))
        self.assertEqual(2000, self.lua.globals().PacketGain(1))
        self.assertEqual(2000, self.lua.globals().Responses[1]["amount"])

    def test_banked_achievement_float_avoids_duplicate_generic_chat(self):
        self.after_gain(
            self.lua.globals().Player,
            self.lua.globals().Paragon,
            4000,
            2,
            0,
        )

        self.assertEqual(0, len(self.lua.globals().Packets))
        self.assertEqual(4000, self.lua.globals().Responses[1]["amount"])

    def test_creature_gain_waits_for_and_anchors_to_the_victim(self):
        self.after_gain(
            self.lua.globals().Player,
            self.lua.globals().Paragon,
            321,
            1,
            9001,
        )
        self.assertEqual(0, len(self.lua.globals().Packets))
        self.assertEqual(1, len(self.lua.globals().Timers))

        self.after_creature(
            self.lua.globals().Player, self.lua.globals().Creature
        )
        self.assertEqual(1, len(self.lua.globals().Packets))
        self.assertEqual(321, self.lua.globals().PacketGain(1))
        self.assertEqual(
            "creature-9001", self.lua.globals().Packets[1]["victim"]
        )
        self.assertEqual(0, len(self.lua.globals().Responses))

        self.lua.globals().RunTimer(1)
        self.assertEqual(1, len(self.lua.globals().Packets))

    def test_one_time_reward_never_carries_into_the_next_kill(self):
        self.after_gain(
            self.lua.globals().Player,
            self.lua.globals().Paragon,
            4000000,
            8,
            72286,
        )
        self.after_gain(
            self.lua.globals().Player,
            self.lua.globals().Paragon,
            280,
            1,
            9001,
        )
        self.after_creature(
            self.lua.globals().Player, self.lua.globals().Creature
        )

        self.assertEqual(1, len(self.lua.globals().Packets))
        self.assertEqual(280, self.lua.globals().PacketGain(1))
        self.assertEqual(4000000, self.lua.globals().Responses[1]["amount"])

    def test_unanchored_creature_fallback_resolves_the_full_player_guid(self):
        self.after_gain(
            self.lua.globals().Player,
            self.lua.globals().Paragon,
            777,
            1,
            9001,
        )
        self.lua.globals().RunTimer(1)

        self.assertEqual("player-guid-77", self.lua.globals().LookedUpGUID)
        self.assertEqual(777, self.lua.globals().PacketGain(1))
        self.assertEqual(777, self.lua.globals().Responses[1]["amount"])


@unittest.skipUnless(LuaRuntime, "lupa.lua52 is required for Lua behavior tests")
class ParagonExperienceDropClientTests(unittest.TestCase):
    def test_client_handler_draws_one_valid_standard_combat_text_message(self):
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.execute(
            r"""
            function ChatFrame_AddMessageEventFilter(_, _) end
            CombatText_StandardScroll = {}
            FloatMessages = {}
            function BreakUpLargeNumbers(value)
                if value == 2000 then return "2,000" end
                return tostring(value)
            end
            function CombatText_AddMessage(message, scroll, red, green, blue)
                FloatMessages[#FloatMessages + 1] = {
                    message = message, scroll = scroll,
                    red = red, green = green, blue = blue,
                }
            end
            """
        )
        with open(CLIENT, encoding="utf-8") as handle:
            lua.execute(handle.read())

        handler = lua.globals().UIParagon_OnReceiveExperienceDrop
        handler(None, lua.table_from([None]))
        handler(None, lua.table_from([-1]))
        handler(None, lua.table_from([2000]))

        self.assertEqual(1, len(lua.globals().FloatMessages))
        self.assertEqual(
            "+2,000 Paragon XP",
            lua.globals().FloatMessages[1]["message"],
        )

    def test_network_contract_maps_response_eight_to_float_handler(self):
        with open(NETWORK, encoding="utf-8") as handle:
            network = handle.read()
        self.assertIn(
            '[8] = "UIParagon_OnReceiveExperienceDrop"', network
        )


class ParagonExperienceDropHookContractTests(unittest.TestCase):
    def test_shared_hook_publishes_exact_amount_and_source(self):
        with open(HOOK, encoding="utf-8") as handle:
            hook = handle.read()
        self.assertIn(
            "arguments = { player, paragon, specific_experience, source_type, entry }",
            hook,
        )


if __name__ == "__main__":
    unittest.main()
