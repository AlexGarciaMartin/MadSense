"""Class request observations, separate from whether current decoding is correct."""
import errno

from consumer_session import bt_state, require


CASES = ('output-write', 'output-ioctl', 'output-control', 'feature-status')


def packet(case):
    if case == 'feature-status': return bytes.fromhex('800300')
    if case == 'feature-edge': return bytes([0x65, *(17+3*i for i in range(1,64))])
    require(case in CASES, 'Unknown fixed control report case')
    result=bytearray(48);result[0]=2;result[2]=4
    shift=CASES.index(case)
    result[45:48]=bytes([0x45+0x11*shift,0x67+0x11*shift,0x89+0x11*shift])
    return bytes(result)


def observe(api, start, end, controls, core, bluetooth):
    require(api.get('schema')==1 and api.get('identityVerified') is True,
            'Probe did not verify the exact virtual Sony identity')
    case=api.get('case');request=packet(case)
    require(api.get('requestHex')==request.hex() and api.get('requestedBytes')==len(request),
            'Probe request differs from the fixed experimental packet')
    require(all(api.get(key) is False for key in ('driverDetached','kernelPolicyModified','physicalDeviceAccess','latencyValidated')),
            'Unexpected control experiment scope')
    require(all(type(api.get(key)) is int for key in ('returned','getReturned','errno','startedMonotonicNs',
                    'endedMonotonicNs','getEndedMonotonicNs'))
            and 0<start<=api['startedMonotonicNs']<=api['endedMonotonicNs']<=api['getEndedMonotonicNs']<=end,
            'Invalid API return or experiment clock interval')
    rows=[row for row in controls if start<=row['monotonicTicks']<=end]
    for row in rows:
        require(row['outDataLength']==len(bytes.fromhex(row['outDataHex'])), 'Truncated class request snapshot')
    feature=case.startswith('feature-')
    value=((3 if feature else 2)<<8)|request[0]
    matching=[row for row in rows if row['setup']['request']==9 and row['setup']['value']==value]
    result=dict(case=case, requestedHex=request.hex(), returned=api['returned'], errno=api['errno'],
        controlSetReports=matching, controlPathObserved=bool(matching), reportIdInControlPayload=None,
        behaviorMatchesExpected=False, physicalActuationVerified=False, latencyValidated=False)
    if case=='output-control' and api['returned']==-1 and api['errno']==errno.EBUSY:
        require(not matching, 'EBUSY operation unexpectedly reached the virtual class handler')
        result.update(outcome='kernel-interface-busy', behaviorMatchesExpected=None,
                      driverDetachAttempted=False, controlPayloadNotTested=True)
        return result
    require(api.get('error') is None and api['errno']==0 and api['returned']==len(request),
            'Report API did not complete the fixed request; retain its refusal without normalizing it')
    if feature or case=='output-control':
        require(len(matching)==1, 'Expected one SET_REPORT in the class observer')
    elif matching:
        require(len(matching)==1, 'Ambiguous fallback to SET_REPORT')
    if matching:
        row=matching[0]
        require(row['setup']==dict(requestType=0x21,request=9,value=value,index=3,length=len(request)),
                'Unexpected setup packet: preserve it rather than changing recipient/report length')
        require(bytes.fromhex(row['outDataHex'])==request, 'Kernel class payload changed before the virtual handler')
        result['reportIdInControlPayload']=True
    if feature:
        response=bytes.fromhex(api['responseHex'])
        require(api['getReturned']==64 and len(response)==64 and response[0]==(0x81 if case=='feature-status' else 0x65),
                'Feature read did not return the complete response')
        reads=[row for row in rows if row['setup']==dict(requestType=0xa1,request=1,
                  value=0x0300|response[0],index=3,length=64) and row['outDataLength']==0
                  and row['monotonicTicks']>=matching[0]['monotonicTicks']]
        require(len(reads)==1, 'Expected one subsequent GET_REPORT for the response')
        expected=bytearray(64);expected[0]=response[0]
        if case=='feature-status': expected[1]=1;expected[4]=1
        else: expected[:]=request
        result.update(outcome='feature-observed', responseHex=response.hex(), expectedResponseHex=expected.hex(),
                      behaviorMatchesExpected=response==expected)
        return result
    raw=[row for row in core if row.get('kind')=='virtual-output-raw' and start<=row['timestamp']<=end]
    for row in raw:
        require(row['value']['length']==len(bytes.fromhex(row['value']['hex'])), 'Truncated raw output observer')
    # Record all accepted raw output in this exclusive case. A duplicated ID
    # must remain visible; it must never be stripped by this verifier.
    result['rawOutputReports']=raw
    exact=[row for row in raw if row['value']['length']==48 and bytes.fromhex(row['value']['hex'])==request]
    delivered=[]
    if len(exact)==1:
        for row in bluetooth:
            if row.get('kind')!='output' or not exact[0]['timestamp']<=row['monotonicNs']<=end: continue
            state=bt_state(bytes.fromhex(row['hex']))
            if state is not None and state[1]&4 and state[44:47]==request[45:48]: delivered.append(row)
    result.update(outcome='output-observed', exactUsbReportCount=len(exact), bluetoothRgbReports=delivered,
                  behaviorMatchesExpected=len(exact)==1 and len(delivered)==1)
    return result


def final_capture(stopped):
    require(stopped.get('completed') is True and stopped.get('cleanupConfirmed') is True
            and stopped.get('evidenceOverflow') is False, 'Core did not complete and clean up')
    require(stopped.get('rawOutputObservation')==dict(rejectedSnapshots=0,unwrittenSnapshots=0,error=None),
            'Raw output evidence was not fully retained')
    row=stopped.get('controlObservation',{})
    require(row.get('sourceCopyOnly') is True and row.get('latencyValidated') is False
            and row.get('completed') is True and row.get('error','missing') is None
            and type(row.get('rejected')) is int and row['rejected']==0
            and type(row.get('admitted')) is int and row['admitted']==row.get('written')
            and 0<row['written']<=256 and row.get('timestampFrequency')==1_000_000_000,
            'Class request evidence was not fully retained')
    return True
