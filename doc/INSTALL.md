# 📦 Paragon System Installation Guide

Complete installation instructions for the **Paragon System** on AzerothCore with ALE.

---

## 📋 Prerequisites

Before installing the Paragon System, ensure you have:

- ✅ **AzerothCore** 3.3.5a server running
- ✅ **ALE (Azeroth Lua Engine)** installed and configured
- ✅ **Database access** (MySQL/MariaDB) with appropriate credentials
- ✅ **World of Warcraft 3.3.5a client** for testing
- ✅ Git or file explorer for copying files

### Required Dependencies

The system requires these libraries to be present in your ALE scripts directory:
- **classic.lua** - Object-Oriented Programming library
- **CSMH** - Client-Server Message Handler framework
- **Mediator** - Event-driven architecture pattern

All dependencies are included in the project.

---

## 🚀 Server-Side Installation (Lua Scripts)

### Step 1: Copy the Paragon Folder

Copy the entire `paragon` folder to your ALE scripts directory:

```bash
# Typical AzerothCore ALE scripts location:
cp -r paragon /path/to/lua_scripts/
```

**Directory structure after copy:**
```
your_ale_scripts_directory/
└── paragon/
    ├── lib/
    │   ├── classic/
    │   ├── Mediator/
    │   └── CSMH/
    ├── modules/
    │   └── paragon_anniversary.lua
    ├── sql/
    │   ├── 01_create_database.sql
    │   ├── 02_create_config_tables.sql
    │   ├── 03_create_experience_tables.sql
    │   ├── 04_create_paragon_tables.sql
    │   ├── 05_create_triggers.sql
    │   ├── 06_insert_default_config.sql
    │   ├── 07_apply_anniversary_config.sql
    │   ├── 08_create_runtime_tables.sql
    │   └── README.md
    ├── paragon_class.lua
    ├── paragon_config.lua
    ├── paragon_constant.lua
    ├── paragon_hook.lua
    ├── paragon_repository.lua
    ├── HOOKS.md
    └── INSTALLATION.md
```

### Step 2: Execute SQL Migrations

**IMPORTANT:** You **MUST** execute all SQL migration files manually **BEFORE** starting your server.

Navigate to the `sql/` directory and execute all files in order using your MySQL client:

```sql
-- Execute in this exact order:
SOURCE 01_create_database.sql;
SOURCE 02_create_config_tables.sql;
SOURCE 03_create_experience_tables.sql;
SOURCE 04_create_paragon_tables.sql;
SOURCE 05_create_triggers.sql;
SOURCE 06_insert_default_config.sql;
SOURCE 07_apply_anniversary_config.sql;
SOURCE 08_create_runtime_tables.sql;
```

**Alternative methods:**
- Use MySQL Workbench, HeidiSQL, DBeaver, or any MySQL client
- Execute all files at once by running them in sequence
- Use command line: `mysql -u username -p < filename.sql`

### Step 3: Verify Database Installation

Check the database to confirm all tables were created:

```sql
-- Connect to the database
USE acore_ale;  -- or your configured database name

-- Verify all paragon tables exist
SHOW TABLES LIKE 'paragon%';
SHOW TABLES LIKE '%paragon%';

-- Expected tables (15 total):
-- paragon_config
-- paragon_config_category
-- paragon_config_statistic
-- paragon_config_experience_achievement
-- paragon_config_experience_creature
-- paragon_config_experience_quest
-- paragon_config_experience_skill
-- character_paragon
-- character_paragon_stats
-- account_paragon
-- paragon_collectible_spell_xp
-- paragon_collectible_item_xp
-- paragon_rewarded_collectible_spell
-- paragon_rewarded_appearance
-- paragon_banked_experience

-- Verify default configuration was inserted
SELECT COUNT(*) FROM paragon_config;
-- Should return at least 17 rows
```

### Step 4: Verify ALE Configuration

Ensure your ALE configuration in `mod-ale.conf` includes:

```ini
# Lua Engine settings
ALE.Enabled = 1
ALE.ScriptPath = "lua_scripts"
```

### Step 5: Start the Server

Start or restart your AzerothCore server. The system will:

1. ✅ Verify the database schema exists
2. ✅ Load the Paragon scripts
3. ✅ Load configuration from the database
4. ✅ Initialize all modules

```bash
# Start AzerothCore
./worldserver
```

**Expected console output:**
```
[Paragon System] Database schema verified successfully.
[Paragon] Paragon Anniversary Experience module loaded
[Paragon] Paragon Anniversary Level Animation module loaded
```

Monitor the server console for any error messages related to Paragon initialization.

---

## ⚙️ Initial Configuration

### Access the Database Configuration

The Paragon System configuration is stored in the `paragon_config` table. You can modify settings while the server is running:

```sql
-- View all current configuration
SELECT * FROM paragon_config;

-- Update a specific setting
UPDATE paragon_config SET value = '30000' WHERE field = 'BASE_MAX_EXPERIENCE';
```

### Essential Configuration Options

#### Enable/Disable the System

```sql
UPDATE paragon_config
SET value = '1'
WHERE field = 'ENABLE_PARAGON_SYSTEM';

-- 1 = Enabled (default)
-- 0 = Disabled (system won't function)
```

#### Choose Progression Mode

```sql
UPDATE paragon_config
SET value = '1'
WHERE field = 'LEVEL_LINKED_TO_ACCOUNT';

-- 0 = Character-linked - Each character has independent progression
-- 1 = Account-linked (Anniversary default) - All characters share level/XP
--     but have separate stat investments
```

#### Set Paragon Level Cap

```sql
UPDATE paragon_config
SET value = '10000'
WHERE field = 'PARAGON_LEVEL_CAP';

-- 0 = Unlimited (default)
-- Any positive number = Maximum achievable paragon level
```

### Configure Experience Rewards

Set how much paragon experience players earn from different activities:

```sql
-- Creature kills (default: 50)
UPDATE paragon_config SET value = '50' WHERE field = 'UNIVERSAL_CREATURE_EXPERIENCE';

-- Achievements (default: 100)
UPDATE paragon_config SET value = '100' WHERE field = 'UNIVERSAL_ACHIEVEVEMENT_EXPERIENCE';

-- Skill increases (default: 25)
UPDATE paragon_config SET value = '25' WHERE field = 'UNIVERSAL_SKILL_EXPERIENCE';

-- Quest completion fallback (default: 1; normal rewards use quest data)
UPDATE paragon_config SET value = '1' WHERE field = 'UNIVERSAL_QUEST_EXPERIENCE';
```

### Configure Progression Speed

```sql
-- Experience required for the first Paragon level
UPDATE paragon_config SET value = '30000' WHERE field = 'BASE_MAX_EXPERIENCE';

-- Points awarded per level
UPDATE paragon_config SET value = '1' WHERE field = 'POINTS_PER_LEVEL';

-- Starting level for new characters
UPDATE paragon_config SET value = '1' WHERE field = 'PARAGON_STARTING_LEVEL';

-- No Paragon XP is granted merely by creating/loading a progression record
UPDATE paragon_config SET value = '0' WHERE field = 'PARAGON_STARTING_EXPERIENCE';

-- Characters below this level cannot receive Paragon XP
UPDATE paragon_config SET value = '80' WHERE field = 'MINIMUM_LEVEL_FOR_PARAGON_XP';
```

### Configure Experience Multipliers

```sql
-- Low-level Paragon multiplier
UPDATE paragon_config SET value = '1' WHERE field = 'EXPERIENCE_MULTIPLIER_LOW_LEVEL';

-- Paragon level threshold for low-level bonus
UPDATE paragon_config SET value = '5' WHERE field = 'LOW_LEVEL_THRESHOLD';

-- High-level Paragon multiplier
UPDATE paragon_config SET value = '1' WHERE field = 'EXPERIENCE_MULTIPLIER_HIGH_LEVEL';

-- Paragon level threshold for high-level penalty
UPDATE paragon_config SET value = '100' WHERE field = 'HIGH_LEVEL_THRESHOLD';
```

---

## 🎮 Client-Side Installation (UI/Addon)

### Status
The client-side UI is currently **in development**. Basic functionality is working, but some advanced features are still being completed.

### Installation (When Available)

#### As a Patch/FrameXML

1. Copy the client-side files to a `Patch-4.MPQ` patch directory
2. Test in game

---

## 🧪 Testing the Installation

### Verify Server-Side is Working

1. **Create a test character** on your server
2. **Gain experience** by:
   - Killing creatures
   - Completing quests
   - Completing achievements
   - Increasing skills
3. **Check database** for paragon progression:

```sql
-- Check character paragon data
SELECT * FROM character_paragon WHERE guid = YOUR_CHARACTER_GUID;

-- Check invested statistics
SELECT * FROM character_paragon_stats WHERE guid = YOUR_CHARACTER_GUID;
```

### Expected Server Output

When a player gains paragon experience, you should see:
- Level increases reflected in the database
- Experience accumulation toward the next level
- Points awarded per level
- Stat modifiers applied

### Verify Client-Side is Working

Once the client addon is complete:
1. Log in to the game
2. Verify you can see:
   - Current paragon level
   - Experience progress bar
   - Available points
   - Stat allocation interface

---

## 🔧 Troubleshooting

### Error: "Database schema not initialized!" or "Database not found!"

**Problem**: Server shows error message about missing database or tables

**Error message example:**
```
=================================================================
[PARAGON SYSTEM ERROR] Database not found!
=================================================================

The database 'acore_ale' does not exist.

SOLUTION:
  1. Navigate to: lua_scripts/game/systems/paragon/sql/
  2. Execute 01_create_database.sql
  3. Execute all other SQL files (02 through 08)
  4. Reload Eluna scripts: .reload eluna
=================================================================
```

**Solutions**:
1. You haven't executed the SQL migration files yet (see Step 2 above)
2. Check which tables are missing:
```sql
SHOW TABLES LIKE 'paragon%';
```
3. Execute the missing SQL files from the `sql/` directory
4. Reload Eluna scripts: `.reload eluna` or restart the server

### Error: "Table already exists"

**Problem**: SQL execution shows "Table already exists" warnings

**Solution**: This is normal and safe to ignore. The SQL files use `CREATE TABLE IF NOT EXISTS`, so they can be run multiple times without issues.

### Custom Database Name

**Problem**: You're using a different database name than `acore_ale`

**Solutions**:
1. Edit `paragon_constant.lua` and change the `DB_NAME` constant to your database name
2. Replace all occurrences of `acore_ale` in the SQL files with your database name
3. Re-execute the SQL files

### No Categories or Statistics Available

**Problem**: The system is installed but no categories or statistics appear in the UI or database

**Cause**: The base migration files (01-06) only create the table structure and default configuration. They **do not** include any example categories or statistics.

**Solutions**:

1. **Use the example data file** - A complete example configuration is provided:
   ```sql
   -- Located at: sql/11-13-2026_Example_Data.sql
   -- This file includes:
   -- - 3 example categories (Combat, Stats, Special)
   -- - 25+ configured statistics
   -- - All properly configured with types, factors, and limits

   SOURCE sql/11-13-2026_Example_Data.sql;
   ```

2. **Manually create categories and statistics**:
   ```sql
   -- Create a category
   INSERT INTO paragon_config_category (id, name) VALUES (1, 'Combat');

   -- Add a statistic to the category
   INSERT INTO paragon_config_statistic
   (id, category, type, type_value, icon, factor, limit, application)
   VALUES
   (1, 1, 'COMBAT_RATING', 8, 'Interface\\Icons\\Inv_sword_27', 10, 255, 0);
   -- Where type_value = 8 is CRIT_MELEE (see paragon_constant.lua for all values)
   ```

3. **Import from another server** - Export your existing configuration using:
   ```sql
   -- Export categories and statistics
   SELECT * FROM paragon_config_category;
   SELECT * FROM paragon_config_statistic;
   ```

**Note**: After adding categories/statistics, you may need to reload the Lua scripts or restart the server for changes to take effect.

### Experience Not Being Awarded

**Problem**: Players gain creature kills but no paragon XP

**Solutions**:
1. Check if system is enabled:
```sql
SELECT value FROM paragon_config WHERE field = 'ENABLE_PARAGON_SYSTEM';
-- Should return: 1
```

2. Verify player meets minimum level requirement:
```sql
SELECT value FROM paragon_config WHERE field = 'MINIMUM_LEVEL_FOR_PARAGON_XP';
```

3. Check ALE logs for errors

### Configuration Changes Not Taking Effect

**Problem**: Config updates don't apply immediately

**Solutions**:
- The system caches config on server startup
- For changes to take effect immediately, either:
  - Reload Lua scripts: `.reload ale` (if command is available)
  - Restart the server

### Player Data Not Saving

**Problem**: Paragon progress is lost on logout

**Solutions**:
1. Check character_paragon table exists
2. Verify player logout hook is firing
3. Check database permissions (INSERT/UPDATE rights)

### Addon Communication Errors

**Problem**: Client-server communication failing

**Solutions**:
- Verify addon prefix matches: `ParagonAnniversary`
- Check CSMH library is properly loaded
- Review ALE error logs

---

## 📚 Additional Configuration

### Adding Custom Statistics

See [README.md](../README.md#adding-custom-stats) for detailed instructions on adding custom paragon statistics.

### Configuring Experience Per Creature/Achievement/Quest

You can set custom experience rewards for specific creatures, achievements, quests, or skills:

```sql
-- Add custom experience reward for a creature (overrides universal default)
INSERT INTO paragon_config_experience_creature (entry, experience)
VALUES (1, 500);  -- Creature entry 1 grants 500 paragon XP

-- Same for other sources:
-- paragon_config_experience_achievement
-- paragon_config_experience_quest
-- paragon_config_experience_skill
```

---

## 📖 Next Steps

1. **Read the main documentation**: [README.md](../README.md)
2. **Learn about hooks/extensibility**: [HOOKS](HOOKS.md)
3. **Create custom modules**: [MODULES](MODULES.md)
4. **Configure your server** according to your needs
5. **Test thoroughly** before deploying to production

---

## 🆘 Getting Help

If you encounter issues:

1. **Check the documentation**: README.md, HOOKS.md, modules/README.md
2. **Review server console** for error messages
3. **Check database tables** to verify installation
4. **Open an issue** on the project repository with:
   - Your AzerothCore version
   - ALE version
   - Error messages (full stack trace)
   - Steps to reproduce the problem

---

<div align="center">

### 🎉 **Installation Complete!**

Your Paragon System is now ready for use. Enjoy endless progression!

**[Back to README](../README.md)**

</div>
