# Paragon System - SQL Migration Files

This directory contains all SQL migration files required to set up the Paragon system database.

## Installation Instructions

**IMPORTANT:** You must execute these SQL files manually in the correct order before starting your server with the Paragon system enabled.

### Execution Order

Execute the following files in order using your preferred MySQL client (MySQL Workbench, HeidiSQL, command line, etc.):

1. **01_create_database.sql** - Creates the `acore_ale` database
2. **02_create_tables.sql** - Creates every table required by the base system and Anniversary modules
3. **03_create_triggers.sql** - Creates validation triggers for statistics
4. **04_insert_default_config.sql** - Inserts default configuration values
5. **05_apply_anniversary_config.sql** - Updates an existing installation to the canonical Anniversary realm values

### Quick Installation (All at once)

You can also execute all files at once by running them in sequence, or by creating a master script that sources all files:

```sql
SOURCE 01_create_database.sql;
SOURCE 02_create_tables.sql;
SOURCE 03_create_triggers.sql;
SOURCE 04_insert_default_config.sql;
SOURCE 05_apply_anniversary_config.sql;
```

### Verification

After running all migration files, verify the installation by checking that the following tables exist:

- `acore_ale.paragon_config_category`
- `acore_ale.paragon_config_statistic`
- `acore_ale.paragon_config`
- `acore_ale.paragon_config_experience_creature`
- `acore_ale.paragon_config_experience_achievement`
- `acore_ale.paragon_config_experience_skill`
- `acore_ale.paragon_config_experience_quest`
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
```

`04_insert_default_config.sql` is non-destructive and only fills missing rows.
Run `05_apply_anniversary_config.sql` once when upgrading an existing database;
it intentionally replaces previous configuration values with this fork's realm
preset.

## Error Handling

If you start the server without executing these migration files, you will see error messages in the console indicating which tables are missing. Simply execute the required SQL files and reload the Lua scripts using `.reload eluna`.

## Example Data

**File:** `11-13-2026_Example_Data.sql`

This file contains a **complete example configuration** with:
- ✅ **3 Categories**: Combat, Stats, Special
- ✅ **25+ Statistics**: Fully configured with proper types, values, icons, factors, and limits
- ✅ **All table structures**: Includes all tables with example data

**When to use this file:**
- You're setting up a new server and want a working configuration immediately
- You want to see examples of properly configured statistics
- You're testing the Paragon system

**How to use:**
```sql
-- After executing files 01-05, optionally load the example data:
SOURCE 11-13-2026_Example_Data.sql;
```

**Important Notes:**
- This file uses `DROP TABLE IF EXISTS`, so it will **replace** your existing data
- If you have custom categories/statistics, back them up before running this file
- You can use this as a reference and manually insert only the data you need

## Database Name

**Note:** All SQL files are configured to use the database name `acore_ale`. If you need to use a different database name, you must:

1. Update the `DB_NAME` constant in `paragon_constant.lua`
2. Replace all occurrences of `acore_ale` in the SQL files with your database name
