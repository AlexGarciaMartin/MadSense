import copy
import ctypes
import ctypes.util
import hashlib
import json
from pathlib import Path, PurePosixPath
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from pipewire_defaults import PREFIX
from pipewire_gain import (CHANNELS, EXACT_VOLUMES_LUA, compare_pcm_channels, control_snapshot, dump_volumes, exact_volumes,
                           front_preserving_request, marked_pcm, measure_pcm,
                           optional_result, restore_values, route_request, run_optional,
                           require_route_restorable, same_target, stream_binding, target_state)


# Reduced, unmodified binding fields from native run 41632075665, commit 4d5cc8.
# Original pipewire-gain/005-baseline.json SHA256: 2002d3ba39ca458972b58839f87967ec76c1deb12a0bba2a5c619d8b90f8f533
NATIVE_PLAYBACK = [{'id': 40, 'type': 'PipeWire:Interface:Port', 'info': {'props': {'node.id': 37, 'audio.channel': 'FL'}}},
 {'id': 42, 'type': 'PipeWire:Interface:Port', 'info': {'props': {'node.id': 37, 'audio.channel': 'FR'}}},
 {'id': 44, 'type': 'PipeWire:Interface:Port', 'info': {'props': {'node.id': 37, 'audio.channel': 'RL'}}},
 {'id': 46, 'type': 'PipeWire:Interface:Port', 'info': {'props': {'node.id': 37, 'audio.channel': 'RR'}}},
 {'id': 50,
  'type': 'PipeWire:Interface:Client',
  'info': {'props': {'pipewire.sec.pid': 11333,
                     'pipewire.sec.uid': 1001,
                     'pipewire.sec.gid': 1001,
                     'application.process.id': 11333,
                     'object.serial': 57}}},
 {'id': 51,
  'type': 'PipeWire:Interface:Node',
  'info': {'props': {'client.id': 50, 'media.class': 'Stream/Output/Audio', 'object.serial': 58}}},
 {'id': 52, 'type': 'PipeWire:Interface:Port', 'info': {'props': {'node.id': 51, 'audio.channel': 'FL'}}},
 {'id': 53, 'type': 'PipeWire:Interface:Port', 'info': {'props': {'node.id': 51, 'audio.channel': 'FR'}}},
 {'id': 54, 'type': 'PipeWire:Interface:Port', 'info': {'props': {'node.id': 51, 'audio.channel': 'RL'}}},
 {'id': 55, 'type': 'PipeWire:Interface:Port', 'info': {'props': {'node.id': 51, 'audio.channel': 'RR'}}},
 {'id': 56,
  'type': 'PipeWire:Interface:Link',
  'info': {'state': 'active',
           'output-node-id': 51,
           'input-node-id': 37,
           'output-port-id': 52,
           'input-port-id': 40}},
 {'id': 57,
  'type': 'PipeWire:Interface:Link',
  'info': {'state': 'active',
           'output-node-id': 51,
           'input-node-id': 37,
           'output-port-id': 53,
           'input-port-id': 42}},
 {'id': 58,
  'type': 'PipeWire:Interface:Link',
  'info': {'state': 'active',
           'output-node-id': 51,
           'input-node-id': 37,
           'output-port-id': 54,
           'input-port-id': 44}},
 {'id': 59,
  'type': 'PipeWire:Interface:Link',
  'info': {'state': 'active',
           'output-node-id': 51,
           'input-node-id': 37,
           'output-port-id': 55,
           'input-port-id': 46}}]


class GainEvidenceTests(unittest.TestCase):
    def setUp(self):
        # Reduced shape of the real Noble 7d9f906 card/route, not a claim that
        # these fabricated IDs belong to a live server.
        self.sysfs = PurePosixPath('/sys/devices/platform/vhci_hcd.0/usb1/1-1/1-1:1.0/sound/card0')
        volumes = dict(channelMap=CHANNELS, channelVolumes=[.063997] * 4, softVolumes=[.904004] * 4, mute=False)
        self.card = dict(id=38, type=PREFIX + 'Device', info=dict(props={
            'device.api': 'alsa', 'api.alsa.card': 0, 'device.sysfs.path': str(self.sysfs)[4:],
            'device.name': 'alsa_card.owned', 'object.serial': 40}, params={
                'Profile': [dict(index=2, name='output:analog-surround-40+input:iec958-stereo', save=False)],
                'Route': [dict(index=2, direction='Output', device=4, profile=2, save=False, props=copy.deepcopy(volumes))]}))
        self.node = dict(id=35, type=PREFIX + 'Node', info=dict(props={
            'device.id': 38, 'api.alsa.pcm.card': 0, 'card.profile.device': 4,
            'media.class': 'Audio/Sink', 'node.name': 'alsa_output.owned', 'object.serial': 41}, params={
                'EnumFormat': [dict(format='S16LE', rate=48000, channels=4, position=CHANNELS)],
                'Props': [copy.deepcopy(volumes)]}))
        self.metadata = dict(id=30, type=PREFIX + 'Metadata', props={'metadata.name': 'default'}, metadata=[
            dict(subject=0, key='default.audio.sink', type='Spa:String:JSON', value={'name': 'alsa_output.owned'})])
        self.snapshot = [self.card, self.node, self.metadata]

    def target(self):
        return target_state(self.snapshot, 0, self.sysfs)

    def test_target_binds_card_node_route_and_raw_soft_volumes_separately(self):
        state = self.target()
        self.assertEqual(state['identity']['cardSerial'], 40)
        self.assertEqual(state['identity']['nodeSerial'], 41)
        self.assertNotEqual(state['routeProps']['channelVolumes'], state['routeProps']['softVolumes'])
        request = route_request(state, [.063997, .063997, 1, 1])
        self.assertEqual(request, dict(index=2, device=4, props={'channelVolumes': [.063997, .063997, 1, 1]}, save=False))
        self.assertNotIn('softVolumes', request['props'])

    def test_native_unity_node_cannot_be_restored_by_its_attenuated_route(self):
        # Native 41636078566 / 64d: Route had these values while the Node
        # remained unity through baseline PCM. Restore updated Node to Route,
        # so equality with the initial snapshot was impossible, not delayed.
        state = self.target()
        require_route_restorable(state)
        for fields in (('channelVolumes',), ('softVolumes',), ('channelVolumes', 'softVolumes')):
            changed = copy.deepcopy(state)
            for field in fields:
                changed['nodeProps'][field] = [1.0] * 4
            with self.subTest(fields=fields), self.assertRaisesRegex(RuntimeError, 'Route-only rollback'):
                require_route_restorable(changed)
        for field, replacement in (('channelMap', ['FR', 'FL', 'RL', 'RR']), ('mute', True)):
            changed = copy.deepcopy(state); changed['nodeProps'][field] = replacement
            with self.subTest(field=field), self.assertRaises(RuntimeError): require_route_restorable(changed)

    def test_reused_id_changed_profile_or_default_does_not_authorize_a_write(self):
        before = self.target()
        for key in ('cardSerial', 'nodeSerial', 'routeIndex', 'routeDevice', 'cardSysfs'):
            changed = copy.deepcopy(before); changed['identity'][key] = 'replacement'
            with self.subTest(key=key), self.assertRaises(RuntimeError): same_target(changed, before)
        for field in ('profiles', 'defaultSink'):
            changed = copy.deepcopy(before); changed[field] = None
            with self.subTest(field=field), self.assertRaises(RuntimeError): same_target(changed, before)

    def test_missing_soft_wrong_map_saved_route_and_foreign_sink_are_rejected(self):
        original = copy.deepcopy(self.snapshot)
        for case in ('soft', 'map', 'saved', 'foreign'):
            self.snapshot = copy.deepcopy(original)
            route = self.snapshot[0]['info']['params']['Route'][0]
            if case == 'soft': del route['props']['softVolumes']
            elif case == 'map': route['props']['channelMap'] = ['RL', 'RR', 'FL', 'FR']
            elif case == 'saved': route['save'] = True
            else: self.snapshot[1]['info']['props']['device.id'] = 99
            with self.subTest(case=case), self.assertRaises(RuntimeError): self.target()

    def test_volume_request_rejects_nonfinite_bool_and_amplification(self):
        for invalid in (float('nan'), float('inf'), True, -1, 1.01):
            with self.subTest(invalid=invalid), self.assertRaises(RuntimeError):
                route_request(self.target(), [1, 1, invalid, 1])

    def test_playback_binding_rejects_other_sink_and_channel_swap(self):
        target = self.target()
        client = dict(id=79, type=PREFIX + 'Client', info=dict(props={
            'pipewire.sec.pid': 456, 'pipewire.sec.uid': 1001, 'pipewire.sec.gid': 1002, 'object.serial': 89}))
        stream = dict(id=80, type=PREFIX + 'Node', info=dict(props={
            'media.class': 'Stream/Output/Audio', 'client.id': 79, 'object.serial': 90}))
        extra = [client, stream]
        for i, channel in enumerate(CHANNELS):
            extra.extend([dict(id=100+i, type=PREFIX+'Port', info=dict(props={'audio.channel': channel, 'node.id': 80})),
                          dict(id=110+i, type=PREFIX+'Port', info=dict(props={'audio.channel': channel, 'node.id': 35})),
                          dict(id=120+i, type=PREFIX+'Link', info={
                              'state': 'active', 'output-node-id': 80, 'input-node-id': 35,
                              'output-port-id': 100+i, 'input-port-id': 110+i})])
        snapshot = self.snapshot + extra
        self.assertEqual(stream_binding(snapshot, 456, 1001, 1002, target)['streamId'], 80)
        snapshot[-1]['info']['input-node-id'] = 99
        with self.assertRaises(RuntimeError): stream_binding(snapshot, 456, 1001, 1002, target)
        snapshot[-1]['info']['input-node-id'] = 35
        snapshot[-2]['info']['props']['audio.channel'] = 'FL'
        with self.assertRaises(RuntimeError): stream_binding(snapshot, 456, 1001, 1002, target)

    def test_native_client_node_join_without_node_process_property(self):
        target = {'identity': {'nodeId': 37}}
        binding = stream_binding(NATIVE_PLAYBACK, 11333, 1001, 1001, target)
        self.assertEqual((binding['clientId'], binding['clientSerial'], binding['streamId'], binding['streamSerial']),
                         (50, 57, 51, 58))
        self.assertEqual(len(binding['links']), 4)
        # An application-supplied node PID or name cannot replace peer identity.
        for field, value in (('pipewire.sec.pid', 11334), ('pipewire.sec.uid', 1002), ('pipewire.sec.gid', 1002)):
            changed = copy.deepcopy(NATIVE_PLAYBACK)
            client = next(row for row in changed if row['id'] == 50)
            client['info']['props'][field] = value
            node = next(row for row in changed if row['id'] == 51)
            node['info']['props']['application.process.id'] = 11333
            with self.subTest(field=field):
                if field.endswith('.pid'):
                    self.assertIsNone(stream_binding(changed, 11333, 1001, 1001, target))
                else:
                    with self.assertRaises(RuntimeError): stream_binding(changed, 11333, 1001, 1001, target)

    def test_native_playback_missing_client_wrong_port_owner_and_inactive_links_are_not_bound(self):
        target = {'identity': {'nodeId': 37}}
        self.assertIsNone(stream_binding([row for row in NATIVE_PLAYBACK if row['id'] != 50], 11333, 1001, 1001, target))
        changed = copy.deepcopy(NATIVE_PLAYBACK)
        next(row for row in changed if row['id'] == 52)['info']['props']['node.id'] = 99
        with self.assertRaises(RuntimeError): stream_binding(changed, 11333, 1001, 1001, target)
        changed = copy.deepcopy(NATIVE_PLAYBACK)
        next(row for row in changed if row['id'] == 56)['info']['state'] = 'paused'
        self.assertIsNone(stream_binding(changed, 11333, 1001, 1001, target))


class PcmGainTests(unittest.TestCase):
    def test_rear_unity_alone_does_not_preserve_front_pcm(self):
        source = marked_pcm(9123, 2048)
        baseline = b''.join(struct.pack('<hhhh', *(round(value * .9) for value in row))
                            for row in struct.iter_unpack('<hhhh', source))
        rear_only = b''.join(struct.pack('<hhhh', round(a * .064), round(b * .064), c, d)
                             for a, b, c, d in struct.iter_unpack('<hhhh', source))
        corrected = b''.join(struct.pack('<hhhh', a, b, c, d) for (a, b, _, _), (_, _, c, d) in
                             zip(struct.iter_unpack('<hhhh', baseline), struct.iter_unpack('<hhhh', source)))
        self.assertTrue(measure_pcm(source, rear_only)[0]['rearUnityPcmExact'])
        self.assertTrue(all(row['differingSamples'] > 0 for row in compare_pcm_channels(baseline, rear_only)[:2]))
        comparisons = compare_pcm_channels(baseline, corrected)
        self.assertEqual([row['differingSamples'] for row in comparisons[:2]], [0, 0])
        self.assertTrue(measure_pcm(source, corrected)[0]['rearUnityPcmExact'])
        # One changed sample, including after the anchor, cannot be tolerated.
        changed = bytearray(corrected)
        value = struct.unpack_from('<h', changed, 1700 * 8)[0]
        struct.pack_into('<h', changed, 1700 * 8, value + 1)
        result = compare_pcm_channels(baseline, changed)
        self.assertEqual((result[0]['differingSamples'], result[0]['maximumDifferenceLsb']), (1, 1))
        self.assertEqual(compare_pcm_channels(baseline, baseline),
                         [dict(channel=c, samples=2048, differingSamples=0, maximumDifferenceLsb=0) for c in CHANNELS])

    def test_channel_comparison_cannot_ignore_missing_or_partial_frames(self):
        for left, right in ((b'', b''), (bytes(8), bytes(16)), (bytes(9), bytes(9))):
            with self.subTest(sizes=(len(left), len(right))), self.assertRaises(RuntimeError):
                compare_pcm_channels(left, right)

    def test_independent_channel_gains_and_exact_rear_are_measured_in_one_alignment(self):
        source = marked_pcm(123, 2048)
        gained = b''.join(struct.pack('<hhhh', round(a*.25), round(b*.5), c, d)
                          for a,b,c,d in struct.iter_unpack('<hhhh', source))
        result, aligned = measure_pcm(source, bytes(101*8) + gained + bytes(19*8))
        self.assertEqual(result['markerOffsetFrames'], 101)
        self.assertEqual(aligned, gained)
        self.assertTrue(result['rearUnityPcmExact'])
        for row, expected in zip(result['channels'], (.25, .5, 1, 1)):
            self.assertAlmostEqual(row['fittedGain'], expected, places=5)
            self.assertLess(row['maximumResidualLsb'], .51)

    def test_scalar_gain_or_swapped_rear_does_not_become_unity(self):
        source = marked_pcm(321, 1024)
        for swap in (False, True):
            captured = b''.join(struct.pack('<hhhh', a, b, d if swap else round(c*.9), c if swap else round(d*.9))
                                for a,b,c,d in struct.iter_unpack('<hhhh', source))
            result, _ = measure_pcm(source, captured)
            self.assertFalse(result['rearUnityPcmExact'])

    def test_repeated_source_and_missing_marker_are_not_silently_realigned(self):
        source = marked_pcm(456, 1024)
        for captured in (source + source, bytes(len(source)), source[:200*8]):
            with self.assertRaises(RuntimeError): measure_pcm(source, captured)

    def test_dropped_or_reordered_middle_remains_visible_after_anchor(self):
        source = marked_pcm(789, 1024)
        for captured in (source[:200*8] + source[201*8:] + bytes(8),
                         source[:200*8] + source[300*8:400*8] + source[200*8:300*8] + source[400*8:]):
            result, _ = measure_pcm(source, captured)
            self.assertFalse(result['rearUnityPcmExact'])
            self.assertGreater(result['channels'][2]['maximumResidualLsb'], 1000)


class AlsaRestoreTests(unittest.TestCase):
    text = """numid=4,iface=MIXER,name='PCM Playback Volume'
  ; type=INTEGER,access=rw---R--,values=1,min=0,max=100,step=0
  : values=80
  | dBminmax-min=-100.00dB,max=0.00dB
numid=7,iface=CARD,name='Headphone Jack'
  ; type=BOOLEAN,access=r-------,values=1
  : values=off
"""

    def test_only_changed_writable_control_is_restored(self):
        before = control_snapshot(self.text)
        after = control_snapshot(self.text.replace('values=80', 'values=77'))
        self.assertEqual(restore_values(before, after), [(4, '80')])
        self.assertEqual(restore_values(before, before), [])

    def test_replacement_readonly_change_or_unknown_writable_schema_blocks_restore(self):
        before = control_snapshot(self.text)
        for text in (self.text.replace('max=100', 'max=101'), self.text.replace('values=off', 'values=on'),
                     self.text.replace('numid=4', 'numid=5')):
            with self.assertRaises(RuntimeError): restore_values(before, control_snapshot(text))
        with self.assertRaises(RuntimeError): control_snapshot(self.text.replace('type=INTEGER', 'type=BYTES'))


class PreciseVolumeTests(unittest.TestCase):
    def setUp(self):
        self.state = dict(identity=dict(cardId=38, cardSerial=40, nodeId=35, nodeSerial=41, routeIndex=2, routeDevice=4),
                          routeProps=dict(channelVolumes=[.063997]*4, softVolumes=[.904004]*4),
                          nodeProps=dict(channelVolumes=[.063997]*4, softVolumes=[.904004]*4))
        # Exact float32 values for ACP's integer levels 26214 and 63368;
        # both lose digits in pw-dump's text representation.
        self.raw, self.soft = 0.0639970675110817, 0.9040036797523499
        self.record = self.state['identity'] | dict(routeRaw=[self.raw]*4, routeSoft=[self.soft]*4,
                                                  nodeRaw=[self.raw]*4, nodeSoft=[self.soft]*4)

    def test_preserves_exact_original_software_front_not_rounded_raw_front(self):
        data = json.loads(json.dumps(self.record))
        self.assertEqual(exact_volumes(data, self.state), self.record)
        wanted = front_preserving_request(self.state, data)
        self.assertEqual(wanted, [self.soft, self.soft, 1., 1.])
        self.assertNotEqual(wanted[0], .904004)
        self.assertNotEqual(struct.pack('<f', wanted[0]), struct.pack('<f', .904004))
        self.assertEqual(dump_volumes(wanted), [.904004, .904004, 1., 1.])

    def test_exact_reader_rejects_replacement_nonfloat32_unbounded_and_graph_disagreement(self):
        with self.assertRaisesRegex(RuntimeError, "Precise volume reader failed: ambiguous"):
            exact_volumes({"error": "ambiguous node volume props"}, self.state)
        for change in (dict(cardSerial=41), dict(nodeSerial=42), dict(routeDevice=3), dict(nodeId=True),
                       dict(extra='unknown'), dict(routeSoft=[.904004]*4), dict(nodeRaw=[float('nan')]*4),
                       dict(routeRaw=[True]*4), dict(nodeSoft=[1.01]*4), dict(nodeRaw=[1.0]*4)):
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                exact_volumes(self.record | change, self.state)

    def test_distinct_float32_values_hidden_by_dump_rounding_cannot_restore_one_baseline(self):
        bits = struct.unpack('<I', struct.pack('<f', self.soft))[0]
        adjacent = struct.unpack('<f', struct.pack('<I', bits + 1))[0]
        self.assertEqual(dump_volumes([adjacent]), dump_volumes([self.soft]))
        changed = self.record | dict(nodeSoft=[adjacent]*4)
        exact_volumes(changed, self.state)  # Text alone cannot distinguish it.
        with self.assertRaisesRegex(RuntimeError, 'Route-only rollback'):
            front_preserving_request(self.state, changed)


class OptionalGainTests(unittest.TestCase):
    ready = dict(ownedGuestToken="owned-guest", usbParent="/sys/devices/platform/vhci_hcd.0/usb1/1-2")

    def record(self):
        return dict(scope="owned-private-pipewire-gain-experiment", ready=self.ready, result="failed",
                    accountProcessesAndFilesRemoved=True, experimentCleanupVerified=True,
                    graphMutationAttempted=True, liveGraphRestored=True, alsaPreSessionRestored=True,
                    error="No complete marked PCM alignment")

    def test_each_cleanup_component_is_required_even_with_top_level_success(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            for change in (dict(accountProcessesAndFilesRemoved=False), dict(experimentCleanupVerified=False),
                           dict(alsaPreSessionRestored=False), dict(liveGraphRestored=False),
                           dict(graphMutationAttempted=None), dict(cleanupErrors=["ACL changed"]),
                           dict(stateRestorationErrors=["policy process remains"])):
                path.write_text(json.dumps(self.record() | change))
                with self.subTest(change=change), self.assertRaises(RuntimeError): optional_result(path, self.ready)

    def test_nonunity_measurement_is_evidence_not_compatibility_success(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            record = self.record() | dict(result="measured", phases=[dict(label=x) for x in
                ("baseline", "adjusted", "front-preserving", "restored")], rearUnityPcmExact=False,
                frontSoftwareGainsPreserved=True, frontPcmEqualsBaseline=False,
                frontPreservingRearUnityPcmExact=True, restoredMarkedPcmEqualsBaseline=True)
            path.write_text(json.dumps(record))
            summary = optional_result(path, self.ready)
            self.assertFalse(summary["rearUnityPcmExact"])
            self.assertFalse(summary["frontPcmEqualsBaseline"])
            self.assertTrue(summary["frontPreservingRearUnityPcmExact"])
            self.assertFalse(summary["gatesConsumerCompatibility"])
            self.assertFalse(summary["physicalGainValidated"])
            self.assertEqual(summary["resultSha256"], hashlib.sha256(path.read_bytes()).hexdigest())
            record["phases"].pop(); path.write_text(json.dumps(record))
            with self.assertRaises(RuntimeError): optional_result(path, self.ready)

    def test_missing_stale_or_other_device_evidence_does_not_authorize_continuation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); ready = root / "ready.json"; ready.write_text(json.dumps(self.ready))
            evidence = root / "pipewire-gain"
            with patch("pipewire_gain.run"), self.assertRaises(RuntimeError): run_optional(ready, evidence, "fixture")
            evidence.mkdir(); path = evidence / "result.json"; path.write_text(json.dumps(self.record()))
            with patch("pipewire_gain.run") as run, self.assertRaises(RuntimeError): run_optional(ready, evidence, "fixture")
            run.assert_not_called()
            for change in (dict(ownedGuestToken="foreign"), dict(usbParent=self.ready["usbParent"] + "x")):
                with self.subTest(change=change), self.assertRaises(RuntimeError): optional_result(path, self.ready | change)

    def test_failed_measurement_reaches_later_sessions_only_after_verified_cleanup(self):
        from consumer_session import _run_primary_suite
        for cleanup in (True, False):
            with self.subTest(cleanup=cleanup), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); ready = root / "ready.json"; ready.write_text(json.dumps(self.ready))
                events = []
                def measure(*args, **kwargs):
                    self.assertIs(kwargs["adjust_owned_rear_unity"], True)
                    events.append("gain-failed")
                    output = Path(args[1]); output.mkdir()
                    (output / "result.json").write_text(json.dumps(self.record() | dict(experimentCleanupVerified=cleanup)))
                def pulse(*_):
                    events.append("pulse"); return {}
                def consumer(*_):
                    events.append("consumer"); raise RuntimeError("consumer boundary")
                with patch("pipewire_gain.run", side_effect=measure), patch("consumer_session.ConsumerSession", side_effect=consumer), \
                     patch("consumer_session.run_audio_defaults_observer", side_effect=pulse):
                    with self.assertRaises(RuntimeError):
                        _run_primary_suite(ready, None, None, None, root / "consumer-suite", "fixture",
                                           observe_audio_defaults=True, adjust_owned_rear_unity=True)
                self.assertEqual(events, ["gain-failed", "pulse", "consumer"] if cleanup else ["gain-failed"])

    def test_unmodified_incoherent_baseline_remains_failed_and_allows_next_session_after_cleanup(self):
        from consumer_session import _run_primary_suite
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); ready = root / 'ready.json'; ready.write_text(json.dumps(self.ready))
            events = []
            def measure(*args, **kwargs):
                output = Path(args[1]); output.mkdir()
                record = self.record() | dict(graphMutationAttempted=False, liveGraphRestored=None,
                    error='Initial Route/Node channelVolumes disagree; Route-only rollback cannot restore this baseline')
                (output / 'result.json').write_text(json.dumps(record))
                summary = optional_result(output / 'result.json', self.ready)
                self.assertEqual(summary['result'], 'failed')
                self.assertIsNone(summary['rearUnityPcmExact'])
                events.append('unmodified-gain-failed')
            def consumer(*_):
                events.append('next-consumer-session'); raise RuntimeError('consumer boundary')
            with patch('pipewire_gain.run', side_effect=measure), patch('consumer_session.ConsumerSession', side_effect=consumer):
                with self.assertRaisesRegex(RuntimeError, 'consumer boundary'):
                    _run_primary_suite(ready, None, None, None, root / 'consumer-suite', 'fixture',
                                       adjust_owned_rear_unity=True)
            self.assertEqual(events, ['unmodified-gain-failed', 'next-consumer-session'])

    def test_real_staged_gain_module_imports_without_repository_dependencies(self):
        from qemu_consumers import stage_consumer_research
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); stage_consumer_research(root); research = root / "linux/research"
            code = ("import pathlib,sys; root=pathlib.Path(sys.argv[1]); sys.path.insert(0,str(root)); "
                    "import pipewire_gain,pipewire_defaults,consumer_session; "
                    "assert all(pathlib.Path(m.__file__).parent==root for m in "
                    "(pipewire_gain,pipewire_defaults,consumer_session)); assert callable(pipewire_gain.run_optional)")
            result = subprocess.run([sys.executable, "-I", "-c", code, str(research)], cwd=root,
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)


@unittest.skipUnless(sys.platform == "linux", "Uses the distribution Lua 5.4 library without an audio daemon")
class PreciseLuaTests(unittest.TestCase):
    """Run the exact embedded reader, not a Python copy of its selection logic."""
    @classmethod
    def setUpClass(cls):
        name = ctypes.util.find_library("lua5.4")
        if name is None:
            raise RuntimeError("liblua5.4-0 is required for the Linux precise-reader regression")
        cls.lua = ctypes.CDLL(name)
        for symbol, restype, argtypes in (
            ("luaL_newstate", ctypes.c_void_p, []),
            ("luaL_openlibs", None, [ctypes.c_void_p]),
            ("luaL_loadbufferx", ctypes.c_int, [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t,
                                              ctypes.c_char_p, ctypes.c_char_p]),
            ("lua_pcallk", ctypes.c_int, [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_ssize_t, ctypes.c_void_p]),
            ("lua_tolstring", ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_size_t)]),
            ("lua_close", None, [ctypes.c_void_p]),
        ):
            fn = getattr(cls.lua, symbol)
            fn.restype, fn.argtypes = restype, argtypes

    @staticmethod
    def literal(value):
        if isinstance(value, dict):
            return "{" + ",".join("[" + PreciseLuaTests.literal(k) + "]=" + PreciseLuaTests.literal(v)
                                  for k, v in value.items()) + "}"
        if isinstance(value, list):
            return "{" + ",".join(map(PreciseLuaTests.literal, value)) + "}"
        return json.dumps(value)

    def setUp(self):
        self.identity = dict(cardId=38, cardSerial=40, nodeId=39, nodeSerial=41, routeIndex=2, routeDevice=4)
        # Shape and unrelated fields retained from native 2a17811, run41639823652,
        # pipewire-gain/004-precise-original-before.json SHA256
        # 94d8f396fab0479f1ffcf8e0090ccefce436a3172563939acac9b93155afadd4.
        # Rounded dump values below are representative float32 test inputs,
        # not a claim that the dump retained native coefficient precision.
        self.raw = struct.unpack("<f", struct.pack("<f", .063997))[0]
        self.soft = struct.unpack("<f", struct.pack("<f", .904004))[0]
        def array(kind, values):
            return dict(pod_type="Array", value_type=kind) | {i+1: value for i, value in enumerate(values)}
        self.volume = dict(mute=False, channelMap=array("Spa:Id", CHANNELS),
                           channelVolumes=array("Spa:Float", [self.raw]*4),
                           softVolumes=array("Spa:Float", [self.soft]*4))
        self.follower = dict(device="surround40:0", deviceName="", cardName="", latencyOffsetNsec=0,
                             params=["audio.channels", 4, "audio.rate", 0, "audio.format", "UNKNOWN",
                                     "audio.position", "[ FL, FR, RL, RR ]"])

    def execute(self, node_props, *, identity=None, node_identity=None):
        def pod(properties, object_id="Props"):
            return dict(pod_type="Object", object_id=object_id, properties=properties)
        route = dict(index=2, device=4, direction="Output", save=False, props=pod(self.volume, "Route"))
        card = {"bound-id": 38, "properties": {"object.serial": "40"}, "parameters": [pod(route, "Route")]}
        node = {"bound-id": 39, "properties": node_identity or
                {"object.serial": "41", "device.id": "38", "card.profile.device": "4"},
                "parameters": [pod(p) for p in node_props]}
        # Only WirePlumber object delivery is mocked. The production Lua performs
        # every selection, identity check, POD check, float formatting and error exit.
        wrapper = r'''
local card, node, args = CARD, NODE, ARGS
local function iterate(values, boxed)
  local i = 0
  return function ()
    i = i + 1
    local value = values[i]
    if not value then return nil end
    if boxed then return {parse=function() return value end} end
    return value
  end
end
for _, obj in ipairs({card, node}) do
  obj.iterate_params = function(self) return iterate(self.parameters, true) end
end
local messages, quits = {}, 0
print = function(value) table.insert(messages, value) end
Core = {quit=function() quits = quits + 1 end}
Interest = function(value) return value end
Constraint = function(value) return value end
ObjectManager = function()
  return {
    connect=function(self, _, callback) self.callback = callback end,
    activate=function(self) self.callback() end,
    get_n_objects=function() return 2 end,
    iterate=function() return iterate({card, node}, false) end,
  }
end
assert(load(SCRIPT, "exact-volumes.lua"))(args)
assert(quits == 1, "reader must quit after success or failure")
assert(#messages == 1, "reader must return exactly one result")
return messages[1]
'''
        wrapper = wrapper.replace("CARD", self.literal(card)).replace("NODE", self.literal(node))
        wrapper = wrapper.replace("ARGS", self.literal(identity or self.identity))
        # Long Lua strings preserve the exact script bytes, including its escapes.
        wrapper = wrapper.replace("SCRIPT", "[====[" + EXACT_VOLUMES_LUA + "]====]")
        encoded = wrapper.encode()
        state = self.lua.luaL_newstate()
        self.assertTrue(state)
        try:
            self.lua.luaL_openlibs(state)
            status = self.lua.luaL_loadbufferx(state, encoded, len(encoded), b"reader-regression", None)
            if status == 0:
                status = self.lua.lua_pcallk(state, 0, 1, 0, 0, None)
            length = ctypes.c_size_t()
            pointer = self.lua.lua_tolstring(state, -1, ctypes.byref(length))
            text = ctypes.string_at(pointer, length.value).decode() if pointer else "<no Lua result>"
            self.assertEqual(status, 0, text)
            return json.loads(text)
        finally:
            self.lua.lua_close(state)

    def test_native_follower_props_does_not_make_the_unique_volume_object_ambiguous(self):
        expected = self.identity | dict(routeRaw=[self.raw]*4, routeSoft=[self.soft]*4,
                                         nodeRaw=[self.raw]*4, nodeSoft=[self.soft]*4)
        for rows in ([self.volume, self.follower], [self.follower, self.volume]):
            with self.subTest(rows=rows):
                self.assertEqual(self.execute(rows), expected)

    def test_duplicate_or_partial_volume_props_fail_and_quit_immediately(self):
        cases = [[self.volume, self.volume], [self.follower]]
        for key in ("volume", "mute", "channelMap", "channelVolumes", "softMute", "softVolumes", "monitorMute", "monitorVolumes"):
            partial = {key: self.volume.get(key, 1)}
            cases.extend(([partial, self.volume], [self.volume, partial], [partial]))
        for rows in cases:
            with self.subTest(rows=rows):
                result = self.execute(rows)
                self.assertEqual(set(result), {"error"})
                self.assertTrue(result["error"])
                with self.assertRaisesRegex(RuntimeError, "Precise volume reader failed"):
                    exact_volumes(result, {})

    def test_replaced_node_or_wrong_profile_device_cannot_supply_volumes(self):
        props = {"object.serial": "41", "device.id": "38", "card.profile.device": "4"}
        for key in props:
            with self.subTest(key=key):
                result = self.execute([self.volume, self.follower], node_identity=props | {key: "999"})
                self.assertEqual(set(result), {"error"})


if __name__ == '__main__':
    unittest.main()
