#!/bin/bash
# ===========================================================================
#  nana_image2 - one-click install for Codex (macOS, Apple Silicon/Intel)
#  Copies the skill into ~/.codex/skills/nana_image2 and resolves Python:
#    1. system python3 (3.9+)          -> developers keep their own Python
#    2. the bundled private runtime     -> nothing to install for anyone else
#  No config is modified; uninstall = double-click quick-uninstall-mac.command
#  (or just delete the folder).
# ===========================================================================
set -euo pipefail
cd "$(dirname "$0")"

SKILL_NAME="nana_image2"
DEST="$HOME/.codex/skills/$SKILL_NAME"

say() { printf '  %s\n' "$*"; }
ok()  { printf '  [ok] %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; read -n 1 -s -r -p "Press any key to close."; exit 1; }

# ---- resolve Python: system first, bundled runtime as fallback ----
PY=""
if command -v python3 >/dev/null 2>&1; then
    if python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
        PY="$(command -v python3)"
        ok "using system Python $("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])') ($("$PY" -c 'import platform; print(platform.machine())'))"
    fi
fi
if [ -z "$PY" ]; then
    # runtime/ is arm64 (Apple Silicon), runtime-x64/ is Intel; try the
    # matching one first, then the other (exec fails cleanly on wrong arch).
    for dir in runtime runtime-x64; do
        if [ -x "$dir/bin/python3" ] && "$dir/bin/python3" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
            PY="$PWD/$dir/bin/python3"
            ok "no usable system Python - using the bundled private runtime ($("$PY" --version 2>&1), $dir)"
            say "(the runtime lives only inside the skill folder; nothing is installed system-wide)"
            break
        fi
    done
fi
[ -n "$PY" ] || die "no Python 3.9+ found and this package carries no bundled runtime.
  Re-download the full macOS package (nana_image2-macos.zip) or install Python 3.9+."

# ---- sanity: the pieces we copy must exist here ----
[ -f SKILL.md ] && [ -f scripts/nana_image2.py ] && [ -f scripts/psd_io.py ] \
    || die "run this from the unpacked skill folder (SKILL.md must be next to it)."

# ---- offline self-test before touching anything ----
if [ -d tests ]; then
    "$PY" -B -m unittest discover -s tests -p 'test_*.py' >/dev/null 2>&1 \
        && ok "offline self-test passed" \
        || say "self-test failed - installing anyway; run tests manually to inspect"
else
    "$PY" -B scripts/nana_image2.py --help >/dev/null 2>&1 \
        && ok "CLI smoke check passed" \
        || die "the CLI failed to run with the resolved interpreter."
fi

# ---- install: plain folder copy, idempotent ----
rm -rf "$DEST"
mkdir -p "$DEST"
cp SKILL.md AGENTS.md LICENSE "$DEST/"
cp -R scripts "$DEST/scripts"
rm -rf "$DEST/scripts/__pycache__"
if [ -d runtime ] || [ -d runtime-x64 ]; then
    [ -d runtime ] && cp -R runtime "$DEST/runtime"
    [ -d runtime-x64 ] && cp -R runtime-x64 "$DEST/runtime-x64"
    ok "installed (with private runtimes) to $DEST"
else
    ok "installed to $DEST"
fi

cat <<EOF

============================================================
 Done. Restart Codex, then try:
     "用 nana_image2 生成一张 1024x1024 的橙色猫海报"
     "把 poster.psd 的背景层导出来，换成夕阳背景再合成 PSD"

 Rollback: double-click quick-uninstall-mac.command
 (deletes $SKILL_NAME only; your Codex config is never touched)
============================================================
EOF
read -n 1 -s -r -p "Press any key to close."
