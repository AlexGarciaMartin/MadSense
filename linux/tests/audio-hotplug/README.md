# Private audio arrival and removal experiment

The first native run at `2a17811`, workflow `41639824132`, completed the automatic
case: both sink and source switched to the virtual controller on both arrivals
and returned to the controls after removal. Physical fixture permissions, the
app/helper and the private audio account were cleaned up. The explicit case
stopped before attach because `wpctl 0.4` rejects `Audio/Source/Virtual` for
set-default, although automatic policy accepts it. The control now uses exact
`Audio/Source` with `node.virtual=true` and the same software factory. The repeat
at `0a9c30d`, workflow `41643216241`, completed both cases: automatic selection
again replaced both defaults on both arrivals; explicit selection preserved both
through both attach/detach cycles. Both accounts, app/helper leases, physical
fixture permissions and QEMU/KVM state were cleaned up. Exact-source independent
recalculation passed for both cases. The original negative result remains retained. Both runs verified
93 guest source files, four installed helper files and base gates 10/9/10 plus
two installations/four removal cases; QEMU stopped and its KVM ACL was restored.

This adds no production audio
policy, UCM rule, default-device change, or generic Sony match. It uses the real
LinuxBridgeController through the unchanged AppProbe, a fresh UHID fixture and a
separate ordinary audio UID in the explicitly owned QEMU guest. Existing broker,
core and application results remain separate and must pass first.

Two fresh sessions exercise automatic selection and explicit user selection.
Both start distribution PipeWire 1.0/WirePlumber 0.4 before the first app Resume.
Two private software controls (sink priority 900, source priority 1900) exist
first. The explicit case selects both with wpctl; the automatic case never sets
configured defaults. Those controlled priorities are part of the experiment,
not a claim to reproduce every desktop. Each session observes attach, pause,
resume and second pause while the same graph and controls remain alive.
The source class follows wpctl's exact
[accepted device classes](https://github.com/PipeWire/wireplumber/blob/0.4.17/src/tools/wpctl.c#L14);
the `node.virtual` property and absence of any ALSA parent preserve its software
scope. Changing this class is an experiment-fixture correction, not a product
policy or a relaxation of the default-preservation check.

The root coordinator joins the app's sole VHCI import to the broker journal's
physical path, expected parent and parent inode/device. Virtual HID_UNIQ must be
02:D5 and distinct from the fixture's 02:FA MAC. Only then does the audio UID get
ACLs on the exact sound control/playback/capture nodes below that parent. It gets
no access to physical UHID, physical evdev or virtual HID. The app actor retains
the existing fixture-only permission setup needed to exercise production.

No broad audio group or audio udev rule is installed. PipeWire's existing
[1.0.5 ALSA access watcher](https://github.com/PipeWire/pipewire/blob/1.0.5/spa/plugins/alsa/alsa-udev.c#L763)
reevaluates sound-node ACL changes via IN_ATTRIB. The experiment records when
attach and ACL grants occurred. No graph restart or artificial udev refresh is
used to hide a failure to discover the new device.

Snapshots retain both actual and configured defaults, node id plus object.serial,
exact USB/ALSA/PipeWire parent joins, profiles, maps and volumes. A continuous
pw-metadata monitor detects transient default changes, including removal and
restoration between snapshots; pw-dump --monitor retains graph changes. Host
verification recomputes summaries from those raw files. A control id reused for
another node is invalid evidence, not a preserved default.

A default replacement remains an `observed` result with preservation false; it
does not erase the passing functional baseline or become a compatibility pass.
Sink and source are separate observations. The source check matters because the
product does not feed physical microphone PCM into the virtual capture queue.
No speaker, microphone, radio, game, desktop shell or physical latency is tested.

Pause must remove the exact USB parent, sound nodes and broker lease before old
ACL records are retired. No ACL is written to a replacement at a reused path.
The final case requires restored physical fixture permissions, helper cleanup
ACK, no observer processes, deleted private account/state and QEMU/KVM cleanup.
Cleanup failure prevents the second case and invalidates the observation.
Commands, status reads and snapshot traversal all execute as the ordinary app
UID with no supplementary groups. Directory and regular-file opens reject
symlinks; snapshots reject hard links, foreign ownership and unbounded data.
Root writes only the returned bounded snapshot into its evidence directory.

The platform workflow dispatch has `audio_hotplug`, default false. When enabled,
this wrapper replaces its usual native invocation: it runs the broker/core/app
base gates (10/9/10 cases), including the existing idle-upgrade/removal stage,
then the two audio sessions in the same owned VM. It does not run or claim the
extra protocol or Xbox force-feedback cases. Ordinary push/PR runs retain those
existing extensions. Portable hotplug tests run on both Linux architectures.
After publishing the usual neutral, core and application probes, an equivalent
explicit standalone invocation is:

```sh
python3 linux/tests/audio-hotplug/qemu_audio_hotplug.py --run-private-audio-hotplug \
  --neutral-probe-directory /tmp/neutral-probe --core-probe-directory /tmp/core-probe \
  --app-probe-directory /tmp/app-probe --output /tmp/hotplug-evidence --work /tmp/hotplug-private
```

An existing owned-VM coordinator can instead call `add_payload(payload)` before
upload and `run_stage(guest)` after its baseline/core/app stages. The result stays
in `result/audio-hotplug/`. Required guest packages are the existing app/helper
dependencies plus pipewire, pipewire-bin, wireplumber, alsa-utils, pulseaudio,
pulseaudio-utils, dbus and acl; Pulse tools are used only for shared inventory.
The adapter deliberately rejects other PipeWire/WirePlumber version families.

## Private prepublication policy variant (native execution pending)

After the unchanged two-case baseline completes, the opt-in launcher now runs
three further fresh sessions in `result/audio-hotplug-policy`: scoped automatic,
scoped explicit, and a foreign-parent negative control. Each has two attach and
detach epochs. Baseline evidence and its negative automatic result are retained
separately even if the policy stage fails. The production application, broker,
USB descriptors, UCM and system audio configuration are unchanged.

In this otherwise idle owned VM, the coordinator reads the current VHCI table
and uses the production parent-mapping function to predict the first free port.
Before starting the private graph, an ordinary-UID writer creates one private
WirePlumber 0.4 Lua fragment. Its Device rule requires that complete USB parent
plus its sound-card suffix and the expected vendor/product. It adds a fresh
`api.alsa.card.madsense-session` nonce. Only nodes carrying that nonce receive
the playback `priority.session=1` or capture `node.disabled=true` properties.
The [0.4 ALSA monitor](https://github.com/PipeWire/wireplumber/blob/0.4.17/src/scripts/monitors/alsa.lua#L148)
copies those card properties and applies the node rules before creating the
adapter. No graph restart or later metadata/default restoration is performed.

Each real app lease must subsequently match the predicted parent, journal
identity and inode before any sound ACL is granted. The negative session points
the same rule at the second distinct free parent while the app still imports
at the first. It must retain both ordinary ALSA nodes, no marker and the
unmodified 1009/2008 priorities; its automatic default replacement remains the
expected negative observation. This checks the rule against a real same-VID/PID
device outside its parent scope, not just a string-matching mock.

Positive cases require the four-channel playback format FL/FR/RL/RR, the
original USB/card association and all ALSA playback/capture/control devices.
Only the desktop capture node is suppressed; USB capture is not removed. Full
`pw-dump --monitor` evidence is parsed after all observer processes have exited:
both device epochs must be present, playback must have the marker and low
priority at first publication, and any capture publication, even transient,
invalidates the positive case. Raw snapshots preserve Route and Node properties
independently. No gain, profile, channel-map or playback-PCM adjustment is made;
this stage does not claim acoustic or PCM delivery validation.

This fixed-parent prediction is an experiment boundary, not a production
ownership policy for reused ports. Production adoption would need a live-lease
check or an exact pre-import udev identity marker with crash restoration. Low
priority alone also does not exclude a node when it is the only candidate; this
experiment checks preservation while the two controls remain available. The
WirePlumber 0.5 equivalent has only been read, not run; the adapter still rejects
that version family. Complete cleanup remains required before another session.
