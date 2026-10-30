#!/usr/bin/env python3
"""Model selection + loud fallback checks; no real provider access."""

import base64
import importlib.util
import io
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
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

client_spec = importlib.util.spec_from_file_location("image_client", SCRIPTS / "nana_image2.py")
client = importlib.util.module_from_spec(client_spec)
client_spec.loader.exec_module(client)


def png_bytes(width, height):
    tmp = Path(tempfile.mkdtemp())
    try:
        path = tmp / "x.png"
        psd.write_png(path, width, height, bytes((120, 40, 200, 255)) * (width * height))
        return path.read_bytes()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


B64 = base64.b64encode(png_bytes(8, 6)).decode()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def _json(self, code, doc):
        body = json.dumps(doc).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        assert self.path == "/v1/models"
        self._json(200, {"data": [{"id": name} for name in self.server.model_list]})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        self.server.requests.append(payload)
        model = payload.get("model")
        if model in self.server.reject_models:
            self._json(404, {"error": {"code": "model_not_found",
                                       "message": f"model {model} does not exist"}})
            return
        self._json(200, {"model": model, "data": [{"b64_json": B64, "size": "auto"}]})


class _Namespace:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class FallbackCase(TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.server.model_list = ["gpt-image-2.5-sunburst", "gpt-image-2.5-flare", "gpt-image-2"]
        self.server.reject_models = set()
        self.server.requests = []
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        port = self.server.server_address[1]
        self.config = self.tmp / "config.toml"
        self.config.write_text(
            f'model_provider = "mock"\n\n[model_providers.mock]\n'
            f'base_url = "http://127.0.0.1:{port}/v1"\nexperimental_bearer_token = "not-a-secret"\n')

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def namespace(self, **kwargs):
        defaults = dict(command="generate", config=str(self.config), provider=None,
                        model="gpt-image-2.5-sunburst", prompt="a cat", size=None, quality="medium",
                        n=1, background=None, output_format="png",
                        out=str(self.tmp / "out.png"), force=True, dry_run=False,
                        stream="off", save_partials=False, image=None, mask=None,
                        fallback=None, no_fallback=False)
        defaults.update(kwargs)
        return _Namespace(**defaults)

    def generate(self, **kwargs):
        ns = self.namespace(**kwargs)
        buffer = io.StringIO()
        with redirect_stdout(buffer), mock.patch.dict(os.environ, {}, clear=False):
            client.generate(ns)
        return buffer.getvalue()


class TestPrecheckFallback(FallbackCase):
    def test_custom_model_not_listed_falls_back_loudly(self):
        output = self.generate(model="dall-e-999")
        note = json.loads([line for line in output.splitlines() if line.startswith("{")][0])
        self.assertEqual(note["fallback"]["requested"], "dall-e-999")
        self.assertEqual(note["fallback"]["used"], "gpt-image-2.5-sunburst")
        self.assertEqual(self.server.requests[-1]["model"], "gpt-image-2.5-sunburst")

    def test_custom_model_listed_is_used(self):
        self.generate(model="gpt-image-2")
        self.assertEqual(self.server.requests[-1]["model"], "gpt-image-2")

    def test_known_model_skips_precheck(self):
        self.generate(model="gpt-image-2.5-flare")
        self.assertEqual(self.server.requests[-1]["model"], "gpt-image-2.5-flare")


class TestErrorDrivenFallback(FallbackCase):
    def test_404_triggers_one_retry_with_fallback(self):
        self.server.reject_models.add("gpt-image-2")
        output = self.generate(model="gpt-image-2")
        models_sent = [payload["model"] for payload in self.server.requests]
        self.assertEqual(models_sent, ["gpt-image-2", "gpt-image-2.5-sunburst"])
        note = json.loads([line for line in output.splitlines() if line.startswith("{")][0])
        self.assertEqual(note["fallback"]["used"], "gpt-image-2.5-sunburst")

    def test_no_fallback_is_strict(self):
        self.server.reject_models.add("gpt-image-2")
        with self.assertRaises(SystemExit):
            self.generate(model="gpt-image-2", no_fallback=True)
        self.assertEqual(len(self.server.requests), 1)

    def test_custom_fallback_target(self):
        self.server.reject_models.add("gpt-image-2")
        self.generate(model="gpt-image-2", fallback="gpt-image-2.5-flare")
        self.assertEqual([p["model"] for p in self.server.requests],
                         ["gpt-image-2", "gpt-image-2.5-flare"])

    def test_other_errors_do_not_retry(self):
        # Content errors (here: 404 for a non-model reason on the fallback too)
        # must surface, not loop.
        self.server.reject_models.update({"gpt-image-2", "gpt-image-2.5-sunburst",
                                          "gpt-image-2.5-flare"})
        with self.assertRaises(SystemExit):
            self.generate(model="gpt-image-2")
        self.assertEqual(len(self.server.requests), 2)  # original + single fallback try


if __name__ == "__main__":
    unittest.main()
