#!/bin/bash
# ===========================================================================
#  sub2api-imagegen - one-click install for Codex (macOS, Apple Silicon/Intel)
#  Copies the skill into ~/.codex/skills/sub2api-imagegen and checks Python.
#  No config is modified; uninstall = double-click quick-uninstall-mac.command
#  (or just delete the folder).
# ===========================================================================
set -euo pipefail
cd "$(dirname "$0")"

SKILL_NAME="sub2api-imagegen"
DEST="$HOME/.codex/skills/$SKILL_NAME"

say() { printf '  %s\n' "$*"; }
ok()  { printf '  [ok] %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; read -n 1 -s -r -p "Press any key to close."; exit 1; }

# ---- Python (skill needs 3.9+; the CLI works without the OpenAI SDK) ----
PY="$(command -v python3 || true)"
[ -n "$PY" ] || die "python3 not found. Install Python 3.9+ (brew install python) and re-run."
"$PY" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' \
    || die "Python 3.9+ required; $PY is $("$PY" --version 2>&1)."
ok "Python $("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])') ($("$PY" -c 'import platform; print(platform.machine())'))"

# ---- sanity: the pieces we copy must exist here ----
[ -f SKILL.md ] && [ -f scripts/sub2api_image_gen.py ] && [ -f scripts/psd_io.py ] \
    || die "run this from the unpacked skill folder (SKILL.md must be next to it)."

# ---- offline self-test before touching anything ----
"$PY" -B -m unittest discover -s tests -p 'test_*.py' >/dev/null 2>&1 \
    && ok "offline self-test passed" \
    || say "self-test failed - installing anyway; run tests manually to inspect"

# ---- install: plain folder copy, idempotent ----
mkdir -p "$DEST"
rm -rf "$DEST"
mkdir -p "$DEST"
cp SKILL.md AGENTS.md LICENSE "$DEST/"
cp -R scripts "$DEST/scripts"
ok "installed to $DEST"

cat <<EOF

============================================================
 Done. Restart Codex, then try:
     "用 sub2api-imagegen 生成一张 1024x1024 的橙色猫海报"
     "把 poster.psd 的背景层导出来，换成夕阳背景再合成 PSD"

 Rollback: double-click quick-uninstall-mac.command
 (deletes $SKILL_NAME only; your Codex config is never touched)
============================================================
EOF
read -n 1 -s -r -p "Press any key to close."
