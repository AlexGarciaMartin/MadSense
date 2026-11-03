import copy
import unittest
from unittest.mock import Mock, patch

from export_policy import snapshot_source
from pcm_node import exact_node, node_state, set_raw
from test_export import node, PLAN

def snapshot():
    value=node()
    value['info']['params']['Props']=[dict(volume=1,mute=False,softMute=False,
        channelMap=['FL','FR','RL','RR'],channelVolumes=[1,1,1,1],softVolumes=[1,1,1,1])]
    return [value]

IDENTITY=dict(id=45,serial=10,clientId=40)
EXACT=dict(nodeId=45,nodeSerial=10,raw=[1,1,1,1],soft=[1,1,1,1])

class NodeGainContracts(unittest.TestCase):
    def test_actual_node_map_identity_and_precise_arrays_are_required(self):
        state=node_state(snapshot(),PLAN,IDENTITY)
        self.assertEqual(exact_node(EXACT,state),EXACT)
        for mutation in (dict(nodeSerial=99),dict(raw=[.99999999]*4),dict(raw=[.5,1,1,1]),dict(error='read failed')):
            with self.subTest(mutation=mutation),self.assertRaises(RuntimeError):exact_node(EXACT|mutation,state)
        for field,value in (('channelMap',['FR','FL','RL','RR']),('mute',True),('volume',.5),('softVolumes',[1,1])):
            changed=snapshot();changed[0]['info']['params']['Props'][0][field]=value
            with self.subTest(field=field),self.assertRaises(RuntimeError):node_state(changed,PLAN,IDENTITY)

    def test_write_targets_only_exact_node_and_refuses_unexpected_prior_gain(self):
        from types import SimpleNamespace
        session=SimpleNamespace(env={'PIPEWIRE_REMOTE':'/owned/pipewire-0'},result={},save=Mock(),command=Mock())
        requested=EXACT|dict(raw=[.5,.25,1,1])
        with patch('pcm_node.read_node',side_effect=[({},EXACT),({},requested)]):
            self.assertEqual(set_raw(session,IDENTITY,EXACT,[[1,1,1,1]],'front',[.5,.25,1,1]),requested)
        argv=session.command.call_args.args[1]
        self.assertEqual(argv[:6],['pw-cli','--remote','/owned/pipewire-0','set-param','45','Props'])
        self.assertNotIn('softVolumes',argv[6]);self.assertNotIn('Route',argv)
        session.command.reset_mock()
        with patch('pcm_node.read_node',return_value=({},EXACT|dict(raw=[.2,1,1,1]))),self.assertRaises(RuntimeError):
            set_raw(session,IDENTITY,EXACT,[[1,1,1,1]],'front',[.5,.25,1,1])
        session.command.assert_not_called()

if __name__=='__main__':unittest.main()
