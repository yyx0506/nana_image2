#!/usr/bin/env python3
"""Offline streaming + degradation-verdict checks; no real provider access."""

import base64
import importlib.util
import io
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import sys
import shutil
import tempfile
import threading
from contextlib import redirect_stdout
from unittest import TestCase, mock
import unittest

SCRIPTS = Path(__file__).parents[1] / "scripts"

spec = importlib.util.spec_from_file_location("psd_io", SCRIPTS / "psd_io.py")
psd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(psd)

client_spec = importlib.util.spec_from_file_location("image_client", SCRIPTS / "sub2api_image_gen.py")
client = importlib.util.module_from_spec(client_spec)
client_spec.loader.exec_module(client)


def png_bytes(width, height, pixel=(120, 40, 200, 255)):
    """A real PNG with known dimensions, via the pure-stdlib writer."""
    tmp = Path(tempfile.mkdtemp())
    try:
        path = tmp / "x.png"
        psd.write_png(path, width, height, bytes(pixel) * (width * height))
        return path.read_bytes()
    finally:
        path.unlink(missing_ok=True)
        tmp.rmdir()


class MockHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def _json(self, doc):
        body = json.dumps(doc).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _sse(self, events):
        body = b"".join(b"data: " + json.dumps(event).encode() + b"\n\n" for event in events) + b"data: [DONE]\n\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        ctype = (self.headers.get("Content-Type") or "").lower()
        if "multipart" in ctype:
            # edit path: record the raw body; the test asserts on it directly.
            self.server.last_raw = raw
            image = png_bytes(*self.server.image_size)
            self._json({"model": self.server.model_echo,
                        "data": [{"b64_json": base64.b64encode(image).decode(),
                                  "size": self.server.report_size}]})
            return
        payload = json.loads(raw or b"{}")
        self.server.last_payload = payload
        mode = self.server.mode
        image = png_bytes(*self.server.image_size)
        b64 = base64.b64encode(image).decode()
        if mode == "sse":
            self._sse([
                {"type": "image_generation.partial_image", "partial_image_index": 1, "b64_json": base64.b64encode(png_bytes(4, 4)).decode()},
                {"type": "image_generation.completed", "data": [{"b64_json": b64, "size": self.server.report_size}]},
            ])
        elif mode == "sse_no_final":
            self._sse([
                {"type": "image_generation.partial_image", "partial_image_index": 1, "b64_json": b64},
                {"type": "image_generation.partial_image", "partial_image_index": 2, "b64_json": b64},
            ])
        elif mode == "json_despite_stream":
            self._json({"model": self.server.model_echo, "data": [{"b64_json": b64, "size": self.server.report_size}]})
        else:  # plain json
            self._json({"model": self.server.model_echo, "data": [{"b64_json": b64, "size": self.server.report_size}]})


class MockServer(TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.server = HTTPServer(("127.0.0.1", 0), MockHandler)
        self.server.mode = "json"
        self.server.image_size = (8, 6)
        self.server.report_size = None
        self.server.model_echo = "gpt-image-2.5-sunburst"
        self.server.last_payload = None
        self.server.last_raw = None
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        port = self.server.server_address[1]
        self.config = self.tmp / "config.toml"
        self.config.write_text(
            f'model_provider = "mock"\n\n[model_providers.mock]\n'
            f'base_url = "http://127.0.0.1:{port}"\nexperimental_bearer_token = "not-a-secret"\n')

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def namespace(self, **kwargs):
        defaults = dict(command="generate", config=str(self.config), provider=None,
                        model="gpt-image-2.5-sunburst", prompt="a cat", size=None, quality="medium",
                        n=1, background=None, output_format="png",
                        out=str(self.tmp / "out.png"), force=True, dry_run=False,
                        stream="auto", save_partials=False, image=None, mask=None)
        defaults.update(kwargs)
        return _Namespace(**defaults)


class _Namespace:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class TestStreamHeuristic(TestCase):
    def test_auto_streams_slow_jobs_only(self):
        fast = _Namespace(stream="auto")
        self.assertFalse(client.should_stream(fast, {"quality": "medium", "size": "1024x1024"}))
        self.assertTrue(client.should_stream(fast, {"quality": "high", "size": "1024x1024"}))
        self.assertTrue(client.should_stream(fast, {"quality": "medium", "size": "3840x2160"}))
        self.assertFalse(client.should_stream(fast, {"quality": "medium", "size": "auto"}))

    def test_forced_modes(self):
        self.assertTrue(client.should_stream(_Namespace(stream="on"), {"quality": "low", "size": "auto"}))
        self.assertFalse(client.should_stream(_Namespace(stream="off"), {"quality": "max", "size": "3840x2160"}))


class TestGenerateStream(MockServer):
    def test_sse_generation_saves_final_and_optionally_partials(self):
        self.server.mode = "sse"
        out = self.tmp / "streamed.png"
        buffer = io.StringIO()
        with redirect_stdout(buffer), mock.patch.dict(os.environ, {}, clear=False):
            client.generate(self.namespace(out=str(out), stream="on", save_partials=True,
                                            model="gpt-image-2.5-sunburst"))
        self.assertTrue(out.exists())
        self.assertTrue((self.tmp / "streamed-partial1.png").exists())
        payload = self.server.last_payload
        self.assertIs(payload.get("stream"), True)

    def test_sse_without_final_uses_last_partial(self):
        self.server.mode = "sse_no_final"
        out = self.tmp / "partial.png"
        with redirect_stdout(io.StringIO()):
            client.generate(self.namespace(out=str(out), stream="on"))
        self.assertTrue(out.exists())

    def test_json_fallback_when_gateway_ignores_stream(self):
        self.server.mode = "json_despite_stream"
        out = self.tmp / "fallback.png"
        with redirect_stdout(io.StringIO()):
            client.generate(self.namespace(out=str(out), stream="on"))
        self.assertTrue(out.exists())


class TestDegradationVerdict(MockServer):
    def test_size_mismatch_prints_degraded_json(self):
        # request 1024x1024 (valid), provider returns an 8x6 PNG without a size field
        self.server.report_size = None
        out = self.tmp / "degraded.png"
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            client.generate(self.namespace(out=str(out), size="1024x1024"))
        lines = [line for line in buffer.getvalue().splitlines() if line.startswith("{")]
        verdict = json.loads(lines[-1])
        self.assertTrue(verdict["degraded"])
        self.assertEqual(verdict["requested"], "1024x1024")
        self.assertEqual(verdict["actual"], "8x6")
        self.assertIn("advice", verdict)

    def test_model_echo_swap_detected(self):
        self.server.model_echo = "gpt-image-2-codex"
        self.server.report_size = "auto"
        out = self.tmp / "swap.png"
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            client.generate(self.namespace(out=str(out), size="auto"))
        verdict = json.loads([l for l in buffer.getvalue().splitlines() if l.startswith("{")][-1])
        self.assertTrue(verdict["degraded"])
        self.assertEqual(verdict["model_echo"], "gpt-image-2-codex")

    def test_matching_output_has_no_verdict(self):
        self.server.report_size = "1024x1024"
        out = self.tmp / "fine.png"
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            client.generate(self.namespace(out=str(out), size="1024x1024"))
        self.assertFalse([line for line in buffer.getvalue().splitlines() if line.startswith("{")])


class TestEditNeverStreams(MockServer):
    def test_edit_payload_has_no_stream_field(self):
        # sub2api #8027: edits+stream returns 502 and drains the pool via
        # failover; the edit path must never send a stream flag.
        source = self.tmp / "in.png"
        psd.write_png(source, 4, 4, bytes([1, 2, 3, 255]) * 16)
        out = self.tmp / "edited.png"
        with redirect_stdout(io.StringIO()):
            client.edit(self.namespace(image=str(source), out=str(out)))
        self.assertTrue(out.exists())
        raw = self.server.last_raw or b""
        self.assertNotIn(b'name="stream"', raw)


if __name__ == "__main__":
    unittest.main()
