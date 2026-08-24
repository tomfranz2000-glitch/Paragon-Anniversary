# Profession XP data generator

Run from the repository root while the test-server containers are running:

```powershell
python tools\gen_profession_xp.py --dbc-container ac-worldserver --database-container ac-database
python tools\gen_profession_xp.py --dbc-container ac-worldserver --database-container ac-database --check
```

The first command copies the active DBCs from
`/azerothcore/env/dist/data/dbc`, reads `acore_world` through the database
container, and writes:

- `serverside/paragon/modules/paragon_profession_data.lua`
- `tools/generated/profession_xp_audit.json`

`--check` performs the same live snapshot and fails if either artifact is stale.
For an unpacked server data directory on the host, replace `--dbc-container`
with `--dbc-dir <path-to-dbc-directory>`; the populated world database must
still be available through `--database-container`. Container/database names,
DBC root, output paths, override path, and the default 5,000 base-XP cap all
have explicit CLI options; run `python tools\gen_profession_xp.py --help` for
the full list.

The starting model uses tier weights `1/1.5/2/2.5/3/4`, craft multiplier `10`,
gather multiplier `50`, and processing multiplier `5`. Loot groups and
references are resolved as expected values. Recursive intermediates use the
cheapest bounded acyclic route; vendor-only inputs use a conservative,
price-bounded 25%-50% fraction of intrinsic tier value. Every reachable action
receives positive XP except the two deliberately excluded, lossless,
no-cooldown shard conversions (`craft:28022` and `craft:42615`). Cyclic recipes
with genuinely consumed external reagents ignore only the returned SCC member
and value those external materials; this keeps rechargeable tools such as
`craft:13240` rewarding without making the returned tool an XP source.
Vendor-reagent and otherwise zero-cost reachable paths use bounded fallback
values instead of becoming silent zero-XP actions; time-gated cycles use an
audited intrinsic-value fallback. Every successful gather/fishing action also
uses its profession-tier weight as a minimum material value before bounded
spawn scarcity; the raw expected material value and whether the floor applied
are retained in the audit. Scarcity, cooldown, per-unit quantity, and base XP
are bounded. The audit retains raw and awarded statistics and must report zero
silent gaps.
The generated Lua publishes the same returned table through the legacy
`ParagonProfessionData` global and
`package.loaded["paragon.modules.paragon_profession_data"]`, so ALE basename
auto-execution and dotted `require` do not allocate a second dataset.

## Overrides

`tools/profession_xp_overrides.json` is intentionally empty by default. Item
keys are decimal item IDs; action keys are `<kind>:<contextId>`, where `kind` is
one of `craft`, `gather_gameobject`, `gather_creature`, `fishing_area`,
`fishing_hole`, `prospect`, `mill`, or `disenchant`.

```json
{
  "version": 1,
  "items": {
    "12345": { "value": 3.0 }
  },
  "actions": {
    "craft:67890": { "xp": 250, "skill": 164, "tier": 5 },
    "gather_gameobject:111": { "exclude": "script-only test node" },
    "fishing_area:222": { "per_unit": true }
  }
}
```

An override targeting an undiscovered action, a non-positive XP override, a
duplicate context, or any action with neither positive XP nor an exclusion
reason fails generation.
