import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from export_policy import configuration, publication, validate_node, validate_plan, IgnoreRule, one_shot_snapshot, snapshot_source
from stage_export import ROOT, DEVICE, HELPER, TARGET, OLD, NEW, transform, verify_overlay, digest
from qemu_audio_hotplug import add_payload
from run_export_guest import descriptor_evidence

PARENT = "/sys/devices/platform/vhci_hcd.0/usb1/1-1"
NONCE = "0123456789abcdef"*2
PLAN = dict(actualParent=PARENT, targetParent=PARENT, nonce=NONCE, ruleNonce=NONCE, matched=True)

def stat_with_uid(path, uid):
    # Simulate only ownership for portable non-root CI. Mode, inode, device and
    # file contents remain real; production still requires a root-owned rule.
    fields = list(path.lstat()); fields[4] = uid
    return os.stat_result(fields)

def node(serial=10):
    return dict(id=45,type="PipeWire:Interface:Node",info=dict(props={
        "object.serial":serial,"client.id":40,"device.api":"alsa","api.alsa.pcm.card":0,
        "media.class":"Audio/Sink","madsense.audio.lease":NONCE,"node.name":"madsense-owned-"+NONCE,
        "priority.session":1,"audio.channels":4,"audio.position":"FL,FR,RL,RR",
        "device.vendor.id":"0x054c","device.product.id":"0x0ce6","device.bus":"usb",
        "device.sysfs.path":PARENT[4:]+"/1-1:1.0/sound/card0"},
        params={"EnumFormat":[dict(format="S16LE",rate=48000,channels=4,position=["FL","FR","RL","RR"])]}))

class Contracts(unittest.TestCase):
    def test_one_shot_dump_accepts_only_tombstone_prefix_before_final_snapshot(self):
        live = json.dumps([node()]).encode()
        raw = b'[{"id":48,"info":null}]\n[{"id":49,"props":null}]\n'+live
        self.assertEqual(one_shot_snapshot(raw),[node()])
        self.assertEqual(one_shot_snapshot(live),[node()])
        normalized = (json.dumps([node()],indent=2)+"\n").encode()
        source = snapshot_source(raw,normalized)
        self.assertEqual(source["rawBytes"],len(raw))
        self.assertNotEqual(source,snapshot_source(live,normalized))
        with self.assertRaisesRegex(RuntimeError,"differs from raw"):
            snapshot_source(raw,json.dumps([node(99)]).encode())

    def test_one_shot_dump_rejects_extra_live_frame_truncation_and_tombstones_only(self):
        live = json.dumps([node()]).encode(); removed = b'[{"id":48,"info":null}]'
        for raw in (live+live, live+removed, removed, removed+live[:-1], b'', b'[]',
                    b'[{"id":48,"info":null,"unexpected":true}]'+live,
                    b'[{"id":48,"info":null},{"id":48,"info":null}]'+live):
            with self.subTest(raw=raw),self.assertRaises((RuntimeError,json.JSONDecodeError)):
                one_shot_snapshot(raw)

    def test_atomic_rule_publication_does_not_overwrite_a_racing_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);rule=IgnoreRule(PLAN,root)
            rule.path=root/"lease.rules";rule.temporary=root/"lease.pending"
            rule.path.write_text("foreign-rule")
            try:
                with self.assertRaises(FileExistsError):rule.publish()
                self.assertEqual(rule.path.read_text(),"foreign-rule")
                self.assertIsNotNone(rule.temporary_identity)
            finally:
                with patch.object(Path,"lstat",return_value=stat_with_uid(rule.temporary,0)):
                    rule.remove_temporary()
            self.assertFalse(rule.temporary.exists())
            self.assertEqual(rule.path.read_text(),"foreign-rule")

    def test_temporary_cleanup_preserves_a_replaced_inode(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);rule=IgnoreRule(PLAN,root)
            rule.path=root/"lease.rules";rule.temporary=root/"lease.pending"
            rule.path.write_text("foreign-rule")
            with self.assertRaises(FileExistsError):rule.publish()
            rule.temporary.rename(root/"original.pending")
            rule.temporary.write_text("replacement")
            with patch.object(Path,"lstat",return_value=stat_with_uid(rule.temporary,0)), \
                 self.assertRaisesRegex(RuntimeError,"preserve replacement"):
                rule.remove_temporary()
            self.assertEqual(rule.temporary.read_text(),"replacement")

    def test_temporary_cleanup_rejects_non_root_owner_with_same_inode(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);rule=IgnoreRule(PLAN,root)
            rule.path=root/"lease.rules";rule.temporary=root/"lease.pending"
            rule.path.write_text("foreign-rule")
            with self.assertRaises(FileExistsError):rule.publish()
            with patch.object(Path,"lstat",return_value=stat_with_uid(rule.temporary,1001)), \
                 self.assertRaisesRegex(RuntimeError,"preserve replacement"):
                rule.remove_temporary()
            self.assertEqual(rule.temporary.read_bytes(),rule.content)

    def test_case_cannot_relabel_a_missed_rule_as_a_positive(self):
        validate_plan(PLAN,"owned-automatic")
        for case, plan in (("foreign-parent",PLAN),("foreign-nonce",PLAN),
                           ("owned-explicit",PLAN | {"matched":False}),
                           ("owned-automatic",PLAN | {"ruleNonce":"f"*32})):
            with self.subTest(case=case,plan=plan),self.assertRaises(RuntimeError):validate_plan(plan,case)
    def test_udev_scope_requires_full_parent_and_interface_nonce(self):
        rule=configuration(PARENT,NONCE)
        self.assertIn('DEVPATH=="'+PARENT[4:]+'/1-1:1.0/sound/card[0-9]*"',rule)
        self.assertIn('ATTRS{interface}=="MadSense-audio-lease-'+NONCE+'"',rule)
        self.assertIn('ENV{ACP_IGNORE}:="1"',rule);self.assertIn('ENV{PULSE_IGNORE}:="1"',rule)
        for parent, nonce in ((PARENT+"/*",NONCE),(PARENT+"/../1-2",NONCE),(PARENT,NONCE+'"'),(PARENT,"A"*32)):
            with self.subTest(parent=parent,nonce=nonce),self.assertRaises(RuntimeError):configuration(parent,nonce)

    def test_publication_rejects_transient_card_or_capture_or_late_priority(self):
        value=node();self.assertEqual(publication(json.dumps([value]),PLAN,1)["exportedNodes"],1)
        # Every variant is followed by a good snapshot; an earlier transient
        # must still invalidate the observation.
        variants=[]
        for key,value in (("media.class","Audio/Source"),("priority.session",1009),("madsense.audio.lease","0"*32),
                          ("device.sysfs.path",PARENT[4:]+"0/1-10:1.0/sound/card0")):
            bad=node();bad["info"]["props"][key]=value;variants.append(bad)
        variants.append(dict(type="PipeWire:Interface:Device",id=1,info={"props":{"device.api":"alsa"}}))
        for bad in variants:
            with self.subTest(bad=bad),self.assertRaises(RuntimeError):publication(json.dumps([bad])+json.dumps([node()]),PLAN,1)

    def test_monitor_counts_object_epochs_not_reused_ids(self):
        rows=json.dumps([node(10)])+json.dumps([{"id":45,"info":None}])+json.dumps([node(11)])
        self.assertEqual(publication(rows,PLAN,2)["nodeIdentities"],[[45,10],[45,11]])
        with self.assertRaises(RuntimeError):publication(rows,PLAN,1)
        with self.assertRaises(json.JSONDecodeError):publication(rows[:-1],PLAN,2)

    def test_actual_quad_format_is_required_not_just_properties(self):
        value=node();validate_node(value,PLAN,require_format=True)
        value["info"]["params"]["EnumFormat"][0]["channels"]=2
        with self.assertRaises(RuntimeError):validate_node(value,PLAN,require_format=True)

    def test_overlay_changes_only_exact_device_construction(self):
        source=(ROOT/DEVICE).read_text();changed=transform(source)
        self.assertEqual(source.replace(OLD,"REPLACED"),changed.replace(NEW,"REPLACED"))
        with self.assertRaises(ValueError):transform(source+source)
        with self.assertRaises(ValueError):transform(changed)

    def test_overlay_provenance_rejects_an_extra_or_modified_source_hook(self):
        original=(ROOT/DEVICE).read_bytes();helper=(ROOT/HELPER).read_bytes()
        modified=transform(original.decode().replace("\r\n","\n")).encode()
        value=dict(schema=1,sourceCommit="a"*40,sourceArchiveSha256="b"*64,
            productionBinary=False,kind="audio-interface-research-copy",changes={
                DEVICE:dict(originalSha256=digest(original),instrumentedSha256=digest(modified)),
                TARGET:dict(originalSha256=None,instrumentedSha256=digest(helper),copiedFrom=HELPER)})
        self.assertTrue(verify_overlay(value,original,helper))
        with self.assertRaises(ValueError):verify_overlay(value,original,helper+b"changed")
        value["changes"]["other.cs"]={}
        with self.assertRaises(ValueError):verify_overlay(value,original,helper)

    def test_runtime_descriptor_gate_rejects_hid_and_identity_mutations(self):
        device=bytearray(18);config=bytearray(20);hid=b"known-hid"
        reference=dict(device=device.hex(),config=config.hex(),hid=hid.hex(),strings=["vendor","product"])
        expected=device+config;expected[35]=3
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary);descriptor_evidence(path,1,reference,bytes(expected),hid)
            for changed, report in ((bytes(expected),hid+b"x"),(bytes(expected[:16]+b"\x01"+expected[17:]),hid)):
                with self.assertRaises(RuntimeError):descriptor_evidence(path,2,reference,changed,report)

    def test_actual_payload_imports_without_checkout_and_excludes_build_products(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);payload=root/"payload";payload.mkdir();hashes=add_payload(payload)
            self.assertTrue(any(name.endswith("export/client.c") for name in hashes))
            self.assertFalse(any("/bin/" in name or "/obj/" in name for name in hashes))
            directory=payload/"linux/tests/audio-hotplug/export"
            code="import sys;sys.path.insert(0,sys.argv[1]);import run_export_guest,qemu_export,export_policy,export_desktop;assert callable(run_export_guest.verify_result)"
            result=subprocess.run([sys.executable,"-I","-c",code,str(directory)],cwd=root,capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stderr)

if __name__=="__main__":unittest.main()
