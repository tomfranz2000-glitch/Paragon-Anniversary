# Legacy UI archive

`patch-4.MPQ` is the original, unmarked UI-art archive retained only as a
historical recovery copy. **Do not install or rename it.** It predates the
Paragon ownership marker and the safe archive-replacement checks.

The authoritative sources are the 14 BLP files under
`clientside/Interface` outside `AddOns`. Build the supported archive directly
from those files:

```bash
python tools/build_ui_art.py
```

That command writes `patch-W.MPQ`, verifies every source byte, and adds the
ownership marker required for safe future rebuilds.
