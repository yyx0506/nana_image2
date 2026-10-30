---
name: "nana_image2"
description: "Generate or edit raster images through the current Codex custom provider or sub2api when the built-in image_gen tool is unavailable, missing, or blocked. Use for image assets and visual variants; do not use for SVG, HTML/CSS, diagrams, or other deterministic code-native visuals."
---

# Sub2API Image Generation

Use the bundled dependency-free client instead of the system `imagegen` CLI. It reads the active provider's `base_url`, `experimental_bearer_token`, and `http_headers` from Codex config, falling back to the adjacent `auth.json` top-level `OPENAI_API_KEY` when no environment/config key is set, then sends a non-SDK User-Agent to avoid the known sub2api/Cloudflare SDK-header block.

## Workflow

1. Select the model using the prompt-routing rules below, then resolve `<skill-dir>` as the directory containing this `SKILL.md`. Run the no-cost connectivity check:

   ```bash
   "<skill-dir>/scripts/run" doctor --model <selected-model>   # Windows: "<skill-dir>\scripts\run.cmd" doctor --model <selected-model>
   ```

   Commands go through the bundled launcher (`scripts/run`, Windows `scripts\run.cmd`): it uses a system Python 3.9+ when present and otherwise the private runtime installed with the skill - no Python installation is required on the user's machine, and the launcher runs the interpreter with `-E -s` so developer environments (conda/pyenv) are never touched. On Python 3.9/3.10 the config parser falls back to a minimal TOML-subset reader; anything richer fails with a clear message.

2. Normalize the user's request into a concise prompt. Preserve exact text and explicitly state edit invariants such as `change only the background; keep the subject unchanged`.
3. Generate or edit exactly once, explicitly passing the same `--model <selected-model>` used for doctor. A user request to create/edit an image authorizes that call; diagnostics alone do not authorize a billed smoke test.
4. Read the output carefully. When an explicit size was requested, the client prints a final JSON verdict line: if it says `"degraded": true`, the upstream accepted the request but returned a different size/model (known sub2api silent downgrade, issues #6120/#3302/#7119) — report the ACTUAL size to the user along with the verdict's advice instead of claiming success. Inspect the saved image with `view_image`. Retry only for a specific defect the user asked to fix.
5. Report the model, final prompt, and absolute saved path. For project assets, save under the project's `output/imagegen/` or the user-named path.

Streaming rules (important, both are workaround-driven):

- `generate` streams automatically for slow jobs (quality high/xhigh/max, or explicit size ≥2MP such as 4K): slow generations can outrun the proxy's ~100s origin timeout (Cloudflare 524) unless bytes flow early. `--stream on|off` forces it; `--save-partials` keeps intermediate images from the stream.
- `edit` NEVER streams. sub2api issue #8027: edits+stream returns 502 and the failover drains the whole account pool. This client sends no stream flag on the edits path — do not work around that by hand.

## Prompt routing

- An explicit Flare, Sunburst, full model ID, or dated snapshot requested by the user takes precedence over inferred intent. Map Flare to `gpt-image-2.5-flare` and Sunburst to `gpt-image-2.5-sunburst`; preserve explicitly requested full IDs/snapshots.
- Without an explicit model, use Flare for speed, drafts, or everyday generation requests; use Sunburst for precise editing, preserving subject/text details, or when no preference is given. If both speed and detail are requested without a model, prefer Sunburst for preservation-critical edits.
- Both models support generation and editing. Choose `generate` versus `edit` from whether the user wants a new image or an edit/reference workflow, not from the model name. Never send a reference image when the user requests text-only generation.
- Pass the selected full ID via `--model` to both doctor and generate/edit. Merely mentioning a model in `--prompt` does not route the Python client. Report unavailable models; never silently switch variants or endpoints.

Generate:

```bash
"<skill-dir>/scripts/run" generate \
  --model gpt-image-2.5-flare \
  --prompt "A ceramic coffee mug in soft studio light; no logo, text, or watermark" \
  --quality medium \
  --out output/imagegen/mug.png
```

Edit:

```bash
"<skill-dir>/scripts/run" edit \
  --model gpt-image-2.5-sunburst \
  --image input.png \
  --prompt "Replace only the background with a warm sunset; keep the product and edges unchanged" \
  --out output/imagegen/sunset-edit.png
```

Use `--dry-run` to inspect a redacted request without network or cost. Use `--provider NAME` or `--config PATH` only when the active Codex provider is not the intended route. `OPENAI_BASE_URL` and `OPENAI_API_KEY` override config values when set.

The config path is `--config`, then `$CODEX_HOME/config.toml`, then `~/.codex/config.toml`. Key precedence is environment, provider token, then `auth.json` beside the selected config. Only the top-level `OPENAI_API_KEY` is read, never OAuth tokens. The endpoint must still come from environment/config; auth fallback never switches providers or defaults to an official endpoint. Ensure the key belongs to that endpoint.

## PSD layer workflow

When the user mentions a PSD file, layers (图层), or wants generated assets delivered as a layered file, use the offline PSD helpers first; they never touch the provider or credentials.

```bash
# 1. Inspect: list layers (name/index/bbox/visibility) — always start here
"<skill-dir>/scripts/run" psd-layers poster.psd

# 2. Extract one layer as PNG to feed generate/edit (or --all --outdir DIR)
"<skill-dir>/scripts/run" psd-export poster.psd --name 背景 --out output/bg.png

# 3. Generate/edit with the exported layer as the reference image (billed, same rules as above)
"<skill-dir>/scripts/run" edit --image output/bg.png \
  --model gpt-image-2.5-sunburst --prompt "..." --out output/bg-new.png

# 4. Compose a NEW layered PSD (bottom layer first); original file is never modified
"<skill-dir>/scripts/run" psd-compose \
  --layer "背景=output/bg-new.png" --layer "产品=output/product.png" --out output/poster-v2.psd
```

Rules and boundaries — state them plainly rather than working around them:

- Supported: 8-bit RGB PSDs, layer records and bitmap pixels, raw/RLE/ZIP/ZIP-with-prediction compression; writing new layered PSDs (RLE channels) that Photoshop, Photopea, Krita and Affinity open.
- Not supported (the command fails with a clear error, never silently): PSB, 16/32-bit depth, CMYK/grayscale modes, layer masks as separate exports, writing back into the existing PSD (`psd-compose` always produces a new file).
- Layers without pixel data (adjustment layers) are listed with `pixels: false` and skipped on export; text layers come out as their rendered bitmap only.
- All layer PNGs in `psd-compose` must share one canvas size; resize first or pick one canvas.
- For "分层出图" requests (background/subject/decoration as separate images), generate each image at the same size, then compose.

## Resolution

- Default model: `gpt-image-2.5-sunburst`, size `auto`. Choose `--model gpt-image-2.5-flare` for fast everyday generation; Sunburst prioritizes editing precision. Both support generation and editing, and dated `-2026-09-08` snapshots. All commands accept `--model`; no separate variant parameter is needed. The bare `gpt-image-2.5` is only a provider-specific alias, warns when explicitly used, and must never be reported as a verified official variant.
- Official 2.5 quality settings: `low`, `medium`, `high`, `xhigh`, `max`, `auto`. Client default remains `medium` (official default is `auto`). Both models have equal token rates, not necessarily equal per-image costs.
- Official 2.5 size constraints: multiples of 16, max edge 3840, aspect ratio 1:3 to 3:1, 655,360–8,294,400 pixels; above 2560x1440 is experimental. Transparent output supports PNG/WebP. Source: [Image generation guide](https://developers.openai.com/api/docs/guides/image-generation), checked 2026-09-27.
- Provider-returned bytes are authoritative. The client reports any requested/actual mismatch and never upscales, resamples, or pads locally.

## Boundaries

- Never print, persist, or ask the user to paste a token. Do not copy credentials into project files.
- Never auto-retry a paid request. Surface the API error and change only the demonstrated cause.
- Never claim requested dimensions are actual dimensions; report the decoded/API-reported output size.
- Do not overwrite an existing file unless the user requested replacement; otherwise choose a versioned filename. `--force` is explicit overwrite authorization.
- Both GPT Image 2.5 variants support transparent output. Keep the selected model and pass `--background transparent --output-format png` (or `webp`).
- Prefer the built-in `image_gen` tool when it is actually available and working. This skill exists for the custom-provider fallback path.
