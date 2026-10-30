#!/bin/bash
# ===========================================================================
#  nanaiamge - one-click rollback (macOS)
#  Deletes ~/.codex/skills/nanaiamge. Nothing else is touched:
#  the skill only ever READS the Codex config, so there is nothing to undo.
# ===========================================================================
set -euo pipefail

SKILL_NAME="nanaiamge"
DEST="$HOME/.codex/skills/$SKILL_NAME"

if [ -d "$DEST" ]; then
    rm -rf "$DEST"
    echo "  [ok] removed $DEST"
else
    echo "  $DEST was not installed (nothing to do)."
fi

echo
echo "============================================================"
echo " Rollback complete. Restart Codex and the skill is gone."
echo " Your Codex config.toml was never modified by this skill."
echo "============================================================"
read -n 1 -s -r -p "Press any key to close."
