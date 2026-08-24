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
cannot accidentally omit the profession ledger, configuration, or triggers.

### Execution Order

Execute the following files in order using your preferred MySQL client (MySQL Workbench, HeidiSQL, command line, etc.):

1. **01_create_database.sql** - Creates the `acore_ale` database
2. **02_create_tables.sql** - Creates every table required by the base system and Anniversary modules
3. **03_create_triggers.sql** - Creates validation triggers for statistics
4. **04_insert_default_config.sql** - Inserts default configuration values
5. **05_apply_anniversary_config.sql** - Updates an existing installation to the canonical Anniversary realm values

### Component migrations

`install.sql` sources these files in the required order:

```sql
SOURCE sql/01_create_database.sql;
SOURCE sql/02_create_tables.sql;
SOURCE sql/03_create_triggers.sql;
SOURCE sql/04_insert_default_config.sql;
SOURCE sql/05_apply_anniversary_config.sql;
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
- `acore_ale.character_paragon`
- `acore_ale.account_paragon`
- `acore_ale.character_paragon_stats`
- `acore_ale.paragon_collectible_spell_xp`
- `acore_ale.paragon_collectible_item_xp`
- `acore_ale.paragon_rewarded_collectible_spell`
- `acore_ale.paragon_rewarded_appearance`
- `acore_ale.paragon_banked_experience`
- `acore_ale.paragon_codex_alloc`
- `acore_ale.paragon_custom_glyph`
- `acore_ale.paragon_racial_pick`
- `acore_ale.paragon_rare_kills`
- `acore_ale.paragon_solo_clears`

And verify that default configuration values were inserted:

```sql
SELECT COUNT(*) FROM acore_ale.paragon_config;
-- Should return at least 22 rows
SELECT value FROM acore_ale.paragon_config
WHERE field = 'UNIVERSAL_SKILL_EXPERIENCE';
-- Should return 2000 (final XP)
SELECT value FROM acore_ale.paragon_config
WHERE field = 'PARAGON_ACHIEVEMENT_POINT_XP';
-- Should return 2000 (final XP per achievement point)
SELECT COUNT(*) FROM acore_ale.paragon_config
WHERE field = 'PARAGON_ONE_TIME_XP_MULTIPLIER';
-- Should return 0 (obsolete runtime policy is removed)
SELECT COUNT(*) FROM acore_ale.paragon_config_category;
-- Should return at least 4
SELECT COUNT(*) FROM acore_ale.paragon_config_statistic;
-- Should return at least 17
```

`04_insert_default_config.sql` is non-destructive and only fills missing rows.
The bootstrap's final migration intentionally replaces previous configuration
values with this fork's realm preset, updates the legacy skill-override column
default to 2000, removes the obsolete runtime one-time multiplier, creates the
profession ledger, and seeds current account/character high-water values
without retroactive XP. Existing unpaid 1000-point profession/achievement
claims are upgraded once while their old authority rows still identify them;
new profession skill-up awards store the final universal value directly.
Legacy per-skill rows remain for schema compatibility but do not override that
flat high-water contract.

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
categories and the 17 statistics implemented by the current runtime.

## Database Name

**Note:** All SQL files are configured to use the database name `acore_ale`. If you need to use a different database name, you must:

1. Update the `DB_NAME` constant in `paragon_constant.lua`
2. Replace all occurrences of `acore_ale` in the SQL files with your database name
