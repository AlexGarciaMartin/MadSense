import binascii
import copy
import errno
import struct
import unittest

from control_evidence import final_capture, observe, packet


def api(case):
    request=packet(case)
    response=bytearray(64)
    if case=='feature-status':response[0]=0x81;response[1]=1;response[4]=1
    elif case=='feature-edge':response[:]=request
    return dict(schema=1,case=case,error=None,errno=0,returned=len(request),getReturned=64 if case.startswith('feature') else -1,
        identityVerified=True,requestedBytes=len(request),requestHex=request.hex(),responseHex=response.hex(),
        startedMonotonicNs=110,endedMonotonicNs=150,getEndedMonotonicNs=180,
        driverDetached=False,kernelPolicyModified=False,physicalDeviceAccess=False,latencyValidated=False)


def setup(case):
    feature=case.startswith('feature');data=packet(case)
    rows=[dict(monotonicTicks=120,setup=dict(requestType=0x21,request=9,value=(0x0300 if feature else 0x0200)|data[0],
        index=3,length=len(data)),outDataHex=data.hex(),outDataLength=len(data))]
    if feature:
        rows.append(dict(monotonicTicks=160,setup=dict(requestType=0xa1,request=1,
            value=0x0381 if case=='feature-status' else 0x0365,index=3,length=64),outDataHex='',outDataLength=0))
    return rows


def outputs(case,duplicate=False):
    data=packet(case);raw=(data[:1]+data) if duplicate else data
    core=[dict(kind='virtual-output-raw',timestamp=125,value=dict(hex=raw.hex(),length=len(raw)))]
    bt=bytearray(78);bt[0]=0x31;bt[3:50]=data[1:]
    struct.pack_into('<I',bt,74,binascii.crc32(b'\xa2'+bt[:-4]))
    return core,[dict(kind='output',monotonicNs=170,hex=bt.hex())]


class EvidenceTests(unittest.TestCase):
    def test_ioctl_can_use_interrupt_or_control_and_is_labelled_from_actual_capture(self):
        for controls in ([],setup('output-ioctl')):
            result=observe(api('output-ioctl'),100,200,controls,*outputs('output-ioctl'))
            self.assertTrue(result['behaviorMatchesExpected'])
            self.assertEqual(result['controlPathObserved'],bool(controls))

    def test_nonzero_report_id_is_retained_before_normalization(self):
        result=observe(api('output-control'),100,200,setup('output-control'),*outputs('output-control',duplicate=True))
        self.assertTrue(result['reportIdInControlPayload'])
        self.assertFalse(result['behaviorMatchesExpected'])
        self.assertEqual(result['rawOutputReports'][0]['value']['length'],49)

    def test_feature_status_shift_is_visible_even_when_both_apis_succeed(self):
        value=api('feature-status')
        observed=observe(value,100,200,setup('feature-status'),[],[])
        self.assertTrue(observed['behaviorMatchesExpected'])
        response=bytearray.fromhex(value['responseHex']);response[4]=0;value['responseHex']=response.hex()
        self.assertFalse(observe(value,100,200,setup('feature-status'),[],[])['behaviorMatchesExpected'])

    def test_edge_shift_is_not_repaired_by_verifier(self):
        value=api('feature-edge')
        self.assertTrue(observe(value,100,200,setup('feature-edge'),[],[])['behaviorMatchesExpected'])
        data=bytes.fromhex(value['responseHex']);value['responseHex']=(data[:1]+data[:-1]).hex()
        self.assertFalse(observe(value,100,200,setup('feature-edge'),[],[])['behaviorMatchesExpected'])

    def test_busy_control_is_not_a_success_or_permission_bypass(self):
        value=api('output-control');value.update(returned=-1,errno=errno.EBUSY,error='report operation')
        result=observe(value,100,200,[],[],[])
        self.assertIsNone(result['behaviorMatchesExpected'])
        self.assertTrue(result['controlPayloadNotTested'])
        with self.assertRaises(RuntimeError):observe(value,100,200,setup('output-control'),[],[])

    def test_bad_setup_crc_time_and_missing_capture_fail(self):
        for kind in ('setup','crc','time','missing'):
            controls=setup('output-control');core,bt=outputs('output-control')
            if kind=='setup':controls[0]['setup']['requestType']=0x20
            if kind=='crc':bt[0]['hex']=bt[0]['hex'][:-2]+'00'
            if kind=='time':controls[0]['monotonicTicks']=90
            if kind=='missing':controls=[]
            with self.assertRaises(RuntimeError):observe(api('output-control'),100,200,controls,core,bt)

    def test_final_drain_is_mandatory(self):
        value=dict(completed=True,cleanupConfirmed=True,evidenceOverflow=False,
            rawOutputObservation=dict(rejectedSnapshots=0,unwrittenSnapshots=0,error=None),
            controlObservation=dict(sourceCopyOnly=True,latencyValidated=False,completed=True,error=None,
                rejected=0,admitted=10,written=10,timestampFrequency=1_000_000_000))
        self.assertTrue(final_capture(value))
        for key,bad in (('rejected',1),('written',9),('error','disk failure'),('completed',False)):
            changed=copy.deepcopy(value);changed['controlObservation'][key]=bad
            with self.assertRaises(RuntimeError):final_capture(changed)


if __name__=='__main__':unittest.main()
