/* Research only: a client-owned server adapter, never a lingering ALSA monitor.
 * The separate ordinary UID and exact sound ACLs are owned by the coordinator.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <signal.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <unistd.h>
#include <pipewire/pipewire.h>

struct state { struct pw_main_loop *loop; int result; bool bound; };
static bool line_equal(const char *path, const char *expected)
{
    char text[PATH_MAX]; FILE *file = fopen(path, "r");
    if (!file) return false;
    bool ok = fgets(text, sizeof(text), file) != NULL; fclose(file);
    if (!ok) return false;
    text[strcspn(text, "\r\n")] = 0;
    return ok && !strcmp(text, expected);
}
static bool beneath(const char *path, const char *parent)
{ size_t n = strlen(parent); return !strncmp(path, parent, n) && path[n] == '/'; }
static void stop(void *data, int signal_number)
{ (void)signal_number; pw_main_loop_quit(((struct state *)data)->loop); }
static void lifetime(void *data, int fd, uint32_t mask)
{
    struct state *state = data; char byte;
    if (mask & (SPA_IO_HUP | SPA_IO_ERR) || read(fd, &byte, 1) <= 0) pw_main_loop_quit(state->loop);
    else { state->result = 1; pw_main_loop_quit(state->loop); } /* No command protocol. */
}
static void error(void *data, uint32_t id, int seq, int res, const char *message)
{
    struct state *state = data; state->result = 1;
    fprintf(stderr, "PipeWire error id=%u seq=%d code=%d: %s\n", id, seq, res, message);
    pw_main_loop_quit(state->loop);
}
static void bound(void *data, uint32_t id)
{
    struct state *state = data; state->bound = true;
    printf("{\"event\":\"bound\",\"nodeId\":%u,\"pid\":%ld,\"linger\":false,\"productionBinary\":false}\n", id, (long)getpid());
    fflush(stdout);
}
static const struct pw_core_events core_events = { PW_VERSION_CORE_EVENTS, .error = error };
static const struct pw_proxy_events proxy_events = { PW_VERSION_PROXY_EVENTS, .bound = bound };

int main(int argc, char **argv)
{
    /* OWNER_UUID EXACT_USB_PARENT EXACT_CARD_SYSFS NONCE LIFETIME_FD */
    if (argc != 6 || !getuid() || !geteuid()) return 2;
    const char *token = argv[1], *parent = argv[2], *card = argv[3], *nonce = argv[4];
    struct stat marker; char canonical[PATH_MAX], path[PATH_MAX], text[128];
    if (strlen(token) != 36 || lstat("/etc/madsense-ci-owner", &marker) || !S_ISREG(marker.st_mode)
        || marker.st_uid || (marker.st_mode & 022) || !line_equal("/etc/madsense-ci-owner", token)
        || !line_equal("/sys/class/dmi/id/sys_vendor", "QEMU")
        || strncmp(parent, "/sys/devices/platform/vhci_hcd.", sizeof("/sys/devices/platform/vhci_hcd.")-1)
        || !realpath(parent, canonical) || strcmp(parent, canonical)
        || !realpath(card, canonical) || strcmp(card, canonical) || !beneath(card, parent)
        || strlen(nonce) != 32 || strspn(nonce, "0123456789abcdef") != 32) return 2;
    const char *name = strrchr(card, '/');
    if (!name || strncmp(++name, "card", 4) || !name[4] || strspn(name+4, "0123456789") != strlen(name+4)) return 2;
    unsigned long number = strtoul(name+4, NULL, 10);
    if (number > 63) return 2;
    snprintf(path, sizeof(path), "%s/idVendor", parent); if (!line_equal(path, "054c")) return 2;
    snprintf(path, sizeof(path), "%s/idProduct", parent); if (!line_equal(path, "0ce6")) return 2;
    const char *busid = strrchr(parent, '/') + 1;
    snprintf(path, sizeof(path), "%s/%s:1.0/interface", parent, busid);
    snprintf(text, sizeof(text), "MadSense-audio-lease-%s", nonce);
    if (!line_equal(path, text)) return 2;
    snprintf(path, sizeof(path), "/sys/class/sound/card%lu", number);
    if (!realpath(path, canonical) || strcmp(card, canonical)) return 2;
    struct stat pcm; unsigned device_major, device_minor;
    snprintf(path, sizeof(path), "/dev/snd/pcmC%luD0p", number);
    if (lstat(path, &pcm) || !S_ISCHR(pcm.st_mode)) return 2;
    snprintf(path, sizeof(path), "/sys/class/sound/pcmC%luD0p", number);
    if (!realpath(path, canonical) || !beneath(canonical, card)) return 2;
    snprintf(path, sizeof(path), "/sys/class/sound/pcmC%luD0p/dev", number);
    FILE *devfile = fopen(path, "r"); if (!devfile) return 2;
    int scanned = fscanf(devfile, "%u:%u", &device_major, &device_minor); fclose(devfile);
    if (scanned != 2 || pcm.st_rdev != makedev(device_major, device_minor)) return 2;
    char *end = NULL; long owner_fd = strtol(argv[5], &end, 10); struct stat owner;
    if (!end || *end || owner_fd < 3 || owner_fd > INT_MAX || fstat((int)owner_fd, &owner) || !S_ISFIFO(owner.st_mode)) return 2;
    int owner_flags = fcntl((int)owner_fd, F_GETFL);
    if (owner_flags < 0 || (owner_flags & O_ACCMODE) != O_RDONLY) return 2;
    const char *remote = getenv("PIPEWIRE_REMOTE"), *runtime = getenv("XDG_RUNTIME_DIR");
    if (!remote || !runtime || strncmp(runtime, "/opt/madsense-consumers-", sizeof("/opt/madsense-consumers-")-1)) return 2;
    struct stat runtime_info;
    if (!realpath(runtime, canonical) || strcmp(runtime, canonical) || lstat(runtime, &runtime_info)
        || !S_ISDIR(runtime_info.st_mode) || runtime_info.st_uid != getuid() || (runtime_info.st_mode & 077)) return 2;
    snprintf(path, sizeof(path), "%s/pipewire-0", runtime);
    struct stat socket_info;
    if (strcmp(remote, path) || !realpath(remote, canonical) || strcmp(remote, canonical)
        || lstat(path, &socket_info) || !S_ISSOCK(socket_info.st_mode) || socket_info.st_uid != getuid()) return 2;

    pw_init(&argc, &argv); struct state state = {0};
    state.loop = pw_main_loop_new(NULL); if (!state.loop) return 1;
    struct pw_loop *loop = pw_main_loop_get_loop(state.loop);
    struct pw_context *context = pw_context_new(loop, NULL, 0);
    if (!context) { pw_main_loop_destroy(state.loop); return 1; }
    struct pw_core *core = pw_context_connect(context, pw_properties_new(PW_KEY_REMOTE_NAME, remote, NULL), 0);
    if (!core) { pw_context_destroy(context); pw_main_loop_destroy(state.loop); return 1; }
    struct spa_hook core_listener, proxy_listener;
    pw_core_add_listener(core, &core_listener, &core_events, &state);
    snprintf(path, sizeof(path), "hw:%lu,0", number);
    snprintf(text, sizeof(text), "madsense-owned-%s", nonce);
    struct pw_properties *properties = pw_properties_new(
        "factory.name", "api.alsa.pcm.sink", "api.alsa.path", path,
        "api.alsa.pcm.stream", "playback", "media.class", "Audio/Sink",
        "node.name", text, "node.description", "MadSense owned DualSense playback",
        "audio.format", "S16LE", "audio.rate", "48000", "audio.channels", "4",
        "audio.position", "[ FL FR RL RR ]", "priority.session", "1", "object.linger", "false",
        "adapter.auto-port-config", "{ mode = dsp monitor = false position = preserve }",
        "device.api", "alsa", "device.bus", "usb", "device.vendor.id", "0x054c",
        "device.product.id", "0x0ce6", "device.sysfs.path", card + 4,
        "madsense.audio.lease", nonce, NULL);
    if (!properties) { pw_core_disconnect(core); pw_context_destroy(context); pw_main_loop_destroy(state.loop); return 1; }
    snprintf(text, sizeof(text), "%lu", number);
    pw_properties_set(properties, "api.alsa.pcm.card", text);
    struct pw_proxy *proxy = pw_core_create_object(core, "adapter", PW_TYPE_INTERFACE_Node, PW_VERSION_NODE, &properties->dict, 0);
    pw_properties_free(properties);
    if (!proxy) { pw_core_disconnect(core); pw_context_destroy(context); pw_main_loop_destroy(state.loop); return 1; }
    pw_proxy_add_listener(proxy, &proxy_listener, &proxy_events, &state);
    struct spa_source *term = pw_loop_add_signal(loop, SIGTERM, stop, &state);
    struct spa_source *interrupt = pw_loop_add_signal(loop, SIGINT, stop, &state);
    struct spa_source *owner_source = pw_loop_add_io(loop, (int)owner_fd, SPA_IO_IN | SPA_IO_HUP | SPA_IO_ERR, false, lifetime, &state);
    if (!term || !interrupt || !owner_source) state.result = 1;
    else if (pw_main_loop_run(state.loop) < 0) state.result = 1;
    pw_proxy_destroy(proxy); pw_core_disconnect(core); pw_context_destroy(context); pw_main_loop_destroy(state.loop);
    pw_deinit(); return state.bound ? state.result : 1;
}
