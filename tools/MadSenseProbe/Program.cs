using System.Buffers.Binary;
using System.Diagnostics;
using System.Text.Json;
using DualSenseClient.Hid;

// Observer-only by default. --echo is explicit: zero-valid-flags USB output with
// a host timestamp marker; no rumble, trigger, light or audio flags are enabled.
// This is host-observed transport timing, never physical actuator latency.
static class Program
{
    record Sample(uint Motion, uint Device, long Tick, ulong Hash, ulong MotionHash, uint Echo, byte Right, byte Left, byte Status2);
    static readonly JsonSerializerOptions Json = new() { WriteIndented = true };
    static string? Opt(string[] a, string key) { var i=Array.IndexOf(a,key); return i>=0 && i+1<a.Length ? a[i+1] : null; }
    static int Main(string[] args)
    {
        try { return Run(args); } catch(Exception e) { Console.Error.WriteLine(e); return 1; }
    }
    static int Run(string[] args)
    {
        if (args.Contains("--self-test")) return SelfTest();
        using var enumerator = new HidDeviceEnumerator();
        var devices = enumerator.Enumerate(0x054c).Where(x => x.ProductId is 0x0ce6 or 0x0df2 && x.UsagePage == 1).ToArray();
        if(args.Contains("--list")) { Console.WriteLine(JsonSerializer.Serialize(devices.Select(x=>new{x.Path,x.ProductName,Bus=x.BusType.ToString(),x.InterfaceNumber}),Json)); return 0; }
        var bt=devices.Where(x=>x.BusType==ConnectionType.Bluetooth).ToArray();
        var usb=devices.Where(x=>x.BusType==ConnectionType.Usb).ToArray();
        var physicalPath=Opt(args,"--physical") ?? (bt.Length==1 ? bt[0].Path : throw new Exception($"Expected one BT pad, found {bt.Length}. Authorize this EXE in HidHide or use --physical."));
        var virtualPath=Opt(args,"--virtual") ?? (usb.Length==1 ? usb[0].Path : throw new Exception($"Expected one USB virtual pad, found {usb.Length}. Unplug physical USB pads/use --virtual."));
        if(physicalPath==virtualPath) throw new Exception("Physical and virtual paths must differ.");
        int seconds=Math.Clamp(int.Parse(Opt(args,"--seconds")??"20"),5,120);
        string label=Opt(args,"--label")??"unnamed";
        string output=Opt(args,"--out")??"probe-result.json";
        using var physical=enumerator.OpenDevice(physicalPath);
        using var virtualDevice=enumerator.OpenDevice(virtualPath);
        object calibration;
        try
        {
            var pc=physical.GetFeatureReport(0x05,41); var vc=virtualDevice.GetFeatureReport(0x05,41);
            calibration=new {Physical=Convert.ToHexString(pc),Virtual=Convert.ToHexString(vc),Equal=pc.SequenceEqual(vc),Meaning="Calibration report0x05 read before timing capture. Exact raw equality includes trailer bytes."};
        }
        catch(Exception e) { calibration=new {Error=e.Message}; }
        using var cancel=new CancellationTokenSource();
        using var ready=new CountdownEvent(2);
        using var start=new ManualResetEventSlim();
        List<Sample> physicalSamples=new(100000),virtualSamples=new(100000);
        List<string> errors=[];
        Thread Reader(IHidDevice hid,bool bluetooth,List<Sample> samples)
        {
            var thread=new Thread(()=>
            {
                byte[] raw=new byte[128]; ready.Signal(); start.Wait();
                try
                {
                    while(!cancel.IsCancellationRequested)
                    {
                        int n=hid.Read(raw,0,raw.Length,25);
                        long tick=Stopwatch.GetTimestamp();
                        int o=bluetooth ? 2 : 1;
                        if(n<o+63 || raw[0]!=(bluetooth?0x31:0x01)) continue;
                        samples.Add(Parse(raw,o,tick));
                    }
                }
                catch(Exception e) { lock(errors) errors.Add(e.Message); }
            }) { IsBackground=true,Name=bluetooth?"physical-observer":"virtual-observer" };
            thread.Start(); return thread;
        }
        var pt=Reader(physical,true,physicalSamples); var vt=Reader(virtualDevice,false,virtualSamples);
        ready.Wait(); long epoch=Stopwatch.GetTimestamp(); start.Set();
        List<(uint Tag,long Tick,int Result)> writes=[];
        Thread.Sleep(1000); // Ignore initial queued reports and warm up readers.
        if(args.Contains("--echo"))
        {
            byte[] raw=new byte[48]; raw[0]=0x02;
            for(int i=0;i<(seconds-1)*10;i++)
            {
                uint tag=0x4D530000u+(uint)i+1;
                BinaryPrimitives.WriteUInt32LittleEndian(raw.AsSpan(33,4),tag);
                long tick=Stopwatch.GetTimestamp(); int result=virtualDevice.Write(raw,0,raw.Length);
                writes.Add((tag,tick,result)); Thread.Sleep(100);
            }
        }
        else Thread.Sleep((seconds-1)*1000);
        long endBench=Stopwatch.GetTimestamp();
        long triggerOn=0,triggerOff=0; int triggerOnResult=0,triggerOffResult=0;
        if(args.Contains("--trigger-probe"))
        {
            // Explicit small effect on both triggers for400ms, then explicit Off.
            byte[] raw=TriggerReport(true);
            try { triggerOn=Stopwatch.GetTimestamp(); triggerOnResult=virtualDevice.Write(raw,0,raw.Length); Thread.Sleep(400); }
            finally { raw=TriggerReport(false); triggerOff=Stopwatch.GetTimestamp(); triggerOffResult=virtualDevice.Write(raw,0,raw.Length); }
            Thread.Sleep(600);
        }
        cancel.Cancel(); pt.Join(2000); vt.Join(2000);
        long cutoff=epoch+Stopwatch.Frequency;
        var p=physicalSamples.Where(s=>s.Tick>=cutoff && s.Tick<endBench).ToArray();
        var v=virtualSamples.Where(s=>s.Tick>=cutoff && s.Tick<endBench).ToArray();
        var matched=Match(p,v);
        var echoP=physicalSamples.Where(x=>x.Echo>=0x4D530001u && x.Echo<0x4D540000u).GroupBy(x=>x.Echo).ToDictionary(g=>g.Key,g=>g.Min(s=>s.Tick));
        var echoV=virtualSamples.Where(x=>x.Echo>=0x4D530001u && x.Echo<0x4D540000u).GroupBy(x=>x.Echo).ToDictionary(g=>g.Key,g=>g.Min(s=>s.Tick));
        var echoPhysical=writes.Where(w=>echoP.ContainsKey(w.Tag)).Select(w=>Ms(echoP[w.Tag]-w.Tick)).ToArray();
        var echoVirtual=writes.Where(w=>echoV.ContainsKey(w.Tag)).Select(w=>Ms(echoV[w.Tag]-w.Tick)).ToArray();
        var resultObject=new {
            Label=label,Timestamp=DateTimeOffset.Now,Seconds=seconds,PhysicalPath=physicalPath,VirtualPath=virtualPath,
            Metric="Virtual minus physical input read-completion time for identical motion timestamp; observer scheduling is included. NOT end-to-end or mechanical latency.",
            WarmupExcludedSeconds=1,PhysicalReports=p.Length,VirtualReports=v.Length,UniquePhysical=p.Select(s=>s.Motion).Distinct().Count(),UniqueVirtual=v.Select(s=>s.Motion).Distinct().Count(),
            Matched=matched.Count,MatchFractionOfVirtual=matched.Count/(double)Math.Max(1,v.Select(s=>s.Motion).Distinct().Count()),
            ForwardingMs=Stats(matched.Select(x=>x.Delta).ToArray()),NegativeObservedDeltas=matched.Count(x=>x.Delta<0),
            RawPayloadExactMatches=matched.Count(x=>x.P.Hash==x.V.Hash),TriggerStatusExactMatches=matched.Count(x=>x.P.Right==x.V.Right && x.P.Left==x.V.Left && x.P.Status2==x.V.Status2),
            RawGyroAccelExactMatches=matched.Count(x=>x.P.MotionHash==x.V.MotionHash),
            PhysicalIntervalsMs=Stats(Intervals(p)),VirtualIntervalsMs=Stats(Intervals(v)),
            OutputEcho=new {Requested=writes.Count,ShortWrites=writes.Count(x=>x.Result<48),PhysicalResponses=echoPhysical.Length,VirtualResponses=echoVirtual.Length,PhysicalRoundTripMs=Stats(echoPhysical),VirtualRoundTripMs=Stats(echoVirtual),Meaning="Virtual HID write to observed host timestamp echo; includes output and input transport plus scheduling, not actuator motion. Missing echoes make this metric unsupported, not zero latency."},
            Calibration=calibration,
            TriggerProbe=new {Requested=args.Contains("--trigger-probe"),OnWriteResult=triggerOnResult,OffWriteResult=triggerOffResult,OnWriteTick=triggerOn,OffWriteTick=triggerOff,StopwatchFrequency=Stopwatch.Frequency,
                PhysicalAck=TriggerAck(physicalSamples,triggerOn,triggerOff),VirtualAck=TriggerAck(virtualSamples,triggerOn,triggerOff),
                PhysicalBefore=Statuses(physicalSamples,endBench-Stopwatch.Frequency/2,endBench),
                PhysicalDuring=Statuses(physicalSamples,triggerOn,triggerOff),VirtualDuring=Statuses(virtualSamples,triggerOn,triggerOff),
                PhysicalAfter=Statuses(physicalSamples,triggerOff,long.MaxValue),VirtualAfter=Statuses(virtualSamples,triggerOff,long.MaxValue),
                Meaning="Mild Feedback(3,2) for400ms with explicit Off, valid flags only both triggers. Status bytes can remain unchanged without finger pressure. No change is inconclusive, never proof of broken effects or zero latency."},
            Errors=errors,Observer="Two normal-priority dedicated read threads, Stopwatch clock, no process priority changes. Multiple HID handles may affect scheduling; compare repeated same-condition runs."};
        Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(output))!);
        File.WriteAllText(output,JsonSerializer.Serialize(resultObject,Json));
        File.WriteAllLines(Path.ChangeExtension(output,".csv"),new[]{"motion_timestamp,physical_tick,virtual_tick,delta_ms,raw_equal,trigger_equal"}.Concat(matched.Select(m=>$"{m.P.Motion},{m.P.Tick},{m.V.Tick},{m.Delta.ToString("R",System.Globalization.CultureInfo.InvariantCulture)},{m.P.Hash==m.V.Hash},{m.P.Right==m.V.Right && m.P.Left==m.V.Left && m.P.Status2==m.V.Status2}")));
        Console.WriteLine(JsonSerializer.Serialize(resultObject,Json));
        return errors.Count==0 && matched.Count>100 ? 0 : 2;
    }
    static Sample Parse(byte[] raw,int o,long tick)
    {
        ulong hash=14695981039346656037UL;
        foreach(byte b in raw.AsSpan(o,63)) { hash^=b; hash*=1099511628211UL; }
        ulong motionHash=14695981039346656037UL;
        foreach(byte b in raw.AsSpan(o+15,12)) { motionHash^=b; motionHash*=1099511628211UL; }
        return new(BinaryPrimitives.ReadUInt32LittleEndian(raw.AsSpan(o+27,4)),BinaryPrimitives.ReadUInt32LittleEndian(raw.AsSpan(o+48,4)),tick,hash,motionHash,BinaryPrimitives.ReadUInt32LittleEndian(raw.AsSpan(o+43,4)),raw[o+41],raw[o+42],raw[o+47]);
    }
    static List<(Sample P,Sample V,double Delta)> Match(Sample[] p,Sample[] v)
    {
        var pd=p.GroupBy(s=>s.Motion).ToDictionary(g=>g.Key,g=>g.MinBy(s=>s.Tick)!);
        return v.GroupBy(s=>s.Motion).Select(g=>g.MinBy(s=>s.Tick)!).Where(s=>pd.ContainsKey(s.Motion)).Select(s=>(pd[s.Motion],s,Ms(s.Tick-pd[s.Motion].Tick))).ToList();
    }
    static double Ms(long ticks)=>ticks*1000.0/Stopwatch.Frequency;
    static object TriggerAck(List<Sample> s,long on,long off)
    {
        var before=s.LastOrDefault(x=>x.Tick<on);
        var activated=on==0 || before is null || (before.Status2&0x11)!=0 ? null : s.FirstOrDefault(x=>x.Tick>=on && x.Tick<off && (x.Status2&0x11)==0x11);
        var cleared=activated==null ? null : s.FirstOrDefault(x=>x.Tick>=off && (x.Status2&0x11)==0);
        return new {BaselineInactive=before!=null && (before.Status2&0x11)==0,ActivationMs=activated==null?(double?)null:Ms(activated.Tick-on),ClearMs=cleared==null?(double?)null:Ms(cleared.Tick-off),Meaning="Observed status2 bits0/4 asserted/cleared after explicit writes. Controller-reported state acknowledgement plus transport/scheduling; NOT felt force, calibrated actuator onset or mechanical latency."};
    }
    static object[] Statuses(List<Sample> samples,long begin,long end)=>samples.Where(x=>x.Tick>=begin && x.Tick<end).GroupBy(s=>(s.Right,s.Left,s.Status2)).Select(g=>(object)new {R2=g.Key.Right,L2=g.Key.Left,Status2=g.Key.Status2,Count=g.Count(),FirstTick=g.Min(s=>s.Tick)}).ToArray();
    static byte[] TriggerReport(bool on)
    {
        byte[] raw=new byte[48]; raw[0]=0x02; raw[1]=0x0c;
        foreach(int offset in new[]{11,22})
        {
            raw[offset]=(byte)(on?0x21:0x05);
            if(!on)continue;
            ushort zones=0;uint forces=0;
            for(int i=3;i<10;i++){zones|=(ushort)(1<<i);forces|=1u<<(i*3);}
            BinaryPrimitives.WriteUInt16LittleEndian(raw.AsSpan(offset+1,2),zones);
            BinaryPrimitives.WriteUInt32LittleEndian(raw.AsSpan(offset+3,4),forces);
        }
        return raw;
    }
    static double[] Intervals(Sample[] s)=>s.Zip(s.Skip(1),(a,b)=>Ms(b.Tick-a.Tick)).ToArray();
    static object? Stats(double[] a)
    {
        if(a.Length==0)return null; Array.Sort(a);
        double Q(double q)=>a[(int)Math.Round((a.Length-1)*q)];
        return new {Count=a.Length,Min=a[0],Median=Q(.5),P95=Q(.95),P99=Q(.99),Max=a[^1],Mean=a.Average()};
    }
    static int SelfTest()
    {
        var bt=new byte[78]; var usb=new byte[64];
        for(int i=0;i<63;i++) bt[i+2]=usb[i+1]=(byte)(i*3);
        var p=Parse(bt,2,10000);var v=Parse(usb,1,20000);
        if(p.Motion!=v.Motion || p.Hash!=v.Hash || p.Right!=v.Right || p.Echo!=v.Echo)throw new Exception("Normalization test failed");
        var matches=Match([p],[v,v with {Tick=30000}]);
        if(matches.Count!=1 || matches[0].Delta!=Ms(10000))throw new Exception("First-arrival matching test failed");
        if(Match([p],[v with {Motion=1}]).Count!=0)throw new Exception("Unknown timestamp incorrectly matched");
        if(Match([v],[p])[0].Delta>=0)throw new Exception("Negative observer timing incorrectly discarded");
        Console.WriteLine("4 offline tests passed. No devices opened.");return 0;
    }
}
