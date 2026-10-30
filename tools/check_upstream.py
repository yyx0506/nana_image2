#!/usr/bin/env python3
"""Admin tool: compare pinned internal versions against upstream releases.

    python3 tools/check_upstream.py            # table, exit 1 if anything is behind
    python3 tools/check_upstream.py --json     # machine-readable

Reads tools/upstream.json (name/repo/pinned). Talks to api.github.com through
$HTTPS_PROXY when set. Colleagues never run this - it is the admin's upgrade
radar for the internal freeze-and-republish cycle (see docs/internal-distribution.md).
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import urllib.request
from pathlib import Path

LIST = Path(__file__).resolve().parent / "upstream.json"


def latest_tag(repo: str) -> str | None:
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    request = urllib.request.Request(url, headers={"User-Agent": "nana_image2-check-upstream",
                                                   "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read()).get("tag_name")
    except Exception as exc:  # noqa: BLE001 - report and continue with the rest
        return f"error: {exc.__class__.__name__}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    entries = json.loads(LIST.read_text(encoding="utf-8"))
    results = []
    behind = 0
    for entry in entries:
        latest = latest_tag(entry["repo"])
        norm = (latest or "").lstrip("v")
        if latest is None or str(latest).startswith("error"):
            status = "?"
        elif norm == entry["pinned"].lstrip("v"):
            status = "ok"
        elif entry["pinned"].startswith("not-"):
            status = "n/a"          # not distributed internally (yet); informational
        else:
            status = "UPDATE"
        if status == "UPDATE":
            behind += 1
        results.append({"name": entry["name"], "repo": entry["repo"],
                        "pinned": entry["pinned"], "latest": latest, "status": status})

    if args.json:
        print(json.dumps({"updates_available": behind, "items": results},
                         ensure_ascii=False, indent=2))
    else:
        print(f"{'project':<22} {'pinned':<12} {'upstream':<16} status")
        print("-" * 62)
        for item in results:
            print(f"{item['name']:<22} {item['pinned']:<12} {str(item['latest']):<16} {item['status']}")
        print("-" * 62)
        print("all pinned" if not behind else f"{behind} project(s) have newer upstream releases")
        print("(workflow: pull, verify on your machine, re-run make_dist, republish - see docs/internal-distribution.md)")
    return 1 if behind else 0


if __name__ == "__main__":
    sys.exit(main())
