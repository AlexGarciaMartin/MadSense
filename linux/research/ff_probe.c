/* Native Linux input force-feedback consumer, restricted to an owned Xbox VHCI
 * node. A successful write is not delivery proof: correlate the marker bytes
 * with the virtual exporter's xbox-output log in the orchestrator.
 */
#define _XOPEN_SOURCE 700
#include <fcntl.h>
#include <inttypes.h>
#include <linux/input.h>
#include <stdint.h>
#include <sys/ioctl.h>
#include <time.h>
#include "consumer_guard.h"

static struct { const char *name; uint64_t ns; } phases[12];
static unsigned phase_count;
static uint64_t phase(const char *name)
{
    struct timespec now;
    uint64_t ns = clock_gettime(CLOCK_MONOTONIC, &now) == 0
        ? (uint64_t)now.tv_sec * UINT64_C(1000000000) + (uint64_t)now.tv_nsec : 0;
    if (phase_count < sizeof(phases) / sizeof(phases[0])) {
        phases[phase_count].name = name; phases[phase_count++].ns = ns;
    }
    fprintf(stderr, "{\"kind\":\"ff-phase\",\"name\":\"%s\",\"monotonicNs\":%" PRIu64 "}\n", name, ns);
    fflush(stderr);
    return ns;
}

static bool delay_ms(long milliseconds)
{
    struct timespec delay = { .tv_sec = milliseconds / 1000, .tv_nsec = (milliseconds % 1000) * 1000000 };
    while (nanosleep(&delay, &delay)) if (errno != EINTR) return false;
    return true;
}

static bool send_effect(int fd, int id, int value)
{
    struct input_event event = { .type = EV_FF, .code = (uint16_t)id, .value = value };
    return write(fd, &event, sizeof(event)) == (ssize_t)sizeof(event);
}

int main(int argc, char **argv)
{
    if (argc != 4) { fputs("usage: ff_probe TOKEN USB_PARENT EVENT\n", stderr); return 2; }
    if (!consumer_scope(argv[1], argv[2], argv[3], "-")) {
        fputs("Owned QEMU USB ancestry and ordinary account required\n", stderr); return 2;
    }
    const char *error = NULL;
    int saved_errno = 0, effects = -1;
    bool identity = false, rumble = false, uploaded = false, started = false, stopped = false, erased = false;
    uint64_t start_before = 0, stop_before = 0;
    struct input_id id = {0};
    struct ff_effect effect = { .type = FF_RUMBLE, .id = -1,
        .u.rumble = { .strong_magnitude = 0x1234, .weak_magnitude = 0x5678 },
        .replay = { .length = 60000, .delay = 0 } };
    unsigned char supported[(FF_MAX / 8) + 1] = {0};
    int fd = open(argv[3], O_RDWR | O_NONBLOCK | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) error = "open evdev";
    else if (ioctl(fd, EVIOCGID, &id) < 0) error = "query identity";
    else if (!(identity = id.bustype == BUS_USB && id.vendor == 0x045e && id.product == 0x028e))
        error = "Expected virtual USB Xbox 360 identity";
    else if (ioctl(fd, EVIOCGBIT(EV_FF, sizeof(supported)), supported) < 0
             || ioctl(fd, EVIOCGEFFECTS, &effects) < 0) error = "query FF capabilities";
    else if (!(rumble = (supported[FF_RUMBLE / 8] & (1u << (FF_RUMBLE % 8))) != 0))
        error = "FF_RUMBLE unavailable";
    else if (ioctl(fd, EVIOCSFF, &effect) < 0) error = "upload rumble";
    else {
        uploaded = true;
        start_before = phase("start-call-before");
        if (!(started = send_effect(fd, effect.id, 1))) error = "start rumble";
        phase("start-call-after");
        if (started && !delay_ms(200)) error = "observe rumble";
    }
    if (error) saved_errno = errno;
    if (uploaded) {
        stop_before = phase("stop-call-before");
        stopped = send_effect(fd, effect.id, 0);
        phase("stop-call-after");
        if (!delay_ms(300) && !error) { error = "observe explicit stop"; saved_errno = errno; }
        phase("stop-observation-end");
        phase("erase-before");
        erased = ioctl(fd, EVIOCRMFF, effect.id) == 0;
        phase("erase-after");
        if ((!stopped || !erased) && !error) { error = "stop or erase rumble"; saved_errno = errno; }
    }
    if (fd >= 0) { phase("close-before"); close(fd); phase("close-after"); }
    bool timing_valid = phase_count > 0;
    for (unsigned i = 0; i < phase_count; ++i)
        if (!phases[i].ns || (i && phases[i].ns < phases[i-1].ns)) timing_valid = false;
    bool before_expiry = start_before && stop_before >= start_before &&
        stop_before - start_before < (uint64_t)effect.replay.length * UINT64_C(1000000);
    fputs("{\"schema\":1,\"result\":", stdout); json_string(error ? "failed" : "passed");
    fputs(",\"error\":", stdout); json_string(error);
    printf(",\"errno\":%d,\"bus\":%u,\"vendor\":%u,\"product\":%u,\"identityVerified\":%s,\"ffRumble\":%s,\"effectSlots\":%d",
           saved_errno, id.bustype, id.vendor, id.product, identity ? "true" : "false", rumble ? "true" : "false", effects);
    printf(",\"uploaded\":%s,\"started\":%s,\"stopped\":%s,\"erased\":%s,\"marker\":{\"strong\":4660,\"weak\":22136,\"expectedXboxLeftMotor\":18,\"expectedXboxRightMotor\":86},\"outputDeliveryVerified\":false,\"physicalLatencyValidated\":false",
           uploaded ? "true" : "false", started ? "true" : "false", stopped ? "true" : "false", erased ? "true" : "false");
    printf(",\"phaseClock\":\"CLOCK_MONOTONIC\",\"phaseTimingValid\":%s,\"effectDurationMs\":%u,\"explicitStopBeforeAutoExpiry\":%s,\"phases\":[",
           timing_valid ? "true" : "false", (unsigned)effect.replay.length, before_expiry ? "true" : "false");
    for (unsigned i = 0; i < phase_count; ++i) {
        if (i) putchar(',');
        printf("{\"name\":\"%s\",\"monotonicNs\":%" PRIu64 "}", phases[i].name, phases[i].ns);
    }
    fputs("]}\n", stdout);
    return error ? 1 : 0;
}
