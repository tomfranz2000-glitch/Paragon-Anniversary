# Paragon Anniversary — Complete Installation Guide

This guide applies to the `wintermute` branch of this repository. It replaces
the generic upstream instructions: this fork requires core/module patches,
database migrations, generated server content, three client patch files, and
the complete addon.

Do not start the worldserver until the server SQL, generated content, Lua
scripts, and ALE extensions are all installed. The database container may run
while the generators execute.

## Installation order

1. Clone the pinned playerbot core and required modules.
2. Apply the patches under `patches/` to their correct repositories.
3. Build the core and complete the normal AzerothCore database import.
4. Apply `sql/01_create_database.sql` through
   `sql/05_apply_anniversary_config.sql` in order.
5. Install `serverside/paragon` and the ALE extensions under the configured
   `ALE.ScriptPath`.
6. Run the two class-data generators, then the unified client/content
   generator.
7. Populate collection and quest XP.
8. Install the 27-file addon and build the 14-file UI-art archive.
9. Verify the installation, then start the worldserver and fully restart the
   client.

## 1. Prerequisites and repository layout

Required software and data:

- Python 3 and `mpyq`: `python -m pip install mpyq`
- Git, CMake/build tools, and MySQL client access
- Docker when using the supplied generator commands; they currently connect
  to a database container named `ac-database`
- A clean enUS World of Warcraft 3.3.5a client
- The playerbot AzerothCore fork and commits recorded in
  [`patches/PINS.md`](../patches/PINS.md)
- `mod-ale`, `mod-transmog`, and their own prerequisites
- `mod-collections` when collection XP should include account-wide mounts and
  companions; without it those awards remain zero

Set the database password and the real client `Data` directory before running
any tool. `PARAGON_CLIENT_DATA` points to `Data`, not the WoW root.

PowerShell:

```powershell
$env:ACORE_DB_PASS = "your-database-root-password"
$env:PARAGON_CLIENT_DATA = "C:\Games\World of Warcraft 3.3.5a\Data"
```

Bash:

```bash
export ACORE_DB_PASS="your-database-root-password"
export PARAGON_CLIENT_DATA="/games/wow-3.3.5a/Data"
```

The tools retain `Paragon-Anniversary/Client/Data` as a fallback for the
original workspace layout, but a fresh installation should set the environment
variable explicitly.

## 2. Clone modules and apply patches

The core must be the pinned `mod-playerbots/azerothcore-wotlk` Playerbot fork,
not stock AzerothCore. Apply patches against the base commits listed in
`patches/PINS.md`; a failed hunk means the checkout has drifted and should not
be forced.

### ALE directory name

Clone the upstream repository with the explicit target **`modules/mod-ale`**:

```bash
cd /path/to/azerothcore
git clone https://github.com/azerothcore/mod-eluna.git modules/mod-ale
```

The default target `modules/mod-eluna` is wrong for this core. Module discovery
finds it, but `modules/CMakeLists.txt` skips `ConfigureALEModule`, so the build
fails later with `fatal error: 'lua.h' file not found`.

### Patch targets

Apply each patch from the repository named in the second column:

| Patch | Apply from | Required |
|---|---|---|
| `patches/01-core-paragon.patch` | AzerothCore root | Yes |
| `patches/04-core-docker-build-jobs.patch` | AzerothCore root | Docker builds; allows a safe `CBUILD_JOBS` cap |
| `patches/05-mod-ale.patch` | `modules/mod-ale` | Yes |
| `patches/06-AccountBound.patch` | `modules/AccountBound` | When using the pinned AccountBound title-sync module |

Example:

```bash
cd /path/to/azerothcore
git apply /path/to/Paragon-Anniversary/patches/01-core-paragon.patch
git apply /path/to/Paragon-Anniversary/patches/04-core-docker-build-jobs.patch

cd modules/mod-ale
git apply /path/to/Paragon-Anniversary/patches/05-mod-ale.patch
```

Build the core only after all applicable patches and modules are present. For
Docker, pass a conservative build width when needed, for example
`CBUILD_JOBS=4` through the compose build arguments.

## 3. Initialize AzerothCore and Paragon databases

Complete AzerothCore's normal auth/characters/world/playerbots import first.
The Paragon generators query populated world tables and cannot run against an
empty `acore_world` database.

Run these five files in exactly this order:

```sql
SOURCE sql/01_create_database.sql;
SOURCE sql/02_create_tables.sql;
SOURCE sql/03_create_triggers.sql;
SOURCE sql/04_insert_default_config.sql;
SOURCE sql/05_apply_anniversary_config.sql;
```

There is no required `sql/06` file. `02_create_tables.sql` is the single
authoritative schema and creates all 20 base and Anniversary tables, including
the five collection/codex support tables that older installs lacked.

Do not load `sql/11-13-2026_Example_Data.sql` on an existing realm. It is a
destructive example/reference file and can replace configuration data.

Verify the preset before continuing:

```sql
SELECT COUNT(*) FROM acore_ale.paragon_config;
SELECT field, value FROM acore_ale.paragon_config
WHERE field IN ('BASE_MAX_EXPERIENCE', 'MINIMUM_LEVEL_FOR_PARAGON_XP');
```

The Anniversary preset contains at least 22 settings, starts at 30,000 XP,
and permits Paragon XP only from character level 80.

## 4. Install server Lua and ALE extensions

Set `ALE.ScriptPath` to one directory containing both `paragon/` and
`extensions/`.

Native layout:

```ini
ALE.Enabled = true
ALE.ScriptPath = "lua_scripts"
```

Repository Docker layout:

```ini
ALE.Enabled = true
ALE.ScriptPath = "/azerothcore/env/dist/etc/lua_scripts"
```

Copy the complete server package:

```bash
cp -r /path/to/Paragon-Anniversary/serverside/paragon \
      /path/to/lua_scripts/
```

Then copy ALE's extensions into that same path. `ObjectVariables.ext` supplies
the `SetData` and `GetData` methods used by Paragon.

```bash
cp -r /path/to/azerothcore/modules/mod-ale/src/LuaEngine/extensions \
      /path/to/lua_scripts/
```

For the supplied Docker layout, use the idempotent form:

```bash
mkdir -p env/dist/etc/lua_scripts/extensions
cp -r modules/mod-ale/src/LuaEngine/extensions/. \
      env/dist/etc/lua_scripts/extensions/
```

The final layout must contain:

```text
lua_scripts/
├── extensions/
│   └── ObjectVariables.ext
└── paragon/
    ├── modules/
    ├── lib/
    ├── paragon_class.lua
    ├── paragon_config.lua
    ├── paragon_constant.lua
    ├── paragon_hook.lua
    └── paragon_repository.lua
```

Docker's production worldserver image copies the executable but not CMake's
`bin/lua_scripts/extensions` output. The explicit copy into the bind-mounted
script path is therefore required even after a successful rebuild.

## 5. Generate and apply custom content

Keep `ac-database` running and the worldserver stopped. From the
Paragon-Anniversary repository root, run:

```bash
python tools/gen_class_talents.py --emit
python tools/gen_class_trainers.py --emit
python tools/paragon_client_patch.py --apply
```

The first two commands create the intermediate
`tools/generated/class_talent_ranks.py` and
`tools/generated/class_trainer_ranks.py` modules. The unified generator refuses
to run without them.

`paragon_client_patch.py --apply` then:

- generates `tools/generated/paragon_content.sql`;
- applies that SQL to `acore_world`;
- generates all custom spells, talents, trainer ranks, achievements, criteria,
  and both custom title override rows;
- builds `patch-X.MPQ` in `PARAGON_CLIENT_DATA`;
- builds `patch-enUS-X.MPQ` in `PARAGON_CLIENT_DATA/enUS`;
- verifies both archives before replacing an existing Paragon-owned output.

### The single content SQL

`sql/content/01_paragon_content.sql` is the checked-in snapshot of the unified
generator output. A normal install using `--apply` must **not** apply it as a
second required step; the generator has already applied the equivalent SQL.

For an intentionally SQL-only deployment, omit `--apply`, build the client
archives, and import the snapshot into `acore_world` yourself:

```bash
mysql -u root -p acore_world < sql/content/01_paragon_content.sql
```

There are no separate `02`, `03`, or `04` content migrations. Their former
extended-talent, Consecration, reward-aura, and title rows are consolidated in
the unified generator and `01_paragon_content.sql`.

Do not use historical single-feature generators or SQL copies: a partial DBC
build can silently remove the other custom records from the client archives.

## 6. Populate collection and quest XP

After the base schema exists and the AzerothCore world import is complete, run:

```bash
python tools/paragon_collectible_xp.py --seed
python tools/populate_quest_paragon_xp.py
```

Use `--seed` on the first install. It records already-owned collectibles in the
one-time reward mirrors so existing collections do not grant a retroactive XP
windfall. The tool also repopulates the collectible reward values and writes a
review CSV under `tools/generated/`.

The quest generator replaces `paragon_config_experience_quest` with the full
level-appropriate QuestXP values. Both tools are rerunnable, but their values
are loaded by the server at startup.

The remaining `gen_*` scripts are content-maintenance tools. Their generated
Lua/addon outputs are already committed and are not part of a fresh install.

## 7. Install the client addon and art

The client UI is complete. It consists of a normal 27-file addon plus 14 BLP
art files. The addon must not be packed into an MPQ.

Copy the addon directory to the WoW client:

```bash
cp -r clientside/Interface/AddOns/Paragon \
      /path/to/WoW/Interface/AddOns/
```

Build the art archive from the tracked BLP sources:

```bash
python tools/build_ui_art.py
```

`build_ui_art.py` stages only `clientside/Interface` outside `AddOns`, invokes
`tools/build_mpq.py`, verifies all 14 source files byte-for-byte, adds the
Paragon ownership marker, and writes `patch-W.MPQ` to
`PARAGON_CLIENT_DATA`.

The old `tools/mpq-backup/patch-4.MPQ` is an unmarked historical recovery copy.
Do not install, rename, or overwrite a client archive with it.

After generation and addon installation, these exact files must exist:

```text
WoW/
├── Data/
│   ├── patch-W.MPQ
│   ├── patch-X.MPQ
│   └── enUS/
│       └── patch-enUS-X.MPQ
└── Interface/
    └── AddOns/
        └── Paragon/
            └── Paragon.toc
```

Run the collision checker before launching the client:

```bash
python tools/check_patch_collisions.py
```

If `W` or `X` is already occupied by an unrelated archive, choose free
single-character names. Use matching options for the DBC generator and
collision checker; the UI builder has its own output option:

```bash
python tools/paragon_client_patch.py --apply \
    --general-name patch-Y.MPQ --locale-name patch-enUS-Y.MPQ
python tools/build_ui_art.py --output-name patch-V.MPQ
python tools/check_patch_collisions.py \
    --ui-name patch-V.MPQ --general-name patch-Y.MPQ \
    --locale-name patch-enUS-Y.MPQ
```

The tools refuse to overwrite an existing archive unless its exact Paragon
ownership marker is present.

## 8. Start and verify

Start or restart the worldserver only after all SQL and generators complete.
The custom DBC override tables load at process startup, so a Lua reload is not
sufficient. Fully exit and restart `Wow.exe` as well; patch MPQs load once per
process.

### Server verification

Run these checks:

```sql
SELECT COUNT(*) AS settings FROM acore_ale.paragon_config;
SELECT COUNT(*) AS custom_spells
FROM acore_world.spell_dbc
WHERE ID >= 1900000 AND ID < 2000000;
SELECT ID, Name_Lang_enUS, Mask_ID
FROM acore_world.chartitles_dbc
WHERE ID IN (200, 201)
ORDER BY ID;
SELECT COUNT(*) FROM acore_ale.paragon_collectible_spell_xp;
SELECT COUNT(*) FROM acore_ale.paragon_config_experience_quest;
```

On the current branch, the custom-spell coverage audit reports 743
client-generated records plus 21 deliberately server-only records. Both title
rows must be present on a fresh host.

At login, the console must not report `SetData`/`GetData` errors. A level-80
character should earn Paragon XP from configured sources; a lower-level
character should not. The first Paragon level requires 30,000 XP with the
Anniversary preset.

### Client verification

At the character-selection addon list, confirm `Paragon` is enabled. In game,
verify the Paragon micro button, XP bar, codex, reward track, and allocation UI
open without Lua errors. Custom spell/talent names and custom achievements must
be visible; missing names indicate that the generated DBC archive did not win
the patch load order.

## Troubleshooting

### `fatal error: 'lua.h' file not found`

ALE was cloned as `modules/mod-eluna`. Rename or clone it as
`modules/mod-ale`, remove the failed CMake build directory/cache, and configure
again.

### `SetData` or `GetData` is nil

`extensions/ObjectVariables.ext` is absent from the configured
`ALE.ScriptPath`. Copy the complete mod-ale extension directory there and
restart the worldserver.

### `missing generated class data`

Run these commands before the unified generator:

```bash
python tools/gen_class_talents.py --emit
python tools/gen_class_trainers.py --emit
```

### `set ACORE_DB_PASS` or MySQL connection failure

Set `ACORE_DB_PASS`, confirm the container is named `ac-database`, and confirm
the AzerothCore world import completed. The generators use `docker exec` and
the populated world schema.

### A DBC cannot be found

Set `PARAGON_CLIENT_DATA` to the client's actual `Data` directory and confirm
the enUS locale archives exist below `Data/enUS`. Delete only the regenerable
`tools/cache` directory if it contains extracts from a different client.

### Archive overwrite refused

The target name belongs to an unmarked or third-party MPQ. Do not delete it
blindly. Inspect/move it or select a free one-character patch suffix, then run
the generator and collision checker with matching names.

### Server boot-loops on missing Paragon tables

Reapply `sql/01_create_database.sql` through
`sql/05_apply_anniversary_config.sql` in order. Do not rely on Lua to create
the schema.

## Updating an existing installation

Back up the three AzerothCore databases plus `acore_ale`, update the repository
and pinned patches together, rerun the base SQL (it is idempotent except for
the documented Anniversary preset), regenerate the two class intermediate
files, rerun `paragon_client_patch.py --apply`, repopulate collection/quest XP,
rebuild `patch-W.MPQ`, and recopy the addon. Finish with a worldserver and full
client restart.

For implementation details and hard-won compatibility notes, see
[`doc/CORE_PATCHES.md`](CORE_PATCHES.md).
