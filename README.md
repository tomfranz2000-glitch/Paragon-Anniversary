> [!NOTE]
> **Paragon Anniversary** server and client features are complete. The project
> remains in stabilization while the complete installation path is reproduced
> on fresh hosts.

___

<div align="center">

<img width="292" height="298" alt="Paragon_AI_Logo" src="https://github.com/user-attachments/assets/27482a85-186e-401a-b493-29622ce739b4" />
</div>

<div align="center">
  
# ⚡ Paragon System
### *for AzerothCore*

<img src="https://img.shields.io/badge/AzerothCore-3.3.5a-blue?style=for-the-badge&logo=world-of-warcraft" alt="AzerothCore Badge">
<img src="https://img.shields.io/badge/Language-Lua-purple?style=for-the-badge&logo=lua" alt="Lua Badge">
<img src="https://img.shields.io/badge/Engine-ALE-orange?style=for-the-badge" alt="ALE Badge">

*Endless progression system - Continue growing beyond max level*

</div>

---

## 📊 Status

| Component | Status | Notes |
|-----------|--------|-------|
| **Serverside** | ⚙️ **Beta** | All core features complete, dual-mode system fully implemented, stabilization in progress |
| **Clientside** | ✅ **Feature complete** | Full 27-file addon, 14 UI-art assets, and generated DBC archives |
| **Documentation** | 🛠️ **Stabilizing** | Main-branch install and implementation guides |

---

## ⚠️ Platform Availability

> [!IMPORTANT]
> **The Paragon System is currently available exclusively for AzerothCore (3.3.5a).**
>
> We are focusing on ensuring all functionality is stable and bug-free on AzerothCore before expanding to other platforms. This allows us to provide a reliable and well-tested experience.
>
> **Future Plans:**
> - 📅 After stabilization on AzerothCore, a port to **ElunaTrinityWotlk** is planned
> - 🔄 Additional emulator support may follow based on community demand
>
> If you're using a different emulator and interested in compatibility, please open an issue on the project repository.

---

## 🌟 What's This?

The **Paragon System** introduces an endgame progression mechanic for AzerothCore servers. After reaching max level, players continue to earn **paragon experience** and unlock **stat bonuses** through a point-based talent system.

### ✨ Key Features

- **📊 Paragon Levels**: Unlimited progression beyond max level
- **⚡ Stat Bonuses**: Invest points in Combat Ratings, Stats, and Special Auras
- **🎯 Three Categories**:
  - **Combat**: Hit, Crit, Haste, Expertise, Armor Penetration
  - **Stats**: Strength, Agility, Stamina, Resistances, HP/Mana
  - **Auras**: Loot, Reputation, and Experience bonuses
- **🎮 Multi-Source Experience**: Gain paragon XP from creatures, achievements, quests, profession actions, and profession skill mastery
- **💰 Point System**: Earn points to distribute among available statistics
- **🔄 Client Integration**: In-game interface via custom addon
- **💾 Persistent**: All progress saved to database

---

## 🎬 Preview

<div align="center">

[![Watch Paragon Anniversary Demo](https://img.youtube.com/vi/JEyiI8Y-l8M/maxresdefault.jpg)](https://www.youtube.com/watch?v=6ZtVBOo93YI)

**Click to watch the Paragon Anniversary demo on YouTube** 🎥

</div>

---

## 🏗️ Architecture

<table>
<tr>
<td width="50%">

### 📦 **Core Components**

- `paragon_constant.lua` - Constants & SQL queries
- `paragon_repository.lua` - Database access layer (Singleton)
- `paragon_config.lua` - Configuration service (Singleton)
- `paragon_class.lua` - Paragon business logic & state
- `paragon_hook.lua` - Event handlers & client communication

### 🧩 **Module System**

- `modules/paragon_anniversary.lua` - Experience & level-up mechanics
- `modules/paragon_profession_xp.lua` - Profession actions, skill high-water marks, and pre-80 banks
- `modules/paragon_profession_data.lua` - Generated craft/gather/process valuations
- `modules/paragon_pvp_xp.lua` - Durable honor, battleground, arena, outdoor, and duel rewards
- Extensible via Mediator pattern for custom features

</td>
<td width="50%">

### 🗄️ **Database**

**Configuration Tables:**
- `paragon_config_category` - Stat categories
- `paragon_config_statistic` - Available stats
- `paragon_config` - General settings (key-value pairs)
- `paragon_config_experience_*` - Experience rewards by source

**Character Data (Character-Linked Mode):**
- `character_paragon` - Player levels & XP per character
- `character_paragon_stats` - Invested points per character

**Account Data (Account-Linked Mode):**
- `account_paragon` - Account-wide levels & XP
- `character_paragon_stats` - Stats always per character

**Profession Progress:**
- `paragon_profession_progress` - Durable per-scope skill high-water marks and pending XP

**PvP Merit:**
- `paragon_pvp_reward_claim` - Account-wide idempotency, DR, cap, breadth, and pending-payout ledger

</td>
</tr>
</table>

### 🔄 **Dual-Mode System**

Configure `LEVEL_LINKED_TO_ACCOUNT` in `paragon_config`:
- **`0`**: Character-linked - Each character has independent progression
- **`1` (Anniversary default)**: Account-linked - All characters on account share level/XP but have separate stat investments

---

## 🚀 Quick Installation

### Authoritative source

The sole authoritative install branch is **`main`**. Clone it explicitly so a
fresh installation cannot silently select a historical or development branch:

```bash
git clone --branch main --single-branch \
    https://github.com/tomfranz2000-glitch/Paragon-Anniversary.git
cd Paragon-Anniversary
```

Paragon's appearance rewards require the suite's `mod-transmog` fork. Clone
its authoritative `master` branch under the AzerothCore `modules` directory,
then detach at the tested commit:

```bash
cd /path/to/azerothcore
git clone --branch master --single-branch \
    https://github.com/tomfranz2000-glitch/mod-transmog.git \
    modules/mod-transmog
git -C modules/mod-transmog checkout --detach \
    31633595cad7b12042b6484ffe3ea34f355b9821
```

That exact revision supplies the required `StoreNewItem` appearance capture
and the server's transmog configuration defaults. Do not substitute upstream
`azerothcore/mod-transmog` or an unpinned fork revision.

### Required order

1. 🧩 Install the pinned modules and apply every applicable patch.
2. 🏗️ Build the core and complete AzerothCore's normal database import.
3. 📦 Install the pinned Python dependencies with `python -m pip install -r requirements.txt`.
4. ⏹️ Stop the worldserver, then run the complete, rerunnable installer from
   this repository's root. Database credentials are read only inside the
   `ac-database` container:

   ```bash
   python tools/install.py --apply \
       --core-root /path/to/azerothcore \
       --client-root /path/to/WowWotlk
   ```

5. ✅ Run the same command with `--check`, restart the worldserver, and fully
   restart the client.

The installer applies `sql/install.sql`, runs every required data generator,
deploys server Lua/ALE extensions and the addon, builds all client archives,
seeds existing collections safely, and verifies the result. Use `--dry-run` to
print its exact ordered plan without reading secrets or changing external state.
`--check` regenerates all three MPQs in private temporary storage and exactly
compares the generator-owned database rows without persistent database writes.
Both `--apply` and `--check` also reject an incomplete patched C++ source tree,
including missing ALE enabled-hook registrations and incomplete PvP Merit
events 77–81. This validates the selected checkout; rebuild the server after
patching because it does not establish which source produced an existing image.

### Versioned instance-XP upgrade

Servers on Paragon commit `05ea122dc80b6a08ba01a6f0506523a13cdbe1c2`
can use the focused `instance-xp-v1` release package instead of rerunning the
full client-aware installer. It patches the already-installed ALE source,
builds and force-recreates only worldserver, migrates only the five new
instance-XP settings, validates and atomically replaces only the changed
server Lua module, and keeps durable rollback material. Other generated or
locally customized Lua files are preserved. It never accepts or writes a
client path.

Package operators should follow
[`upgrades/instance-xp-v1/README.md`](upgrades/instance-xp-v1/README.md).
Maintainers produce the deterministic ZIP only from a clean, published `main`:

```bash
python tools/build_instance_xp_upgrade.py --ref <release-tag> --output-dir dist
```

The builder reads the single deployable Lua file and native payloads from the
resolved Git commit rather than the checkout. It rejects any baseline-to-target
runtime change outside the focal Lua, patch 05, and the two configuration SQL
files because the focused installer would not deploy it. Template and archive
contents use exact allowlists, with client assets and secret-like paths
refused. The resulting uncompressed ZIP is byte-identical across supported
hosts and includes full release metadata and internal per-file SHA-256
checksums. An adjacent `<archive>.zip.sha256` verifies the complete ZIP before
extraction; publish the ZIP and its sidecar together.

The cumulative ALE patches are included only as checksummed dependency
provenance. This focused package applies its separate incremental ALE delta to
an existing `05ea122` installation; it is not a fresh-install kit, and it never
reapplies a cumulative patch.

To validate the repository without deploying, install `requirements.txt` and
run `python -m unittest discover -s tools -p "test_*.py"`. The complete
installer runs this same suite before it changes external state.

> [!IMPORTANT]
> Clone `https://github.com/azerothcore/mod-eluna.git` with the explicit target
> `modules/mod-ale`. The core identifies ALE by that directory name; a default
> clone into `modules/mod-eluna` fails later while compiling `LuaEngine` because
> the Lua headers and library were not configured.
>
> Installing mod-ale is not enough in Docker: its runtime image omits the ALE
> extension files. Copy them into the bind-mounted script path explicitly; see
> the [detailed installation guide](doc/INSTALL.md).

### 📖 Detailed Installation Guide

For complete installation instructions including:
- ✅ Prerequisites and dependencies
- ✅ Step-by-step server setup
- ✅ Database configuration
- ✅ Client-side addon installation
- ✅ Testing and troubleshooting

**👉 [Read the Full Installation Guide](doc/INSTALL.md)**

---

## ⚙️ Configuration

Configure the system via database entries in `paragon_config`:

### System Control

| Field | Description | Default |
|-------|-------------|---------|
| `ENABLE_PARAGON_SYSTEM` | Enable/disable the entire system | `1` |
| `LEVEL_LINKED_TO_ACCOUNT` | Character-linked (0) vs Account-linked (1) mode | `1` |
| `PARAGON_LEVEL_CAP` | Maximum paragon level (0 = unlimited) | `10000` |
| `MINIMUM_LEVEL_FOR_PARAGON_XP` | Minimum character level to earn paragon XP | `80` |
| `LEVEL_UP_ANIMATION` | Spell visual played on a Paragon level-up | `64785` |

With `ENABLE_PARAGON_SYSTEM=0`, no source awards XP and new profession skill
gains are not added to the pre-level bank. Pending XP earned before disabling
remains durable and can be paid after the system is enabled again.

### Progression Settings

| Field | Description | Default |
|-------|-------------|---------|
| `BASE_MAX_EXPERIENCE` | XP required for the first Paragon level | `30000` |
| `POINTS_PER_LEVEL` | Points awarded per paragon level | `1` |
| `PARAGON_STARTING_LEVEL` | Starting paragon level for new characters | `1` |
| `PARAGON_STARTING_EXPERIENCE` | Starting experience value | `0` |
| `PARAGON_CURVE_R0` | Initial growth rate for the decaying XP curve | `0.0429` |
| `PARAGON_CURVE_K` | Decay constant for the XP curve | `20` |

### Experience Rewards

| Field | Description | Default |
|-------|-------------|---------|
| `UNIVERSAL_CREATURE_EXPERIENCE` | Default XP for creature kills | `50` |
| `UNIVERSAL_ACHIEVEVEMENT_EXPERIENCE` | Default XP for achievements | `100` |
| `UNIVERSAL_SKILL_EXPERIENCE` | Exact XP per new profession high-water point | `2000` |
| `UNIVERSAL_QUEST_EXPERIENCE` | Fallback XP for quest completion | `1` |
| `PARAGON_ACHIEVEMENT_POINT_XP` | Exact XP awarded per achievement point | `2000` |
| `PARAGON_GROUP_XP_DISTANCE` | Maximum distance for party kill-XP sharing | `74` |
| `PARAGON_CREATURE_XP_TBC_HEROIC_DUNGEON_MULTIPLIER` | TBC heroic-dungeon monster XP factor | `1.25` |
| `PARAGON_CREATURE_XP_WOTLK_HEROIC_DUNGEON_MULTIPLIER` | WotLK heroic-dungeon monster XP factor | `1.5` |
| `PARAGON_CREATURE_XP_TBC_RAID_MULTIPLIER` | TBC raid monster XP factor | `2` |
| `PARAGON_CREATURE_XP_WOTLK_NORMAL_RAID_MULTIPLIER` | WotLK normal-raid monster XP factor | `2.5` |
| `PARAGON_CREATURE_XP_WOTLK_HEROIC_RAID_MULTIPLIER` | WotLK heroic-raid monster XP factor | `4` |

Instance factors scale the creature's native at-level XP pool before the gray
penalty and group share. They do not affect quests, PvP, professions, or
one-time rewards. TBC raids use one `2×` rule because TBC has no heroic raid
difficulty.

One-time rewards are stored at their final values: a profession high-water
point and an ordinary appearance pay 2,000 XP, a 10-point achievement pays
20,000 XP, and a baseline mount pays 160,000 XP. The collection generator
stores every pet, mount, ordinary appearance, and rarity override at its final
doubled value.
These rewards remain outside every personal Paragon XP modifier; quests and
repeatable craft/gather/process rewards are unchanged.

### Experience Multipliers

| Field | Description | Default |
|-------|-------------|---------|
| `EXPERIENCE_MULTIPLIER_LOW_LEVEL` | Multiplier for low-level paragons | `1` |
| `EXPERIENCE_MULTIPLIER_HIGH_LEVEL` | Multiplier for high-level paragons | `1` |
| `LOW_LEVEL_THRESHOLD` | Paragon level below which bonus applies | `5` |
| `HIGH_LEVEL_THRESHOLD` | Paragon level above which penalty applies | `100` |

### Other Settings

| Field | Description | Default |
|-------|-------------|---------|
| `DEFAULT_STAT_LIMIT` | Maximum points per individual stat (1-255) | `255` |

### Adding Custom Stats

1. Add categories to `paragon_config_category`
2. Define statistics in `paragon_config_statistic`
3. Configure `type`, `factor`, and `limit` for each stat

**Stat Configuration Fields:**
- `type`: `AURA`, `COMBAT_RATING`, or `UNIT_MODS`
- `type_value`: The symbolic key from `paragon_constant.lua`
- `factor`: Multiplier for each point invested
- `limit`: Maximum points that can be invested (max 255)
- `application`: How the stat bonus is applied

The canonical database bootstrap supplies four categories and all 17
runtime-supported statistics. `sql/11-13-2026_Example_Data.sql` is a destructive
historical dump; never load it during installation or upgrade.

---

## 🎮 Stat Types

<table>
<tr>
<td width="33%">

### ⚔️ **Combat Rating**
- Weapon Skill
- Defense / Dodge / Parry / Block
- Hit (Melee/Ranged/Spell)
- Crit (Melee/Ranged/Spell)
- Haste (Melee/Ranged/Spell)
- Expertise
- Armor Penetration

</td>
<td width="33%">

### 💪 **Unit Modifiers**
- Primary Stats (Str/Agi/Sta/Int/Spi)
- Resources (HP/Mana/Rage/Energy/etc)
- Armor & Resistances
- Attack Power
- Damage (Mainhand/Offhand/Ranged)

</td>
<td width="33%">

### ✨ **Aura Bonuses**
- Loot Bonus (1900000)
- Reputation Gain (1900001)
- Experience Gain (1900002)

*Custom aura IDs: 1900000+*

</td>
</tr>
</table>

---

## 🔧 Technical Overview

### Architecture
- **Singleton Pattern**: Config and Repository services
- **Repository Pattern**: Database abstraction layer
- **Mediator Pattern**: Event-driven extensibility
- **Object-Oriented**: Using classic.lua library

### Key Features
- **Async Database**: Non-blocking queries
- **Manual Migrations**: SQL files in `sql/` directory
- **Client Communication**: Custom addon protocol (`ParagonAnniversary`)
- **Extensible**: Module system via Mediator events

**📖 Detailed Technical Documentation**:
- [HOOKS.md](doc/HOOKS.md) - Complete Mediator event system
- [MODULES.md](doc/MODULES.md) - Creating custom modules
- [LIBRARIES.md](doc/LIBRARIES.md) - Library documentation
- [PVP_MERIT.md](doc/PVP_MERIT.md) - PvP economy, bridge, eligibility, DR, caps, and durability

---

## 📚 Documentation

Complete documentation is available in the `doc/` directory:

| Document | Description |
|----------|-------------|
| **[INSTALL.md](doc/INSTALL.md)** | Complete installation guide with SQL setup |
| **[HOOKS.md](doc/HOOKS.md)** | Mediator event system reference |
| **[MODULES.md](doc/MODULES.md)** | Creating custom modules |
| **[LIBRARIES.md](doc/LIBRARIES.md)** | Classic, CSMH, and Mediator libraries |
| **[PVP_MERIT.md](doc/PVP_MERIT.md)** | PvP Merit values and integration contract |

All code includes **LuaDoc** comments for inline documentation.

---

## 📊 Compatibility

### Emulator Support

| Emulator | Version | Status | Notes |
|----------|---------|--------|-------|
| 🎮 **AzerothCore** | 3.3.5a | ✅ **Supported** | Primary development platform |
| 🌙 **ElunaTrinityWotlk** | 3.3.5a | 📅 **Planned** | Port scheduled after AzerothCore stabilization |

### Required Dependencies

| Component | Version | Status |
|-----------|---------|--------|
| 🔧 **ALE** | [`9e5b8c66efeb383871ec58b925e47094c92cc8d5`](patches/PINS.md) | ✅ **Required** |
| 📚 **Classic** | Any | ✅ **Required** |
| 🔌 **CSMH** | Any | ✅ **Required** |

---

## 📁 Project Structure

```
paragon/
├── lib/
│   ├── classic/
│   │   └── classic.ext             # OOP library
│   ├── Mediator/
│   │   └── mediator.lua            # Event system
│   └── CSMH/
│       └── SMH.ext
├── modules/
│   ├── paragon_anniversary.lua     # Experience & level-up mechanics
│   ├── paragon_profession_xp.lua   # Profession actions and mastery ledger
│   ├── paragon_profession_data.lua # Generated profession valuations
│   ├── paragon_pvp_xp.lua          # Durable PvP Merit rewards and ledgers
│   └── README.md                   # Module documentation
├── paragon_constant.lua            # Constants, SQL queries, stat enums
├── paragon_repository.lua          # Database access layer (Singleton)
├── paragon_config.lua              # Configuration service (Singleton)
├── paragon_class.lua               # Paragon entity & business logic
├── paragon_hook.lua                # Event handlers & entry point
└── README.md                       # This file

doc/
├── INSTALL.md                      # Installation guide
├── HOOKS.md                        # Complete hook documentation
├── MODULES.md                      # Module development guide
├── LIBRARIES.md                    # Libraries documentation (Classic, CSMH, Mediator)
└── PVP_MERIT.md                    # PvP economy and bridge contract

sql/
├── install.sql                     # Canonical fresh-install/upgrade entrypoint
├── 01_create_database.sql          # Database creation
├── 02_create_tables.sql            # Complete table schema
├── 03_create_triggers.sql          # Validation triggers
├── 04_insert_default_config.sql    # Default configuration
├── 05_apply_anniversary_config.sql # Anniversary realm configuration
├── 11-13-2026_Example_Data.sql     # Destructive historical reference only
└── README.md                       # SQL installation guide
```

---

## 🔄 Data Flow

```
Player Login
    ↓
Hook.OnPlayerLogin (paragon_hook.lua)
    ↓
Create Paragon Instance (paragon_class.lua)
    ↓
Load Level & Statistics from DB (paragon_repository.lua)
    ↓
Callback: Hook.OnPlayerStatLoad
    ↓
Apply Statistics to Player & Send Data to Client (ParagonAnniversary addon)
```

---

## 🎯 Recent Improvements

### Latest Features (Latest Release)
- ✅ **Dual-Mode System**: Character-linked and account-linked paragon progression
- ✅ **Mediator Pattern Integration**: Extensible event system for custom modules
- ✅ **Module System**: Modular business logic via `paragon_anniversary.lua`
- ✅ **Robust Error Handling**: Fallback defaults for all configuration values
- ✅ **Complete Documentation**: HOOKS.md with all Mediator events documented
- ✅ **Advanced Routing**: Runtime table selection based on LEVEL_LINKED_TO_ACCOUNT

### Architecture Highlights
- **Singleton Pattern**: Config and Repository are single instances
- **Repository Pattern**: Clean database abstraction layer
- **Mediator Pattern**: Decoupled event-driven architecture
- **Object-Oriented Design**: Using classic.lua for OOP

---

## 🏆 Credits

- 🔧 **Development**: Custom system for AzerothCore
- 🎨 **Concept**: Inspired by Diablo 3 Paragon systems
- 🙏 **Thanks**: AzerothCore & ALE communities

---

<div align="center">

### ⚡ **Ready to add endless progression?**

*Stable serverside system ready for production use on AzerothCore with ALE*

</div>
