import copy
import json
import unittest
from evidence import control_nodes, compare, metadata_changes, preservation

PREFIX = "PipeWire:Interface:"


def snapshot():
    nodes = [dict(id=i, type=PREFIX+"Node", info=dict(props={"node.name": name,
        "object.serial": i+100, "media.class": media})) for i, name, media in
        ((10, "control-sink", "Audio/Sink"), (11, "control-source", "Audio/Source/Virtual"),
         (12, "virtual-sink", "Audio/Sink"), (13, "virtual-source", "Audio/Source"))]
    return nodes+[dict(id=2, type=PREFIX+"Metadata", props={"metadata.name":"default"}, metadata=[
        dict(subject=0, key="default.audio."+kind, type="Spa:String:JSON", value=dict(name="control-"+kind))
        for kind in ("sink", "source")])]


class DefaultsEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.data = snapshot()
        self.controls = control_nodes(self.data, dict(sink="control-sink", source="control-source"))

    def test_sink_and_silent_source_replacement_remain_distinct_measurements(self):
        value = compare(self.data, self.controls)
        self.assertEqual(value["defaultPreserved"], dict(sink=True, source=True))
        self.data[-1]["metadata"][1]["value"]["name"] = "virtual-source"
        self.assertEqual(compare(self.data, self.controls)["defaultPreserved"], dict(sink=True, source=False))

    def test_registry_reuse_and_missing_default_are_invalid_not_preserved(self):
        self.data[0]["info"]["props"]["object.serial"] += 1
        with self.assertRaises(RuntimeError): compare(self.data, self.controls)
        self.data = snapshot(); self.data[-1]["metadata"].pop()
        with self.assertRaises(RuntimeError): compare(self.data, self.controls)

    def test_configured_name_does_not_substitute_actual_default(self):
        self.data[-1]["metadata"].append(dict(subject=0, key="default.configured.audio.sink",
            type="Spa:String:JSON", value=dict(name="control-sink")))
        self.data[-1]["metadata"][0]["value"]["name"] = "virtual-sink"
        self.assertFalse(compare(self.data, self.controls)["defaultPreserved"]["sink"])

    def test_transient_switch_and_removal_survive_a_restored_final_snapshot(self):
        text = 'Found "default" metadata 2\n'
        for kind, name in (("sink", "control-sink"), ("source", "control-source"),
                           ("sink", "virtual-sink"), ("sink", "control-sink")):
            text += f"update: id:0 key:'default.audio.{kind}' value:'{json.dumps(dict(name=name))}' type:'Spa:String:JSON'\n"
        observed = metadata_changes(text, self.controls)
        self.assertFalse(observed["allObservedDefaultsPreserved"])
        self.assertEqual(len(observed["events"]), 4)
        self.assertEqual(preservation([compare(self.data, self.controls)], observed), dict(sink=False, source=True))
        removed = metadata_changes(text+"remove: id:0 all keys\n", self.controls)
        self.assertIsNone(removed["events"][-1]["name"])

    def test_missing_monitor_initial_keys_rebind_and_unknown_lines_fail(self):
        for text in ('', 'Found "default" metadata 2\n', 'garbage\n',
                     'Found "default" metadata 2\nFound "default" metadata 4\n'):
            with self.subTest(text=text), self.assertRaises(RuntimeError): metadata_changes(text, self.controls)


if __name__ == "__main__": unittest.main()
