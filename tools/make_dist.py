#!/usr/bin/env python3
"""Build self-contained distribution zips for the nana_image2 skill.

    python3 tools/make_dist.py --win-runtime /path/cpython-...windows...install_only.zip \
                               --mac-runtime /path/cpython-...darwin...install_only.tar.gz \
                               [--output dist]

Produces:
    dist/nana_image2-windows-x64.zip   (Windows, bundled runtime - no Python needed)
    dist/nana_image2-macos.zip         (macOS, bundled runtime - no Python needed)

Each zip contains SKILL.md, AGENTS.md, LICENSE, scripts/ (CLI + launchers),
the per-platform quick installers, and a private portable CPython under
runtime/. The runtime is invoked only by absolute path via scripts/run
(.cmd) - it never touches PATH, the registry, or an existing Python.

Runtime archives follow the python-build-standalone "install_only" layout
(everything inside a top-level python/ directory). Download them from
https://github.com/astral-sh/python-build-standalone/releases - see
tools/make_dist.py --print-urls for the exact asset naming.

Standard library only.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

COMMON_FILES = ["SKILL.md", "AGENTS.md", "LICENSE"]
COMMON_SCRIPTS = ["nana_image2.py", "psd_io.py", "run", "run.cmd"]

WIN_LAYOUT = {
    "files": COMMON_FILES,
    "scripts": COMMON_SCRIPTS,
    "runtime": {"run.cmd", "python.exe", "pythonw.exe"},
    "installers": ["quick-install-windows.bat", "quick-uninstall-windows.bat"],
    "name": "nana_image2-windows-x64.zip",
    "exclude_scripts": set(),
}

MAC_LAYOUT = {
    "files": COMMON_FILES,
    "scripts": COMMON_SCRIPTS,
    "runtime": {"run"},          # everything under runtime/ is kept
    "installers": ["quick-install-mac.command", "quick-uninstall-mac.command"],
    "name": "nana_image2-macos.zip",
    "exclude_scripts": {"run.cmd"},
}


def fail(message: str) -> None:
    raise SystemExit(f"Error: {message}")


def extract_runtime(archive: Path, target: Path) -> None:
    """Extract an install_only archive (python/ inside) so its content becomes target/."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(target.parent)
    elif archive.name.endswith(".tar.gz") or archive.suffix == ".tgz":
        with tarfile.open(archive, "r:gz") as tf:
            try:
                tf.extractall(target.parent, filter="data")
            except TypeError:  # Python without the filter= parameter
                tf.extractall(target.parent)
    else:
        fail(f"unsupported runtime archive: {archive}")
    extracted = target.parent / "python"
    if not extracted.is_dir():
        fail(f"{archive} did not contain a top-level python/ directory.")
    if target.exists():
        shutil.rmtree(target)
    extracted.rename(target)


def _git_pin(directory: Path) -> tuple[str, str]:
    """Best-effort (repo-url, commit) of a vendored checkout; (unknown, '') otherwise."""
    import subprocess
    if not (directory / ".git").exists():
        return "unknown-source", ""
    try:
        url = subprocess.run(["git", "-C", str(directory), "remote", "get-url", "origin"],
                             capture_output=True, text=True, timeout=15, check=False
                             ).stdout.strip() or "unknown-source"
        ref = subprocess.run(["git", "-C", str(directory), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=15, check=False
                             ).stdout.strip()
        return url, ref
    except (OSError, subprocess.SubprocessError):
        return "unknown-source", ""


def collect_vendors(specs: list[str]) -> list:
    vendors = []
    for spec in specs or []:
        name, sep, directory = spec.partition("=")
        if not sep or not name.strip() or not directory.strip():
            fail(f"--vendor must be NAME=PATH, got: {spec!r}")
        path = Path(directory.strip()).expanduser()
        if not (path / "SKILL.md").is_file():
            fail(f"{path} is not a skill directory (SKILL.md missing); cannot vendor as {name}.")
        repo, ref = _git_pin(path)
        vendors.append((name.strip(), path, repo, ref))
    return vendors


def build(layout: dict, runtimes: dict, output_dir: Path, vendors: list | None = None) -> Path:
    for target, archive in runtimes.items():
        if not archive or not archive.is_file():
            fail(f"runtime archive not found for {target}: {archive}")
    work = Path(tempfile.mkdtemp(prefix="nana_image2-dist-"))
    pkg = work / "nana_image2"
    pkg.mkdir()
    try:
        for name in layout["files"]:
            source = ROOT / name
            if not source.is_file():
                fail(f"missing {name} in the repository root")
            shutil.copy2(source, pkg / name)
        scripts = pkg / "scripts"
        scripts.mkdir()
        for name in layout["scripts"]:
            if name in layout["exclude_scripts"]:
                continue
            source = ROOT / "scripts" / name
            if not source.is_file():
                fail(f"missing scripts/{name}")
            shutil.copy2(source, scripts / name)
            if not name.endswith(".py"):
                (scripts / name).chmod(0o755)
        for name in layout["installers"]:
            source = ROOT / name
            if not source.is_file():
                fail(f"missing {name} in the repository root")
            shutil.copy2(source, pkg / name)
            if name.endswith(".command"):
                (pkg / name).chmod(0o755)
        if layout["runtime"]:
            for target, archive in runtimes.items():
                print(f"  bundling {target}/ from {archive.name} ...")
                extract_runtime(archive, pkg / target)
        if vendors:
            lines = ["# Frozen third-party skills bundled into this package.",
                     "# Upgrade = admin pulls a newer pin, re-runs make_dist, republishes.",
                     ""]
            for name, directory, repo, ref in vendors:
                target = pkg / "vendor" / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(directory, target,
                                ignore=shutil.ignore_patterns(".git", "__pycache__"))
                print(f"  vendoring {name} ({repo}@{ref[:12] if ref else 'n/a'})")
                lines.append(f"{name}: {repo}@{ref} (frozen {__import__('time').strftime('%Y-%m-%d')})")
            (pkg / "VENDOR-VERSIONS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        output_dir.mkdir(parents=True, exist_ok=True)
        out = output_dir / layout["name"]
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            for path in sorted(pkg.rglob("*")):
                zf.write(path, path.relative_to(work))
        return out
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--win-runtime", type=Path, help="python-build-standalone windows install_only(.zip/.tar.gz)")
    ap.add_argument("--mac-runtime", type=Path, help="python-build-standalone macOS arm64 install_only.tar.gz (bundled as runtime/)")
    ap.add_argument("--mac-runtime-x64", type=Path, help="python-build-standalone macOS x86_64 install_only.tar.gz (bundled as runtime-x64/ for Intel Macs)")
    ap.add_argument("--vendor", action="append", metavar="NAME=PATH", default=[],
                    help="Freeze a third-party skill directory into the package under vendor/NAME "
                         "(its git remote+commit are recorded in VENDOR-VERSIONS.txt).")
    ap.add_argument("--output", type=Path, default=ROOT / "dist")
    ap.add_argument("--print-urls", action="store_true",
                    help="show the python-build-standalone asset naming to download and exit")
    args = ap.parse_args()

    if args.print_urls:
        print("Download from https://github.com/astral-sh/python-build-standalone/releases/latest :")
        print("  Windows x64 : cpython-3.11.<build>-x86_64-pc-windows-msvc-install_only.zip")
        print("  macOS arm64 : cpython-3.11.<build>-aarch64-apple-darwin-install_only.tar.gz")
        print("  macOS x64   : cpython-3.11.<build>-x86_64-apple-darwin-install_only.tar.gz")
        return

    vendors = collect_vendors(args.vendor)
    outputs = []
    if args.win_runtime:
        outputs.append(build(WIN_LAYOUT, {"runtime": args.win_runtime}, args.output, vendors))
    if args.mac_runtime:
        mac = {"runtime": args.mac_runtime}
        if args.mac_runtime_x64:
            mac["runtime-x64"] = args.mac_runtime_x64
        outputs.append(build(MAC_LAYOUT, mac, args.output, vendors))
    if not outputs:
        ap.error("nothing to build: pass --win-runtime and/or --mac-runtime")
    for out in outputs:
        size = out.stat().st_size / (1024 * 1024)
        print(f"built {out}  ({size:.1f} MB)")


if __name__ == "__main__":
    main()
