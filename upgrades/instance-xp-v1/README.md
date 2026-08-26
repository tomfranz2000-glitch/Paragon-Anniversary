# Paragon instance-XP v1 server upgrade

This package upgrades a server running the known prior Paragon release
`05ea122dc80b6a08ba01a6f0506523a13cdbe1c2`. It adds expansion-aware heroic
dungeon and raid creature XP and the compiled ALE `Map:GetExpansion()` method.
It does not read or write a WoW client.

The release archive is self-contained. Keep its layout intact:

```text
install.py
manifest.json
RELEASE.json
SHA256SUMS
native/mod-ale-instance-xp.patch
sql/instance-xp.sql
server/serverside/paragon/modules/paragon_rework_sources.lua
patches/05-mod-ale.patch
patches/07-mod-ale-profession-xp.patch
patches/09-mod-ale-pvp-merit.patch
```

The release also ships an external `<archive>.zip.sha256` sidecar. Download it
beside the ZIP and verify the complete archive before extraction. On systems
with GNU `sha256sum`, run:

```bash
sha256sum -c Paragon-Anniversary-upgrade-*.zip.sha256
```

The sidecar uses the standard `<digest>  <filename>` format. On PowerShell,
compare `Get-FileHash <archive>.zip -Algorithm SHA256` with the first value in
`Get-Content <archive>.zip.sha256`. `SHA256SUMS` inside the verified archive
separately covers every packaged file. ZIP entries are deliberately stored
without compression so identical committed inputs produce byte-identical
archives across supported hosts.

Python 3.10+, Git, Docker, and Docker Compose v2 with `config --hash` support
are required. The database container must be running. The existing worldserver
container may be running; the candidate image is deliberately built before
downtime begins.

## Plan first

Neither `--plan` nor the default mode writes anything:

```powershell
python install.py `
  --core-root "C:\path\to\azerothcore-wotlk" `
  --lua-root "C:\path\to\ALE\lua_scripts" `
  --compose-project azerothcore-test
```

The equivalent typical Linux command is:

```bash
python3 install.py \
  --core-root /srv/azerothcore-wotlk \
  --lua-root /srv/azerothcore-wotlk/env/dist/etc/lua_scripts \
  --compose-project azerothcore-test
```

`--lua-root` is the configured `ALE.ScriptPath`; the installer replaces only
its `paragon/modules/paragon_rework_sources.lua` child. If Compose is not using
its default files, repeat
`--compose-file` in the same order used to operate the server. Container and
service names can be changed with `--database-container`,
`--worldserver-container`, and `--compose-service`. Repeat `--env-file` in the
same order used when the containers were created. Without explicit
`--env-file`, an existing `<core-root>/.env` is automatically pinned and passed
to Compose.

The package contains only the Lua file this focused upgrade deploys, not the
unrelated full Paragon Lua tree. The plan verifies every entry in `SHA256SUMS`,
the immutable focal Lua payload, dependency-reference patches, database engine
and current values, Compose ownership, each Compose environment file, the
rendered configuration, the service configuration hash, and native source
state. The rendered
`docker compose config --hash SERVICE` must equal the live container's
`com.docker.compose.config-hash`. `RELEASE.json` carries both the raw LF
Git-blob SHA-256 and the exact Windows CRLF checkout SHA-256 for the supported
baseline and target `modules/paragon_rework_sources.lua`. The installed focal
file must match one of those four hashes; an unknown or locally modified focal
file is never overwritten. Deployment always writes the exact target LF bytes.
Native state must be one of:

- `prior`: all known prior ALE bridges exist and `Map:GetExpansion()` is absent;
- `target`: the exact method and registration already exist (a safe rerun);
- `partial`: only part of either contract exists; execution is refused;
- `unknown`: not the supported prior or target; this focused package refuses it.

For an unknown state or a fresh installation, use the full Paragon repository's
canonical installer and complete core/ALE patch set. The cumulative ALE patches
inside this archive are provenance references, not an alternate install path.

This classification is semantic. It does not need the Paragon repository's Git
history and does not reset, stash, or discard local work.

## Apply

Run the identical command with `--apply`:

```powershell
python install.py --apply `
  --core-root "C:\path\to\azerothcore-wotlk" `
  --lua-root "C:\path\to\ALE\lua_scripts" `
  --compose-project azerothcore-test
```

On Linux, add `--apply` to the plan command shown above.

The installer:

1. acquires an exclusive lock, stages hash-verified read-only copies of the
   focal Lua, incremental patch, and SQL, and creates a durable journal/native
   backup; subsequent phases consume only those staged bytes;
2. applies the audited two-file ALE delta when needed;
3. tags the old image and builds the candidate while the old server runs;
4. stops worldserver, dumps `acore_ale`, refreshes the rollback configuration
   snapshot, and backs up the one installed Lua file changed by this release;
5. applies only the five configuration rows in one transaction and atomically
   replaces only `modules/paragon_rework_sources.lua`, preserving every other
   installed, generated, and operator-owned Lua file plus the focal file's safe
   original mode and ownership; symlinks, special files, and group/world-
   writable POSIX focal files are refused;
6. force-recreates worldserver and verifies its image ID, readiness log, native
   registration string, boot errors, database values, and exact focal-file hash.

The ready log pattern is mandatory even when Docker reports a healthy
container. If worldserver was stopped before the upgrade, the candidate is
temporarily started for verification and stopped again afterward.

Backups default to
`<core-root-parent>/paragon-upgrade-backups/instance-xp-v1/<run-id>`. Keeping
database dumps outside the core avoids inflating Docker's build context. The old Docker
image receives a durable backup tag so an ordinary tag-moving build does not
destroy the rollback target. Override the parent with `--backup-root`.

If any operation fails (including Ctrl+C), the installer stops the candidate
and restores the old image, focal Lua file, stopped-server five-row
configuration snapshot, and native source files,
then restarts the old worldserver if it was originally running. It retains the
full database dump and journal.

Compose-input drift during rollback never blocks restoration of Lua, database,
native source, or the old image. If the exact existing container still uses the
recorded old image, rollback starts it directly without reinterpreting drifted
Compose inputs. Only when that container is absent or uses the wrong image is a
previously running server left stopped and the journal records
`rollback-state-restored-compose-blocked` until the exact Compose files and
environment inputs are restored.

An operator can explicitly replay a retained rollback:

```powershell
python install.py --rollback "C:\path\to\run-id" `
  --core-root "C:\path\to\azerothcore-wotlk" `
  --lua-root "C:\path\to\ALE\lua_scripts" `
  --compose-project azerothcore-test
```

On Linux, use the same explicit core, Lua, project, Compose-file, and env-file
selection that the journaled upgrade used.

Do not delete the backup directory or old-image tag until the upgraded realm
has been exercised in-game.

## Source-checkout development

The release archive is the supported deployment input and rejects
`--lua-source`; its checksummed focal bytes cannot be substituted. Maintainers may run the
template directly from this repository with `--allow-development-layout`; that
explicitly permits repository-relative `serverside/paragon` and warns when the
archive `SHA256SUMS` is unavailable. This escape hatch is not intended for realm
operators.

## Common refusals

- Reapplying the enlarged cumulative patch to an old installed tree is refused;
  the incremental delta is used instead.
- A method without its Lua registration (or the reverse) is a partial state and
  is never repaired speculatively.
- A mismatched checksum, ambiguous Compose project/service, changed Compose
  digest, non-InnoDB config table, unknown focal Lua hash, truncated dump,
  failed build, stale candidate
  image, failed readiness check, wrong running image, or boot-time Paragon/ALE
  error aborts the cutover.
- A source change without a different candidate image ID is treated as stale
  Docker cache and refused.

See `RELEASE_NOTES.md` for the gameplay values and scope.
