/* Independent Linux host-side probe for the CI-owned virtual USB device.
 * Build: cc -std=c11 -O2 -Wall -Wextra -Werror host_probe.c -o host_probe
 * The caller must prove the device node belongs to its newly attached VHCI port.
 * This program never discovers or selects a device, changes drivers, or uses a radio.
 */
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <fcntl.h>
#include <linux/hidraw.h>
#include <linux/input.h>
#include <poll.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define ARRAY_COUNT(a) (sizeof(a) / sizeof((a)[0]))
#define BIT_BYTES(last) (((last) / 8u) + 1u)

struct feature_result {
    unsigned id;
    size_t requested;
    int length;
    unsigned char bytes[64];
};

struct axis_result {
    unsigned code;
    const char *name;
    bool supported;
    bool queried;
    int expected;
    struct input_absinfo value;
};

struct result {
    const char *mode;
    bool passed;
    const char *error;
    int error_number;
    unsigned bus, vendor, product;
    bool identity_checked;
    int descriptor_length;
    bool descriptor_equal;
    unsigned char descriptor[HID_MAX_DESCRIPTOR_SIZE];
    struct feature_result features[3];
    int input_length;
    unsigned char input[65];
    bool neutral;
    int output_length;
    unsigned char output[48];
    bool key_capability, abs_capability, buttons_supported;
    unsigned pressed_buttons;
    struct axis_result axes[8];
};

static int failure(struct result *r, const char *message, int number)
{
    r->error = message;
    r->error_number = number;
    return -1;
}

static bool bit_is_set(const unsigned char *bits, size_t size, unsigned bit)
{
    return bit / 8u < size && (bits[bit / 8u] & (1u << (bit % 8u))) != 0;
}

static int open_node(struct result *r, const char *path, bool writable)
{
    int fd = open(path, (writable ? O_RDWR : O_RDONLY) | O_NONBLOCK | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) return failure(r, "open device node", errno);
    struct stat st;
    if (fstat(fd, &st) != 0) {
        int saved = errno;
        close(fd);
        return failure(r, "stat device node", saved);
    }
    if (!S_ISCHR(st.st_mode)) {
        close(fd);
        return failure(r, "device path is not a character device", 0);
    }
    return fd;
}

static int64_t monotonic_ms(void)
{
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) return -1;
    return (int64_t)now.tv_sec * 1000 + now.tv_nsec / 1000000;
}

static int read_neutral_input(struct result *r, int fd)
{
    int64_t start = monotonic_ms();
    if (start < 0) return failure(r, "read monotonic clock", errno);
    for (;;) {
        int64_t now = monotonic_ms();
        if (now < 0) return failure(r, "read monotonic clock", errno);
        int64_t remaining = 5000 - (now - start);
        if (remaining <= 0) return failure(r, "neutral input timed out after 5 seconds", ETIMEDOUT);
        struct pollfd wait = { .fd = fd, .events = POLLIN, .revents = 0 };
        int status = poll(&wait, 1, (int)remaining);
        if (status < 0 && errno == EINTR) continue;
        if (status < 0) return failure(r, "poll HID input", errno);
        if (status == 0) continue;
        if (wait.revents & (POLLERR | POLLHUP | POLLNVAL))
            return failure(r, "HID disconnected while awaiting input", 0);
        if (!(wait.revents & POLLIN)) continue;
        ssize_t count = read(fd, r->input, sizeof(r->input));
        if (count < 0 && (errno == EINTR || errno == EAGAIN)) continue;
        if (count < 0) return failure(r, "read HID input", errno);
        r->input_length = (int)count;
        if (count != 64) return failure(r, "USB DualSense input must contain exactly 64 bytes", 0);
        r->neutral = r->input[0] == 1 && r->input[1] == 128 && r->input[2] == 128
            && r->input[3] == 128 && r->input[4] == 128 && r->input[5] == 0
            && r->input[6] == 0 && r->input[8] == 8 && r->input[9] == 0
            && r->input[10] == 0;
        if (!r->neutral) return failure(r, "USB DualSense controls are not neutral", 0);
        return 0;
    }
}

static int hid_probe(struct result *r, int fd, const char *expected_path)
{
    struct hidraw_devinfo identity = {0};
    if (ioctl(fd, HIDIOCGRAWINFO, &identity) < 0) return failure(r, "HIDIOCGRAWINFO", errno);
    r->bus = identity.bustype;
    r->vendor = (uint16_t)identity.vendor;
    r->product = (uint16_t)identity.product;
    r->identity_checked = true;
    if (r->bus != BUS_USB || r->vendor != 0x054c || (r->product != 0x0ce6 && r->product != 0x0df2))
        return failure(r, "expected USB Sony DualSense or DualSense Edge", 0);

    int length = 0;
    if (ioctl(fd, HIDIOCGRDESCSIZE, &length) < 0) return failure(r, "HIDIOCGRDESCSIZE", errno);
    if (length <= 0 || length > HID_MAX_DESCRIPTOR_SIZE)
        return failure(r, "HID descriptor length is outside the supported range", 0);
    struct hidraw_report_descriptor descriptor = { .size = (unsigned)length };
    if (ioctl(fd, HIDIOCGRDESC, &descriptor) < 0) return failure(r, "HIDIOCGRDESC", errno);
    if (descriptor.size != (unsigned)length)
        return failure(r, "HID descriptor changed between size and content reads", 0);
    r->descriptor_length = length;
    memcpy(r->descriptor, descriptor.value, (size_t)length);

    unsigned char expected[HID_MAX_DESCRIPTOR_SIZE + 1];
    FILE *file = fopen(expected_path, "rb");
    if (!file) return failure(r, "open expected HID descriptor", errno);
    size_t expected_length = fread(expected, 1, sizeof(expected), file);
    bool read_failed = ferror(file) != 0;
    int saved = errno;
    int close_status = fclose(file);
    if (read_failed) return failure(r, "read expected HID descriptor", saved);
    if (close_status != 0) return failure(r, "close expected HID descriptor", errno);
    r->descriptor_equal = expected_length == (size_t)length
        && memcmp(expected, descriptor.value, (size_t)length) == 0;
    if (!r->descriptor_equal) return failure(r, "kernel HID descriptor differs from the expected descriptor", 0);

    for (size_t i = 0; i < ARRAY_COUNT(r->features); ++i) {
        struct feature_result *feature = &r->features[i];
        feature->bytes[0] = (unsigned char)feature->id;
        int count = ioctl(fd, HIDIOCGFEATURE(feature->requested), feature->bytes);
        feature->length = count;
        if (count < 0) return failure(r, "HIDIOCGFEATURE", errno);
        if (count != (int)feature->requested || feature->bytes[0] != feature->id)
            return failure(r, "feature report has the wrong length or report ID", 0);
    }

    if (read_neutral_input(r, fd) != 0) return -1;
    r->output[0] = 0x02;
    r->output[2] = 0x04; /* valid_flag1: lightbar color is present */
    r->output[45] = 0x12;
    r->output[46] = 0x34;
    r->output[47] = 0x56;
    ssize_t written;
    do { written = write(fd, r->output, sizeof(r->output)); } while (written < 0 && errno == EINTR);
    r->output_length = (int)written;
    if (written < 0) return failure(r, "write virtual HID output marker", errno);
    if (written != (ssize_t)sizeof(r->output)) return failure(r, "short virtual HID output write", 0);
    return 0;
}

static int evdev_probe(struct result *r, int fd)
{
    struct input_id identity = {0};
    if (ioctl(fd, EVIOCGID, &identity) < 0) return failure(r, "EVIOCGID", errno);
    r->bus = identity.bustype;
    r->vendor = identity.vendor;
    r->product = identity.product;
    r->identity_checked = true;
    bool sony = r->vendor == 0x054c && (r->product == 0x0ce6 || r->product == 0x0df2);
    bool xbox = r->vendor == 0x045e && r->product == 0x028e;
    if (r->bus != BUS_USB || (!sony && !xbox))
        return failure(r, "expected USB DualSense, DualSense Edge, or Xbox 360 primary gamepad", 0);

    unsigned char event_bits[BIT_BYTES(EV_MAX)] = {0};
    unsigned char key_bits[BIT_BYTES(KEY_MAX)] = {0};
    unsigned char axis_bits[BIT_BYTES(ABS_MAX)] = {0};
    unsigned char pressed[BIT_BYTES(KEY_MAX)] = {0};
    if (ioctl(fd, EVIOCGBIT(0, sizeof(event_bits)), event_bits) < 0)
        return failure(r, "EVIOCGBIT event types", errno);
    r->key_capability = bit_is_set(event_bits, sizeof(event_bits), EV_KEY);
    r->abs_capability = bit_is_set(event_bits, sizeof(event_bits), EV_ABS);
    if (!r->key_capability || !r->abs_capability)
        return failure(r, "evdev node lacks primary gamepad event types", 0);
    if (ioctl(fd, EVIOCGBIT(EV_KEY, sizeof(key_bits)), key_bits) < 0)
        return failure(r, "EVIOCGBIT keys", errno);
    if (ioctl(fd, EVIOCGBIT(EV_ABS, sizeof(axis_bits)), axis_bits) < 0)
        return failure(r, "EVIOCGBIT axes", errno);
    if (ioctl(fd, EVIOCGKEY(sizeof(pressed)), pressed) < 0)
        return failure(r, "EVIOCGKEY", errno);
    const unsigned required_keys[] = {
        BTN_SOUTH, BTN_EAST, BTN_NORTH, BTN_WEST, BTN_TL, BTN_TR,
        BTN_SELECT, BTN_START, BTN_THUMBL, BTN_THUMBR
    };
    r->buttons_supported = true;
    for (size_t i = 0; i < ARRAY_COUNT(required_keys); ++i)
        if (!bit_is_set(key_bits, sizeof(key_bits), required_keys[i])) r->buttons_supported = false;
    for (unsigned key = 0; key <= KEY_MAX; ++key)
        if (bit_is_set(pressed, sizeof(pressed), key)) ++r->pressed_buttons;
    if (!r->buttons_supported) return failure(r, "evdev node lacks required primary gamepad buttons", 0);
    if (r->pressed_buttons != 0) return failure(r, "virtual gamepad has a pressed button", 0);

    bool all_neutral = true;
    for (size_t i = 0; i < ARRAY_COUNT(r->axes); ++i) {
        struct axis_result *axis = &r->axes[i];
        axis->supported = bit_is_set(axis_bits, sizeof(axis_bits), axis->code);
        if (!axis->supported) return failure(r, "evdev node lacks a required gamepad axis", 0);
        if (ioctl(fd, EVIOCGABS(axis->code), &axis->value) < 0)
            return failure(r, "EVIOCGABS", errno);
        axis->queried = true;
        if (axis->code == ABS_Z || axis->code == ABS_RZ) {
            axis->expected = axis->value.minimum;
        } else if (axis->code == ABS_HAT0X || axis->code == ABS_HAT0Y) {
            axis->expected = 0;
        } else {
            /* ceil((min + max) / 2), without signed overflow: Sony 128,
             * Xbox 0. Linux xpad applies input-core fuzz to its inverted Y.
             */
            int64_t minimum = axis->value.minimum;
            int64_t maximum = axis->value.maximum;
            axis->expected = (int)(minimum + (maximum - minimum + 1) / 2);
        }
        if (axis->value.minimum >= axis->value.maximum
            || axis->expected < axis->value.minimum || axis->expected > axis->value.maximum
            || axis->value.value != axis->expected)
            all_neutral = false;
    }
    r->neutral = all_neutral;
    if (!r->neutral) return failure(r, "virtual gamepad axes are not neutral or have invalid ranges", 0);
    return 0;
}

static void json_string(FILE *file, const char *value)
{
    fputc('"', file);
    for (const unsigned char *p = (const unsigned char *)value; *p; ++p) {
        if (*p == '"' || *p == '\\') { fputc('\\', file); fputc(*p, file); }
        else if (*p < 32) fprintf(file, "\\u%04x", *p);
        else fputc(*p, file);
    }
    fputc('"', file);
}

static void hex_string(FILE *file, const unsigned char *data, int count, size_t capacity)
{
    fputc('"', file);
    if (count > 0 && (size_t)count <= capacity)
        for (int i = 0; i < count; ++i) fprintf(file, "%02x", data[i]);
    fputc('"', file);
}

static const char *boolean(bool value) { return value ? "true" : "false"; }

static int write_result(const char *path, const struct result *r)
{
    FILE *file = fopen(path, "w");
    if (!file) { perror("open result JSON"); return -1; }
    fprintf(file, "{\n  \"schema_version\": 1,\n  \"mode\": ");
    json_string(file, r->mode);
    fprintf(file, ",\n  \"passed\": %s,\n  \"error\": ", boolean(r->passed));
    if (r->error) json_string(file, r->error); else fputs("null", file);
    fprintf(file, ",\n  \"errno\": %d,\n  \"identity\": {\"checked\": %s, \"bus\": %u, \"vendor\": %u, \"product\": %u}",
        r->error_number, boolean(r->identity_checked), r->bus, r->vendor, r->product);
    if (strcmp(r->mode, "hid") == 0) {
        fprintf(file, ",\n  \"descriptor\": {\"length\": %d, \"equal\": %s, \"hex\": ",
            r->descriptor_length, boolean(r->descriptor_equal));
        hex_string(file, r->descriptor, r->descriptor_length, sizeof(r->descriptor));
        fputs("},\n  \"features\": {", file);
        for (size_t i = 0; i < ARRAY_COUNT(r->features); ++i) {
            const struct feature_result *feature = &r->features[i];
            fprintf(file, "%s\"%02x\": {\"requested\": %zu, \"length\": %d, \"hex\": ",
                i ? ", " : "", feature->id, feature->requested, feature->length);
            hex_string(file, feature->bytes, feature->length, sizeof(feature->bytes));
            fputc('}', file);
        }
        fprintf(file, "},\n  \"input\": {\"length\": %d, \"neutral\": %s, \"hex\": ",
            r->input_length, boolean(r->neutral));
        hex_string(file, r->input, r->input_length, sizeof(r->input));
        fprintf(file, "},\n  \"output\": {\"length\": %d, \"hex\": ", r->output_length);
        hex_string(file, r->output, r->output_length, sizeof(r->output));
        fputc('}', file);
    } else {
        fprintf(file, ",\n  \"capabilities\": {\"EV_KEY\": %s, \"EV_ABS\": %s, \"buttons_supported\": %s},\n  \"pressed_buttons\": %u,\n  \"neutral\": %s,\n  \"axes\": [",
            boolean(r->key_capability), boolean(r->abs_capability), boolean(r->buttons_supported),
            r->pressed_buttons, boolean(r->neutral));
        for (size_t i = 0; i < ARRAY_COUNT(r->axes); ++i) {
            const struct axis_result *axis = &r->axes[i];
            fprintf(file, "%s{\"code\": %u, \"name\": ", i ? ", " : "", axis->code);
            json_string(file, axis->name);
            fprintf(file, ", \"supported\": %s, \"queried\": %s, \"min\": %d, \"max\": %d, \"value\": %d, \"expected\": %d, \"fuzz\": %d, \"flat\": %d}",
                boolean(axis->supported), boolean(axis->queried), axis->value.minimum, axis->value.maximum,
                axis->value.value, axis->expected, axis->value.fuzz, axis->value.flat);
        }
        fputc(']', file);
    }
    fputs("\n}\n", file);
    bool failed = ferror(file) != 0;
    if (fclose(file) != 0) failed = true;
    if (failed) { fputs("failed to write result JSON\n", stderr); return -1; }
    return 0;
}

int main(int argc, char **argv)
{
    bool hid = argc == 5 && strcmp(argv[1], "hid") == 0;
    bool evdev = argc == 4 && strcmp(argv[1], "evdev") == 0;
    if (!hid && !evdev) {
        fputs("usage: host_probe hid HIDRAW EXPECTED_DESCRIPTOR OUTPUT_JSON\n"
              "       host_probe evdev EVENT OUTPUT_JSON\n", stderr);
        return 2;
    }
    struct result result = {
        .mode = hid ? "hid" : "evdev",
        .features = {{.id = 0x09, .requested = 20}, {.id = 0x20, .requested = 64}, {.id = 0x05, .requested = 41}},
        .axes = {
            {.code = ABS_X, .name = "ABS_X"}, {.code = ABS_Y, .name = "ABS_Y"},
            {.code = ABS_RX, .name = "ABS_RX"}, {.code = ABS_RY, .name = "ABS_RY"},
            {.code = ABS_Z, .name = "ABS_Z"}, {.code = ABS_RZ, .name = "ABS_RZ"},
            {.code = ABS_HAT0X, .name = "ABS_HAT0X"}, {.code = ABS_HAT0Y, .name = "ABS_HAT0Y"}
        }
    };
    int fd = open_node(&result, argv[2], hid);
    if (fd >= 0) {
        int status = hid ? hid_probe(&result, fd, argv[3]) : evdev_probe(&result, fd);
        int close_status = close(fd);
        if (status == 0 && close_status != 0) status = failure(&result, "close device node", errno);
        result.passed = status == 0;
    }
    if (write_result(argv[hid ? 4 : 3], &result) != 0) return 1;
    if (!result.passed) {
        fprintf(stderr, "%s: %s (errno %d)\n", result.mode, result.error ? result.error : "failed", result.error_number);
        return 1;
    }
    return 0;
}
