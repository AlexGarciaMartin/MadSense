/* Build with either SDL2 >= 2.24 or SDL3 >= 3.2; see CONSUMERS.md.
 * stdout is one evidence JSON object. Successful API calls still require the
 * orchestrator to correlate output markers with exporter/physical-fixture logs.
 */
#define _XOPEN_SOURCE 700
#include <inttypes.h>
#include <stdint.h>
#include <time.h>
#include "consumer_guard.h"
#if CONSUMER_SDL_MAJOR == 3
#include <SDL3/SDL.h>
typedef SDL_Gamepad Pad;
#define PAD_OPEN SDL_OpenGamepad
#define PAD_CLOSE SDL_CloseGamepad
#define PAD_NAME SDL_GetGamepadName
#define PAD_SERIAL SDL_GetGamepadSerial
#define PAD_VENDOR SDL_GetGamepadVendor
#define PAD_PRODUCT SDL_GetGamepadProduct
#define PAD_MAPPING SDL_GetGamepadMapping
#define PAD_HAS_SENSOR SDL_GamepadHasSensor
#define PAD_AXIS(p, a) SDL_GetGamepadAxis(p, (SDL_GamepadAxis)(a))
#define PAD_BUTTON(p, b) SDL_GetGamepadButton(p, (SDL_GamepadButton)(b))
#define PAD_BUTTON_COUNT SDL_GAMEPAD_BUTTON_COUNT
#define PAD_RUMBLE(p, l, r, ms) SDL_RumbleGamepad(p, l, r, ms)
#define PAD_LED(p, r, g, b) SDL_SetGamepadLED(p, r, g, b)
#else
#include <SDL2/SDL.h>
typedef SDL_GameController Pad;
#define PAD_OPEN SDL_GameControllerOpen
#define PAD_CLOSE SDL_GameControllerClose
#define PAD_NAME SDL_GameControllerName
#define PAD_SERIAL SDL_GameControllerGetSerial
#define PAD_VENDOR SDL_GameControllerGetVendor
#define PAD_PRODUCT SDL_GameControllerGetProduct
#define PAD_MAPPING SDL_GameControllerMapping
#define PAD_HAS_SENSOR SDL_GameControllerHasSensor
#define PAD_AXIS(p, a) SDL_GameControllerGetAxis(p, (SDL_GameControllerAxis)(a))
#define PAD_BUTTON(p, b) SDL_GameControllerGetButton(p, (SDL_GameControllerButton)(b))
#define PAD_BUTTON_COUNT SDL_CONTROLLER_BUTTON_MAX
#define PAD_RUMBLE(p, l, r, ms) (SDL_GameControllerRumble(p, l, r, ms) == 0)
#define PAD_LED(p, r, g, b) (SDL_GameControllerSetLED(p, r, g, b) == 0)
#endif

/* CLOCK_MONOTONIC shares the Linux guest clock used by the fixture. These
 * boundaries describe API phases, not a hardware-latency measurement. Keep
 * an immediate stderr copy even if a later API call prevents final JSON. */
struct Phase { const char *name; uint64_t nanoseconds; };
static struct Phase phases[16];
static unsigned phase_count;

static uint64_t phase(const char *name)
{
    struct timespec now;
    uint64_t ns = clock_gettime(CLOCK_MONOTONIC, &now) == 0
        ? (uint64_t)now.tv_sec * UINT64_C(1000000000) + (uint64_t)now.tv_nsec : 0;
    if (phase_count < sizeof(phases) / sizeof(phases[0]))
        phases[phase_count++] = (struct Phase){name, ns};
    fprintf(stderr, "{\"sdlPhase\":\"%s\",\"monotonicNs\":%" PRIu64 "}\n", name, ns);
    fflush(stderr);
    return ns;
}

static void pump(unsigned milliseconds)
{
    for (unsigned elapsed = 0; elapsed < milliseconds; elapsed += 10) {
        SDL_PumpEvents();
        SDL_Delay(10);
    }
}

int main(int argc, char **argv)
{
    if (argc != 8 || (strcmp(argv[7], "neutral") && strcmp(argv[7], "south"))) {
        fprintf(stderr, "usage: consumer_sdl TOKEN USB_PARENT EVENT HIDRAW_OR_DASH VID_HEX PID_HEX neutral|south\n");
        return 2;
    }
    if (!consumer_scope(argv[1], argv[2], argv[3], argv[4])) {
        fputs("Owned QEMU USB ancestry and ordinary account required\n", stderr);
        return 2;
    }
    unsigned vendor = (unsigned)strtoul(argv[5], NULL, 16);
    unsigned product = (unsigned)strtoul(argv[6], NULL, 16);
    if (!((vendor == 0x054c && (product == 0x0ce6 || product == 0x0df2))
          || (vendor == 0x045e && product == 0x028e))) return 2;
    const char *error = NULL;
    Pad *pad = NULL;
    char *mapping = NULL;
    const char *path = NULL;
    int count = 0, matches = 0, axes[6] = {0}, pressed = 0;
    bool initialized = false, identity = false, neutral = false, input_matches = false, south = false;
    bool expect_south = !strcmp(argv[7], "south");
    bool rumble = false, led = false, trigger_rumble = false, gyro = false, accel = false;
    bool rumble_sent = false, rumble_stopped = false, led_sent = false;
    const uint32_t rumble_duration_ms = 60000; /* Longer than the harness's 45s command timeout. */
    uint64_t rumble_before = 0, stop_before = 0;
    char version[40] = {0};
    SDL_SetHint(SDL_HINT_JOYSTICK_ALLOW_BACKGROUND_EVENTS, "1");
#if CONSUMER_SDL_MAJOR == 3
    initialized = SDL_Init(SDL_INIT_GAMEPAD | SDL_INIT_SENSOR);
    int v = SDL_GetVersion();
    snprintf(version, sizeof(version), "%d.%d.%d", SDL_VERSIONNUM_MAJOR(v), SDL_VERSIONNUM_MINOR(v), SDL_VERSIONNUM_MICRO(v));
    SDL_JoystickID *ids = NULL;
    SDL_JoystickID chosen = 0;
    if (initialized) {
        ids = SDL_GetGamepads(&count);
        for (int i = 0; ids && i < count; ++i) {
            const char *candidate = SDL_GetGamepadPathForID(ids[i]);
            if (selected_path(candidate, argv[3], argv[4])) { ++matches; chosen = ids[i]; }
        }
        if (matches == 1) { pad = PAD_OPEN(chosen); path = SDL_GetGamepadPathForID(chosen); }
    }
    SDL_free(ids);
#else
    initialized = SDL_Init(SDL_INIT_GAMECONTROLLER | SDL_INIT_SENSOR) == 0;
    SDL_version v; SDL_GetVersion(&v);
    snprintf(version, sizeof(version), "%u.%u.%u", v.major, v.minor, v.patch);
    int chosen = -1;
    if (initialized) {
        count = SDL_NumJoysticks();
        for (int i = 0; i < count; ++i) {
            if (!SDL_IsGameController(i)) continue;
            const char *candidate = SDL_GameControllerPathForIndex(i);
            if (selected_path(candidate, argv[3], argv[4])) { ++matches; chosen = i; }
        }
        if (matches == 1) { pad = PAD_OPEN(chosen); path = SDL_GameControllerPathForIndex(chosen); }
    }
#endif
    if (!initialized) error = "SDL initialization failed";
    else if (matches != 1) error = "Expected exactly one mapped controller at the owned node";
    else if (!pad) error = "SDL open failed";
    if (pad) {
        identity = PAD_VENDOR(pad) == vendor && PAD_PRODUCT(pad) == product;
        if (!identity) error = "SDL VID/PID mismatch";
        else {
            pump(500);
            neutral = true;
            for (int i = 0; i < 6; ++i) {
                axes[i] = PAD_AXIS(pad, i);
                /* 8-bit Sony midpoint 128 maps to a small positive SDL quantum. */
                if ((i < 4 && abs(axes[i]) > 256) || (i >= 4 && axes[i] != 0)) neutral = false;
            }
            for (int i = 0; i < PAD_BUTTON_COUNT; ++i) if (PAD_BUTTON(pad, i)) ++pressed;
            south = PAD_BUTTON(pad, 0);
            input_matches = neutral && (expect_south ? pressed == 1 && south : !pressed);
            neutral = neutral && !pressed;
            mapping = PAD_MAPPING(pad);
#if CONSUMER_SDL_MAJOR == 3
            SDL_PropertiesID props = SDL_GetGamepadProperties(pad);
            rumble = SDL_GetBooleanProperty(props, SDL_PROP_GAMEPAD_CAP_RUMBLE_BOOLEAN, false);
            led = SDL_GetBooleanProperty(props, SDL_PROP_GAMEPAD_CAP_RGB_LED_BOOLEAN, false);
            trigger_rumble = SDL_GetBooleanProperty(props, SDL_PROP_GAMEPAD_CAP_TRIGGER_RUMBLE_BOOLEAN, false);
#else
            rumble = SDL_GameControllerHasRumble(pad);
            led = SDL_GameControllerHasLED(pad);
            trigger_rumble = SDL_GameControllerHasRumbleTriggers(pad);
#endif
            gyro = PAD_HAS_SENSOR(pad, SDL_SENSOR_GYRO);
            accel = PAD_HAS_SENSOR(pad, SDL_SENSOR_ACCEL);
            if (!input_matches) error = "SDL input did not match the selected fixture state";
            else if (!rumble) error = "SDL reports no rumble capability";
            else {
                rumble_before = phase("rumble-call-before");
                rumble_sent = PAD_RUMBLE(pad, 0x1234, 0x5678, rumble_duration_ms);
                phase("rumble-call-after");
                pump(200);
                stop_before = phase("stop-call-before");
                rumble_stopped = PAD_RUMBLE(pad, 0, 0, 0);
                phase("stop-call-after");
                /* Give the explicit stop its own delivery interval. No LED or
                 * close operation can explain packets in this interval. */
                pump(300);
                phase("stop-observation-end");
                if (led) {
                    phase("led-call-before");
                    led_sent = PAD_LED(pad, 0x12, 0x34, 0x56);
                    phase("led-call-after");
                }
                pump(100);
                phase("output-observation-end");
                if (!rumble_sent || !rumble_stopped || (led && !led_sent)) error = "SDL output API failed";
            }
        }
    }
    printf("{\"schema\":1,\"sdlMajor\":%d,\"version\":", CONSUMER_SDL_MAJOR);
    json_string(version); fputs(",\"result\":", stdout); json_string(error ? "failed" : "passed");
    fputs(",\"error\":", stdout); json_string(error); fputs(",\"sdlError\":", stdout); json_string(SDL_GetError());
    printf(",\"enumerated\":%d,\"matches\":%d,\"path\":", count, matches); json_string(path);
    fputs(",\"name\":", stdout); json_string(pad ? PAD_NAME(pad) : NULL);
    fputs(",\"serial\":", stdout); json_string(pad ? PAD_SERIAL(pad) : NULL);
    fputs(",\"mapping\":", stdout); json_string(mapping);
    fputs(",\"expectedInputState\":", stdout); json_string(argv[7]);
    printf(",\"identityVerified\":%s,\"neutral\":%s,\"inputMatches\":%s,\"southPressed\":%s,\"pressedButtons\":%d,\"axes\":[%d,%d,%d,%d,%d,%d]",
           identity ? "true" : "false", neutral ? "true" : "false", input_matches ? "true" : "false", south ? "true" : "false",
           pressed, axes[0], axes[1], axes[2], axes[3], axes[4], axes[5]);
    printf(",\"capabilities\":{\"rumble\":%s,\"rgbLed\":%s,\"triggerRumble\":%s,\"gyro\":%s,\"accelerometer\":%s}",
           rumble ? "true" : "false", led ? "true" : "false", trigger_rumble ? "true" : "false", gyro ? "true" : "false", accel ? "true" : "false");
    printf(",\"marker\":{\"lowFrequency\":4660,\"highFrequency\":22136,\"rgb\":[18,52,86],\"rumbleAccepted\":%s,\"stopAccepted\":%s,\"ledAccepted\":%s},\"outputDeliveryVerified\":false,\"gamesValidated\":false,\"physicalLatencyValidated\":false",
           rumble_sent ? "true" : "false", rumble_stopped ? "true" : "false", led_sent ? "true" : "false");
    SDL_free(mapping);
    if (pad) { phase("close-before"); PAD_CLOSE(pad); phase("close-after"); }
    if (initialized) { phase("quit-before"); SDL_Quit(); phase("quit-after"); }
    bool timing_valid = phase_count > 0;
    for (unsigned i = 0; i < phase_count; ++i)
        if (!phases[i].nanoseconds || (i && phases[i].nanoseconds < phases[i-1].nanoseconds)) timing_valid = false;
    bool before_expiry = rumble_before && stop_before >= rumble_before &&
        stop_before - rumble_before < (uint64_t)rumble_duration_ms * UINT64_C(1000000);
    printf(",\"phaseClock\":\"CLOCK_MONOTONIC\",\"phaseTimingValid\":%s,\"rumbleDurationMs\":%u,\"explicitStopBeforeAutoExpiry\":%s,\"phases\":[",
           timing_valid ? "true" : "false", (unsigned)rumble_duration_ms, before_expiry ? "true" : "false");
    for (unsigned i = 0; i < phase_count; ++i) {
        if (i) putchar(',');
        printf("{\"name\":\"%s\",\"monotonicNs\":%" PRIu64 "}", phases[i].name, phases[i].nanoseconds);
    }
    fputs("]}\n", stdout);
    return error ? 1 : 0;
}
