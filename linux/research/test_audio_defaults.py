import copy
from pathlib import PurePosixPath
import unittest

from audio_defaults import describe_sinks, observe_defaults, select_card


class DefaultAudioEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.sysfs = PurePosixPath("/sys/devices/platform/vhci_hcd.0/usb1/1-1/1-1:1.0/sound/card2")
        self.card = dict(index=7, name="alsa_card.owned", owner_module=12, active_profile="Direct",
                         properties={"alsa.card": "2", "sysfs.path": str(self.sysfs)[4:]})
        channels = ["front-left", "front-right", "rear-left", "rear-right"]
        self.sink = dict(index=9, name="alsa_output.owned.Direct", owner_module=12,
                         properties={"alsa.card": "2"}, channel_map=",".join(channels),
                         volume={channel: {"value": value} for channel, value in zip(channels, (32768, 32768, 65536, 65536))},
                         sample_specification="s16le 4ch 48000Hz", mute=False)

    def test_exact_card_join_uses_alsa_index_and_sysfs_not_description(self):
        foreign = copy.deepcopy(self.card)
        foreign["properties"]["alsa.card"] = "3"
        self.assertIs(select_card([foreign, self.card], 2, self.sysfs), self.card)

    def test_rejects_same_alsa_number_at_another_usb_path(self):
        self.card["properties"]["sysfs.path"] = "/devices/platform/vhci_hcd.0/usb1/1-2/sound/card2"
        with self.assertRaises(RuntimeError):
            select_card([self.card], 2, self.sysfs)

    def test_rejects_ambiguous_card_identity(self):
        with self.assertRaises(RuntimeError):
            select_card([self.card, copy.deepcopy(self.card)], 2, self.sysfs)

    def test_pactl_sink_without_card_field_preserves_channel_volumes(self):
        description = describe_sinks([self.sink], self.card)[0]
        self.assertTrue(description["quadMap"])
        self.assertTrue(description["rearVolumeAt100Percent"])
        self.assertEqual(description["volume"]["front-left"]["value"], 32768)

    def test_does_not_accept_same_alsa_card_from_another_module(self):
        self.sink["owner_module"] = 13
        with self.assertRaises(RuntimeError):
            describe_sinks([self.sink], self.card)

    def test_mismatched_order_or_attenuation_is_not_reported_as_quad_full_volume(self):
        self.sink["channel_map"] = "rear-left,rear-right,front-left,front-right"
        self.sink["volume"]["rear-left"]["value"] = 65535
        description = describe_sinks([self.sink], self.card)[0]
        self.assertFalse(description["quadMap"])
        self.assertFalse(description["rearVolumeAt100Percent"])

    def test_missing_volume_does_not_become_zero_or_full_volume(self):
        del self.sink["volume"]["rear-left"]
        with self.assertRaises(RuntimeError):
            describe_sinks([self.sink], self.card)

    def test_defaults_preserved_only_when_nonempty_and_equal(self):
        observed = observe_defaults({"default_sink_name": "sentinel"}, {"default_sink_name": "sentinel"})
        self.assertTrue(observed["preservedDuringObservation"])
        self.assertFalse(observed["hotplugPreservationValidated"])
        self.assertFalse(observe_defaults({}, {})["preservedDuringObservation"])
        self.assertFalse(observe_defaults({"default_sink_name": "sentinel"}, {"default_sink_name": "controller"})["preservedDuringObservation"])


if __name__ == "__main__":
    unittest.main()
