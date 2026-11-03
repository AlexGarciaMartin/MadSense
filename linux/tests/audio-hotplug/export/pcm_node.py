"""Exact properties of the explicitly created node; no ACP Device/Route writes."""
import json
import math
import struct

from export_policy import validate_node, require

NODE_VOLUMES_LUA = r'''local args = ...
local id, serial = tonumber(args.nodeId), tonumber(args.nodeSerial)
assert(id and serial and id >= 0 and serial >= 0 and id % 1 == 0 and serial % 1 == 0)
local om = ObjectManager { Interest { type = "node", Constraint { "bound-id", "=", id, type = "gobject" } } }
local function read()
  assert(om:get_n_objects() == 1, "missing exact node")
  local answer
  for node in om:iterate() do
    assert(tonumber(node.properties["object.serial"]) == serial, "node replaced")
    for parameter in node:iterate_params("Props") do
      local pod = parameter:parse()
      assert(pod.pod_type == "Object" and pod.object_id == "Props", "unexpected POD")
      local p = pod.properties
      if p.volume ~= nil or p.channelVolumes ~= nil or p.softVolumes ~= nil then
        assert(not answer and p.mute == false and p.softMute == false and p.volume == 1, "ambiguous/muted/scaled node")
        assert(p.channelMap.pod_type == "Array" and #p.channelMap == 4, "invalid map")
        local names = { "FL", "FR", "RL", "RR" }
        for i = 1, 4 do assert(p.channelMap[i] == names[i], "channel map changed") end
        local function array(a)
          assert(a.pod_type == "Array" and a.value_type == "Spa:Float" and #a == 4, "invalid float array")
          local values = {}
          for i = 1, 4 do assert(a[i] >= 0 and a[i] <= 1, "volume out of range"); values[i] = string.format("%.17g", a[i]) end
          return "[" .. table.concat(values, ",") .. "]"
        end
        answer = string.format('{"nodeId":%d,"nodeSerial":%d,"raw":%s,"soft":%s}',id,serial,array(p.channelVolumes),array(p.softVolumes))
      end
    end
  end
  assert(answer, "missing node volume Props");return answer
end
om:connect("installed", function ()
  local ok, value = pcall(read)
  if ok then print(value) else
    local escaped = tostring(value):gsub('[%z\1-\31\\"]',function(c) return string.format("\\u%04x",string.byte(c)) end)
    print('{"error":"' .. escaped .. '"}')
  end
  Core.quit()
end)
om:activate()
'''

def node_state(snapshot, plan, identity):
    rows=[row for row in snapshot if row.get('type')=='PipeWire:Interface:Node' and row.get('id')==identity['id']]
    require(len(rows)==1,'Export node missing')
    value=validate_node(rows[0],plan,require_format=True)
    require({key:value[key] for key in ('id','serial','clientId')}==identity,'Export node identity changed')
    volumes=[p for p in value['parameters'].get('Props',[]) if 'volume' in p or 'channelVolumes' in p or 'softVolumes' in p]
    require(len(volumes)==1,'One complete node volume Props required')
    props=volumes[0]
    require(props.get('volume')==1 and props.get('mute') is False and props.get('softMute') is False
        and props.get('channelMap')==['FL','FR','RL','RR'],'Unexpected node map/mute/scalar gain')
    for name in ('channelVolumes','softVolumes'):
        require(isinstance(props.get(name),list) and len(props[name])==4 and all(type(x) in (float,int)
            and math.isfinite(x) and 0<=x<=1 for x in props[name]),'Incomplete node gain array')
    return dict(identity=identity,properties=value['properties'],volumeProps=props)

def exact_node(record,state):
    require(set(record)=={'nodeId','nodeSerial','raw','soft'} and record['nodeId']==state['identity']['id']
        and record['nodeSerial']==state['identity']['serial'],'Precise reader identity/error differs')
    for name,field in (('raw','channelVolumes'),('soft','softVolumes')):
        values=record[name]
        require(isinstance(values,list) and len(values)==4 and all(type(x) in (float,int) and math.isfinite(x)
            and 0<=x<=1 and struct.unpack('<f',struct.pack('<f',x))[0]==x for x in values),'Exact float32 gains required')
        require([float(format(x,'.6f')) for x in values]==state['volumeProps'][field],'Precise reader and snapshot disagree')
    return record

def read_node(session,identity,label):
    before=node_state(session.capture_snapshot(label+'-before'),session.plan,identity)
    if not hasattr(session,'pcm_volume_script'):
        source=session.output/'pcm-node-volumes.lua';source.write_text(NODE_VOLUMES_LUA)
        session.pcm_volume_script=session.stage(source)
    _,raw=session.command(label,['wpexec',session.pcm_volume_script,f"nodeId={identity['id']}",f"nodeSerial={identity['serial']}"],timeout=10)
    exact=exact_node(json.loads(raw),before)
    after=node_state(session.capture_snapshot(label+'-after'),session.plan,identity)
    require(before==after,'Node changed during exact volume read')
    session.result.setdefault('pcmNodeReads',[]).append(dict(label=label,state=before,exact=exact));session.save()
    return before,exact

def set_raw(session,identity,original,allowed,label,wanted):
    state,precise=read_node(session,identity,label+'-check')
    require(precise['raw'] in allowed and precise['soft']==original['soft'],'Unexpected node gain change; do not overwrite')
    require(wanted in (original['raw'],[.5,.25,1.,1.]),'Only original or explicit front control gains allowed')
    session.result.setdefault('pcmNodeRequests',[]).append(dict(label=label,identity=identity,wanted=wanted));session.save()
    session.command(label,['pw-cli','--remote',session.env['PIPEWIRE_REMOTE'],'set-param',str(identity['id']),
        'Props',json.dumps(dict(channelVolumes=wanted))])
    _,after=read_node(session,identity,label+'-confirmed')
    require(after['raw']==wanted and after['soft']==original['soft'],'Node did not confirm the exact requested gains')
    return after
