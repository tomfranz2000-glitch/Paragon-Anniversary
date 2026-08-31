# Paragon System - SQL Migration Files

This directory contains all SQL migration files required to set up the Paragon system database.

## Installation Instructions

**IMPORTANT:** Initialize the normal AzerothCore databases first. A complete
installation should use `python tools/install.py --apply`; it invokes this
database bootstrap automatically. For a database-only recovery, run from the
repository root before starting the worldserver:

```bash
mysql [connection options] < sql/install.sql
```

`sql/install.sql` is the supported entrypoint for both fresh installations and
upgrades. It is rerunnable and fixes the component order so an installation
cannot accidentally omit configuration, triggers, or any one-time reward
ledger.

### Execution Order

Execute the following files in order using your preferred MySQL client (MySQL Workbench, HeidiSQL, command line, etc.):

1. **01_create_database.sql** - Creates the `acore_ale` database
2. **02_create_tables.sql** - Creates every table required by the base system and Anniversary modules
3. **03_create_triggers.sql** - Creates validation triggers for statistics
4. **04_insert_default_config.sql** - Inserts default configuration values
5. **05_apply_anniversary_config.sql** - Updates an existing installation to the canonical Anniversary realm values
6. **06_add_recipe_rewards.sql** - Adds rerunnable recipe claim/seed ledgers for existing installations
7. **07_add_achievement_reward_claims.sql** - Adds and no-XP-seeds the account achievement claim ledger
8. **08_add_collection_pending_claims.sql** - Upgrades collection mirrors to crash-safe pending ledgers
9. **09_add_reputation_and_account_collection_rewards.sql** - Adds the account-wide reputation high-water ledger and typed toy/heirloom value and claim ledgers
10. **10_expand_skill_mastery_rewards.sql** - No-XP-seeds weapon and lockpicking high-water marks for the expanded mastery allowlist

### Component migrations

`install.sql` sources these files in the required order:

```sql
SOURCE sql/01_create_database.sql;
SOURCE sql/02_create_tables.sql;
SOURCE sql/03_create_triggers.sql;
SOURCE sql/04_insert_default_config.sql;
SOURCE sql/05_apply_anniversary_config.sql;
SOURCE sql/06_add_recipe_rewards.sql;
SOURCE sql/07_add_achievement_reward_claims.sql;
SOURCE sql/08_add_collection_pending_claims.sql;
SOURCE sql/09_add_reputation_and_account_collection_rewards.sql;
SOURCE sql/10_expand_skill_mastery_rewards.sql;
```

The component files remain available for diagnosis and review. Do not run them
as an alternative installation path; use `sql/install.sql` so future migrations
are picked up automatically. Because MySQL resolves `SOURCE` relative to the
client's current directory, invoke the installer from the repository root.

### Verification

After running `sql/install.sql`, verify the installation by checking that the following tables exist:

- `acore_ale.paragon_config_category`
- `acore_ale.paragon_config_statistic`
- `acore_ale.paragon_config`
- `acore_ale.paragon_config_experience_creature`
- `acore_ale.paragon_config_experience_achievement`
- `acore_ale.paragon_config_experience_skill`
- `acore_ale.paragon_config_experience_quest`
- `acore_ale.paragon_profession_progress`
- `acore_ale.paragon_reputation_progress`
- `acore_ale.paragon_recipe_reward_claim`
- `acore_ale.paragon_recipe_reward_seed`
- `acore_ale.paragon_pvp_reward_claim`
- `acore_ale.character_paragon`
- `acore_ale.account_paragon`
- `acore_ale.character_paragon_stats`
- `acore_ale.paragon_collectible_spell_xp`
- `acore_ale.paragon_collectible_item_xp`
- `acore_ale.paragon_collectible_account_item_xp`
- `acore_ale.paragon_rewarded_collectible_spell`
- `acore_ale.paragon_rewarded_appearance`
- `acore_ale.paragon_rewarded_account_item`
- `acore_ale.paragon_rewarded_achievement`
- `acore_ale.paragon_banked_experience`
- `acore_ale.paragon_codex_alloc`
- `acore_ale.paragon_custom_glyph`
- `acore_ale.paragon_racial_pick`
- `acore_ale.paragon_rare_kills`
- `acore_ale.paragon_solo_clears`

And verify that default configuration values were inserted:

```sql
SELECT COUNT(*) FROM acore_ale.paragon_config;
-- A clean canonical install returns exactly 90 rows; an upgraded realm with
-- custom settings may return more
SELECT value FROM acore_ale.paragon_config
WHERE field = 'UNIVERSAL_SKILL_EXPERIENCE';
-- Should return 5000 (final XP per eligible mastery point)
SELECT value FROM acore_ale.paragon_config
WHERE field = 'PARAGON_ACHIEVEMENT_POINT_XP';
-- Should return 10000 (final XP per achievement point)
SELECT field, value FROM acore_ale.paragon_config
WHERE field IN ('PARAGON_REPUTATION_XP_ENABLED',
                'PARAGON_REPUTATION_XP_PER_POINT');
-- Should return 1 and 50
SELECT field, value FROM acore_ale.paragon_config
WHERE field LIKE 'PARAGON_CREATURE_XP_%_MULTIPLIER'
ORDER BY field;
-- Should return the five instance factors: 1.25, 1.5, 2, 2.5, and 4
SELECT COUNT(*) FROM acore_ale.paragon_config
WHERE field = 'PARAGON_ONE_TIME_XP_MULTIPLIER';
-- Should return 0 (obsolete runtime policy is removed)
SELECT COUNT(*) FROM acore_ale.paragon_config_category;
-- Should return at least 4
SELECT COUNT(*) FROM acore_ale.paragon_config_statistic;
-- A clean canonical install returns exactly 19 rows; an upgraded realm with
-- custom statistics may return more
SELECT kind, COUNT(*) AS ids, SUM(xp) AS total_xp,
       MIN(xp) AS minimum_xp, MAX(xp) AS maximum_xp
FROM acore_ale.paragon_collectible_account_item_xp
GROUP BY kind
ORDER BY kind;
-- heirloom: 38 / 3800000 / 100000 / 100000
-- toy: 78 / 43500000 / 50000 / 3000000
```

`04_insert_default_config.sql` is non-destructive and only fills missing rows.
The Anniversary configuration migration intentionally replaces previous
configuration values with this fork's realm preset, updates the legacy
skill-override column default to 5000, removes the obsolete runtime one-time
multiplier, creates the profession/recipe/achievement ledgers, and seeds
existing skills, recipes, and achievements without retroactive XP. Migration
`09` creates an empty durable
account/faction reputation high-water and pending ledger; at runtime, the first
Paragon-ready login seeds current standings from every account character
without XP. Only later committed progress above that high-water pays the flat
50 XP per point through post-commit ALE event 82.

The same migration adds `paragon_collectible_account_item_xp` and
`paragon_rewarded_account_item`. Both keys include `kind`, so an identical
numeric ID in the toy and heirloom namespaces cannot overlap. The collection
generator supplies an exact 78-ID toy catalog with explicit rarity-informed
per-ID values (50,000 floor, 3,000,000 cap) and 38 heirlooms at exactly 100,000
XP each. EZCollections' account toy/heirloom ownership rows are the runtime
authority, and `--seed` records current ownership with zero pending XP.

Migration `10_expand_skill_mastery_rewards.sql` expands the forward-only skill
seed to all 14 professions, 15 canonical use-leveled weapon tracks, and
lockpicking. Fist Weapons (473) canonicalizes to Unarmed (162). Defense (95),
Dual Wield (118), Feral Combat (134), Shield (433), Riding (762), Runeforging
(776), and direct auto-max grants remain excluded from awards. Existing values
only raise the appropriate account- and character-scope high-water rows; they
create no pending XP or backpay. The complete 12,700-point mastery catalog is
worth 63,500,000 XP at the fixed 5,000-XP rate.

Existing awarded and pending one-time XP is not reconciled; future rewards use
the canonical values directly. Legacy per-skill rows remain for schema
compatibility but do not override that flat high-water contract.

The same bootstrap creates the PvP Merit claim ledger and writes every PvP
economy value directly into `paragon_config`. There is no hidden global or
bot-only practice multiplier. See [`doc/PVP_MERIT.md`](../doc/PVP_MERIT.md)
for the bridge, reset, idempotency, cap, and DR contracts.

## Generated world content

`content/01_paragon_content.sql` is the single complete snapshot for custom
spells, talents, trainer ranks, achievements, criteria, and titles in
`acore_world`. The canonical installation command is:

```bash
python tools/paragon_client_patch.py --apply
```

That command regenerates `sql/content/01_paragon_content.sql`, builds the
matching client DBC archives, and applies the SQL. Import
`content/01_paragon_content.sql` manually only for an intentional SQL-only
deployment or recovery; do not apply it as an additional mandatory step after
`--apply`.

Older separate content files for extended talents, Consecration, reward auras,
and the Paragon title were consolidated into this one source and removed.

## Error Handling

If you start the server without executing these migration files, the console
reports the missing tables. Stop the worldserver, execute the required SQL and
generated-content steps, then start it again. A script reload cannot refresh
the custom DBC override tables.

## Historical example dump

**File:** `11-13-2026_Example_Data.sql`

This dated export is retained only as historical reference. Do **not** execute
it during an installation or upgrade: it drops tables, replaces data, omits
newer Anniversary tables, and contains the unsupported legacy `GOLD` and
`MOVE_SPEED` statistic rows. The canonical bootstrap already inserts all four
categories and the 19 statistics implemented by the current runtime.

## Database Name

**Note:** All SQL files are configured to use the database name `acore_ale`. If you need to use a different database name, you must:

1. Update the `DB_NAME` constant in `paragon_constant.lua`
2. Replace all occurrences of `acore_ale` in the SQL files with your database name
