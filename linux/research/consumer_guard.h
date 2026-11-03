/* Shared scope checks for consumer experiments in an owned, hardware-free VM.
 * The orchestrator additionally checks DMI UUID and grants its ordinary consumer
 * account access only to the exact virtual nodes. These probes never attach USB.
 */
#ifndef MADSENSE_CONSUMER_GUARD_H
#define MADSENSE_CONSUMER_GUARD_H
#include <errno.h>
#include <limits.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static void json_string(const char *value)
{
    if (!value) { fputs("null", stdout); return; }
    putchar('"');
    for (const unsigned char *p = (const unsigned char *)value; *p; ++p) {
        if (*p == '"' || *p == '\\') printf("\\%c", *p);
        else if (*p < 32) printf("\\u%04x", *p);
        else putchar(*p);
    }
    putchar('"');
}

static bool read_line_equal(const char *path, const char *expected)
{
    char line[PATH_MAX];
    FILE *file = fopen(path, "r");
    if (!file) return false;
    bool ok = fgets(line, sizeof(line), file) != NULL;
    fclose(file);
    if (!ok) return false;
    line[strcspn(line, "\r\n")] = 0;
    return strcmp(line, expected) == 0;
}

static bool owned_node(const char *parent, const char *node, bool hidraw)
{
    const char *prefix = hidraw ? "/dev/hidraw" : "/dev/input/event";
    if (strncmp(node, prefix, strlen(prefix)) != 0) return false;
    const char *number = node + strlen(prefix);
    if (!*number || strspn(number, "0123456789") != strlen(number)) return false;
    struct stat st;
    if (lstat(node, &st) || !S_ISCHR(st.st_mode)) return false;
    char sysfs[PATH_MAX], resolved[PATH_MAX];
    int n = snprintf(sysfs, sizeof(sysfs), "/sys/class/%s/%s%s/device",
                     hidraw ? "hidraw" : "input", hidraw ? "hidraw" : "event", number);
    if (n < 0 || (size_t)n >= sizeof(sysfs) || !realpath(sysfs, resolved)) return false;
    size_t length = strlen(parent);
    return strncmp(resolved, parent, length) == 0 && resolved[length] == '/';
}

static bool consumer_scope(const char *token, const char *parent,
                           const char *event, const char *hidraw)
{
    struct stat marker;
    char actual_parent[PATH_MAX];
    /* SDL may inspect HIDAPI devices while initializing: never run it as root. */
    if (!getuid() || !geteuid() || strlen(token) != 36
        || lstat("/etc/madsense-ci-owner", &marker) || !S_ISREG(marker.st_mode)
        || marker.st_uid || (marker.st_mode & 022)
        || !read_line_equal("/etc/madsense-ci-owner", token)
        || !read_line_equal("/sys/class/dmi/id/sys_vendor", "QEMU")
        || !realpath(parent, actual_parent) || strcmp(parent, actual_parent)
        || strncmp(parent, "/sys/devices/platform/vhci_hcd.", sizeof("/sys/devices/platform/vhci_hcd.") - 1)
        || !owned_node(parent, event, false)) return false;
    return !strcmp(hidraw, "-") || owned_node(parent, hidraw, true);
}

static inline bool selected_path(const char *candidate, const char *event, const char *hidraw)
{
    return candidate && (!strcmp(candidate, event)
                         || (strcmp(hidraw, "-") && !strcmp(candidate, hidraw)));
}
#endif
