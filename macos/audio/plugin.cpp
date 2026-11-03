#include "AudioProtocol.hpp"
#include <CoreFoundation/CFPlugInCOM.h>
#include <mach/mach_time.h>
#include <os/log.h>
#include <sys/sysctl.h>
#include <sys/proc.h>
#include <unistd.h>
#include <atomic>
#include <cerrno>
#include <cmath>
#include <cstring>
#include <map>
#include <mutex>
#include <vector>

using namespace madsense;
namespace {
AudioServerPlugInDriverInterface interface{};
AudioServerPlugInDriverInterface* interfacePointer = &interface;
AudioServerPlugInHostRef host = nullptr;
std::atomic<UInt32> references{1}, running{0};
std::atomic<uint64_t> anchor{0}, timestampSeed{1}, pcmGeneration{1}, leaseDeadline{0};
double ticksPerFrame = 1;
uint64_t leaseTicks = 1;
std::mutex stateMutex;
AudioRing ring;
struct Identity { pid_t pid = 0; uid_t uid = 0; uint64_t seconds = 0, microseconds = 0; };
struct Client { Identity identity; bool active = false; };
Identity owner;
std::map<UInt32, Client> clients;

os_log_t identityLog() {
    static os_log_t log = os_log_create("org.madsense.audio", "identity");
    return log;
}

bool identityFor(pid_t pid, Identity& identity) {
    if (pid <= 0) {
        os_log_error(identityLog(), "Identity denied: HAL caller pid=%{public}d, host pid=%{public}d; anonymous caller", pid, getpid());
        return false;
    }
    // The HAL helper runs as _coreaudiod, not the application's user. Full
    // PROC_PIDTBSDINFO requires the same UID. The public KERN_PROC_PID query
    // returns UID and start time together from one referenced kernel process;
    // never accept a bare PID or a caller-supplied identity as a substitute.
    int mib[] = {CTL_KERN, KERN_PROC, KERN_PROC_PID, pid};
    kinfo_proc info{};
    size_t size = sizeof(info);
    errno = 0;
    int result = sysctl(mib, 4, &info, &size, nullptr, 0);
    if (result != 0 || size != sizeof(info)) {
        int error = errno;
        os_log_error(identityLog(), "Identity denied: caller pid=%{public}d, host pid=%{public}d, host euid=%{public}u, KERN_PROC_PID result=%{public}d size=%{public}zu expected=%{public}zu errno=%{public}d",
                     pid, getpid(), geteuid(), result, size, sizeof(info), error);
        return false;
    }
    const auto& process = info.kp_proc;
    if (process.p_pid != pid || process.p_stat == 0 || process.p_stat == SZOMB ||
        process.p_starttime.tv_sec <= 0 || process.p_starttime.tv_usec < 0 || process.p_starttime.tv_usec >= 1000000) {
        os_log_error(identityLog(), "Identity denied: caller pid=%{public}d, snapshot pid=%{public}d state=%{public}d; missing, dead, or invalid process identity",
                     pid, process.p_pid, process.p_stat);
        return false;
    }
    identity = {pid, info.kp_eproc.e_ucred.cr_uid,
                static_cast<uint64_t>(process.p_starttime.tv_sec), static_cast<uint64_t>(process.p_starttime.tv_usec)};
    return true;
}
bool sameIdentity(const Identity& a, const Identity& b) {
    return a.pid > 0 && a.pid == b.pid && a.uid == b.uid &&
           a.seconds == b.seconds && a.microseconds == b.microseconds;
}
bool isOwner(pid_t pid) {
    Identity caller;
    return identityFor(pid, caller) && sameIdentity(owner, caller);
}
bool known(AudioObjectID id) { return id >= kPlugin && id <= kStream; }
AudioClassID objectClass(AudioObjectID id) {
    switch (id) {
        case kPlugin: return kAudioPlugInClassID;
        case kBox: return kAudioBoxClassID;
        case kDevice: return kAudioDeviceClassID;
        case kStream: return kAudioStreamClassID;
        default: return kAudioObjectClassID;
    }
}
bool qualifierMatches(AudioObjectID id, UInt32 size, const void* data) {
    if (!size) return true;
    if (!data || size % sizeof(AudioClassID)) return false;
    auto classes = static_cast<const AudioClassID*>(data);
    for (UInt32 i = 0; i < size / sizeof(AudioClassID); ++i)
        if (classes[i] == kAudioObjectClassID || classes[i] == objectClass(id)) return true;
    return false;
}
std::vector<AudioObjectID> objects(AudioObjectID id, const AudioObjectPropertyAddress& addr,
                                  UInt32 qualifierSize, const void* qualifier) {
    std::vector<AudioObjectID> values;
    switch (addr.mSelector) {
        case kAudioObjectPropertyOwnedObjects:
            if (id == kPlugin) values = {kBox, kDevice};
            if (id == kDevice && addr.mScope != kAudioObjectPropertyScopeInput) values = {kStream};
            values.erase(std::remove_if(values.begin(), values.end(), [&](auto child) {
                return !qualifierMatches(child, qualifierSize, qualifier);
            }), values.end());
            break;
        case kAudioPlugInPropertyBoxList: values = {kBox}; break;
        case kAudioPlugInPropertyDeviceList:
        case kAudioBoxPropertyDeviceList: values = {kDevice}; break;
        case kAudioDevicePropertyStreams:
            if (addr.mScope != kAudioObjectPropertyScopeInput) values = {kStream};
            break;
    }
    return values;
}
AudioStreamBasicDescription format() {
    return {kSampleRate, kAudioFormatLinearPCM,
            kAudioFormatFlagIsFloat | kAudioFormatFlagsNativeEndian | kAudioFormatFlagIsPacked,
            kChannels * sizeof(float), 1, kChannels * sizeof(float), kChannels, 32, 0};
}
template<class T> OSStatus copyValue(const T& value, UInt32 capacity, UInt32* written, void* out) {
    if (capacity < sizeof(T)) return kAudioHardwareBadPropertySizeError;
    std::memcpy(out, &value, sizeof(T)); *written = sizeof(T); return noErr;
}
OSStatus copyString(CFStringRef string, UInt32 capacity, UInt32* written, void* out) {
    if (capacity < sizeof(string)) return kAudioHardwareBadPropertySizeError;
    CFRetain(string);
    return copyValue(string, capacity, written, out);
}
const AudioServerPlugInCustomPropertyInfo customProperties[] = {
    {kOwner, kAudioServerPlugInCustomPropertyDataTypeCFPropertyList, kAudioServerPlugInCustomPropertyDataTypeNone},
    {kRead, kAudioServerPlugInCustomPropertyDataTypeCFPropertyList, kAudioServerPlugInCustomPropertyDataTypeNone}
};

OSStatus propertySize(AudioObjectID id, const AudioObjectPropertyAddress* addr,
                      UInt32 qs, const void* q, UInt32* size) {
    if (!known(id)) return kAudioHardwareBadObjectError;
    if (!addr || !size) return kAudioHardwareIllegalOperationError;
    if (addr->mElement != kAudioObjectPropertyElementMain) return kAudioHardwareUnknownPropertyError;
    switch (addr->mSelector) {
        case kAudioObjectPropertyBaseClass:
        case kAudioObjectPropertyClass:
        case kAudioObjectPropertyOwner: *size = sizeof(UInt32); return noErr;
        case kAudioObjectPropertyName:
        case kAudioObjectPropertyManufacturer: *size = sizeof(CFStringRef); return noErr;
        case kAudioObjectPropertyOwnedObjects:
            *size = static_cast<UInt32>(objects(id, *addr, qs, q).size() * sizeof(AudioObjectID)); return noErr;
    }
    if (id == kPlugin) switch (addr->mSelector) {
        case kAudioPlugInPropertyBundleID:
        case kAudioPlugInPropertyResourceBundle: *size = sizeof(CFStringRef); return noErr;
        case kAudioPlugInPropertyDeviceList:
        case kAudioPlugInPropertyBoxList:
        case kAudioPlugInPropertyTranslateUIDToDevice:
        case kAudioPlugInPropertyTranslateUIDToBox: *size = sizeof(AudioObjectID); return noErr;
    }
    if (id == kBox) switch (addr->mSelector) {
        case kAudioBoxPropertyBoxUID: *size = sizeof(CFStringRef); return noErr;
        case kAudioBoxPropertyTransportType:
        case kAudioBoxPropertyHasAudio:
        case kAudioBoxPropertyHasVideo:
        case kAudioBoxPropertyHasMIDI:
        case kAudioBoxPropertyIsProtected:
        case kAudioBoxPropertyAcquired:
        case kAudioBoxPropertyAcquisitionFailed:
        case kAudioBoxPropertyDeviceList: *size = sizeof(UInt32); return noErr;
    }
    if (id == kDevice) switch (addr->mSelector) {
        case kAudioDevicePropertyDeviceUID:
        case kAudioDevicePropertyModelUID: *size = sizeof(CFStringRef); return noErr;
        case kAudioDevicePropertyTransportType:
        case kAudioDevicePropertyRelatedDevices:
        case kAudioDevicePropertyClockDomain:
        case kAudioDevicePropertyDeviceIsAlive:
        case kAudioDevicePropertyDeviceIsRunning:
        case kAudioDevicePropertyDeviceCanBeDefaultDevice:
        case kAudioDevicePropertyDeviceCanBeDefaultSystemDevice:
        case kAudioDevicePropertyLatency:
        case kAudioDevicePropertySafetyOffset:
        case kAudioDevicePropertyIsHidden:
        case kAudioDevicePropertyZeroTimeStampPeriod: *size = sizeof(UInt32); return noErr;
        case kAudioDevicePropertyStreams:
            *size = addr->mScope == kAudioObjectPropertyScopeInput ? 0 : sizeof(AudioObjectID); return noErr;
        case kAudioObjectPropertyControlList: *size = 0; return noErr;
        case kAudioDevicePropertyNominalSampleRate: *size = sizeof(Float64); return noErr;
        case kAudioDevicePropertyAvailableNominalSampleRates: *size = sizeof(AudioValueRange); return noErr;
        case kAudioDevicePropertyPreferredChannelsForStereo: *size = 2 * sizeof(UInt32); return noErr;
        case kAudioObjectPropertyCustomPropertyInfoList: *size = sizeof(customProperties); return noErr;
        case kOwner:
        case kRead: *size = sizeof(CFPropertyListRef); return noErr;
    }
    if (id == kStream) switch (addr->mSelector) {
        case kAudioStreamPropertyIsActive:
        case kAudioStreamPropertyDirection:
        case kAudioStreamPropertyTerminalType:
        case kAudioStreamPropertyStartingChannel:
        case kAudioStreamPropertyLatency: *size = sizeof(UInt32); return noErr;
        case kAudioStreamPropertyVirtualFormat:
        case kAudioStreamPropertyPhysicalFormat: *size = sizeof(AudioStreamBasicDescription); return noErr;
        case kAudioStreamPropertyAvailableVirtualFormats:
        case kAudioStreamPropertyAvailablePhysicalFormats: *size = sizeof(AudioStreamRangedDescription); return noErr;
    }
    return kAudioHardwareUnknownPropertyError;
}

OSStatus getProperty(AudioServerPlugInDriverRef, AudioObjectID id, pid_t pid,
                      const AudioObjectPropertyAddress* addr, UInt32 qs, const void* q,
                      UInt32 capacity, UInt32* written, void* out) {
    if (!written) return kAudioHardwareIllegalOperationError;
    *written = 0;
    UInt32 required = 0;
    OSStatus result = propertySize(id, addr, qs, q, &required);
    if (result) return result;
    if (capacity < required) return kAudioHardwareBadPropertySizeError;
    if (!out && required) return kAudioHardwareIllegalOperationError;
    switch (addr->mSelector) {
        case kAudioObjectPropertyBaseClass: return copyValue<UInt32>(kAudioObjectClassID, capacity, written, out);
        case kAudioObjectPropertyClass: return copyValue<UInt32>(objectClass(id), capacity, written, out);
        case kAudioObjectPropertyOwner: return copyValue<UInt32>(id == kPlugin ? kAudioObjectUnknown : id == kStream ? kDevice : kPlugin, capacity, written, out);
        case kAudioObjectPropertyName: return copyString(id == kStream ? CFSTR("Speaker L/R, Haptics L/R") : CFSTR("MadSense DualSense Audio"), capacity, written, out);
        case kAudioObjectPropertyManufacturer: return copyString(CFSTR("MadSense"), capacity, written, out);
        case kAudioObjectPropertyOwnedObjects:
        case kAudioPlugInPropertyDeviceList:
        case kAudioPlugInPropertyBoxList:
        case kAudioBoxPropertyDeviceList:
        case kAudioDevicePropertyStreams: {
            auto values = objects(id, *addr, qs, q);
            if (!values.empty()) std::memcpy(out, values.data(), values.size() * sizeof(AudioObjectID));
            *written = static_cast<UInt32>(values.size() * sizeof(AudioObjectID)); return noErr;
        }
        case kAudioPlugInPropertyBundleID: return copyString(CFSTR("org.madsense.audio.driver"), capacity, written, out);
        case kAudioPlugInPropertyResourceBundle: return copyString(CFSTR(""), capacity, written, out);
        case kAudioPlugInPropertyTranslateUIDToDevice:
        case kAudioPlugInPropertyTranslateUIDToBox: {
            if (qs != sizeof(CFStringRef) || !q) return kAudioHardwareBadPropertySizeError;
            CFStringRef uid = *static_cast<const CFStringRef*>(q);
            bool device = addr->mSelector == kAudioPlugInPropertyTranslateUIDToDevice;
            AudioObjectID found = uid && CFGetTypeID(uid) == CFStringGetTypeID() &&
                CFEqual(uid, device ? CFSTR("org.madsense.audio.dualsense.v1") : CFSTR("org.madsense.audio.box.v1"))
                ? (device ? kDevice : kBox) : kAudioObjectUnknown;
            return copyValue(found, capacity, written, out);
        }
        case kAudioBoxPropertyBoxUID: return copyString(CFSTR("org.madsense.audio.box.v1"), capacity, written, out);
        case kAudioDevicePropertyDeviceUID: return copyString(CFSTR("org.madsense.audio.dualsense.v1"), capacity, written, out);
        case kAudioDevicePropertyModelUID: return copyString(CFSTR("org.madsense.audio.model.v1"), capacity, written, out);
        case kAudioDevicePropertyTransportType: return copyValue<UInt32>(kAudioDeviceTransportTypeVirtual, capacity, written, out);
        case kAudioBoxPropertyHasAudio:
        case kAudioBoxPropertyAcquired:
        case kAudioDevicePropertyDeviceIsAlive:
        case kAudioStreamPropertyIsActive:
        case kAudioStreamPropertyStartingChannel: return copyValue<UInt32>(1, capacity, written, out);
        case kAudioDevicePropertyDeviceIsRunning: return copyValue<UInt32>(running.load() ? 1 : 0, capacity, written, out);
        case kAudioDevicePropertyRelatedDevices: return copyValue<UInt32>(kDevice, capacity, written, out);
        case kAudioDevicePropertyZeroTimeStampPeriod: return copyValue<UInt32>(kTimestampPeriodFrames, capacity, written, out);
        case kAudioStreamPropertyTerminalType: return copyValue<UInt32>(kAudioStreamTerminalTypeSpeaker, capacity, written, out);
        // Never become the system's automatic output or alerts device.
        case kAudioDevicePropertyDeviceCanBeDefaultDevice:
        case kAudioDevicePropertyDeviceCanBeDefaultSystemDevice:
        case kAudioDevicePropertyClockDomain:
        case kAudioBoxPropertyHasVideo:
        case kAudioBoxPropertyHasMIDI:
        case kAudioBoxPropertyIsProtected:
        case kAudioBoxPropertyAcquisitionFailed:
        case kAudioDevicePropertyLatency:
        case kAudioDevicePropertySafetyOffset:
        case kAudioDevicePropertyIsHidden:
        case kAudioStreamPropertyDirection: return copyValue<UInt32>(0, capacity, written, out);
        case kAudioObjectPropertyControlList: return noErr;
        case kAudioDevicePropertyNominalSampleRate: return copyValue<Float64>(kSampleRate, capacity, written, out);
        case kAudioDevicePropertyAvailableNominalSampleRates: return copyValue(AudioValueRange{kSampleRate, kSampleRate}, capacity, written, out);
        case kAudioDevicePropertyPreferredChannelsForStereo: {
            UInt32 stereo[] = {1, 2}; std::memcpy(out, stereo, sizeof(stereo)); *written = sizeof(stereo); return noErr;
        }
        case kAudioStreamPropertyVirtualFormat:
        case kAudioStreamPropertyPhysicalFormat: return copyValue(format(), capacity, written, out);
        case kAudioStreamPropertyAvailableVirtualFormats:
        case kAudioStreamPropertyAvailablePhysicalFormats: return copyValue(AudioStreamRangedDescription{format(), {kSampleRate, kSampleRate}}, capacity, written, out);
        case kAudioObjectPropertyCustomPropertyInfoList:
            std::memcpy(out, customProperties, sizeof(customProperties)); *written = sizeof(customProperties); return noErr;
        case kOwner: {
            std::lock_guard<std::mutex> lock(stateMutex);
            CFBooleanRef value = isOwner(pid) ? kCFBooleanTrue : kCFBooleanFalse;
            CFRetain(value); return copyValue(value, capacity, written, out);
        }
        case kRead: {
            std::lock_guard<std::mutex> lock(stateMutex);
            if (!isOwner(pid)) return kAudioHardwareIllegalOperationError;
            uint64_t now = mach_absolute_time();
            if (now >= leaseDeadline.load(std::memory_order_acquire)) {
                ring.discard();
                pcmGeneration.fetch_add(1, std::memory_order_release);
            }
            leaseDeadline.store(now + leaseTicks, std::memory_order_release);
            AudioPacket packet{};
            packet.version = kProtocolVersion;
            packet.generation = pcmGeneration.load(std::memory_order_acquire);
            packet.frames = ring.popLatest(packet.samples, kReadFrames, packet.generation);
            packet.droppedFrames = ring.dropped();
            packet.activeClients = running.load(std::memory_order_acquire);
            CFDataRef data = CFDataCreate(kCFAllocatorDefault, reinterpret_cast<const UInt8*>(&packet),
                kPacketHeaderSize + packet.frames * kChannels * sizeof(float));
            if (!data) return kAudioHardwareUnspecifiedError;
            return copyValue(data, capacity, written, out);
        }
    }
    return kAudioHardwareUnknownPropertyError;
}

OSStatus setProperty(AudioServerPlugInDriverRef, AudioObjectID id, pid_t pid,
                      const AudioObjectPropertyAddress* addr, UInt32, const void*, UInt32 size, const void* data) {
    if (id != kDevice || !addr || addr->mSelector != kOwner ||
        addr->mScope != kAudioObjectPropertyScopeGlobal || addr->mElement != kAudioObjectPropertyElementMain)
        return kAudioHardwareUnknownPropertyError;
    if (!data || size != sizeof(CFBooleanRef)) {
        os_log_error(identityLog(), "Owner request rejected: caller pid=%{public}d, data size=%{public}u expected=%{public}zu", pid, size, sizeof(CFBooleanRef));
        return kAudioHardwareBadPropertySizeError;
    }
    CFBooleanRef command = *static_cast<const CFBooleanRef*>(data);
    if (!command || CFGetTypeID(command) != CFBooleanGetTypeID()) {
        os_log_error(identityLog(), "Owner request rejected: caller pid=%{public}d, value is not CFBoolean", pid);
        return kAudioHardwareIllegalOperationError;
    }
    os_log(identityLog(), "Owner request: caller pid=%{public}d claim=%{public}d host pid=%{public}d",
           pid, CFBooleanGetValue(command), getpid());
    Identity caller;
    if (!identityFor(pid, caller)) return kAudioHardwareIllegalOperationError;
    std::lock_guard<std::mutex> lock(stateMutex);
    uint64_t now = mach_absolute_time();
    bool mine = sameIdentity(owner, caller);
    if (CFBooleanGetValue(command)) {
        if (!mine && now < leaseDeadline.load()) {
            os_log_error(identityLog(), "Owner claim rejected: live lease belongs to pid=%{public}d; caller pid=%{public}d", owner.pid, pid);
            return kAudioHardwareIllegalOperationError;
        }
        // A crashed backend may reconnect while its user's game is still open.
        // Never transfer a live mixed stream to a different user.
        if (!mine) for (const auto& entry : clients)
            if (entry.second.active && entry.second.identity.uid != caller.uid) {
                os_log_error(identityLog(), "Owner claim rejected: active client uid=%{public}u differs from caller uid=%{public}u", entry.second.identity.uid, caller.uid);
                return kAudioHardwareIllegalOperationError;
            }
        if (!mine || now >= leaseDeadline.load()) {
            ring.discard();
            pcmGeneration.fetch_add(1, std::memory_order_release);
        }
        owner = caller;
        leaseDeadline.store(now + leaseTicks, std::memory_order_release);
        os_log(identityLog(), "Owner claim accepted: pid=%{public}d uid=%{public}u", caller.pid, caller.uid);
    } else {
        if (!mine) return kAudioHardwareIllegalOperationError;
        leaseDeadline.store(0, std::memory_order_release);
        owner = {};
        ring.discard();
    }
    return noErr;
}

OSStatus initialize(AudioServerPlugInDriverRef, AudioServerPlugInHostRef inHost) {
    host = inHost;
    mach_timebase_info_data_t timebase{};
    mach_timebase_info(&timebase);
    ticksPerFrame = (1000000000.0 * timebase.denom / timebase.numer) / kSampleRate;
    leaseTicks = static_cast<uint64_t>(1000000000.0 * timebase.denom / timebase.numer);
    anchor.store(mach_absolute_time());
    os_log(identityLog(), "Audio plugin initialized: host pid=%{public}d euid=%{public}u", getpid(), geteuid());
    return noErr;
}
OSStatus addClient(AudioServerPlugInDriverRef, AudioObjectID id, const AudioServerPlugInClientInfo* info) {
    if (id != kDevice || !info) return kAudioHardwareBadObjectError;
    Identity identity;
    if (!identityFor(info->mProcessID, identity)) return kAudioHardwareIllegalOperationError;
    std::lock_guard<std::mutex> lock(stateMutex);
    if (clients.count(info->mClientID)) return kAudioHardwareIllegalOperationError;
    clients.emplace(info->mClientID, Client{identity, false});
    return noErr;
}
OSStatus removeClient(AudioServerPlugInDriverRef, AudioObjectID id, const AudioServerPlugInClientInfo* info) {
    if (id != kDevice || !info) return kAudioHardwareBadObjectError;
    std::lock_guard<std::mutex> lock(stateMutex);
    auto client = clients.find(info->mClientID);
    if (client != clients.end()) {
        if (client->second.active) running.fetch_sub(1);
        clients.erase(client);
    }
    return noErr;
}
OSStatus startIO(AudioServerPlugInDriverRef, AudioObjectID id, UInt32 clientID) {
    if (id != kDevice) return kAudioHardwareBadObjectError;
    std::lock_guard<std::mutex> lock(stateMutex);
    auto client = clients.find(clientID);
    Identity current;
    if (client == clients.end() || !identityFor(client->second.identity.pid, current) ||
        !sameIdentity(current, client->second.identity) || !owner.pid || current.uid != owner.uid ||
        mach_absolute_time() >= leaseDeadline.load()) return kAudioHardwareIllegalOperationError;
    if (!client->second.active) {
        if (running.load() == 0) {
            // Start a fresh PCM generation even if StopIO/StartIO happened
            // entirely between two consumer polls. No old tail may cross it.
            ring.discard();
            anchor.store(mach_absolute_time());
            timestampSeed.fetch_add(1, std::memory_order_release);
            pcmGeneration.fetch_add(1, std::memory_order_release);
        }
        running.fetch_add(1);
        client->second.active = true;
    }
    return noErr;
}
OSStatus stopIO(AudioServerPlugInDriverRef, AudioObjectID id, UInt32 clientID) {
    if (id != kDevice) return kAudioHardwareBadObjectError;
    std::lock_guard<std::mutex> lock(stateMutex);
    auto client = clients.find(clientID);
    if (client != clients.end() && client->second.active) {
        client->second.active = false; running.fetch_sub(1);
    }
    return noErr;
}
OSStatus zeroTime(AudioServerPlugInDriverRef, AudioObjectID id, UInt32, Float64* sample, UInt64* ticks, UInt64* seed) {
    if (id != kDevice || !sample || !ticks || !seed) return kAudioHardwareBadObjectError;
    uint64_t base = anchor.load();
    double periods = std::floor((mach_absolute_time() - base) / (ticksPerFrame * kTimestampPeriodFrames));
    *sample = periods * kTimestampPeriodFrames;
    *ticks = base + static_cast<uint64_t>(*sample * ticksPerFrame);
    *seed = timestampSeed.load(); return noErr;
}
OSStatus willDo(AudioServerPlugInDriverRef, AudioObjectID id, UInt32, UInt32 op, Boolean* will, Boolean* inPlace) {
    if (id != kDevice || !will || !inPlace) return kAudioHardwareBadObjectError;
    *will = op == kAudioServerPlugInIOOperationWriteMix;
    *inPlace = true; return noErr;
}
OSStatus beginEnd(AudioServerPlugInDriverRef, AudioObjectID id, UInt32, UInt32, UInt32, const AudioServerPlugInIOCycleInfo*) {
    return id == kDevice ? noErr : kAudioHardwareBadObjectError;
}
OSStatus doIO(AudioServerPlugInDriverRef, AudioObjectID id, AudioObjectID stream, UInt32, UInt32 op,
               UInt32 frames, const AudioServerPlugInIOCycleInfo*, void* main, void*) {
    // Capture before checking the lease/running state. A concurrent transition
    // may admit this callback under a renewed lease, but its old PCM tag remains
    // rejectable even if publication finishes after the transition's discard.
    const uint64_t generation = pcmGeneration.load(std::memory_order_acquire);
    if (id != kDevice || stream != kStream) return kAudioHardwareBadObjectError;
    if (op != kAudioServerPlugInIOOperationWriteMix) return noErr;
    if (!main && frames) return kAudioHardwareIllegalOperationError;
    if (running.load(std::memory_order_acquire) && mach_absolute_time() < leaseDeadline.load(std::memory_order_acquire))
        ring.push(static_cast<const float*>(main), frames, generation);
    return noErr;
}
HRESULT query(void*, REFIID uuid, LPVOID* out) {
    if (!out) return E_POINTER;
    *out = nullptr;
    CFUUIDRef requested = CFUUIDCreateFromUUIDBytes(kCFAllocatorDefault, uuid);
    bool supported = requested && (CFEqual(requested, IUnknownUUID) || CFEqual(requested, kAudioServerPlugInDriverInterfaceUUID));
    if (requested) CFRelease(requested);
    if (!supported) return E_NOINTERFACE;
    references.fetch_add(1); *out = &interfacePointer; return S_OK;
}
ULONG addRef(void*) { return references.fetch_add(1) + 1; }
ULONG release(void*) { auto value = references.load(); while (value && !references.compare_exchange_weak(value, value - 1)) {} return value ? value - 1 : 0; }
void configureInterface() {
    interface.QueryInterface = query;
    interface.AddRef = addRef;
    interface.Release = release;
    interface.Initialize = initialize;
    interface.CreateDevice = [](AudioServerPlugInDriverRef, CFDictionaryRef, const AudioServerPlugInClientInfo*, AudioObjectID*) -> OSStatus { return kAudioHardwareUnsupportedOperationError; };
    interface.DestroyDevice = [](AudioServerPlugInDriverRef, AudioObjectID) -> OSStatus { return kAudioHardwareUnsupportedOperationError; };
    interface.AddDeviceClient = addClient;
    interface.RemoveDeviceClient = removeClient;
    interface.PerformDeviceConfigurationChange = [](AudioServerPlugInDriverRef, AudioObjectID, UInt64, void*) -> OSStatus { return kAudioHardwareUnsupportedOperationError; };
    interface.AbortDeviceConfigurationChange = [](AudioServerPlugInDriverRef, AudioObjectID, UInt64, void*) -> OSStatus { return noErr; };
    interface.HasProperty = [](AudioServerPlugInDriverRef, AudioObjectID id, pid_t, const AudioObjectPropertyAddress* addr) -> Boolean { UInt32 size; return propertySize(id, addr, 0, nullptr, &size) == noErr; };
    interface.IsPropertySettable = [](AudioServerPlugInDriverRef, AudioObjectID id, pid_t, const AudioObjectPropertyAddress* addr, Boolean* settable) -> OSStatus {
        if (!settable) return kAudioHardwareIllegalOperationError;
        UInt32 size; auto result = propertySize(id, addr, 0, nullptr, &size); if (result) return result;
        *settable = id == kDevice && addr->mSelector == kOwner; return noErr;
    };
    interface.GetPropertyDataSize = [](AudioServerPlugInDriverRef, AudioObjectID id, pid_t, const AudioObjectPropertyAddress* addr, UInt32 qs, const void* q, UInt32* size) -> OSStatus { return propertySize(id, addr, qs, q, size); };
    interface.GetPropertyData = getProperty;
    interface.SetPropertyData = setProperty;
    interface.StartIO = startIO;
    interface.StopIO = stopIO;
    interface.GetZeroTimeStamp = zeroTime;
    interface.WillDoIOOperation = willDo;
    interface.BeginIOOperation = beginEnd;
    interface.DoIOOperation = doIO;
    interface.EndIOOperation = beginEnd;
}
}

extern "C" __attribute__((visibility("default"))) void* MadSenseAudio_Create(CFAllocatorRef, CFUUIDRef type) {
    if (!type || !CFEqual(type, kAudioServerPlugInTypeUUID)) return nullptr;
    static std::once_flag once;
    std::call_once(once, configureInterface);
    return &interfacePointer;
}
