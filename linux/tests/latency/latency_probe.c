/* Owned-UHID -> real virtual hidraw timing probe. No device paths are opened.
 * The root guest coordinator validates and passes all four exact descriptors.
 * Only UHID_INPUT2 is written; feature/output handling stays in the fixture.
 * Samples remain in memory until both producer and consumer have stopped. */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <linux/hidraw.h>
#include <linux/uhid.h>
#include <poll.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

struct sample {
    uint64_t scheduled, before, after, observed;
    uint32_t duplicates;
    int error, skipped;
};

struct context {
    int input_fd, output_fd;
    uint32_t rate, count, nonce;
    uint8_t template[78];
    uint32_t crc_table[256];
    struct sample *samples;
    atomic_uint_fast64_t done;
    atomic_int fatal;
    uint64_t input_reads, unmatched, malformed, reordered;
};

static uint64_t monotonic_ns(void) {
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now)) abort();
    return (uint64_t)now.tv_sec * UINT64_C(1000000000) + (uint64_t)now.tv_nsec;
}

static uint32_t le32(const uint8_t *bytes) {
    return (uint32_t)bytes[0] | (uint32_t)bytes[1] << 8 | (uint32_t)bytes[2] << 16 | (uint32_t)bytes[3] << 24;
}

static void put32(uint8_t *bytes, uint32_t value) {
    for (int i = 0; i < 4; i++) bytes[i] = (uint8_t)(value >> (8 * i));
}

static uint32_t crc(const struct context *ctx, uint8_t seed, const uint8_t *data, size_t length) {
    uint32_t value = UINT32_C(0xffffffff);
    value = ctx->crc_table[(value ^ seed) & 255] ^ (value >> 8);
    for (size_t i = 0; i < length; i++) value = ctx->crc_table[(value ^ data[i]) & 255] ^ (value >> 8);
    return ~value;
}

static void report(const struct context *ctx, uint32_t sequence, uint8_t *data) {
    memcpy(data, ctx->template, 78);
    data[8] = (uint8_t)sequence;
    /* Sensor timestamp is forwarded verbatim: BT[29..32] -> USB[28..31].
     * Random per-case upper16 plus bounded sequence prevents stale correlation. */
    put32(data + 29, ctx->nonce << 16 | sequence);
    put32(data + 74, crc(ctx, 0xa1, data, 74));
}

static void *consume(void *opaque) {
    struct context *ctx = opaque;
    uint32_t previous = 0;
    int have_previous = 0;
    for (;;) {
        uint64_t done = atomic_load(&ctx->done);
        if (done && monotonic_ns() >= done + UINT64_C(1000000000)) break;
        struct pollfd endpoint = {.fd = ctx->output_fd, .events = POLLIN};
        int ready = poll(&endpoint, 1, 20);
        if (ready < 0) {
            if (errno == EINTR) continue;
            atomic_store(&ctx->fatal, errno); break;
        }
        if (endpoint.revents & (POLLERR | POLLHUP | POLLNVAL)) {
            atomic_store(&ctx->fatal, ENODEV); break;
        }
        if (!(endpoint.revents & POLLIN)) continue;
        uint8_t data[4096], expected[78];
        ssize_t size = read(ctx->output_fd, data, sizeof(data));
        int read_error = errno;
        uint64_t observed = monotonic_ns();
        if (size < 0 && (read_error == EINTR || read_error == EAGAIN)) continue;
        if (size <= 0) { atomic_store(&ctx->fatal, size ? read_error : ENODEV); break; }
        ctx->input_reads++;
        if (size != 64 || data[0] != 1) { ctx->unmatched++; continue; }
        uint32_t tag = le32(data + 28), sequence = tag & 0xffff;
        if (tag >> 16 != ctx->nonce || sequence >= ctx->count) { ctx->unmatched++; continue; }
        report(ctx, sequence, expected);
        if (memcmp(data + 1, expected + 2, 63)) { ctx->malformed++; continue; }
        struct sample *sample = &ctx->samples[sequence];
        if (sample->observed) sample->duplicates++;
        else sample->observed = observed;
        if (have_previous && sequence < previous) ctx->reordered++;
        previous = sequence; have_previous = 1;
    }
    return NULL;
}

static int number(const char *text, int maximum) {
    char *end = NULL;
    errno = 0;
    long value = strtol(text, &end, 10);
    if (errno || !*text || *end || value < 0 || value > maximum) return -1;
    return (int)value;
}

int main(int argc, char **argv) {
    int input_fd = -1, output_fd = -1, samples_fd = -1, template_fd = -1, rate = -1, seconds = -1, nonce = -1;
    if (argc != 15) { fprintf(stderr, "Seven explicit descriptor/rate/duration/nonce arguments required\n"); return 2; }
    for (int i = 1; i < argc; i += 2) {
        int value = number(argv[i + 1], 1000000);
        if (!strcmp(argv[i], "--uhid-fd")) input_fd = value;
        else if (!strcmp(argv[i], "--virtual-fd")) output_fd = value;
        else if (!strcmp(argv[i], "--samples-fd")) samples_fd = value;
        else if (!strcmp(argv[i], "--template-fd")) template_fd = value;
        else if (!strcmp(argv[i], "--rate")) rate = value;
        else if (!strcmp(argv[i], "--seconds")) seconds = value;
        else if (!strcmp(argv[i], "--nonce")) nonce = value;
        else return 2;
    }
    if (input_fd < 3 || output_fd < 3 || samples_fd < 3 || template_fd < 3 ||
        (rate != 100 && rate != 1000) || seconds != 10 || nonce < 1 || nonce > 65535 ||
        input_fd == output_fd || input_fd == samples_fd || input_fd == template_fd ||
        output_fd == samples_fd || output_fd == template_fd || samples_fd == template_fd) return 2;
    struct stat in_info, out_info, samples_info, template_info;
    struct hidraw_devinfo hid = {0};
    if (fstat(input_fd, &in_info) || fstat(output_fd, &out_info) || fstat(samples_fd, &samples_info) ||
        fstat(template_fd, &template_info) || !S_ISCHR(in_info.st_mode) || !S_ISCHR(out_info.st_mode) ||
        !S_ISREG(samples_info.st_mode) || !S_ISREG(template_info.st_mode) || template_info.st_size != 78 ||
        ioctl(output_fd, HIDIOCGRAWINFO, &hid) || hid.bustype != 3 || hid.vendor != 0x054c ||
        (hid.product != 0x0ce6 && hid.product != 0x0df2)) {
        fprintf(stderr, "Inherited descriptors are not the expected fixture/native virtual target\n"); return 2;
    }
    struct context ctx = {.input_fd = input_fd, .output_fd = output_fd, .rate = (uint32_t)rate,
                          .count = (uint32_t)(rate * seconds), .nonce = (uint32_t)nonce};
    atomic_init(&ctx.done, 0); atomic_init(&ctx.fatal, 0);
    for (uint32_t i = 0; i < 256; i++) {
        uint32_t value = i;
        for (int bit = 0; bit < 8; bit++) value = value & 1 ? UINT32_C(0xedb88320) ^ (value >> 1) : value >> 1;
        ctx.crc_table[i] = value;
    }
    if (pread(template_fd, ctx.template, 78, 0) != 78 || ctx.template[0] != 0x31 ||
        le32(ctx.template + 74) != crc(&ctx, 0xa1, ctx.template, 74)) return 2;
    uint64_t clock_pair_min = UINT64_MAX, clock_pair_max = 0;
    for (int i = 0; i < 1000; i++) {
        uint64_t before = monotonic_ns(), elapsed = monotonic_ns() - before;
        if (elapsed < clock_pair_min) clock_pair_min = elapsed;
        if (elapsed > clock_pair_max) clock_pair_max = elapsed;
    }
    struct rusage usage_before, usage_after;
    if (getrusage(RUSAGE_SELF, &usage_before)) return 2;
    ctx.samples = calloc(ctx.count, sizeof(*ctx.samples));
    if (!ctx.samples) return 2;
    pthread_t consumer;
    int thread_error = pthread_create(&consumer, NULL, consume, &ctx);
    if (thread_error) { free(ctx.samples); return 2; }
    uint64_t period = UINT64_C(1000000000) / ctx.rate;
    uint64_t first = monotonic_ns() + UINT64_C(100000000);
    uint32_t attempted = 0, writes = 0, skipped = 0;
    for (uint32_t i = 0; i < ctx.count; i++) {
        if (atomic_load(&ctx.fatal)) break;
        struct sample *sample = &ctx.samples[i];
        sample->scheduled = first + i * period;
        struct timespec target = {.tv_sec = (time_t)(sample->scheduled / UINT64_C(1000000000)),
                                  .tv_nsec = (long)(sample->scheduled % UINT64_C(1000000000))};
        int error;
        while ((error = clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &target, NULL)) == EINTR) {}
        if (error) { atomic_store(&ctx.fatal, error); break; }
        /* Never emit a catch-up burst for expired periods. Account for each
         * missed deadline separately from a transport report that went missing. */
        if (monotonic_ns() >= sample->scheduled + period) { sample->skipped = 1; skipped++; continue; }
        struct uhid_event event = {0};
        event.type = UHID_INPUT2;
        event.u.input2.size = 78;
        report(&ctx, i, event.u.input2.data);
        sample->before = monotonic_ns();
        ssize_t written = write(input_fd, &event, sizeof(event));
        int write_error = errno;
        sample->after = monotonic_ns();
        sample->error = written == (ssize_t)sizeof(event) ? 0 : written < 0 ? write_error : EIO;
        attempted++;
        if (!sample->error) writes++;
        else { atomic_store(&ctx.fatal, sample->error); break; }
    }
    atomic_store(&ctx.done, monotonic_ns());
    pthread_join(consumer, NULL);
    if (getrusage(RUSAGE_SELF, &usage_after)) { free(ctx.samples); return 2; }
    FILE *samples = fdopen(samples_fd, "w");
    if (!samples) { free(ctx.samples); return 2; }
    fputs("sequence,scheduled_ns,inject_before_ns,inject_after_ns,observed_ns,duplicates,write_errno,deadline_skipped\n", samples);
    uint32_t matched = 0;
    for (uint32_t i = 0; i < ctx.count; i++) {
        const struct sample *s = &ctx.samples[i];
        if (s->observed) matched++;
        fprintf(samples, "%u,%" PRIu64 ",%" PRIu64 ",%" PRIu64 ",%" PRIu64 ",%u,%d,%d\n",
                i, s->scheduled, s->before, s->after, s->observed, s->duplicates, s->error, s->skipped);
    }
    int saved = !ferror(samples) && fflush(samples) == 0;
    if (fclose(samples)) saved = 0;
    printf("{\"schema\":1,\"nonce\":%u,\"rate\":%u,\"nominalSamples\":%u,\"attempted\":%u,\"written\":%u,"
           "\"deadlineSkipped\":%u,\"matched\":%u,\"inputReads\":%" PRIu64 ",\"unmatched\":%" PRIu64
           ",\"malformedMatches\":%" PRIu64 ",\"reordered\":%" PRIu64 ",\"fatalErrno\":%d,\"samplesSaved\":%s,"
           "\"timingClockPairMinNs\":%" PRIu64 ",\"timingClockPairMaxNs\":%" PRIu64 ",\"probeUserCpuUs\":%ld,\"probeSystemCpuUs\":%ld}\n",
           ctx.nonce, ctx.rate, ctx.count, attempted, writes, skipped, matched, ctx.input_reads, ctx.unmatched,
           ctx.malformed, ctx.reordered, atomic_load(&ctx.fatal), saved ? "true" : "false", clock_pair_min, clock_pair_max,
           (usage_after.ru_utime.tv_sec - usage_before.ru_utime.tv_sec) * 1000000L + usage_after.ru_utime.tv_usec - usage_before.ru_utime.tv_usec,
           (usage_after.ru_stime.tv_sec - usage_before.ru_stime.tv_sec) * 1000000L + usage_after.ru_stime.tv_usec - usage_before.ru_stime.tv_usec);
    free(ctx.samples);
    return saved ? 0 : 1; /* A measured transport limit is evidence, not a functional gate. */
}
