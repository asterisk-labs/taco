#include "progress.hpp"

#include <cstdio>
#include <mutex>
#include <new>

#ifdef _WIN32
#include <io.h>
#define TACO_ISATTY(stream) _isatty(_fileno(stream))
#else
#include <pthread.h>
#include <unistd.h>
#define TACO_ISATTY(stream) isatty(fileno(stream))
#endif

namespace taco {
namespace {

std::mutex progress_mutex;
// Whether this thread holds progress_mutex to report.
thread_local bool reporting = false;
taco_progress_fn progress_callback = nullptr;
void* progress_user = nullptr;

// progress_mutex, held by this thread for the rest of the scope.
struct ReportLock {
    std::lock_guard<std::mutex> lock{progress_mutex};
    ReportLock() { reporting = true; }
    ~ReportLock() { reporting = false; }
};

#ifndef _WIN32
// Callbacks run under the lock, so fork() cannot wait for it. A child forked
// while another thread reported gets a new lock. One forked from a callback
// keeps the lock, which its only thread holds and releases.
[[maybe_unused]] const int fork_handler = pthread_atfork(nullptr, nullptr, [] {
    if (!reporting)
        new (&progress_mutex) std::mutex;
});
#endif

std::string human_bytes(std::uint64_t bytes) {
    const char* units[] = {"B", "KB", "MB", "GB", "TB"};
    double value = static_cast<double>(bytes);
    int unit = 0;
    while (value >= 1024.0 && unit < 4) {
        value /= 1024.0;
        ++unit;
    }
    char text[32];
    std::snprintf(text, sizeof text, unit == 0 ? "%.0f %s" : "%.1f %s", value, units[unit]);
    return text;
}

// One line per phase, redrawn in place and finished with a newline.
void draw(const std::string& phase, std::uint64_t done, std::uint64_t total) {
    static bool terminal = TACO_ISATTY(stderr) != 0;
    if (!terminal)
        return;
    if (total == 0) {
        std::fprintf(stderr, "\r%s: %s", phase.c_str(), human_bytes(done).c_str());
        return;
    }
    const int width = 24;
    const int filled = static_cast<int>(static_cast<double>(width) * static_cast<double>(done) / static_cast<double>(total));
    std::string bar(static_cast<std::size_t>(filled), '#');
    bar.append(static_cast<std::size_t>(width - filled), '.');
    std::fprintf(stderr, "\r%s [%s] %s / %s", phase.c_str(), bar.c_str(), human_bytes(done).c_str(),
                 human_bytes(total).c_str());
    if (done >= total)
        std::fputc('\n', stderr);
    std::fflush(stderr);
}

} // namespace

void set_progress(taco_progress_fn callback, void* user) {
    const std::lock_guard lock(progress_mutex);
    progress_callback = callback;
    progress_user = user;
}

void report_progress(const std::string& phase, std::uint64_t done, std::uint64_t total) {
    const ReportLock lock;
    if (progress_callback)
        progress_callback(phase.c_str(), done, total, progress_user);
    else
        draw(phase, done, total);
}

} // namespace taco
