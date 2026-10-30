# Project conventions

- `scripts/nana_image2.py` is a Python 3.9+ standard-library Images API client (on 3.11+ it uses `tomllib`; on 3.9/3.10 a minimal TOML-subset fallback parses provider blocks and fails loudly on anything richer). Keep runtime dependencies empty; its inline metadata supports `uv run` without preinstalled Python.
- Default model is `gpt-image-2.5-sunburst` with `size=auto`. Both official 2.5 variants and their 2026-09-08 snapshots use the documented size constraints; `xhigh`/`max` quality is supported. Bare `gpt-image-2.5` is an unverified provider alias, never auto-selected or silently mapped.
- Endpoint precedence: environment > selected Codex provider. Key precedence: environment > provider token > top-level `OPENAI_API_KEY` in `auth.json` beside the selected config. Never use OAuth tokens or silently change endpoints.
- PSD helpers (`psd-layers` / `psd-export` / `psd-compose`, backed by `scripts/psd_io.py`) are fully offline: no provider access, no credentials. The reader supports 8-bit RGB with raw/RLE/ZIP/ZIP-with-prediction; the writer produces RLE-channel layered PSDs. Anything outside that subset must keep failing with a clear `PsdError`, never a silent guess. `psd-compose` always writes a new file; never write back into an existing PSD.
- Streaming: `generate` auto-streams slow jobs (quality high+ or explicit size >=2MP) to dodge proxy origin timeouts (Cloudflare 524); gateways that answer JSON despite `stream` fall back transparently. `edit` must never send a stream flag (sub2api #8027 -> 502 + pool drain).
- Degradation verdict: with an explicit requested size, `save_response` must keep emitting the machine-readable `degraded` JSON line when the actual output or echoed model differs - that is the guard against the known silent downgrade.
- Installers (`quick-install-*.command/.bat`, `quick-uninstall-*`) only copy or delete `~/.codex/skills/nana_image2` (`%USERPROFILE%\.codex\skills\nana_image2`). They must never modify Codex config.
- `scripts/run` / `run.cmd` is the universal launcher: system Python 3.9+ first, then the bundled private `runtime/` (python-build-standalone). Always `-E -s` so developer environments are never touched; SKILL.md commands go through it.
- `tools/make_dist.py --print-urls` documents the runtime downloads; dist zips are built with it and attached to GitHub Releases (never committed).
- Keep README.md and SKILL.md synchronized with CLI defaults, routing, PSD boundaries, and startup instructions.
- Verify with `python3 -B -m unittest discover -s tests -p 'test_*.py'` (offline, dummy credentials; includes PSD/PNG/TOML round-trips) and `git diff --check`. Do not run paid generation solely for verification.

- Public usage docs focus on GPT Image 2.5. Prompt routing lives in SKILL.md: explicit model wins; speed/drafts use Flare, precise edits/default use Sunburst. Always pass the selected full ID via --model; the CLI does not route prompt text.
