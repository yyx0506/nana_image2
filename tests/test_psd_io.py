#!/usr/bin/env python3
"""Offline PSD/PNG/TOML fallback checks; no provider access required."""

import importlib.util
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zlib

SCRIPTS = Path(__file__).parents[1] / "scripts"
spec = importlib.util.spec_from_file_location("psd_io", SCRIPTS / "psd_io.py")
psd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(psd)

client_spec = importlib.util.spec_from_file_location("image_client", SCRIPTS / "sub2api_image_gen.py")
client = importlib.util.module_from_spec(client_spec)
client_spec.loader.exec_module(client)


def solid(width, height, rgba):
    return bytes(rgba) * (width * height)


class TempCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        for item in self.tmp.iterdir():
            item.unlink()
        self.tmp.rmdir()


class TestPsdRoundTrip(TempCase):
    def test_layered_round_trip_all_compressions(self):
        width, height = 12, 8
        layers = [
            {"name": "背景", "rgba": solid(width, height, (255, 0, 0, 255)),
             "visible": True, "opacity": 255},
            {"name": "subject with spaces!", "rgba": solid(width, height, (0, 255, 0, 128)),
             "visible": True, "opacity": 255},
        ]
        for compression in ("raw", "rle", "zip", "zip+prediction"):
            with self.subTest(compression=compression):
                path = self.tmp / f"{compression}.psd"
                psd.write_psd(path, width, height, layers, compression=compression)
                doc = psd.read_psd(path)
                self.assertEqual((doc.width, doc.height), (width, height))
                self.assertEqual([layer.name for layer in doc.layers],
                                 ["背景", "subject with spaces!"])
                bottom = doc.layers[0].rgba
                self.assertEqual(bytes(bottom[0:4]), bytes([255, 0, 0, 255]))
                top = doc.layers[1].rgba
                self.assertEqual(bytes(top[0:4]), bytes([0, 255, 0, 128]))

    def test_hidden_layer_flag_round_trip(self):
        layers = [{"name": "off", "rgba": solid(4, 4, (9, 9, 9, 255)), "visible": False, "opacity": 255}]
        path = self.tmp / "hidden.psd"
        psd.write_psd(path, 4, 4, layers)
        doc = psd.read_psd(path)
        self.assertFalse(doc.layers[0].visible)

    def test_flatten_blends_alpha(self):
        width, height = 2, 2
        layers = [
            {"name": "red", "rgba": solid(width, height, (255, 0, 0, 255)), "visible": True, "opacity": 255},
            {"name": "half-green", "rgba": solid(width, height, (0, 255, 0, 128)), "visible": True, "opacity": 255},
        ]
        composite = psd.flatten(width, height, layers)
        r, g, b, a = composite[0:4]
        self.assertEqual(a, 255)
        self.assertEqual(b, 0)
        # source-over: red 255 under green at alpha 128/255 -> both ~half
        self.assertIn(r, (127, 128))
        self.assertIn(g, (127, 128))

    def test_compose_from_pngs_requires_matching_canvas(self):
        psd.write_png(self.tmp / "small.png", 4, 4, solid(4, 4, (1, 2, 3, 255)))
        psd.write_png(self.tmp / "large.png", 8, 4, solid(8, 4, (1, 2, 3, 255)))
        with self.assertRaises(psd.PsdError):
            psd.compose_from_pngs([("a", self.tmp / "small.png"),
                                   ("b", self.tmp / "large.png")], self.tmp / "out.psd")

    def test_unsupported_files_fail_loudly(self):
        psb = self.tmp / "fake.psd"
        header = b"8BPS" + struct.pack(">H", 2) + b"\x00" * 6 + struct.pack(">HIIHH", 3, 1, 1, 8, 3)
        psb.write_bytes(header + b"\x00" * 12)
        with self.assertRaises(psd.PsdError):
            psd.read_psd(psb)
        notpsd = self.tmp / "nope.psd"
        notpsd.write_bytes(b"clearly not a psd at all")
        with self.assertRaises(psd.PsdError):
            psd.read_psd(notpsd)


class TestPng(TempCase):
    def test_png_round_trip(self):
        width, height = 5, 3
        pixels = bytearray(width * height * 4)
        for i in range(width * height):
            pixels[i * 4:i * 4 + 4] = bytes([(i * 7) % 256, (i * 13) % 256, (i * 29) % 256, (i * 53) % 256])
        psd.write_png(self.tmp / "x.png", width, height, bytes(pixels))
        w, h, out = psd.read_png(self.tmp / "x.png")
        self.assertEqual((w, h), (width, height))
        self.assertEqual(bytes(out), bytes(pixels))

    def test_png_filters_decode(self):
        """Hand-built scanlines using Sub/Up/Average/Paeth must decode identically."""
        width, height, bpp = 4, 4, 4
        rows = [bytes([(x * 31 + y * 7 + c * 11) % 256
                       for x in range(width) for c in range(bpp)])
                for y in range(height)]
        filters = [0, 1, 2, 3, 4][:height]
        raw = bytearray()
        prev = bytes(width * bpp)
        for filt, row in zip(filters, rows):
            raw.append(filt)
            if filt == 0:
                raw += row
                prev = row
            elif filt == 1:
                raw += bytes((row[i] - (row[i - bpp] if i >= bpp else 0)) & 0xFF
                             for i in range(width * bpp))
                prev = row
            elif filt == 2:
                raw += bytes((row[i] - prev[i]) & 0xFF for i in range(width * bpp))
                prev = row
            elif filt == 3:
                raw += bytes((row[i] - ((row[i - bpp] if i >= bpp else 0) + prev[i]) // 2) & 0xFF
                             for i in range(width * bpp))
                prev = row
            else:
                def paeth(a, b, c):
                    p = a + b - c
                    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                    return a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                raw += bytes((row[i] - paeth(row[i - bpp] if i >= bpp else 0, prev[i],
                                             prev[i - bpp] if i >= bpp else 0)) & 0xFF
                             for i in range(width * bpp))
                prev = row

        def chunk(tag, data):
            return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

        ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
        png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
               + chunk(b"IDAT", zlib.compress(bytes(raw))) + chunk(b"IEND", b""))
        path = self.tmp / "filtered.png"
        path.write_bytes(png)
        w, h, out = psd.read_png(path)
        self.assertEqual(bytes(out), b"".join(rows))


class TestRle(unittest.TestCase):
    def test_packbits_round_trip_with_runs(self):
        data = bytes([5] * 300 + list(range(200)) + [9] * 3)
        packed = psd._packbits(data)
        counts = [len(packed)]
        out = psd._unpack_rle(packed, counts, len(data))
        self.assertEqual(bytes(out), data)

    def test_predict_unpredict_inverse(self):
        plane = bytes([(x * 17 + y * 3) % 256 for y in range(6) for x in range(9)])
        predicted = psd._predict(plane, 9, 6)
        self.assertEqual(psd._unpredict(predicted, 9, 6), plane)


class TestMiniToml(unittest.TestCase):
    def test_parses_provider_subset(self):
        text = (
            '# comment\n'
            'model_provider = "sub2api-retry"  # trailing comment\n'
            '\n'
            '[model_providers.sub2api-retry]\n'
            'name = "Gateway (retry)"\n'
            'base_url = "http://127.0.0.1:8888/v1"\n'
            'http_headers = { Authorization = "Bearer sk-live#notacomment", "X-A" = \'plain\' }\n'
            '[profiles.heavy]\n'
            'effort = 5\n'
            'flag = true\n'
        )
        parsed = client._mini_toml(text)
        self.assertEqual(parsed["model_provider"], "sub2api-retry")
        provider = parsed["model_providers"]["sub2api-retry"]
        self.assertEqual(provider["base_url"], "http://127.0.0.1:8888/v1")
        self.assertEqual(provider["http_headers"]["Authorization"], "Bearer sk-live#notacomment")
        self.assertEqual(provider["http_headers"]["X-A"], "plain")
        self.assertEqual(parsed["profiles"]["heavy"]["effort"], 5)
        self.assertIs(parsed["profiles"]["heavy"]["flag"], True)

    def test_matches_tomllib(self):
        text = (
            'model_provider = "p"\n'
            '[model_providers.p]\n'
            'base_url = "http://x/v1"\n'
            'experimental_bearer_token = "t0k"\n'
            'http_headers = { Authorization = "Bearer y" }\n'
        )
        import tomllib
        self.assertEqual(client._mini_toml(text), tomllib.loads(text))


class TestCliPsd(TempCase):
    def _write_two_layer_psd(self):
        layers = [
            {"name": "背景", "rgba": solid(6, 4, (200, 40, 40, 255)), "visible": True, "opacity": 255},
            {"name": "logo", "rgba": solid(6, 4, (10, 10, 220, 255)), "visible": True, "opacity": 255},
        ]
        path = self.tmp / "in.psd"
        psd.write_psd(path, 6, 4, layers)
        return path

    def test_layers_listing(self):
        import argparse
        import io
        import json
        from contextlib import redirect_stdout
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            client.psd_layers(argparse.Namespace(file=str(self._write_two_layer_psd())))
        payload = json.loads(buffer.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual([item["name"] for item in payload["layers"]], ["背景", "logo"])
        self.assertEqual(payload["canvas"], "6x4")

    def test_export_and_compose_cli(self):
        import argparse
        import io
        from contextlib import redirect_stdout
        source = self._write_two_layer_psd()
        with redirect_stdout(io.StringIO()):
            client.psd_export(argparse.Namespace(file=str(source), name="logo", index=None,
                                                 all=False, out=str(self.tmp / "logo.png"),
                                                 outdir=None, force=False))
        self.assertTrue((self.tmp / "logo.png").exists())
        with redirect_stdout(io.StringIO()):
            client.psd_compose(argparse.Namespace(
                layer=[f"new-bg={self.tmp / 'logo.png'}"], out=str(self.tmp / "new.psd"), force=False))
        doc = psd.read_psd(self.tmp / "new.psd")
        self.assertEqual(doc.layers[0].name, "new-bg")

    def test_export_refuses_overwrite(self):
        import argparse
        source = self._write_two_layer_psd()
        target = self.tmp / "logo.png"
        target.write_bytes(b"existing")
        with self.assertRaises(SystemExit):
            client.psd_export(argparse.Namespace(file=str(source), name="logo", index=None,
                                                 all=False, out=str(target), outdir=None, force=False))


if __name__ == "__main__":
    unittest.main()
