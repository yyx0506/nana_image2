#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Minimal layered-PSD and PNG I/O for the imagegen skill.

Pure standard library, Python 3.9+. Covers the subset the AI workflow needs:

Reading  : 8-bit RGB PSDs, layer records (name/bbox/visibility/opacity) and
           bitmap pixels, with raw / RLE / ZIP / ZIP-with-prediction channel
           compression. Files without layers fall back to the composite.
Writing  : simple layered PSDs (one RGBA bitmap per layer, raw channels) that
           Photoshop, Photopea, Krita and Affinity open without conversion.
PNG     : read (filters 0-4) and write, 8-bit RGBA, as the bridge format.

Explicitly unsupported - these fail with a clear error, never silently guess:
PSB (version 2), depths other than 8 bit, color modes other than RGB,
interlaced PNG, layer masks as separate exports, writing back into an
existing PSD (compose always produces a new file).
"""

from __future__ import annotations

import re
import struct
import zlib
from pathlib import Path


class PsdError(Exception):
    """Raised for unsupported or malformed input; the message is user-facing."""


# --------------------------------------------------------------------------
# PNG
# --------------------------------------------------------------------------
def write_png(path: Path, width: int, height: int, rgba: bytes) -> None:
    """Write an 8-bit RGBA PNG. rgba must be width*height*4 bytes."""
    if len(rgba) != width * height * 4:
        raise PsdError("write_png: pixel buffer size does not match dimensions.")
    raw = bytearray()
    stride = width * 4
    for y in range(height):
        raw.append(0)  # filter: None
        raw += rgba[y * stride:(y + 1) * stride]

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    body = (struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6)))
    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", body[:13])
                           + body[13:] + chunk(b"IEND", b""))


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def read_png(path: Path) -> tuple[int, int, bytearray]:
    """Read a PNG into (width, height, RGBA). 8-bit non-interlaced only."""
    data = Path(path).read_bytes()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise PsdError(f"{path}: not a PNG file.")
    offset, width, height, depth, color, interlace = 8, 0, 0, 0, 0, 0
    idat = bytearray()
    palette, trns = b"", b""
    while offset < len(data):
        length, tag = struct.unpack_from(">I4s", data, offset)
        offset += 8
        payload = data[offset:offset + length]
        offset += length + 4  # skip CRC
        if tag == b"IHDR":
            width, height, depth, color, _comp, _filt, interlace = struct.unpack(">IIBBBBB", payload)
        elif tag == b"IDAT":
            idat += payload
        elif tag == b"PLTE":
            palette = payload
        elif tag == b"tRNS":
            trns = payload
        elif tag == b"IEND":
            break
    if depth != 8 or interlace != 0:
        raise PsdError(f"{path}: only 8-bit non-interlaced PNG is supported (got depth={depth}, interlace={interlace}).")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color)
    if channels is None:
        raise PsdError(f"{path}: unsupported PNG color type {color}.")
    stride = width * channels
    raw = zlib.decompress(bytes(idat))
    if len(raw) < (stride + 1) * height:
        raise PsdError(f"{path}: PNG pixel data is truncated.")
    out = bytearray(width * height * 4)
    prev = bytearray(stride)
    pos = 0
    for y in range(height):
        filt = raw[pos]
        pos += 1
        row = bytearray(raw[pos:pos + stride])
        pos += stride
        for x in range(stride):
            left = row[x - channels] if x >= channels else 0
            up = prev[x]
            upleft = prev[x - channels] if x >= channels else 0
            if filt == 1:
                row[x] = (row[x] + left) & 0xFF
            elif filt == 2:
                row[x] = (row[x] + up) & 0xFF
            elif filt == 3:
                row[x] = (row[x] + (left + up) // 2) & 0xFF
            elif filt == 4:
                row[x] = (row[x] + _paeth(left, up, upleft)) & 0xFF
            elif filt != 0:
                raise PsdError(f"{path}: unknown PNG filter {filt}.")
        prev = row
        base = y * width * 4
        for x in range(width):
            i = x * channels
            o = base + x * 4
            if color == 6:
                out[o:o + 4] = row[i:i + 4]
            elif color == 2:
                out[o:o + 3] = row[i:i + 3]
                out[o + 3] = 255
            elif color == 4:
                out[o] = out[o + 1] = out[o + 2] = row[i]
                out[o + 3] = row[i + 1]
            elif color == 0:
                out[o] = out[o + 1] = out[o + 2] = row[i]
                out[o + 3] = 255
            elif color == 3:
                if len(palette) < (row[i] + 1) * 3:
                    raise PsdError(f"{path}: palette index out of range.")
                out[o:o + 3] = palette[row[i] * 3:row[i] * 3 + 3]
                out[o + 3] = trns[row[i]] if row[i] < len(trns) else 255
    return width, height, out


# --------------------------------------------------------------------------
# PSD reading
# --------------------------------------------------------------------------
class Layer:
    def __init__(self, index: int, name: str, rect, visible: bool, opacity: int,
                 flags: int, rgba: bytearray | None):
        self.index, self.name = index, name
        self.top, self.left, self.bottom, self.right = rect
        self.visible, self.opacity, self.flags = visible, opacity, flags
        self.rgba = rgba

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)

    def summary(self) -> dict:
        return {
            "index": self.index,
            "name": self.name,
            "bbox": [self.left, self.top, self.right, self.bottom],
            "size": f"{self.width}x{self.height}",
            "visible": self.visible,
            "opacity": round(self.opacity / 255, 3),
            "pixels": self.rgba is not None,
        }


def _unpack_rle(data: bytes, row_counts, expected: int) -> bytearray:
    """Decode PackBits rows into one planar buffer of `expected` bytes."""
    out = bytearray()
    pos = 0
    for count in row_counts:
        row = bytearray()
        end = pos + count
        while pos < end:
            header = data[pos]
            pos += 1
            if header == 128:
                continue
            if header > 128:
                out_of = data[pos]
                pos += 1
                row += bytes([out_of]) * (257 - header)
            else:
                take = header + 1
                row += data[pos:pos + take]
                pos += take
        out += row
    if len(out) != expected:
        raise PsdError(f"RLE data decoded to {len(out)} bytes, expected {expected}.")
    return out


def _unpredict(plane: bytes, width: int, height: int) -> bytes:
    """Reverse ZIP-with-prediction (per-row prefix sum, TIFF predictor 2)."""
    out = bytearray(plane)
    for y in range(height):
        base = y * width
        running = 0
        for x in range(width):
            running = (running + out[base + x]) & 0xFF
            out[base + x] = running
    return bytes(out)


class PsdDoc:
    def __init__(self, width: int, height: int, layers, composite):
        self.width, self.height, self.layers, self.composite = width, height, layers, composite


def read_psd(path: Path) -> PsdDoc:
    data = memoryview(Path(path).read_bytes())
    if len(data) < 26 or bytes(data[:4]) != b"8BPS":
        raise PsdError(f"{path}: not a PSD file.")
    version, = struct.unpack_from(">H", data, 4)
    if version != 1:
        raise PsdError(f"{path}: PSB (version {version}) is not supported; re-save as PSD.")
    channels, height, width, depth, mode = struct.unpack_from(">HIIHH", data, 12)
    if depth != 8:
        raise PsdError(f"{path}: only 8-bit depth is supported (got {depth}).")
    if mode != 3:
        raise PsdError(f"{path}: only RGB color mode is supported (got mode {mode}).")

    offset = 26
    offset += 4 + struct.unpack_from(">I", data, offset)[0]   # color mode data
    offset += 4 + struct.unpack_from(">I", data, offset)[0]   # image resources
    section_end = offset + 4 + struct.unpack_from(">I", data, offset)[0]
    offset += 4                                               # layer & mask length

    layer_info_len = struct.unpack_from(">I", data, offset)[0]
    offset += 4
    layer_info_end = offset + layer_info_len
    count = struct.unpack_from(">h", data, offset)[0]
    offset += 2
    records = []
    for index in range(abs(count)):
        rect = struct.unpack_from(">iiii", data, offset)
        offset += 16
        ch_count = struct.unpack_from(">H", data, offset)[0]
        offset += 2
        chan_info = []
        for _ in range(ch_count):
            chan_id, length = struct.unpack_from(">hI", data, offset)
            offset += 6
            chan_info.append((chan_id, length))
        offset += 4                                           # '8BIM'
        offset += 4                                           # blend key
        opacity, _clipping, flags, _filler = bytes(data[offset:offset + 4])
        offset += 4
        extra_len = struct.unpack_from(">I", data, offset)[0]
        offset += 4
        extra_end = offset + extra_len
        mask_len = struct.unpack_from(">I", data, offset)[0]
        offset += 4 + mask_len
        ranges_len = struct.unpack_from(">I", data, offset)[0]
        offset += 4 + ranges_len
        name_len = data[offset]
        pascal_name = bytes(data[offset + 1:offset + 1 + name_len]).decode("mac-roman", "replace")
        offset += (1 + name_len + 3) & ~3
        unicode_name = pascal_name
        while offset < extra_end - 8:
            block_sig = bytes(data[offset:offset + 4])
            key = bytes(data[offset + 4:offset + 8])
            block_len = struct.unpack_from(">I", data, offset + 8)[0]
            if block_sig not in (b"8BIM", b"8B64"):
                break
            if key == b"luni":
                chars = struct.unpack_from(">I", data, offset + 12)[0]
                raw = bytes(data[offset + 16:offset + 16 + chars * 2])
                unicode_name = raw.decode("utf-16-be", "replace")
            offset += 12 + block_len + (block_len & 1)
        offset = extra_end
        records.append((unicode_name, rect, not bool(flags & 2), opacity, flags, chan_info))

    layers = []
    for index, (name, rect, visible, opacity, flags, chan_info) in enumerate(records):
        top, left, bottom, right = rect
        w, h = max(0, right - left), max(0, bottom - top)
        planes = {}
        supported = True
        for chan_id, length in chan_info:
            compression = struct.unpack_from(">H", data, offset)[0]
            data_start = offset + 2
            if compression not in (0, 1, 2, 3):
                supported = False
            elif w and h and chan_id in (0, 1, 2, -1):
                try:
                    if compression == 0:
                        plane = bytes(data[data_start:data_start + w * h])
                    elif compression == 1:
                        counts = struct.unpack_from(f">{h}H", data, data_start)
                        packed_start = data_start + h * 2
                        plane = bytes(_unpack_rle(bytes(data[packed_start:]), counts, w * h))
                    else:
                        blob = bytes(data[data_start:data_start + length - 2])
                        plane = zlib.decompress(blob)
                        if compression == 3:
                            plane = _unpredict(plane, w, h)
                except Exception as exc:  # noqa: BLE001
                    raise PsdError(f"{path}: layer '{name}' channel {chan_id}: {exc}") from exc
                if len(plane) != w * h:
                    raise PsdError(f"{path}: layer '{name}' channel {chan_id} has {len(plane)} bytes, expected {w * h}.")
                planes[chan_id] = plane
            offset += length
        rgba = None
        if supported and w and h and all(cid in planes for cid in (0, 1, 2)):
            rgba = bytearray(w * h * 4)
            for i in range(w * h):
                rgba[i * 4] = planes[0][i]
                rgba[i * 4 + 1] = planes[1][i]
                rgba[i * 4 + 2] = planes[2][i]
                rgba[i * 4 + 3] = planes[-1][i] if -1 in planes else 255
        layers.append(Layer(index, name, rect, visible, opacity, flags, rgba))

    offset = max(offset, layer_info_end)
    composite = None
    if offset + 2 <= min(section_end, len(data)) and not layers:
        composite = _read_composite(data, offset, width, height, channels, path)
    return PsdDoc(width, height, layers, composite)


def _read_composite(data: memoryview, offset: int, width: int, height: int, channels: int, path):
    compression = struct.unpack_from(">H", data, offset)[0]
    offset += 2
    size = width * height
    try:
        if compression == 0:
            planes = [bytes(data[offset + c * size:offset + (c + 1) * size]) for c in range(channels)]
        elif compression == 1:
            counts = struct.unpack_from(f">{height * channels}H", data, offset)
            offset += height * channels * 2
            packed = bytes(data[offset:])
            flat = _unpack_rle(packed, counts, size * channels)
            planes = [flat[c * size:(c + 1) * size] for c in range(channels)]
        elif compression in (2, 3):
            flat = zlib.decompress(bytes(data[offset:]))
            if compression == 3:
                flat = _unpredict(flat, width, height * channels)
            planes = [flat[c * size:(c + 1) * size] for c in range(channels)]
        else:
            raise PsdError(f"composite compression {compression} is not supported.")
    except PsdError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise PsdError(f"{path}: cannot decode composite image: {exc}") from exc
    if channels < 3:
        raise PsdError(f"{path}: composite has {channels} channels; expected RGB.")
    rgba = bytearray(width * height * 4)
    for i in range(size):
        rgba[i * 4:i * 4 + 3] = planes[0][i], planes[1][i], planes[2][i]
        rgba[i * 4 + 3] = planes[3][i] if channels > 3 else 255
    return rgba


# --------------------------------------------------------------------------
# PSD writing
# --------------------------------------------------------------------------
def _plane(rgba: bytes, width: int, height: int, channel: int) -> bytes:
    return bytes(rgba[(y * width + x) * 4 + channel]
                 for y in range(height) for x in range(width))


def _packbits(data: bytes) -> bytes:
    """Encode PackBits runs (>=3) and literals; valid classic RLE."""
    out = bytearray()
    i, n = 0, len(data)
    while i < n:
        run = 1
        while i + run < n and run < 128 and data[i + run] == data[i]:
            run += 1
        if run >= 3:
            out.append(257 - run)
            out.append(data[i])
            i += run
            continue
        literal = 1
        while i + literal < n and literal < 128:
            j = i + literal
            if j + 2 < n and data[j] == data[j + 1] == data[j + 2]:
                break
            literal += 1
        out.append(literal - 1)
        out += data[i:i + literal]
        i += literal
    return bytes(out)


def _predict(plane: bytes, width: int, height: int) -> bytes:
    """Apply per-row horizontal delta (TIFF predictor 2), inverse of _unpredict."""
    out = bytearray(plane)
    for y in range(height):
        base = y * width
        running = 0
        for x in range(width):
            value = out[base + x]
            out[base + x] = (value - running) & 0xFF
            running = value
    return bytes(out)


def _encode_channel(plane: bytes, width: int, height: int, compression: str) -> tuple[int, bytes]:
    """Return (compression_code, body) for one planar channel buffer."""
    if compression == "raw":
        return 0, plane
    if compression == "rle":
        rows = [_packbits(plane[y * width:(y + 1) * width]) for y in range(height)]
        counts = struct.pack(f">{len(rows)}H", *(len(row) for row in rows))
        return 1, counts + b"".join(rows)
    if compression == "zip":
        return 2, zlib.compress(plane, 6)
    if compression == "zip+prediction":
        return 3, zlib.compress(_predict(plane, width, height), 6)
    raise PsdError(f"write_psd: unknown compression {compression!r} (raw/rle/zip/zip+prediction).")


def flatten(width: int, height: int, layers) -> bytearray:
    """Simple source-over composite, bottom layer first, alpha respected."""
    size = width * height
    out = bytearray(size * 4)
    for layer in layers:
        if not layer.get("visible", True) or layer.get("rgba") is None:
            continue
        src, op = layer["rgba"], layer.get("opacity", 255) / 255.0
        for i in range(size):
            sa = src[i * 4 + 3] / 255.0 * op
            if sa == 0:
                continue
            da = out[i * 4 + 3] / 255.0
            oa = sa + da * (1 - sa)
            if oa == 0:
                continue
            for c in range(3):
                out[i * 4 + c] = int((src[i * 4 + c] * sa + out[i * 4 + c] * da * (1 - sa)) / oa)
            out[i * 4 + 3] = int(oa * 255)
    return out


def write_psd(path: Path, width: int, height: int, layers, compression: str = "rle") -> None:
    """Write a layered PSD. layers: [{name, rgba(w*h*4), visible, opacity}], bottom first.

    compression: raw (largest, simplest), rle (default, what Photoshop itself
    uses), zip, zip+prediction.
    """
    if not layers:
        raise PsdError("write_psd: at least one layer is required.")
    for layer in layers:
        if len(layer["rgba"]) != width * height * 4:
            raise PsdError(f"write_psd: layer '{layer['name']}' pixel size does not match the canvas.")

    records, channel_data = bytearray(), bytearray()
    for layer in layers:
        encoded = []
        for chan_id in (-1, 0, 1, 2):
            plane = _plane(layer["rgba"], width, height, 3 if chan_id == -1 else chan_id)
            encoded.append((chan_id,) + _encode_channel(plane, width, height, compression))
        records += struct.pack(">iiii", 0, 0, height, width)
        records += struct.pack(">H", len(encoded))
        for chan_id, code, body in encoded:
            # The channel length in the record must cover the real encoded
            # body so any reader can walk the section; it is NOT always 2+w*h.
            records += struct.pack(">hI", chan_id, 2 + len(body))
        records += b"8BIM" + b"norm"
        records += bytes([layer.get("opacity", 255), 0,
                          0 if layer.get("visible", True) else 2, 0])
        name = layer["name"][:255]
        encoded_name = name.encode("mac-roman", "replace")[:255]
        extra = bytearray()
        extra += struct.pack(">I", 0)                              # mask
        extra += struct.pack(">I", 0)                              # blending ranges
        pascal = bytes([len(encoded_name)]) + encoded_name
        pascal += b"\x00" * (-len(pascal) % 4)
        extra += pascal
        utf16 = name.encode("utf-16-be")
        luni = struct.pack(">I", len(utf16) // 2) + utf16
        luni += b"\x00" * (len(luni) & 1)
        extra += b"8BIM" + b"luni" + struct.pack(">I", len(luni)) + luni
        if len(extra) & 1:
            extra += b"\x00"
        records += struct.pack(">I", len(extra)) + extra
        for _chan_id, code, body in encoded:
            channel_data += struct.pack(">H", code) + body

    layer_info = struct.pack(">h", len(layers)) + bytes(records) + bytes(channel_data)
    if len(layer_info) & 1:
        layer_info += b"\x00"
    section = struct.pack(">I", len(layer_info)) + layer_info + struct.pack(">I", 0)

    composite = flatten(width, height, layers)
    planes = [_plane(composite, width, height, channel) for channel in (0, 1, 2)]
    code, body = _encode_channel(b"".join(planes), width, height * 3, compression)
    image_data = struct.pack(">H", code) + body

    header = b"8BPS" + struct.pack(">H", 1) + b"\x00" * 6
    header += struct.pack(">HIIHH", 3, height, width, 8, 3)
    Path(path).write_bytes(header
                           + struct.pack(">I", 0)          # color mode data
                           + struct.pack(">I", 0)          # image resources
                           + struct.pack(">I", len(section)) + section
                           + image_data)


def compose_from_pngs(entries, out_path: Path) -> dict:
    """entries: [(name, png_path)] bottom-first; all must share one canvas size."""
    if not entries:
        raise PsdError("psd-compose: at least one --layer NAME=PATH is required.")
    layers, canvas = [], None
    for name, png in entries:
        width, height, rgba = read_png(Path(png))
        if canvas is None:
            canvas = (width, height)
        elif (width, height) != canvas:
            raise PsdError(
                f"psd-compose: '{png}' is {width}x{height} but the canvas is "
                f"{canvas[0]}x{canvas[1]}; resize layers to a common size first.")
        layers.append({"name": name, "rgba": bytes(rgba), "visible": True, "opacity": 255})
    write_psd(out_path, canvas[0], canvas[1], layers)
    return {"canvas": f"{canvas[0]}x{canvas[1]}", "layers": len(layers), "out": str(out_path)}
