#!/bin/bash
# ===========================================================================
#  nana_image2 - one-click rollback (macOS)
#  Deletes ~/.codex/skills/nana_image2. Nothing else is touched:
#  the skill only ever READS the Codex config, so there is nothing to undo.
# ===========================================================================
set -euo pipefail

SKILL_NAME="nana_image2"
DEST="$HOME/.codex/skills/$SKILL_NAME"

if [ -d "$DEST" ]; then
    MANIFEST="$DEST/.installed-manifest"
    if [ -f "$MANIFEST" ]; then
        # remove every skill this package installed (incl. frozen vendors)
        grep -v '^#' "$MANIFEST" | tr ' ' '\n' | while read -r name; do
            [ -z "$name" ] && continue
            if [ "$name" = "nana_image2" ] || [ -d "$HOME/.codex/skills/$name" ]; then
                rm -rf "$HOME/.codex/skills/$name"
                echo "  [ok] removed skill: $name"
            fi
        done
    else
        rm -rf "$DEST"
        echo "  [ok] removed $DEST"
    fi
else
    echo "  $DEST was not installed (nothing to do)."
fi

echo
echo "============================================================"
echo " Rollback complete. Restart Codex and the skill is gone."
echo " Your Codex config.toml was never modified by this skill."
echo "============================================================"
read -n 1 -s -r -p "Press any key to close." || true
