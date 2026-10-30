#!/usr/bin/env python3
"""Version/upgrade manifest checks; offline via local manifest files."""

import importlib.util
import io
import json
from pathlib import Path
import shutil
import tempfile
from contextlib import redirect_stdout
from unittest import TestCase
import unittest

SCRIPTS = Path(__file__).parents[1] / "scripts"
spec = importlib.util.spec_from_file_location("image_client", SCRIPTS / "nana_image2.py")
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


class _Namespace:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class TestVersionTuple(TestCase):
    def test_comparison(self):
        self.assertGreater(client._version_tuple("1.10.0"), client._version_tuple("1.9.9"))
        self.assertGreater(client._version_tuple("2.0"), client._version_tuple("1.99.1"))
        self.assertEqual(client._version_tuple("1.3.0"), client._version_tuple("1.3.0"))
        self.assertEqual(client._version_tuple("junk"), (0,))
        self.assertGreater(client._version_tuple("1.3"), client._version_tuple("1"))


class TestUpgradeCommand(TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def manifest(self, doc):
        path = self.tmp / "manifest.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        return str(path)

    def run_upgrade(self, manifest=None):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            client.check_upgrade(_Namespace(manifest=manifest))
        return json.JSONDecoder().raw_decode(buffer.getvalue())[0]

    def test_newer_manifest_reports_available(self):
        result = self.run_upgrade(self.manifest(
            {"version": "99.0.0", "url": "http://intranet/new.zip", "notes": "big update"}))
        self.assertTrue(result["upgrade_available"])
        self.assertEqual(result["latest"], "99.0.0")
        self.assertEqual(result["url"], "http://intranet/new.zip")

    def test_same_version_is_up_to_date(self):
        result = self.run_upgrade(self.manifest({"version": client.VERSION}))
        self.assertFalse(result["upgrade_available"])

    def test_older_manifest_not_an_upgrade(self):
        result = self.run_upgrade(self.manifest({"version": "0.1.0"}))
        self.assertFalse(result["upgrade_available"])

    def test_missing_manifest_fails_clearly(self):
        with self.assertRaises(SystemExit):
            self.run_upgrade(str(self.tmp / "nope.json"))

    def test_no_source_configured_fails_clearly(self):
        import os
        saved = os.environ.pop("NANA_IMAGE2_MANIFEST", None)
        try:
            with self.assertRaises(SystemExit):
                self.run_upgrade(None)
        finally:
            if saved:
                os.environ["NANA_IMAGE2_MANIFEST"] = saved


if __name__ == "__main__":
    unittest.main()
