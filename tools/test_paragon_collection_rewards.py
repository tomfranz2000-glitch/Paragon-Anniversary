import importlib.util
import os
import re
import unittest

try:
    from lupa.lua52 import LuaRuntime
except ImportError:  # pragma: no cover - reported as skipped by unittest
    LuaRuntime = None


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COLLECTION_REWARDS = os.path.join(
    ROOT, "serverside", "paragon", "modules", "paragon_collection_rewards.lua")
COLLECTION_GENERATOR = os.path.join(ROOT, "tools", "paragon_collectible_xp.py")
FRESH_SCHEMA = os.path.join(ROOT, "sql", "02_create_tables.sql")
MIGRATION = os.path.join(ROOT, "sql", "08_add_collection_pending_claims.sql")
ACCOUNT_MIGRATION = os.path.join(
    ROOT, "sql", "09_add_reputation_and_account_collection_rewards.sql")


@unittest.skipUnless(LuaRuntime, "lupa.lua52 is required for Lua behavior tests")
class ParagonCollectionRewardTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute(
            r'''
            ConfigValues = {
                ENABLE_PARAGON_SYSTEM = "1",
                MINIMUM_LEVEL_FOR_PARAGON_XP = "80",
                LEVEL_LINKED_TO_ACCOUNT = "1",
                BASE_MAX_EXPERIENCE = "30000",
                PARAGON_LEVEL_CAP = "2000",
            }
            Config = {}
            function Config:GetByField(field) return ConfigValues[field] end
            function ParagonRework_CurveCost(level)
                return 30000 + level * 10000
            end
            package.preload["paragon_config"] = function() return Config end
            package.preload["paragon_constant"] = function()
                return { DB_NAME = "acore_ale" }
            end

            local function Result(rows)
                if #rows == 0 then return nil end
                local index = 1
                local function ALEUInt64(value)
                    -- ALE pushes uint64 as userdata. A table has the same
                    -- crucial Lua boundary: tonumber(value) is nil even when
                    -- tostring(value) contains the decimal representation.
                    return setmetatable({ value = value }, {
                        __tostring = function(self) return tostring(self.value) end,
                    })
                end
                return {
                    GetUInt32 = function(_, column) return rows[index][column + 1] end,
                    GetUInt64 = function(_, column)
                        return ALEUInt64(rows[index][column + 1])
                    end,
                    GetInt32 = function(_, column) return rows[index][column + 1] end,
                    GetString = function(_, column)
                        return tostring(rows[index][column + 1])
                    end,
                    NextRow = function(_)
                        if index < #rows then index = index + 1 return true end
                        return false
                    end,
                }
            end

            spell_claims = {}
            item_claims = {}
            account_item_claims = {}
            unlocked_items = { 12345 }
            collected_account_items = {}
            db_level = 4
            db_experience = 111
            fail_commit = false
            fail_sync = false
            event_log = {}

            local function SortedClaimRows(claims)
                local rows = {}
                for id in pairs(claims) do rows[#rows + 1] = { id } end
                table.sort(rows, function(a, b) return a[1] < b[1] end)
                return rows
            end

            local function Pending(claims)
                local amount = 0
                for _, value in pairs(claims) do amount = amount + value end
                return amount
            end

            local function AccountItemKey(kind, id)
                return kind .. ":" .. tostring(id)
            end

            local function SortedAccountItemClaimRows()
                local rows = {}
                for key in pairs(account_item_claims) do
                    local kind, id = key:match("^([^:]+):(%d+)$")
                    rows[#rows + 1] = { kind, tonumber(id) }
                end
                table.sort(rows, function(a, b)
                    if a[1] == b[1] then return a[2] < b[2] end
                    return a[1] < b[1]
                end)
                return rows
            end

            local function ParseClaimValues(sql, claims)
                for account, id, pending in sql:gmatch(
                        "%((%d+),%s*(%d+),%s*(%d+)%)") do
                    if tonumber(account) == 42 and claims[tonumber(id)] == nil then
                        claims[tonumber(id)] = tonumber(pending)
                    end
                end
            end

            function CharDBQuery(sql)
                event_log[#event_log + 1] = "sql:" .. sql

                if sql:find("SELECT spell_id, kind, name, xp", 1, true) then
                    return Result({ { 72286, "mount", "Invincible's Reins", 4000000 } })
                end
                if sql:find("SELECT item_id, name, xp", 1, true) then
                    return Result({ { 12345, "Test Wardrobe Item", 5000 } })
                end
                if sql:find("SELECT kind, item_id, name, xp", 1, true) then
                    return Result({
                        { "toy", 32542, "Imp in a Ball", 1000000 },
                        { "heirloom", 42943, "Bloodied Arcanite Reaper", 100000 },
                    })
                end
                if sql:find("SELECT spell_id FROM acore_ale.paragon_rewarded_collectible_spell", 1, true) then
                    return Result(SortedClaimRows(spell_claims))
                end
                if sql:find("SELECT item_id FROM acore_ale.paragon_rewarded_appearance", 1, true)
                        and not sql:find(" IN ", 1, true) then
                    return Result(SortedClaimRows(item_claims))
                end
                if sql:find("SELECT kind, item_id FROM acore_ale.paragon_rewarded_account_item", 1, true)
                        and not sql:find(" AND (", 1, true) then
                    return Result(SortedAccountItemClaimRows())
                end
                if sql:find("INSERT IGNORE INTO acore_ale.paragon_rewarded_collectible_spell", 1, true) then
                    ParseClaimValues(sql, spell_claims)
                    return nil
                end
                if sql:find("INSERT IGNORE INTO acore_ale.paragon_rewarded_appearance", 1, true) then
                    ParseClaimValues(sql, item_claims)
                    return nil
                end
                if sql:find("INSERT IGNORE INTO acore_ale.paragon_rewarded_account_item", 1, true) then
                    for account, kind, id, pending in sql:gmatch(
                            "%((%d+),'([^']+)',(%d+),(%d+)%)") do
                        local key = AccountItemKey(kind, tonumber(id))
                        if tonumber(account) == 42 and account_item_claims[key] == nil then
                            account_item_claims[key] = tonumber(pending)
                        end
                    end
                    return nil
                end
                if sql:find("SELECT pending_xp FROM acore_ale.paragon_rewarded_collectible_spell", 1, true) then
                    local id = tonumber(sql:match("spell_id%s*=%s*(%d+)"))
                    return spell_claims[id] ~= nil and Result({ { spell_claims[id] } }) or nil
                end
                if sql:find("SELECT item_id FROM acore_ale.paragon_rewarded_appearance", 1, true)
                        and sql:find(" IN ", 1, true) then
                    local rows = {}
                    local ids = sql:match("item_id IN %(([^)]+)%)") or ""
                    for id in ids:gmatch("%d+") do
                        id = tonumber(id)
                        if item_claims[id] ~= nil then rows[#rows + 1] = { id } end
                    end
                    return Result(rows)
                end
                if sql:find("SELECT kind, item_id FROM acore_ale.paragon_rewarded_account_item", 1, true)
                        and sql:find(" AND (", 1, true) then
                    local rows = {}
                    for kind, id in sql:gmatch("kind='([^']+)' AND item_id=(%d+)") do
                        local key = AccountItemKey(kind, tonumber(id))
                        if account_item_claims[key] ~= nil then
                            rows[#rows + 1] = { kind, tonumber(id) }
                        end
                    end
                    return Result(rows)
                end
                if sql:find("custom_unlocked_appearances", 1, true) then
                    local rows = {}
                    for _, id in ipairs(unlocked_items) do rows[#rows + 1] = { id } end
                    return Result(rows)
                end
                if sql:find("account_collection_toy", 1, true)
                        and sql:find("account_collection_heirloom", 1, true) then
                    local rows = {}
                    for _, value in ipairs(collected_account_items) do
                        rows[#rows + 1] = { value[1], value[2] }
                    end
                    return Result(rows)
                end
                if sql:find("SUM(pending_xp)", 1, true) then
                    if sql:find("paragon_rewarded_collectible_spell", 1, true) then
                        return Result({ { Pending(spell_claims) } })
                    end
                    if sql:find("paragon_rewarded_account_item", 1, true) then
                        return Result({ { Pending(account_item_claims) } })
                    end
                    return Result({ { Pending(item_claims) } })
                end
                if sql:find("INSERT INTO acore_ale.account_paragon", 1, true) then
                    if not fail_sync then
                        local _, level, experience = sql:match("VALUES %((%d+), (%d+), (%d+)%)")
                        db_level = tonumber(level)
                        db_experience = tonumber(experience)
                    end
                    return nil
                end
                if sql:find("UPDATE acore_ale.account_paragon progression", 1, true) then
                    local new_level = tonumber(sql:match("SET progression.level = (%d+)"))
                    local new_experience = tonumber(sql:match("progression.experience = (%d+)"))
                    local old_level = tonumber(sql:match("AND progression.level = (%d+)"))
                    local old_experience = tonumber(sql:match("AND progression.experience = (%d+)"))
                    if not fail_commit and db_level == old_level
                            and db_experience == old_experience then
                        db_level = new_level
                        db_experience = new_experience
                        local claims = item_claims
                        if sql:find("paragon_rewarded_collectible_spell", 1, true) then
                            claims = spell_claims
                        elseif sql:find("paragon_rewarded_account_item", 1, true) then
                            claims = account_item_claims
                        end
                        for id in pairs(claims) do claims[id] = 0 end
                    end
                    return nil
                end
                if sql:find("SELECT COUNT(*) FROM acore_ale.account_paragon", 1, true) then
                    local level = tonumber(sql:match("AND level = (%d+)"))
                    local experience = tonumber(sql:match("AND experience = (%d+)"))
                    return Result({ { (db_level == level and db_experience == experience) and 1 or 0 } })
                end
                error("unexpected character query: " .. sql)
            end

            Hook = {
                Addon = { Prefix = "PARAGON" },
                ExperienceSource = { COLLECTIBLE = 8 },
            }
            award_count = 0
            awarded_total = 0
            award_sources = {}
            award_entries = {}
            function Hook.AwardFlatExperience(player, source, entry, amount)
                event_log[#event_log + 1] = "live-award"
                award_count = award_count + 1
                awarded_total = awarded_total + amount
                award_sources[#award_sources + 1] = source
                award_entries[#award_entries + 1] = entry
                local level = Paragon.level
                local experience = Paragon.experience + amount
                while experience >= ParagonRework_CurveCost(level) do
                    experience = experience - ParagonRework_CurveCost(level)
                    if level < 2000 then level = level + 1 end
                end
                Paragon.level = level
                Paragon.experience = experience
                return true, amount
            end
            package.preload["paragon_hook"] = function() return Hook end

            player_events = {}
            mediator_handlers = {}
            function RegisterPlayerEvent(event_id, callback)
                player_events[event_id] = callback
            end
            function RegisterMediatorEvent(name, callback)
                mediator_handlers[name] = callback
            end

            Paragon = {
                level = 5,
                experience = 29000,
                GetLevel = function(self) return self.level end,
                GetExperience = function(self) return self.experience end,
                SetLevel = function(self, value) self.level = value end,
                SetExperience = function(self, value) self.experience = value end,
            }
            player_data = { Paragon = Paragon }
            character_level = 80
            broadcasts = {}
            timers = {}
            register_count = 0
            Player = {
                IsPlayerBot = function(_) return false end,
                GetLevel = function(_) return character_level end,
                GetGUIDLow = function(_) return 77 end,
                GetAccountId = function(_) return 42 end,
                GetData = function(_, key) return player_data[key] end,
                SetData = function(_, key, value) player_data[key] = value end,
                RegisterEvent = function(_, callback, delay, repeats)
                    register_count = register_count + 1
                    timers[#timers + 1] = { callback, delay, repeats }
                    return register_count
                end,
                SendBroadcastMessage = function(_, message)
                    broadcasts[#broadcasts + 1] = message
                end,
            }

            function RunLatestTimer()
                local timer = timers[#timers]
                timer[1](1, timer[2], timer[3], Player)
            end
            function CollectAccountItem(kind, id)
                collected_account_items[#collected_account_items + 1] = { kind, id }
            end
            '''
        )
        with open(COLLECTION_REWARDS, encoding="utf-8") as handle:
            self.lua.execute(handle.read())

    def event_log(self):
        return [
            self.lua.globals().event_log[index]
            for index in range(1, len(self.lua.globals().event_log) + 1)
        ]

    def learn_mount(self):
        self.lua.globals().player_events[44](
            44, self.lua.globals().Player, 72286)

    def test_mount_claim_commits_before_live_award_and_preserves_unsaved_xp(self):
        self.learn_mount()

        self.assertEqual(1, self.lua.globals().award_count)
        self.assertEqual(4000000, self.lua.globals().awarded_total)
        self.assertEqual(0, self.lua.globals().spell_claims[72286])
        self.assertEqual(self.lua.globals().Paragon.level, self.lua.globals().db_level)
        self.assertEqual(
            self.lua.globals().Paragon.experience,
            self.lua.globals().db_experience,
        )

        events = self.event_log()
        claim = next(i for i, event in enumerate(events)
                     if "INSERT IGNORE INTO acore_ale.paragon_rewarded_collectible_spell" in event)
        sync = next(i for i, event in enumerate(events)
                    if "INSERT INTO acore_ale.account_paragon" in event)
        commit = next(i for i, event in enumerate(events)
                      if "UPDATE acore_ale.account_paragon progression" in event)
        replay = events.index("live-award")
        self.assertLess(claim, sync)
        self.assertLess(sync, commit)
        self.assertLess(commit, replay)

    def test_fractional_live_xp_uses_repository_persistence_semantics(self):
        # High-level repeatable XP applies a raw 0.8 multiplier and can leave a
        # fractional live remainder. Repository `%d` saves truncate it.
        self.lua.globals().Paragon.experience = 29000.75
        self.learn_mount()

        self.assertEqual(1, self.lua.globals().award_count)
        self.assertEqual(0, self.lua.globals().spell_claims[72286])
        self.assertEqual(self.lua.globals().db_level,
                         self.lua.globals().Paragon.level)
        self.assertEqual(self.lua.globals().db_experience,
                         self.lua.globals().Paragon.experience)
        self.assertEqual(
            self.lua.globals().db_experience,
            int(self.lua.globals().db_experience),
        )

        sync_sql = next(
            event for event in self.event_log()
            if "INSERT INTO acore_ale.account_paragon" in event
        )
        self.assertIn("VALUES (42, 5, 29000)", sync_sql)
        self.assertNotIn("29000.75", sync_sql)

    def test_failed_commit_leaves_pending_and_live_state_untouched_then_retries(self):
        self.lua.globals().fail_commit = True
        self.learn_mount()

        self.assertEqual(0, self.lua.globals().award_count)
        self.assertEqual(4000000, self.lua.globals().spell_claims[72286])
        self.assertEqual(5, self.lua.globals().Paragon.level)
        self.assertEqual(29000, self.lua.globals().Paragon.experience)
        # Syncing deliberately persisted the otherwise-unsaved live starting state.
        self.assertEqual(5, self.lua.globals().db_level)
        self.assertEqual(29000, self.lua.globals().db_experience)

        self.lua.globals().fail_commit = False
        self.lua.globals().mediator_handlers["OnAfterPlayerStatReady"](
            self.lua.globals().Player, self.lua.globals().Paragon)
        self.assertEqual(1, self.lua.globals().award_count)
        self.assertEqual(0, self.lua.globals().spell_claims[72286])

    def test_pre_minimum_level_claim_is_durable_and_paid_at_threshold(self):
        self.lua.globals().character_level = 79
        self.learn_mount()
        self.assertEqual(4000000, self.lua.globals().spell_claims[72286])
        self.assertEqual(0, self.lua.globals().award_count)

        self.lua.globals().character_level = 80
        self.lua.globals().player_events[13](
            13, self.lua.globals().Player, 79)
        self.assertEqual(1, self.lua.globals().award_count)
        self.assertEqual(0, self.lua.globals().spell_claims[72286])

    def test_missing_authoritative_curve_fails_closed_with_pending_intact(self):
        self.lua.globals().ParagonRework_CurveCost = None
        self.learn_mount()

        self.assertEqual(0, self.lua.globals().award_count)
        self.assertEqual(4000000, self.lua.globals().spell_claims[72286])
        self.assertEqual(5, self.lua.globals().Paragon.level)
        self.assertEqual(29000, self.lua.globals().Paragon.experience)

    def test_seeded_claim_is_not_rewarded_again(self):
        self.lua.globals().spell_claims[72286] = 0
        self.learn_mount()
        self.assertEqual(0, self.lua.globals().award_count)

    def test_appearance_batch_uses_write_ahead_pending_and_atomic_settlement(self):
        self.lua.globals().mediator_handlers["OnAfterUpdatePlayerStatistics"](
            self.lua.globals().Player, self.lua.globals().Paragon, True)
        self.lua.globals().RunLatestTimer()

        self.assertEqual(5000, self.lua.globals().awarded_total)
        self.assertEqual(0, self.lua.globals().item_claims[12345])
        self.assertIn("+5,000 Paragon XP", self.lua.globals().broadcasts[1])

    def test_unknown_appearance_has_no_claim_or_fallback(self):
        self.lua.globals().unlocked_items[1] = 99999
        self.lua.globals().mediator_handlers["OnAfterUpdatePlayerStatistics"](
            self.lua.globals().Player, self.lua.globals().Paragon, True)
        self.lua.globals().RunLatestTimer()

        self.assertEqual(0, self.lua.globals().award_count)
        self.assertIsNone(self.lua.globals().item_claims[99999])
        unknown = self.lua.globals().player_data["ParagonCollectUnknownItems"]
        self.assertTrue(unknown[99999])

    def test_new_toy_uses_its_typed_account_claim_and_fixed_value(self):
        self.lua.globals().CollectAccountItem("toy", 32542)
        self.lua.globals().mediator_handlers["OnAfterPlayerStatReady"](
            self.lua.globals().Player, self.lua.globals().Paragon)

        self.assertEqual(1000000, self.lua.globals().awarded_total)
        self.assertEqual(
            0, self.lua.globals().account_item_claims["toy:32542"])
        self.assertIn("1 toy", self.lua.globals().broadcasts[1])

    def test_new_heirloom_pays_once_and_journal_copy_cannot_repeat(self):
        self.lua.globals().CollectAccountItem("heirloom", 42943)
        ready = self.lua.globals().mediator_handlers["OnAfterPlayerStatReady"]
        ready(self.lua.globals().Player, self.lua.globals().Paragon)
        ready(self.lua.globals().Player, self.lua.globals().Paragon)

        self.assertEqual(1, self.lua.globals().award_count)
        self.assertEqual(100000, self.lua.globals().awarded_total)
        self.assertEqual(
            0, self.lua.globals().account_item_claims["heirloom:42943"])


class ParagonCollectionGeneratorValueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "paragon_collectible_xp_contract", COLLECTION_GENERATOR)
        cls.generator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.generator)

    def test_exact_category_contract(self):
        self.assertEqual(1000, self.generator.ROUNDING)
        self.assertEqual(
            (354984000, 5000, 3000000),
            tuple(self.generator.CATEGORY_RULES["appearance"][key]
                  for key in ("budget", "floor", "cap")),
        )
        self.assertEqual(
            (187933000, 250000, 10000000),
            tuple(self.generator.CATEGORY_RULES["mount"][key]
                  for key in ("budget", "floor", "cap")),
        )
        self.assertEqual(
            (83525000, 100000, 4000000),
            tuple(self.generator.CATEGORY_RULES["companion"][key]
                  for key in ("budget", "floor", "cap")),
        )
        self.assertEqual(
            (43500000, 50000, 3000000),
            tuple(self.generator.CATEGORY_RULES["toy"][key]
                  for key in ("budget", "floor", "cap")),
        )
        self.assertEqual(78, len(self.generator.TOY_ITEMS))
        self.assertEqual(
            self.generator.TOY_ITEMS,
            set(self.generator.TOY_XP_OVERRIDES),
        )
        self.assertEqual(
            self.generator.TOY_ITEMS,
            set(self.generator.TOY_RATIONALE),
        )
        self.assertEqual(
            43500000, sum(self.generator.TOY_XP_OVERRIDES.values()))
        self.assertEqual(50000, min(self.generator.TOY_XP_OVERRIDES.values()))
        self.assertEqual(100000, self.generator.HEIRLOOM_XP)

    def test_allocator_is_exact_rounded_and_monotonic(self):
        rule = {"budget": 12000, "floor": 1000, "cap": 8000, "beta": 1.0}
        rows = [
            {"id": 1, "score": 1.0},
            {"id": 2, "score": 4.0},
            {"id": 3, "score": 16.0, "minimum_xp": 6000},
        ]
        values = self.generator.allocate_budget(rows, rule)
        self.assertEqual(12000, sum(values.values()))
        self.assertTrue(all(value % 1000 == 0 for value in values.values()))
        self.assertLessEqual(values[1], values[2])
        self.assertLessEqual(values[2], values[3])
        self.assertGreaterEqual(values[3], 6000)

    def test_future_entries_are_explicitly_rare(self):
        for category in ("appearance", "mount", "companion"):
            self.assertGreater(
                self.generator.CATEGORY_RULES[category]["future_score"], 1.0)

    def test_only_dangerous_internal_names_are_quarantined(self):
        for name in ("NPC Equip - Claw", "Sword (Test)", "[PH] Helmet"):
            with self.subTest(name=name):
                self.assertTrue(self.generator.is_dangerous_name(name))
        for name in ("Deprecated Ancient Blade", "Contest Winner's Tabard",
                     "Swift Spectral Tiger"):
            with self.subTest(name=name):
                self.assertFalse(self.generator.is_dangerous_name(name))

    def test_teaching_catalog_keeps_all_aliases(self):
        def item(entry, spell):
            return {"entry": entry, "name": "item %d" % entry, "class": 15,
                    "subclass": 5, "spells": [(spell, 6)]}

        catalog = self.generator.build_teaching_catalog(
            {10: item(10, 100), 11: item(11, 100)}, {100}, set(),
            {100: "Mount"})
        self.assertEqual([10, 11], catalog[100]["items"])

    def test_runtime_has_no_generic_appearance_fallback(self):
        with open(COLLECTION_REWARDS, encoding="utf-8") as handle:
            runtime = handle.read()
        self.assertNotIn("BASELINE_ITEM_XP", runtime)
        self.assertIn("has no authoritative XP value; ignored", runtime)


class ParagonCollectionSettlementContractTests(unittest.TestCase):
    def read(self, path):
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def test_fresh_schema_and_upgrade_add_zero_default_pending_ledgers(self):
        schema = self.read(FRESH_SCHEMA)
        migration = self.read(MIGRATION)
        for table in (
                "paragon_rewarded_collectible_spell",
                "paragon_rewarded_appearance"):
            block = re.search(
                r"CREATE TABLE IF NOT EXISTS `acore_ale`\.`%s` \(.*?\);" % table,
                schema,
                re.DOTALL,
            )
            self.assertIsNotNone(block)
            self.assertIn(
                "`pending_xp` BIGINT UNSIGNED NOT NULL DEFAULT 0", block.group(0))
            self.assertIn(table, migration)
        self.assertIn("information_schema`.`columns", migration)
        self.assertIn("information_schema`.`statistics", migration)

    def test_generator_seed_is_explicitly_zero_pending_and_no_backpay(self):
        generator = self.read(COLLECTION_GENERATOR)
        self.assertIn("(account_id,spell_id,pending_xp)", generator)
        self.assertIn("collection.spell_id,0", generator)
        self.assertIn("(account_id,item_id,pending_xp)", generator)
        self.assertIn("unlocked.item_template_id,0", generator)
        self.assertIn("collection.account_id,'toy',collection.item_id,0", generator)
        self.assertIn(
            "collection.account_id,'heirloom',collection.item_id,0", generator)
        self.assertIn(
            "owner_character.account,'heirloom',instance.itemEntry,0", generator)
        self.assertNotIn(
            "JOIN acore_characters.characters character ", generator)

    def test_typed_account_item_schema_is_canonical_and_transactional(self):
        schema = self.read(FRESH_SCHEMA)
        migration = self.read(ACCOUNT_MIGRATION)
        for table in (
                "paragon_collectible_account_item_xp",
                "paragon_rewarded_account_item"):
            self.assertIn(table, schema)
            self.assertIn(table, migration)
        self.assertIn("PRIMARY KEY (`account_id`, `kind`, `item_id`)", schema)
        self.assertIn(
            "`pending_xp` BIGINT UNSIGNED NOT NULL DEFAULT 0", schema)

    def test_runtime_contract_is_commit_before_live_with_cas(self):
        runtime = self.read(COLLECTION_REWARDS)
        claim = runtime.index("local function ClaimSpell")
        sync = runtime.index("local function SyncCurrentProgression")
        commit = runtime.index("local function CommitPending")
        pay = runtime.index("local function PayPending")
        self.assertLess(claim, pay)
        self.assertLess(sync, pay)
        self.assertLess(commit, pay)
        self.assertIn("AND progression.level = %d", runtime)
        self.assertIn("AND progression.experience = %d", runtime)
        pay_body = runtime[pay:runtime.index("local function PaySpellPending")]
        self.assertLess(pay_body.index("CommitPending("),
                        pay_body.index("Hook.AwardFlatExperience("))


if __name__ == "__main__":
    unittest.main()
