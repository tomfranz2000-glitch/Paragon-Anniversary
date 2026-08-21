# mpq-backup

## patch-4.MPQ is not a backup you can delete

It holds the source art for `patch-W.MPQ` -- 14 BLP textures, 13 of which the
Paragon addon references by name and nothing else does. **No generator can
rebuild it.** Verified to contain zero Blizzard DBC files, so it is ours to
carry.

## What used to be here, and why it is gone

`patch-5.MPQ` and `patch-enUS-5.MPQ` were the legacy DBC archives, superseded
by the `patch-X` naming. Both are removed because they are:

- **regenerable** -- `tools/paragon_client_patch.py` rebuilds their contents
  from your own client extraction, and
- **not ours to redistribute** -- probing their hash tables shows 3 and 8
  whole copies of Blizzard DBC files respectively (`Spell.dbc`, `Talent.dbc`,
  `Achievement*.dbc`, `CharTitles.dbc`, `SkillLineAbility.dbc`,
  `SkillRaceClassInfo.dbc`).

The same reasoning is why `*.MPQ` is gitignored everywhere except this
directory.
