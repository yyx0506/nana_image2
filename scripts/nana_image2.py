#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Generate or edit images through a Codex custom provider without the OpenAI SDK.

Also carries offline PSD helpers (psd-layers / psd-export / psd-compose) that
bridge layered Photoshop files with the generate/edit commands; see psd_io.py.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
from pathlib import Path
import re
import struct
import sys
import tempfile
import uuid
from typing import Any
import urllib.error
import urllib.request

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # Python 3.9/3.10: minimal fallback below
    tomllib = None

_SCRIPT_DIR = str(Path(__file__).resolve().parent)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)
import psd_io

USER_AGENT = "codex-nana_image2/1.0"
DEFAULT_MODEL = "gpt-image-2.5-sunburst"
IMAGE_25_MODELS = {"gpt-image-2.5-sunburst", "gpt-image-2.5-flare",
                   "gpt-image-2.5-sunburst-2026-09-08", "gpt-image-2.5-flare-2026-09-08"}
DEFAULT_SIZE = "3840x2160"
DEFAULT_OUT = "output/imagegen/output.png"
MAX_INPUT_BYTES = 50 * 1024 * 1024


# --------------------------------------------------------------------------
# TOML fallback for Python 3.9/3.10: parses only the subset this tool reads
# (top-level keys, [dotted.tables], string values, flat inline tables).
# Anything richer fails loudly instead of guessing.
# --------------------------------------------------------------------------
def _strip_toml_comment(line: str) -> str:
    out: list[str] = []
    quoted, quote = False, ""
    for ch in line:
        if quoted:
            out.append(ch)
            if ch == quote:
                quoted = False
        elif ch in "\"'":
            quoted, quote = True, ch
            out.append(ch)
        elif ch == "#":
            break
        else:
            out.append(ch)
    return "".join(out)


def _split_inline(text: str) -> list[str]:
    parts, buf = [], []
    quoted, quote = False, ""
    for ch in text:
        if quoted:
            buf.append(ch)
            if ch == quote:
                quoted = False
        elif ch in "\"'":
            quoted, quote = True, ch
            buf.append(ch)
        elif ch == ",":
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return [part.strip() for part in parts if part.strip()]


def _mini_value(text: str) -> Any:
    if text.startswith("{") and text.endswith("}"):
        table: dict[str, Any] = {}
        for part in _split_inline(text[1:-1]):
            key, sep, value = part.partition("=")
            if not sep:
                fail(f"unsupported config.toml inline entry: {part!r}")
            table[key.strip().strip("\"'")] = _mini_value(value.strip())
        return table
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        body = text[1:-1]
        if text[0] == '"':
            body = body.replace('\\"', '"').replace("\\\\", "\\")
        return body
    if text in ("true", "false"):
        return text == "true"
    for cast in (int, float):
        try:
            return cast(text)
        except ValueError:
            continue
    fail(f"unsupported config.toml value: {text!r}")
    return None  # unreachable


def _mini_toml(text: str) -> dict[str, Any]:
    root: dict[str, Any] = {}
    table = root
    for raw in text.splitlines():
        line = _strip_toml_comment(raw).strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            if line.startswith("[["):
                fail("array-of-tables syntax in config.toml is not supported on Python 3.9/3.10; upgrade to Python 3.11+ or simplify the provider block.")
            table = root
            for part in line[1:-1].split("."):
                table = table.setdefault(part.strip().strip("\"'"), {})
            continue
        key, sep, value = line.partition("=")
        if not sep:
            fail(f"unsupported config.toml line: {raw.strip()!r}")
        table[key.strip().strip("\"'")] = _mini_value(value.strip())
    return root


def _load_config(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        return {}
    text = config_path.read_text(encoding="utf-8")
    if tomllib is not None:
        return tomllib.loads(text)
    return _mini_toml(text)


def fail(message: str) -> None:
    raise SystemExit(f"Error: {message}")


def default_config_path() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    return Path(codex_home).expanduser() / "config.toml" if codex_home else Path.home() / ".codex/config.toml"


def load_route(config_path: Path, provider_name: str | None) -> tuple[str, dict[str, str], str]:
    env_url = os.environ.get("OPENAI_BASE_URL")
    env_key = os.environ.get("OPENAI_API_KEY")
    config = _load_config(config_path)

    name = provider_name or str(config.get("model_provider", ""))
    provider = config.get("model_providers", {}).get(name, {}) if name else {}
    base_url = env_url or provider.get("base_url")
    token = env_key or provider.get("experimental_bearer_token")
    if not isinstance(base_url, str) or not base_url.startswith(("https://", "http://")):
        fail("No valid provider base URL. Set OPENAI_BASE_URL or configure model_providers.<name>.base_url.")
    auth_path = config_path.with_name("auth.json")
    used_auth = False
    if not token and auth_path.exists():
        try:
            auth = json.loads(auth_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            fail(f"Cannot read valid JSON from {auth_path}.")
        token = auth.get("OPENAI_API_KEY") if isinstance(auth, dict) else None
        used_auth = bool(token)
    if not isinstance(token, str) or not token:
        fail("No provider token. Set OPENAI_API_KEY, configure experimental_bearer_token, or add OPENAI_API_KEY to auth.json beside config.toml. OAuth login tokens are not supported.")

    custom = provider.get("http_headers", {})
    headers = {
        str(k): str(v)
        for k, v in custom.items()
        if str(k).lower() not in {"authorization", "user-agent"}
    } if isinstance(custom, dict) else {}
    headers.update({"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT, "Accept": "application/json"})
    source = "environment" if env_url or env_key else f"Codex provider {name}"
    if used_auth:
        source += " (API key from auth.json)"
    return base_url.rstrip("/"), headers, source


def request_json(url: str, headers: dict[str, str], data: bytes | None = None, content_type: str | None = None) -> dict[str, Any]:
    request_headers = dict(headers)
    if content_type:
        request_headers["Content-Type"] = content_type
    request = urllib.request.Request(url, data=data, headers=request_headers, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(request, timeout=300 if data is not None else 20) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read(4000).decode("utf-8", "replace").strip()
        fail(f"HTTP {exc.code} from {url}: {detail or exc.reason}")
    except urllib.error.URLError as exc:
        fail(f"Could not reach {url}: {exc.reason}")
    return {}  # unreachable


def output_paths(raw: str, count: int, output_format: str, force: bool) -> list[Path]:
    path = Path(raw)
    if not path.suffix:
        path = path.with_suffix("." + output_format)
    expected = {".jpg", ".jpeg"} if output_format == "jpeg" else {"." + output_format}
    if path.suffix.lower() not in expected:
        fail(f"Output extension {path.suffix} does not match --output-format {output_format}.")
    paths = [path] if count == 1 else [path.with_name(f"{path.stem}-{index}{path.suffix}") for index in range(1, count + 1)]
    existing = [str(item) for item in paths if item.exists()]
    if existing and not force:
        fail(f"Output already exists: {', '.join(existing)} (choose another path or pass --force)")
    return paths


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = ""
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as temp:
            temp.write(content)
            temp_name = temp.name
        Path(temp_name).replace(path)
    finally:
        if temp_name:
            Path(temp_name).unlink(missing_ok=True)


def download_image(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.read()
    except urllib.error.URLError as exc:
        fail(f"Could not download generated image: {exc.reason}")
    return b""  # unreachable


def detect_png_size(content: bytes) -> str | None:
    if len(content) >= 24 and content.startswith(b"\x89PNG\r\n\x1a\n"):
        width, height = struct.unpack(">II", content[16:24])
        if width > 0 and height > 0:
            return f"{width}x{height}"
    return None


# Streaming is only ever used for /images/generations: slow 4K/high-quality
# jobs can outrun Cloudflare's ~100s origin timeout (524) unless bytes start
# flowing early, and sub2api's generations path handles SSE fine. The edits
# path MUST stay non-streaming: sub2api issue #8027 shows edits+stream
# returns 502 and burns the whole account pool via failover.
SLOW_QUALITY = {"high", "xhigh", "max"}
SLOW_AREA = 2_000_000  # ~2048x1024 and above tends to outrun the proxy timeout


def _size_area(size: str | None) -> int | None:
    match = re.fullmatch(r"([1-9][0-9]*)x([1-9][0-9]*)", size or "")
    return int(match[1]) * int(match[2]) if match else None


def should_stream(args: argparse.Namespace, payload: dict[str, Any]) -> bool:
    mode = getattr(args, "stream", "auto")
    if mode == "on":
        return True
    if mode == "off":
        return False
    if payload.get("quality") in SLOW_QUALITY:
        return True
    area = _size_area(payload.get("size"))
    return bool(area and area >= SLOW_AREA)


def request_events(url: str, headers: dict[str, str], payload: dict[str, Any]):
    """POST expecting an SSE stream. Returns (events, None) on success,
    (None, json_doc) when the server answered plain JSON despite stream=True
    (some gateways ignore the flag), and exits on HTTP/network errors."""
    body = json.dumps(payload).encode()
    request_headers = dict(headers)
    request_headers["Content-Type"] = "application/json"
    request_headers["Accept"] = "text/event-stream"
    request = urllib.request.Request(url, data=body, headers=request_headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            ctype = (response.headers.get("Content-Type") or "").lower()
            if "text/event-stream" not in ctype:
                raw = response.read() or b"{}"
                try:
                    return None, json.loads(raw)
                except ValueError:
                    fail(f"Non-SSE response from {url} that is not JSON either: {raw[:200]!r}")
            events = []
            for line in response:
                text = line.decode("utf-8", "replace").strip()
                if not text.startswith("data:"):
                    continue
                data = text[5:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    events.append(json.loads(data))
                except ValueError:
                    continue
            return events, None
    except urllib.error.HTTPError as exc:
        detail = exc.read(4000).decode("utf-8", "replace").strip()
        fail(f"HTTP {exc.code} from {url}: {detail or exc.reason}")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        fail(f"Could not reach {url}: {exc}")
    return None, None  # unreachable


def images_from_events(events: list[Any]) -> tuple[list[dict[str, Any]], dict[int, str]]:
    """Split SSE events into (final_images, partial_images_by_index)."""
    finals: list[dict[str, Any]] = []
    partials: dict[int, str] = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        error = event.get("error")
        if isinstance(error, dict):
            fail(f"Stream error event: {json.dumps(error, ensure_ascii=False)[:400]}")
        items = event.get("data")
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and (item.get("b64_json") or item.get("url")):
                    finals.append(item)
            continue
        kind = str(event.get("type") or "")
        if isinstance(event.get("b64_json"), str):
            if "partial" in kind or event.get("partial_image_index") is not None:
                index = event.get("partial_image_index")
                partials[int(index) if isinstance(index, int) else len(partials)] = event["b64_json"]
            else:
                finals.append({"b64_json": event["b64_json"], "size": event.get("size")})
        elif isinstance(event.get("url"), str) and "partial" not in kind:
            finals.append({"url": event["url"], "size": event.get("size")})
    return finals, partials


def save_response(result: dict[str, Any], paths: list[Path], requested_size: str,
                  requested_model: str | None = None) -> None:
    items = result.get("data")
    if not isinstance(items, list) or len(items) < len(paths):
        fail("Image API response did not contain the expected data array.")
    actual_sizes = []
    for item, path in zip(items, paths):
        if not isinstance(item, dict):
            fail("Image API returned an invalid data item.")
        if item.get("b64_json"):
            try:
                content = base64.b64decode(item["b64_json"], validate=True)
            except (ValueError, TypeError) as exc:
                fail(f"Image API returned invalid base64 data: {exc}")
        elif item.get("url"):
            content = download_image(str(item["url"]))
        else:
            fail("Image API response contained neither b64_json nor url.")
        atomic_write(path, content)
        actual_size = str(item.get("size") or "").strip() or detect_png_size(content)
        actual_sizes.append(actual_size)
        print(path.resolve())

    # Degradation verdict: the known sub2api failure mode accepts the request
    # with HTTP 200 but silently swaps model/size/quality (issues #6120,
    # #3302, #7119). Surface it as machine-readable JSON on the last line so
    # the agent reports it instead of trusting the 200.
    verdict: dict[str, Any] | None = None
    requested = str(requested_size or "").lower()
    if requested and requested != "auto":
        mismatch = next((size for size in actual_sizes
                         if size and size.lower() != requested), None)
        if mismatch:
            verdict = {"degraded": True, "requested": requested_size, "actual": mismatch}
    model_echo = result.get("model")
    if requested_model and isinstance(model_echo, str) and model_echo != requested_model:
        verdict = verdict or {"degraded": True}
        verdict["requested_model"] = requested_model
        verdict["model_echo"] = model_echo
    if verdict:
        verdict["advice"] = (
            "Upstream returned 200 but did not honour the request (known sub2api "
            "silent downgrade, issues #6120/#3302/#7119). Report the actual output; "
            "retrying at a smaller explicit size or routing via an image-capable "
            "channel is the workaround."
        )
        print("Warning: provider did not honour the requested parameters.", file=sys.stderr)
        print(json.dumps(verdict, ensure_ascii=False))


def multipart(fields: dict[str, str], files: list[tuple[str, Path]]) -> tuple[bytes, str]:
    boundary = "----nana_image2-" + uuid.uuid4().hex
    body = bytearray()
    for name, value in fields.items():
        body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode())
    for name, path in files:
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        safe_name = path.name.replace('"', "")
        body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; filename=\"{safe_name}\"\r\nContent-Type: {media_type}\r\n\r\n".encode())
        body.extend(path.read_bytes())
        body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())
    return bytes(body), f"multipart/form-data; boundary={boundary}"


def common_payload(args: argparse.Namespace) -> dict[str, Any]:
    if args.size is None:
        args.size = DEFAULT_SIZE if args.model == "gpt-image-2" else "auto"
    if not args.prompt.strip():
        fail("--prompt must not be blank.")
    if not 1 <= args.n <= 10:
        fail("--n must be between 1 and 10.")
    if (args.model == "gpt-image-2" or args.model in IMAGE_25_MODELS) and args.size != "auto":
        match = re.fullmatch(r"([1-9][0-9]*)x([1-9][0-9]*)", args.size)
        if not match:
            fail(f"{args.model} --size must be auto or WIDTHxHEIGHT.")
        width, height = map(int, match.groups())
        long_edge, short_edge = max(width, height), min(width, height)
        if (
            long_edge > 3840
            or width % 16
            or height % 16
            or long_edge / short_edge > 3
            or not 655_360 <= width * height <= 8_294_400
        ):
            fail(f"{args.model} size exceeds its edge, alignment, ratio, or pixel-count limits.")
    return {key: value for key, value in {
        "model": args.model,
        "prompt": args.prompt.strip(),
        "n": args.n,
        "size": args.size,
        "quality": args.quality,
        "background": args.background,
        "output_format": args.output_format,
    }.items() if value is not None}


def route(args: argparse.Namespace) -> tuple[str, dict[str, str], str]:
    if args.model == "gpt-image-2.5":
        print("Warning: gpt-image-2.5 is not a documented official model ID; use gpt-image-2.5-sunburst or gpt-image-2.5-flare. Passing through as a provider-specific alias; actual model is unverified.", file=sys.stderr)
    base_url, headers, source = load_route(Path(args.config).expanduser(), args.provider)
    return base_url, headers, source


def doctor(args: argparse.Namespace) -> None:
    base_url, headers, source = route(args)
    result = request_json(base_url + "/models", headers)
    models = [item.get("id") for item in result.get("data", []) if isinstance(item, dict)]
    print(f"route: {source}")
    print(f"base_url: {base_url}")
    print(f"user_agent: {USER_AGENT}")
    print(f"model: {args.model} ({'available' if args.model in models else 'not listed'})")
    if args.model not in models:
        raise SystemExit(2)


def generate(args: argparse.Namespace) -> None:
    payload = common_payload(args)
    paths = output_paths(args.out, args.n, args.output_format, args.force)
    base_url, headers, source = route(args)
    use_stream = should_stream(args, payload)
    if use_stream:
        payload = dict(payload, stream=True)
    if args.dry_run:
        print(json.dumps({"route": source, "endpoint": base_url + "/images/generations",
                          "stream": use_stream, "payload": payload,
                          "outputs": [str(path) for path in paths]}, ensure_ascii=False, indent=2))
        return
    if use_stream:
        events, fallback = request_events(base_url + "/images/generations", headers, payload)
        if events is not None:
            finals, partials = images_from_events(events)
            if args.save_partials and partials:
                for index, b64 in sorted(partials.items()):
                    partial_path = paths[0].with_name(f"{paths[0].stem}-partial{index}.png")
                    atomic_write(partial_path, base64.b64decode(b64))
                    print(f"partial {index}: {partial_path.resolve()}")
            if finals:
                result: dict[str, Any] | None = {"data": finals}
            elif partials:
                print("Warning: stream ended without a final image; using the last partial image.", file=sys.stderr)
                result = {"data": [{"b64_json": sorted(partials.items())[-1][1]}]}
            else:
                fail("Stream completed but contained no image data.")
        else:
            result = fallback  # gateway ignored stream=True and answered JSON
    else:
        result = request_json(base_url + "/images/generations", headers,
                              json.dumps(payload).encode(), "application/json")
    save_response(result or {}, paths, args.size, args.model)


def edit(args: argparse.Namespace) -> None:
    # Never streams: sub2api issue #8027 - edits+stream returns 502 and the
    # failover drains the account pool. stream=false is answered correctly.
    image = Path(args.image)
    mask = Path(args.mask) if args.mask else None
    for path in [item for item in (image, mask) if item is not None]:
        if not path.is_file():
            fail(f"Input file not found: {path}")
        if path.stat().st_size > MAX_INPUT_BYTES:
            fail(f"Input exceeds 50 MB: {path}")
    payload = common_payload(args)
    paths = output_paths(args.out, args.n, args.output_format, args.force)
    base_url, headers, source = route(args)
    if args.dry_run:
        print(json.dumps({"route": source, "endpoint": base_url + "/images/edits", "stream": False, "fields": payload, "image": str(image), "mask": str(mask) if mask else None, "outputs": [str(path) for path in paths]}, ensure_ascii=False, indent=2))
        return
    fields = {key: str(value) for key, value in payload.items()}
    body, content_type = multipart(fields, [("image", image)] + ([("mask", mask)] if mask else []))
    result = request_json(base_url + "/images/edits", headers, body, content_type)
    save_response(result, paths, args.size, args.model)


# --------------------------------------------------------------------------
# Offline PSD helpers: bridge layered Photoshop files with generate/edit.
# These never touch the provider or credentials.
# --------------------------------------------------------------------------
def _require_file(value: str, what: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_file():
        fail(f"{what} not found: {path}")
    return path


def psd_layers(args: argparse.Namespace) -> None:
    doc = psd_io.read_psd(_require_file(args.file, "PSD file"))
    result = {"ok": True, "file": args.file, "canvas": f"{doc.width}x{doc.height}",
              "layers": [layer.summary() for layer in doc.layers]}
    if not doc.layers:
        if doc.composite is not None:
            result["layers"] = [{"index": 0, "name": "背景", "bbox": [0, 0, doc.width, doc.height],
                                 "size": f"{doc.width}x{doc.height}", "visible": True,
                                 "opacity": 1.0, "pixels": True}]
            result["note"] = "flattened file with no layers; psd-export writes the composite image."
        else:
            result["note"] = "no pixel layers and no composite found."
    print(json.dumps(result, ensure_ascii=False, indent=2))


def psd_export(args: argparse.Namespace) -> None:
    doc = psd_io.read_psd(_require_file(args.file, "PSD file"))
    exports: list[tuple[str, int, int, bytes]] = []
    if doc.layers:
        if args.all:
            chosen = [layer for layer in doc.layers if layer.rgba is not None]
            if not chosen:
                fail("No layer with decodable pixels; adjustment-type layers carry no bitmap.")
        elif args.index is not None:
            chosen = [next((layer for layer in doc.layers if layer.index == args.index), None) or fail(f"No layer with index {args.index}.")]
        elif args.name:
            chosen = [layer for layer in doc.layers if layer.name == args.name]
            if len(chosen) > 1:
                fail(f"Layer name '{args.name}' is ambiguous ({len(chosen)} matches); use --index.")
            if not chosen:
                fail(f"No layer named '{args.name}'. Run psd-layers to list names.")
        else:
            fail("psd-export needs --name, --index, or --all.")
        for layer in chosen:
            if layer.rgba is None:
                print(f"Warning: layer '{layer.name}' has no pixel data; skipped.", file=sys.stderr)
                continue
            exports.append((layer.name, layer.width, layer.height, bytes(layer.rgba)))
    elif doc.composite is not None:
        exports.append(("背景", doc.width, doc.height, bytes(doc.composite)))
    else:
        fail("The PSD has neither layers nor a composite image.")

    written = []
    if args.all:
        out_dir = Path(args.outdir or "output/imagegen").expanduser()
        for number, (name, width, height, rgba) in enumerate(exports, 1):
            safe = re.sub(r"[^0-9A-Za-z._-]+", "_", name).strip("_") or "layer"
            path = out_dir / f"{number:02d}-{safe}.png"
            if path.exists() and not args.force:
                fail(f"Output already exists: {path} (pass --force to replace).")
            psd_io.write_png(path, width, height, rgba)
            written.append({"layer": name, "size": f"{width}x{height}", "path": str(path)})
    else:
        if not args.out:
            fail("psd-export needs --out PATH (or --all with --outdir).")
        if len(exports) != 1:
            fail(f"{len(exports)} layers matched; use --all --outdir to export them all.")
        name, width, height, rgba = exports[0]
        path = Path(args.out).expanduser()
        if path.exists() and not args.force:
            fail(f"Output already exists: {path} (pass --force to replace).")
        psd_io.write_png(path, width, height, rgba)
        written.append({"layer": name, "size": f"{width}x{height}", "path": str(path)})
    print(json.dumps({"ok": True, "exported": written}, ensure_ascii=False, indent=2))


def psd_compose(args: argparse.Namespace) -> None:
    entries = []
    for spec in args.layer:
        name, sep, path = spec.partition("=")
        if not sep or not name.strip() or not path.strip():
            fail(f"--layer must be NAME=PATH, got: {spec!r}")
        entries.append((name.strip(), _require_file(path.strip(), "Layer PNG")))
    out = Path(args.out).expanduser()
    if out.exists() and not args.force:
        fail(f"Output already exists: {out} (pass --force to replace).")
    result = psd_io.compose_from_pngs(entries, out)
    print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))


def parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=str(default_config_path()))
    common.add_argument("--provider")
    common.add_argument("--model", default=DEFAULT_MODEL)

    image = argparse.ArgumentParser(description=__doc__)
    commands = image.add_subparsers(dest="command", required=True)
    check = commands.add_parser("doctor", parents=[common])
    check.set_defaults(run=doctor)

    for name, handler in (("generate", generate), ("edit", edit)):
        command = commands.add_parser(name, parents=[common])
        command.add_argument("--prompt", required=True)
        command.add_argument("--size", help="Default: 3840x2160 for gpt-image-2, auto for other models.")
        command.add_argument("--quality", choices=("low", "medium", "high", "xhigh", "max", "auto"), default="medium", help="xhigh/max require GPT Image 2.5 or provider support.")
        command.add_argument("--n", type=int, default=1)
        command.add_argument("--background", choices=("transparent", "opaque", "auto"))
        command.add_argument("--output-format", choices=("png", "jpeg", "webp"), default="png")
        command.add_argument("--out", default=DEFAULT_OUT)
        command.add_argument("--force", action="store_true")
        command.add_argument("--dry-run", action="store_true")
        command.set_defaults(run=handler)
    commands.choices["edit"].add_argument("--image", required=True)
    commands.choices["edit"].add_argument("--mask")

    # Streaming exists for generations only: it keeps bytes flowing so slow
    # 4K/high-quality jobs do not hit proxy origin timeouts (Cloudflare 524).
    # The edits path must never stream (sub2api #8027 -> 502 + pool drain),
    # so edit intentionally gets no --stream flag at all.
    gen = commands.choices["generate"]
    gen.add_argument("--stream", choices=("auto", "on", "off"), default="auto",
                     help="auto: stream only slow jobs (quality high+ or >=2MP); on/off force it. Generations only.")
    gen.add_argument("--save-partials", action="store_true",
                     help="Also save intermediate partial images from the stream.")

    listing = commands.add_parser("psd-layers", help="List the layers of a PSD file (offline, no provider access).")
    listing.add_argument("file")
    listing.set_defaults(run=psd_layers)

    export = commands.add_parser("psd-export", help="Export one layer (or all layers) of a PSD as PNG (offline).")
    export.add_argument("file")
    select = export.add_mutually_exclusive_group()
    select.add_argument("--name", help="Layer name (unicode or Pascal); ambiguous names need --index.")
    select.add_argument("--index", type=int, help="Layer index as reported by psd-layers.")
    select.add_argument("--all", action="store_true", help="Export every layer with pixel data.")
    export.add_argument("--out", help="Output PNG path for a single layer.")
    export.add_argument("--outdir", help="Directory for --all exports (default output/imagegen).")
    export.add_argument("--force", action="store_true")
    export.set_defaults(run=psd_export)

    compose = commands.add_parser("psd-compose", help="Compose PNGs into a new layered PSD (offline).")
    compose.add_argument("--layer", action="append", required=True, metavar="NAME=PATH",
                         help="Repeatable, bottom layer first; all PNGs must share one canvas size.")
    compose.add_argument("--out", required=True, help="Output PSD path (a new file; never written back into an existing PSD).")
    compose.add_argument("--force", action="store_true")
    compose.set_defaults(run=psd_compose)
    return image


def main() -> None:
    args = parser().parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
