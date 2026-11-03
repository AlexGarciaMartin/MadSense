#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pinned network fixture and actual upstream container-function tests."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prepare_source as kit


def extract_function(source: str, name: str) -> str:
    match = re.search(r"static BOOL get_usb_container_id\([^\n]+\)\n\{.*?\n\}", source, re.S)
    if not match:
        raise AssertionError("Pinned upstream container function changed")
    return match.group(0).replace("get_usb_container_id", name)


class KitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = kit.load_manifest()
        fixtures = os.environ.get("MADSENSE_WINE_FIXTURES")
        cls.data = {}
        for source in cls.manifest["sources"]:
            data = ((Path(fixtures) / source["path"]).read_bytes() if fixtures else kit.fetch(source["url"]))
            cls.data[source["url"]] = kit.verified(data, source["sha256"], source["path"])

    def test_verified_source_applies_only_to_new_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "prepared"
            original = dict(self.data)
            result = kit.prepare(output, downloader=self.data.__getitem__)
            self.assertEqual(result["kind"], "three-file-verification-fixture-not-buildable")
            self.assertEqual(self.data, original)
            self.assertEqual(len(result["sources"]), 3)
            for entry in result["sources"]:
                self.assertNotEqual(entry["original_sha256"], entry["patched_sha256"])
            self.assertEqual(json.loads((output / "MADSENSE-WINE-USB.json").read_text()), result)

    def test_corrupted_upstream_source_refused_before_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "refused"
            def corrupt(url):
                return self.data[url] + b"\n/* unexpected modification */\n"
            with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
                kit.prepare(output, downloader=corrupt)
            self.assertFalse(output.exists())

    def test_existing_output_is_preserved_without_network_access(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            sentinel = output / "installed-runner-must-not-change"
            sentinel.write_bytes(b"unchanged")
            def forbidden(_):
                self.fail("Existing destination should be rejected before downloads")
            with self.assertRaisesRegex(ValueError, "already exists"):
                kit.prepare(output, downloader=forbidden)
            self.assertEqual(sentinel.read_bytes(), b"unchanged")
            self.assertEqual(list(output.iterdir()), [sentinel])

    def test_full_checkout_export_is_separate_and_preserves_input(self):
        # An offline synthetic commit exercises the full export path. Its
        # manifest is substituted only inside this test; the CLI has no bypass.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_tree = root / "source"
            source_tree.mkdir()
            for source in self.manifest["sources"]:
                path = source_tree / source["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(self.data[source["url"]])
            (source_tree / "configure").write_text("fixture configure file\n")
            (source_tree / ".gitignore").write_text("ignored-build-output\n")
            kit.git(source_tree, "init", "--quiet")
            kit.git(source_tree, "config", "core.autocrlf", "false")
            kit.git(source_tree, "add", ".")
            kit.git(source_tree, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                    "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "Isolated test fixture")
            commit = kit.git(source_tree, "rev-parse", "HEAD")
            manifest = {**self.manifest, "wine_commit": commit}
            (source_tree / "ignored-build-output").write_text("do not export\n")
            with mock.patch.object(kit, "load_manifest", return_value=manifest):
                output = root / "full-export"
                result = kit.prepare(output, source_tree)
                self.assertEqual(result["kind"], "full-public-wine-source-for-manual-macos-build")
                self.assertEqual((output / "configure").read_text(), "fixture configure file\n")
                self.assertFalse((output / "ignored-build-output").exists())
                self.assertEqual(kit.git(source_tree, "status", "--porcelain", "--untracked-files=all"), "")
                for source in self.manifest["sources"]:
                    self.assertEqual((source_tree / source["path"]).read_bytes(), self.data[source["url"]])
                with self.assertRaisesRegex(ValueError, "separate"):
                    kit.prepare(source_tree / "nested-output", source_tree)
                (source_tree / "configure").write_text("modified tracked file\n")
                with self.assertRaisesRegex(ValueError, "must be clean"):
                    kit.prepare(root / "dirty-refused", source_tree)
                self.assertFalse((root / "dirty-refused").exists())

    def test_actual_upstream_usb_guid_pairing_and_uid_parser(self):
        compiler = shutil.which(os.environ.get("CC", "cc"))
        if compiler is None:
            if os.environ.get("REQUIRE_C_COMPILER") == "1":
                self.fail("C compiler is required for upstream parser validation")
            self.skipTest("No local C compiler; required in Ubuntu CI")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepared = root / "prepared"
            kit.prepare(prepared, downloader=self.data.__getitem__)
            audio = extract_function((prepared / "dlls/winecoreaudio.drv/coreaudio.c").read_text(), "audio_container")
            bus = extract_function((prepared / "dlls/winebus.sys/main.c").read_text(), "hid_container")
            harness = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef int BOOL;
#define TRUE 1
#define FALSE 0
enum { BUS_TYPE_USB = 1, BUS_TYPE_BLUETOOTH = 2 };
typedef struct { uint32_t Data1; uint16_t Data2, Data3; uint8_t Data4[8]; } GUID;
struct device_desc { unsigned bus_type; uint32_t uid; };
__FUNCTIONS__
int main(void) {
    GUID audio, hid, other;
    struct device_desc usb = { BUS_TYPE_USB, 0x02100000 };
    assert(hid_container(&usb, &hid));
    assert(audio_container("AppleUSBAudioEngine:Sony Interactive Entertainment:DualSense Edge Wireless Controller:2100000:1", &audio));
    assert(memcmp(&audio, &hid, sizeof(GUID)) == 0);
    assert(audio_container("AppleUSBAudioEngine:Sony:Name:with:colons:02100000:2", &audio));
    assert(memcmp(&audio, &hid, sizeof(GUID)) == 0);
    assert(audio_container("AppleUSBAudioEngine:Sony:Other:2200000:1", &other));
    assert(memcmp(&other, &hid, sizeof(GUID)) != 0);
    usb.bus_type = BUS_TYPE_BLUETOOTH;
    assert(!hid_container(&usb, &other));
    usb.bus_type = BUS_TYPE_USB; usb.uid = 0;
    assert(!hid_container(&usb, &other));
    const char *invalid[] = {
        "", "Bluetooth:Sony:Pad:2100000:1", "AppleUSBAudioEngine:Sony:Pad::1",
        "AppleUSBAudioEngine:Sony:Pad:0:1", "AppleUSBAudioEngine:Sony:Pad:xyz:1",
        "AppleUSBAudioEngine:Sony:Pad:2100000junk:1", "AppleUSBAudioEngine:Sony:Pad:100000000:1"
    };
    for (unsigned i = 0; i < sizeof(invalid) / sizeof(invalid[0]); ++i)
        assert(!audio_container(invalid[i], &other));
    // Preserve and expose upstream's permissive parsing instead of silently
    // treating this parser as validation of a physical device or canonical UID.
    assert(audio_container("AppleUSBAudioEngine:Sony:Pad:+2100000:1", &other));
    assert(audio_container("AppleUSBAudioEngine:Sony:Pad:2100000:", &other));
    puts("USB GUID pairing passed; upstream also accepts +hex and empty interface suffix (documented limitation)");
    return 0;
}
'''.replace("__FUNCTIONS__", audio + "\n" + bus)
            source = root / "container-test.c"
            source.write_text(harness, encoding="utf-8")
            binary = root / "container-test"
            subprocess.run([compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", str(source), "-o", str(binary)],
                           check=True, timeout=30)
            subprocess.run([str(binary)], check=True, timeout=10)


if __name__ == "__main__":
    unittest.main(verbosity=2)
