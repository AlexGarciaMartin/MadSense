"""Identity and default-policy checks for a private graph spanning USB hotplug."""
import json
import re
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "research"))
from consumer_session import require
from pipewire_defaults import objects, props, object_serial, validate_dump

KINDS = {"sink": {"Audio/Sink"}, "source": {"Audio/Source", "Audio/Source/Virtual"}}


def node_identity(node):
    return dict(id=node["id"], serial=object_serial(node), name=props(node).get("node.name"),
                mediaClass=props(node).get("media.class"))


def default_nodes(snapshot):
    validate_dump(snapshot)
    entries = [row for row in objects(snapshot, "Metadata") if row.get("props", {}).get("metadata.name") == "default"]
    require(len(entries) == 1, "Exactly one default metadata object required")
    values = {}
    for row in entries[0].get("metadata", []):
        key = row.get("key")
        if row.get("subject") != 0 or key not in {"default.audio." + k for k in KINDS} | {"default.configured.audio." + k for k in KINDS}:
            continue
        value = row.get("value")
        require(key not in values and row.get("type") == "Spa:String:JSON" and isinstance(value, dict)
                and isinstance(value.get("name"), str) and value["name"], "Malformed or duplicate default metadata")
        values[key] = value["name"]
    answer = {}
    for kind in KINDS:
        name = values.get("default.audio." + kind)
        nodes = [row for row in objects(snapshot, "Node") if props(row).get("node.name") == name]
        require(name is not None and len(nodes) == 1, "Default does not identify one live node")
        # A sink monitor is a possible source fallback. Keep its true class.
        require(props(nodes[0]).get("media.class") in KINDS[kind] | ({"Audio/Sink"} if kind == "source" else set()),
                "Default node has another media class")
        answer[kind] = node_identity(nodes[0]) | dict(configuredName=values.get("default.configured.audio." + kind))
    return answer


def control_nodes(snapshot, names):
    result = {}
    for kind, name in names.items():
        matches = [row for row in objects(snapshot, "Node") if props(row).get("node.name") == name]
        require(len(matches) == 1 and props(matches[0]).get("media.class") in KINDS[kind], "Control node missing or ambiguous")
        require("device.id" not in props(matches[0]) and "api.alsa.pcm.card" not in props(matches[0]),
                "Control must be a private software node, never ALSA")
        result[kind] = node_identity(matches[0])
    return result


def compare(snapshot, controls):
    current = control_nodes(snapshot, {kind: node["name"] for kind, node in controls.items()})
    require(current == controls, "A control node disappeared or its registry id was reused")
    defaults = default_nodes(snapshot)
    preserved = {kind: all(defaults[kind][key] == node[key] for key in ("id", "serial", "name", "mediaClass"))
                 for kind, node in controls.items()}
    return dict(defaults=defaults, controlIdentityPreserved=True, defaultPreserved=preserved)


def metadata_changes(text, controls):
    """Parse pinned pw-metadata 1.0's complete event stream, including transients."""
    require(len(text.encode()) <= 1024 * 1024, "Metadata monitor exceeded evidence bound")
    found, rows = 0, []
    relevant = {"default.audio." + kind: node["name"] for kind, node in controls.items()}
    for line in text.splitlines():
        if re.fullmatch(r'Found "default" metadata \d+', line):
            found += 1
            continue
        update = re.fullmatch(r"update: id:(\d+) key:'([^']+)' value:'(.*)' type:'([^']+)'", line)
        removal = re.fullmatch(r"remove: id:(\d+) (?:key:'([^']+)'|all keys)", line)
        require(update is not None or removal is not None, "Unknown metadata monitor output")
        if update:
            subject, key, value, value_type = update.groups()
            if subject != "0" or key not in relevant:
                continue
            require(value_type == "Spa:String:JSON", "Unknown default metadata event type")
            name = json.loads(value).get("name")
            require(isinstance(name, str) and name, "Empty monitored default")
            rows.append(dict(key=key, name=name, preserved=name == relevant[key]))
        elif removal[1] == "0" and (removal[2] is None or removal[2] in relevant):
            rows.append(dict(key=removal[2], name=None, preserved=False))
    require(found == 1, "Metadata monitor missing or rebound; cannot prove event continuity")
    require(all(any(row["key"] == key for row in rows) for key in relevant), "Monitor did not see both initial defaults")
    return dict(events=rows, allObservedDefaultsPreserved=all(row["preserved"] for row in rows))


def preservation(observations, changes):
    require(observations, "No graph observations")
    return {kind: all(row["defaultPreserved"][kind] for row in observations)
            and all(row["preserved"] for row in changes["events"] if row["key"] in (None, "default.audio."+kind))
            for kind in KINDS}
