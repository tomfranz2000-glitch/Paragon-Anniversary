# Paragon Anniversary — Complete Installation Guide

The sole authoritative install branch is `main`. Clone it explicitly; this
fork requires core/module patches, database migrations, generated server
content, three client patch files, and the complete addon.

```bash
git clone --branch main --single-branch \
    https://github.com/tomfranz2000-glitch/Paragon-Anniversary.git
cd Paragon-Anniversary
```

Do not start the worldserver until the server SQL, generated content, Lua
scripts, and ALE extensions are all installed. The database container may run
while the generators execute.

## Installation order

1. Clone the pinned playerbot core and required modules.
2. Apply the patches under `patches/` to their correct repositories.
3. Build the core and complete the normal AzerothCore database import.
4. Install `requirements.txt`, set the documented paths, and stop the
   worldserver.
5. From the Paragon repository root, run the one complete installation command:

   ```bash
   python tools/install.py --apply \
       --core-root /path/to/azerothcore \
       --client-root /path/to/WowWotlk
   ```

6. Run the command again with `--check`, start the worldserver, and fully
   restart the client.

`tools/install.py` applies the canonical database bootstrap, regenerates all
server/client data, deploys Lua/ALE extensions and the addon, seeds existing
collections without retroactive XP, builds the three owned MPQs, and verifies
the complete payload. `--apply` is rerunnable. `--check` is read-only, while
`--dry-run` prints the exact ordered plan without probing containers, reading
secrets, writing files, or changing databases. Its checks privately rebuild all
three MPQs and exactly compare every generator-owned database row, including
stale IDs from reserved custom ranges. The remaining sections document the
pipeline's prerequisites and component commands for diagnosis/recovery.
The deployed `paragon/`, `extensions/`, and addon directories are managed as
exact copies; add any required ALE extension to the pinned mod-ale source tree
before applying so clean installs and upgrades cannot diverge through stale
files.

## 1. Prerequisites and repository layout

Required software and data:

- Python 3.10+ and the pinned packages:
  `python -m pip install -r requirements.txt`
- Git, CMake/build tools, and MySQL client access
- Docker when using the supplied generator commands; they currently connect
  to a database container named `ac-database`
- A clean enUS World of Warcraft 3.3.5a client
- The playerbot AzerothCore fork and commits recorded in
  [`patches/PINS.md`](../patches/PINS.md)
- `mod-ale` and the pinned `tomfranz2000-glitch/mod-transmog` fork described
  below, plus their own prerequisites
- `mod-collections` when collection XP should include account-wide mounts and
  companions; without it those awards remain zero

Component tools use `PARAGON_CLIENT_DATA` for the real client `Data` directory;
it points to `Data`, not the WoW root. The canonical installer derives the same
path from `--client-root`. Database tools read `MYSQL_ROOT_PASSWORD` only
inside `ac-database`, so the password is never placed in a host command line.

PowerShell:

```powershell
$env:PARAGON_CLIENT_DATA = "C:\Games\World of Warcraft 3.3.5a\Data"
```

Bash:

```bash
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

Clone and detach the exact tested core before adding modules or applying any
patch:

```bash
git clone --branch Playerbot --single-branch \
    https://github.com/mod-playerbots/azerothcore-wotlk.git \
    /path/to/azerothcore
git -C /path/to/azerothcore checkout --detach \
    efe123fab543c5faf3c477674ec17a18fd59f09f
git -C /path/to/azerothcore rev-parse HEAD
```

The final command must print
`efe123fab543c5faf3c477674ec17a18fd59f09f`.

### ALE directory name

Clone the upstream repository with the explicit target **`modules/mod-ale`**:

```bash
cd /path/to/azerothcore
git clone https://github.com/azerothcore/mod-eluna.git modules/mod-ale
git -C modules/mod-ale checkout --detach \
    9e5b8c66efeb383871ec58b925e47094c92cc8d5
git -C modules/mod-ale rev-parse HEAD
```

The final command must print
`9e5b8c66efeb383871ec58b925e47094c92cc8d5`.

The default target `modules/mod-eluna` is wrong for this core. Module discovery
finds it, but `modules/CMakeLists.txt` skips `ConfigureALEModule`, so the build
fails later with `fatal error: 'lua.h' file not found`.

### Required mod-transmog fork

Paragon reads the appearance collection maintained by the suite's fork of
`mod-transmog`. Clone its authoritative default branch, `master`, and pin the
tested revision before configuring AzerothCore:

```bash
cd /path/to/azerothcore
git clone --branch master --single-branch \
    https://github.com/tomfranz2000-glitch/mod-transmog.git \
    modules/mod-transmog
git -C modules/mod-transmog checkout --detach \
    31633595cad7b12042b6484ffe3ea34f355b9821
git -C modules/mod-transmog rev-parse HEAD
```

The final command must print
`31633595cad7b12042b6484ffe3ea34f355b9821`. That revision includes the
required `StoreNewItem` appearance capture and the realm's transmog
configuration defaults. Stock `azerothcore/mod-transmog` and unpinned fork
revisions are not supported installation sources.

Build/install the module, then create or update the active `transmog.conf`
from that pinned revision's `conf/transmog.conf.dist`. Verify these values in
the file the worldserver actually loads; an older retained configuration does
not inherit updated `.dist` values automatically:

```ini
Transmogrification.UseCollectionSystem = 1
Transmogrification.TrackUnusableItems = 1
Transmogrification.AllowPoor = 1
Transmogrification.AllowCommon = 1
Transmogrification.AllowTradeable = 1
Transmogrification.AllowMixedArmorTypes = 1
```

The first two settings make the appearance ledger available to Paragon. The
remaining settings reproduce this realm's collection scope, including poor,
common, tradeable, and mixed-armor appearances.

### Patch targets

Apply each patch from the repository named in the second column:

| Patch | Apply from | Required |
|---|---|---|
| `patches/01-core-paragon.patch` | AzerothCore root | Yes |
| `patches/02-core-profession-xp.patch` | AzerothCore root | Yes; apply after `01` |
| `patches/04-core-docker-build-jobs.patch` | AzerothCore root | Docker builds; allows a safe `CBUILD_JOBS` cap |
| `patches/05-mod-ale.patch` | `modules/mod-ale` | Yes |
| `patches/06-AccountBound.patch` | `modules/AccountBound` | When using the pinned AccountBound title-sync module |
| `patches/07-mod-ale-profession-xp.patch` | `modules/mod-ale` | Yes; apply after `05` |

Example:

```bash
cd /path/to/azerothcore
git apply /path/to/Paragon-Anniversary/patches/01-core-paragon.patch
git apply /path/to/Paragon-Anniversary/patches/02-core-profession-xp.patch
git apply /path/to/Paragon-Anniversary/patches/04-core-docker-build-jobs.patch

cd modules/mod-ale
git apply /path/to/Paragon-Anniversary/patches/05-mod-ale.patch
git apply /path/to/Paragon-Anniversary/patches/07-mod-ale-profession-xp.patch
```

Keep the core patches in exact `01` → `02` → `04` order and the ALE patches in
exact `05` → `07` order. The profession layers depend on the preceding base
patches and must not be folded into a different sequence.

Build the core only after all applicable patches and modules are present. For
Docker, pass a conservative build width when needed, for example
`CBUILD_JOBS=4` through the compose build arguments.

## 3. Initialize AzerothCore and Paragon databases

Complete AzerothCore's normal auth/characters/world/playerbots import first.
The Paragon generators query populated world tables and cannot run against an
empty `acore_world` database.

The normal `tools/install.py --apply` pipeline runs the database bootstrap
automatically by streaming each component into `ac-database` in order. For a
database-only recovery with a host MySQL/MariaDB client, run from the Paragon
repository root:

```bash
mysql [connection options] < sql/install.sql
```

Run the direct form from the repository root: the mysql client resolves its
`SOURCE` paths relative to its working directory. The entrypoint executes
`01_create_database.sql` through `05_apply_anniversary_config.sql` in their
required order and is safe to rerun for an upgrade. `05` intentionally
reapplies the canonical Anniversary realm settings and raises existing
profession high-water marks without awarding retroactive XP.

There is no required `sql/06` file. `02_create_tables.sql` remains the single
authoritative schema and creates all 21 base and Anniversary tables, including
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

## 4. Generate profession data and install server Lua

The populated `acore_world` database and the exact DBC set used by the rebuilt
worldserver are both valuation inputs. The canonical installer generates and
verifies profession data **before** replacing the Lua package so the deployed
resolver cannot be stale. The commands below show that internal step for
diagnosis or a component-only repair.

On a fresh Docker installation, first populate AzerothCore's client-data volume
and create the rebuilt worldserver container without starting it. The generator
uses `docker cp`, which works with this stopped container but cannot address a
container that does not exist:

```bash
cd /path/to/azerothcore
docker compose up --no-deps ac-client-data-init
docker compose create --no-deps ac-worldserver
docker container inspect ac-worldserver >/dev/null
cd /path/to/Paragon-Anniversary
```

The client-data initializer must exit successfully. `docker compose create`
does not start the worldserver, so the new binary still cannot load incomplete
SQL or Lua during this step. Existing installations may reuse their stopped or
running `ac-worldserver` container for generation.

```bash
python tools/gen_profession_xp.py \
    --dbc-container ac-worldserver --database-container ac-database
python tools/gen_profession_xp.py \
    --dbc-container ac-worldserver --database-container ac-database --check
```

For DBCs stored on the host, replace `--dbc-container` with
`--dbc-dir /path/to/server/data/dbc`; the populated world database must still
be available through `--database-container`. The first command rewrites
`serverside/paragon/modules/paragon_profession_data.lua` and its audit; the
second must exit successfully. If the server Lua was copied earlier, copy the
regenerated package again. See
[`tools/PROFESSION_XP_GENERATOR.md`](../tools/PROFESSION_XP_GENERATOR.md) for the
data model, overrides, caps, and audit contract.

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

The canonical installer performs this section in order. For a component-only
repair, keep `ac-database` running and the worldserver stopped, then run from
the Paragon-Anniversary repository root:

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

- regenerates `sql/content/01_paragon_content.sql`;
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

The canonical installer runs both commands after the base schema and content
exist. For a component-only repair, run:

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

The remaining `gen_*` scripts are content-maintenance tools. Profession XP is
the exception: section 4 deliberately regenerates it from this installation's
populated world database and active DBCs before server Lua is copied.

## 7. Install the client addon and art

The client UI is complete. It consists of a normal 27-file addon plus 14 BLP
art files. The addon must not be packed into an MPQ.

The canonical installer replaces the addon directory and verifies it
byte-for-byte. For a component-only repair, copy it to the WoW client:

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
SELECT value AS profession_xp_per_point
FROM acore_ale.paragon_config
WHERE field = 'UNIVERSAL_SKILL_EXPERIENCE';
SELECT COUNT(*) AS custom_spells
FROM acore_world.spell_dbc
WHERE ID >= 1900000 AND ID < 2000000;
SELECT ID, Name_Lang_enUS, Mask_ID
FROM acore_world.chartitles_dbc
WHERE ID IN (200, 201)
ORDER BY ID;
SELECT COUNT(*) AS solo_achievements, SUM(Points) AS solo_points
FROM acore_world.achievement_dbc
WHERE ID BETWEEN 19000 AND 19304;
SELECT COUNT(*) FROM acore_ale.paragon_collectible_spell_xp;
SELECT COUNT(*) FROM acore_ale.paragon_config_experience_quest;
SELECT owner_type, COUNT(*) AS profession_rows, SUM(pending_xp) AS pending_xp
FROM acore_ale.paragon_profession_progress
GROUP BY owner_type;
```

On the authoritative `main` branch, the custom-spell coverage audit reports 743
client-generated records plus 21 deliberately server-only records. Both title
rows must be present on a fresh host. The solo-achievement query must report
96 rows and 1,045 total points; Paragon reads those authoritative world rows
for custom achievement XP. `profession_xp_per_point` must report `1000`.

At login, the console must not report `SetData`/`GetData` errors. A level-80
character should earn exactly 1000 Paragon XP for each genuinely new profession
high-water point, unaffected by personal XP bonuses; weapon and riding skill-ups
earn none. A point earned below level 80 should be recorded as pending and paid
once at eligibility, while existing skills on upgrade must only seed their
high-water marks. Successful craft/gather/process actions use the generated
resource valuation and do receive the normal personal XP modifier exactly once.
The first Paragon level requires 30,000 XP with the Anniversary preset.

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

### MySQL connection failure

Confirm the container is named `ac-database`, its standard
`MYSQL_ROOT_PASSWORD` environment is present, and the AzerothCore world import
completed. The generators read that credential only inside the container and
query the populated world schema through `docker exec`.

### A DBC cannot be found

Set `PARAGON_CLIENT_DATA` to the client's actual `Data` directory and confirm
the enUS locale archives exist below `Data/enUS`. Delete only the regenerable
`tools/cache` directory if it contains extracts from a different client.

### Archive overwrite refused

The target name belongs to an unmarked or third-party MPQ. Do not delete it
blindly. Inspect/move it or select a free one-character patch suffix, then run
the generator and collision checker with matching names.

### Server boot-loops on missing Paragon tables

Reapply `sql/install.sql` from the repository root. Do not rely on Lua to
create the schema.

## Updating an existing installation

Back up the three AzerothCore databases plus `acore_ale`, update Paragon's
`main` branch and the pinned patches together, and restore `mod-transmog` to
`31633595cad7b12042b6484ffe3ea34f355b9821`.

Use this order for an in-place Docker upgrade:

1. Back up `acore_auth`, `acore_characters`, `acore_world`, `acore_playerbots`,
   and `acore_ale`, then update the pinned repositories/patches and rebuild the
   worldserver/authserver images.
2. Keep `ac-database` running, stop `ac-worldserver`, and fully close the game
   client. The installer deliberately refuses `--apply` while the worldserver
   is running, preventing an ALE auto-reload of a partially replaced payload.
3. Run the same canonical command used for a fresh installation:

   ```bash
   cd /path/to/Paragon-Anniversary
   python tools/install.py --apply \
       --core-root /path/to/azerothcore \
       --client-root /path/to/WowWotlk
   python tools/install.py --check \
       --core-root /path/to/azerothcore \
       --client-root /path/to/WowWotlk
   ```

   `--apply` reruns every SQL migration, regenerates data from the upgraded
   world/DBC inputs, replaces the complete Paragon and addon trees atomically,
   replaces the complete ALE extension set, rebuilds owned archives, and verifies
   database/payload invariants. The schema bootstrap preserves custom
   category/statistic rows, while generator-owned tables are refreshed from
   their authoritative inputs.
4. Force-recreate the rebuilt servers, inspect the worldserver boot log for a
   successful profession-module load with no schema/event/Lua error, then fully
   restart the client.

For implementation details and hard-won compatibility notes, see
[`doc/CORE_PATCHES.md`](CORE_PATCHES.md).
