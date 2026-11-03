/* Native Linux input force-feedback consumer, restricted to an owned Xbox VHCI
 * node. A successful write is not delivery proof: correlate the marker bytes
 * with the virtual exporter's xbox-output log in the orchestrator.
 */
#define _XOPEN_SOURCE 700
#include <fcntl.h>
#include <linux/input.h>
#include <stdint.h>
#include <sys/ioctl.h>
#include <time.h>
#include "consumer_guard.h"

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
    struct input_id id = {0};
    struct ff_effect effect = { .type = FF_RUMBLE, .id = -1,
        .u.rumble = { .strong_magnitude = 0x1234, .weak_magnitude = 0x5678 },
        .replay = { .length = 500, .delay = 0 } };
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
        if (!(started = send_effect(fd, effect.id, 1))) error = "start rumble";
        else {
            struct timespec delay = { .tv_sec = 0, .tv_nsec = 200000000 };
            while (nanosleep(&delay, &delay) && errno == EINTR) { }
        }
    }
    if (error) saved_errno = errno;
    if (uploaded) {
        stopped = send_effect(fd, effect.id, 0);
        erased = ioctl(fd, EVIOCRMFF, effect.id) == 0;
        if ((!stopped || !erased) && !error) { error = "stop or erase rumble"; saved_errno = errno; }
    }
    if (fd >= 0) close(fd);
    fputs("{\"schema\":1,\"result\":", stdout); json_string(error ? "failed" : "passed");
    fputs(",\"error\":", stdout); json_string(error);
    printf(",\"errno\":%d,\"bus\":%u,\"vendor\":%u,\"product\":%u,\"identityVerified\":%s,\"ffRumble\":%s,\"effectSlots\":%d",
           saved_errno, id.bustype, id.vendor, id.product, identity ? "true" : "false", rumble ? "true" : "false", effects);
    printf(",\"uploaded\":%s,\"started\":%s,\"stopped\":%s,\"erased\":%s,\"marker\":{\"strong\":4660,\"weak\":22136,\"expectedXboxLeftMotor\":18,\"expectedXboxRightMotor\":86},\"outputDeliveryVerified\":false,\"physicalLatencyValidated\":false}\n",
           uploaded ? "true" : "false", started ? "true" : "false", stopped ? "true" : "false", erased ? "true" : "false");
    return error ? 1 : 0;
}
