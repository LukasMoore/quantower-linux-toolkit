/* SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Quantower Linux Toolkit contributors.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <execinfo.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

/*
 * Optional LD_PRELOAD, crash-only, for the next reviewed Wine launch.
 * Interposes __assert_fail only. No Xlib functions or locks are interposed.
 * Never formats assertion text, file names, locals, strings from app objects,
 * or stack contents. Only return PCs and ELF loader symbol/module metadata.
 */
typedef void (*assert_fail_fn)(const char *, const char *, unsigned int,
                               const char *) __attribute__((noreturn));
static assert_fail_fn next_assert;
static int trace_directory = -1;
static int ready;
static _Atomic int trace_claimed;
static __thread int tracing __attribute__((tls_model("initial-exec")));

static void resolve_original(void)
{
    void *symbol = dlsym(RTLD_NEXT, "__assert_fail");
    _Static_assert(sizeof(symbol) == sizeof(next_assert), "POSIX function pointer");
    memcpy(&next_assert, &symbol, sizeof(symbol));
}

static const char *module_basename(const char *path)
{
    const char *slash;
    if (!path || !*path) return "?";
    slash = strrchr(path, '/');
    return slash ? slash + 1 : path;
}

static void write_all(int fd, const char *data, size_t size)
{
    while (size) {
        ssize_t count = write(fd, data, size);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) return;
        data += count;
        size -= (size_t)count;
    }
}

__attribute__((constructor))
static void prepare_trace(void)
{
    void *frames[16];
    Dl_info metadata;
    char scratch[80];
    struct stat info;
    const char *directory;
    int count;

    resolve_original();
    /*
     * Load the unwinder and exercise loader metadata/formatting before any
     * failing X11 operation. -z now and a DT_NEEDED libgcc_s also avoid lazy
     * PLT/library loading at the assertion. This is not an async-signal handler.
     */
    count = backtrace(frames, 16);
    for (int i = 0; i < count; i++) (void)dladdr(frames[i], &metadata);
    (void)snprintf(scratch, sizeof(scratch), "%ld:%#" PRIxPTR,
                   (long)getpid(), (uintptr_t)frames);
    directory = getenv("QUANTOWER_CRASH_TRACE_DIR");
    if (directory && directory[0] == '/') {
        int fd = open(directory, O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
        if (fd >= 0) {
            if (fstat(fd, &info) == 0 && S_ISDIR(info.st_mode) &&
                info.st_uid == geteuid() && !(info.st_mode & 0077)) {
                trace_directory = fd;
            } else {
                close(fd);
            }
        }
    }
    ready = 1;
}

static void record_backtrace(void)
{
    void *frames[64];
    char filename[80], line[768];
    int count, fd;

    (void)snprintf(filename, sizeof(filename), "assert-native-%ld.log",
                   (long)getpid());
    fd = openat(trace_directory, filename,
                O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0600);
    if (fd < 0) return;
    count = backtrace(frames, 64);
    for (int i = 0; i < count; i++) {
        Dl_info info = {0};
        uintptr_t pc = (uintptr_t)frames[i], module_offset = 0, symbol_offset = 0;
        const char *module = "?", *symbol = "?";
        if (dladdr(frames[i], &info)) {
            module = module_basename(info.dli_fname);
            if (info.dli_fbase) module_offset = pc - (uintptr_t)info.dli_fbase;
            if (info.dli_sname && info.dli_saddr) {
                symbol = info.dli_sname;
                symbol_offset = pc - (uintptr_t)info.dli_saddr;
            }
        }
        int length = snprintf(
            line, sizeof(line),
            "#%02d pc=%#" PRIxPTR " module=%.160s+%#" PRIxPTR
            " symbol=%.256s+%#" PRIxPTR "\n",
            i, pc, module, module_offset, symbol, symbol_offset);
        if (length > 0)
            write_all(fd, line, (size_t)length < sizeof(line) ?
                      (size_t)length : sizeof(line) - 1);
    }
    close(fd);
}

__attribute__((visibility("default"), noreturn))
void __assert_fail(const char *assertion, const char *file, unsigned int line,
                   const char *function)
{
    int saved_errno = errno;
    int selected = (function && strcmp(function, "_XAllocID") == 0) ||
                   (file && strstr(file, "xcb_io") != NULL);
    if (ready && selected && trace_directory >= 0 && !tracing &&
        !atomic_exchange_explicit(&trace_claimed, 1, memory_order_relaxed)) {
        tracing = 1;
        record_backtrace();
        tracing = 0;
    }
    /* Normally already cached by the constructor; cover an early constructor assertion. */
    if (!next_assert) resolve_original();
    errno = saved_errno;
    if (next_assert) next_assert(assertion, file, line, function);
    /* Only a broken loader/symbol lookup reaches this fatal fallback. */
    abort();
}
