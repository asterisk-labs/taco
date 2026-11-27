#include "cache.hpp"

#include "error.hpp"
#include "json.hpp"
#include "paths.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <charconv>
#include <chrono>
#include <cstdlib>
#include <ctime>
#include <fstream>
#include <functional>
#include <map>
#include <memory>
#include <mutex>
#include <new>
#include <sstream>
#include <thread>

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <process.h>
#include <windows.h>
#else
#include <cerrno>
#include <csignal>
#include <cstdio>
#include <fcntl.h>
#include <pthread.h>
#include <unistd.h>
#endif

namespace fs = std::filesystem;

namespace taco {
namespace {

constexpr std::string_view stamp_name = "taco-cache.json";
constexpr std::string_view staging_prefix = ".tmp-";
constexpr std::string_view trash_prefix = ".trash-";
constexpr std::string_view snapshot_prefix = ".open-";
// Each failed attempt to put an entry in place moves an outdated one aside.
constexpr int store_attempts = 8;
constexpr std::uint64_t default_cache_size = 5ULL * 1024 * 1024 * 1024;
constexpr std::string_view cachedir_tag =
    "Signature: 8a477f597d28d172789f06886806bc55\n"
    "# This directory holds metadata that taco can download again.\n"
    "# See https://bford.info/cachedir/\n";

std::string environment(const char* name) {
#if defined(_WIN32)
    char* value = nullptr;
    std::size_t size = 0;
    if (_dupenv_s(&value, &size, name) != 0)
        return {};
    const std::unique_ptr<char, decltype(&std::free)> owned(value, &std::free);
    return owned ? std::string(owned.get()) : std::string();
#else
    const char* value = std::getenv(name);
    return value ? std::string(value) : std::string();
#endif
}

bool refreshing() {
    return !environment("TACO_CACHE_REFRESH").empty();
}

std::uint64_t cache_size_cap() {
    const std::string value = environment("TACO_CACHE_SIZE");
    if (value.empty())
        return default_cache_size;
    char* end = nullptr;
    const auto parsed = std::strtoull(value.c_str(), &end, 10);
    if (end == value.c_str() || *end != '\0')
        fail(std::string("TACO_CACHE_SIZE must be a number of bytes, got ") + value);
    return parsed;
}

std::uint64_t process_id() noexcept {
#ifdef _WIN32
    return static_cast<std::uint64_t>(_getpid());
#else
    return static_cast<std::uint64_t>(getpid());
#endif
}

// Distinct across the threads and processes that share a cache.
std::string unique_suffix() {
    static std::atomic<std::uint64_t> counter{0};
    const auto now = std::chrono::steady_clock::now().time_since_epoch().count();
    const auto thread = std::hash<std::thread::id>{}(std::this_thread::get_id());
    return hex64(fnv1a64(std::to_string(process_id()) + ":" + std::to_string(now) + ":" + std::to_string(thread) +
                         ":" + std::to_string(counter.fetch_add(1))));
}

std::string now_utc() {
    const std::time_t now = std::time(nullptr);
    std::tm parts{};
#ifdef _WIN32
    gmtime_s(&parts, &now);
#else
    gmtime_r(&now, &parts);
#endif
    char text[32];
    std::strftime(text, sizeof text, "%Y-%m-%dT%H:%M:%SZ", &parts);
    return text;
}

std::string stamp_json(const CacheStamp& stamp) {
    return "{\"source\":" + json::quote(stamp.source) + ",\"container\":" + json::quote(stamp.container) +
           ",\"key\":" + json::quote(stamp.key) + ",\"size\":" + std::to_string(stamp.size) +
           ",\"created\":" + json::quote(stamp.created) + ",\"opened\":" + json::quote(stamp.opened) + "}\n";
}

std::optional<CacheStamp> parse_stamp(const std::string& text) {
    json::Value value;
    try {
        value = json::parse(text);
    } catch (const json::InvalidJson&) {
        return std::nullopt;
    }
    if (!value.is_object())
        return std::nullopt;
    CacheStamp stamp;
    for (auto [name, field] : {std::pair{"source", &stamp.source}, std::pair{"container", &stamp.container},
                               std::pair{"key", &stamp.key}, std::pair{"created", &stamp.created},
                               std::pair{"opened", &stamp.opened}}) {
        const json::Value* item = value.find(name);
        if (!item || !item->is_string())
            return std::nullopt;
        *field = item->string;
    }
    const json::Value* size = value.find("size");
    if (!size || size->kind != json::Kind::number)
        return std::nullopt;
    stamp.size = std::strtoull(std::string(size->raw).c_str(), nullptr, 10);
    return stamp;
}

// Nothing when the stamp is missing or damaged, or when another process
// removed the entry meanwhile.
std::optional<CacheStamp> read_stamp(const fs::path& directory) {
    try {
        return parse_stamp(read_local(directory / stamp_name));
    } catch (const Error&) {
        return std::nullopt;
    }
}

// Swaps two existing paths in one step where the platform can, so an entry
// being replaced never goes missing for a reader.
bool exchange(const fs::path& first, const fs::path& second) {
#if defined(__APPLE__) && defined(RENAME_SWAP)
    return ::renamex_np(first.c_str(), second.c_str(), RENAME_SWAP) == 0;
#elif defined(__GLIBC__) && defined(__USE_GNU) && (__GLIBC__ > 2 || (__GLIBC__ == 2 && __GLIBC_MINOR__ >= 28))
    return ::renameat2(AT_FDCWD, first.c_str(), AT_FDCWD, second.c_str(), RENAME_EXCHANGE) == 0;
#else
    static_cast<void>(first);
    static_cast<void>(second);
    return false;
#endif
}

bool complete(const fs::path& directory, const std::vector<std::string>& files) {
    std::error_code error;
    return std::all_of(files.begin(), files.end(), [&](const std::string& file) {
        return fs::is_regular_file(directory / local_path(file), error);
    });
}

// Moves a directory out of the way before deleting it, so a reader that
// still has it open is not left with half a directory. One that another
// process moved first is left to that process.
void discard(const fs::path& directory) {
    const fs::path trash = directory.parent_path() / (std::string(trash_prefix) + unique_suffix());
    std::error_code error;
    fs::rename(directory, trash, error);
    if (!error)
        fs::remove_all(trash, error);
}

// A host tag prevents treating another machine's PID as stale on shared storage.
const std::string& host_tag() {
    static const std::string tag = [] {
        std::array<char, 256> name{};
#ifdef _WIN32
        DWORD size = static_cast<DWORD>(name.size());
        if (!GetComputerNameA(name.data(), &size))
            name[0] = '\0';
#else
        if (gethostname(name.data(), name.size() - 1) != 0)
            name[0] = '\0';
#endif
        return hex64(fnv1a64(name.data())).substr(8);
    }();
    return tag;
}

// Keep snapshots when process liveness is uncertain.
bool process_alive(std::uint64_t pid) {
#ifdef _WIN32
    const auto id = static_cast<DWORD>(pid);
    if (id == 0 || id != pid)
        return true;
    const HANDLE process = OpenProcess(SYNCHRONIZE, FALSE, id);
    if (!process)
        return GetLastError() != ERROR_INVALID_PARAMETER;
    const bool running = WaitForSingleObject(process, 0) == WAIT_TIMEOUT;
    CloseHandle(process);
    return running;
#else
    const auto id = static_cast<pid_t>(pid);
    if (id <= 0 || static_cast<std::uint64_t>(id) != pid)
        return true;
    return ::kill(id, 0) == 0 || errno == EPERM;
#endif
}

// Return the owner PID only for snapshots from this host.
std::optional<std::uint64_t> snapshot_owner(std::string_view name) {
    const std::string prefix = std::string(snapshot_prefix) + host_tag() + "-";
    if (!name.starts_with(prefix))
        return std::nullopt;
    name.remove_prefix(prefix.size());
    std::uint64_t pid = 0;
    const auto [end, error] = std::from_chars(name.data(), name.data() + name.size(), pid);
    if (error != std::errc() || end == name.data() || end == name.data() + name.size() || *end != '-')
        return std::nullopt;
    return pid;
}

void remove_abandoned_snapshots(const fs::path& root) {
    const auto self = process_id();
    std::error_code error;
    for (const auto& item : fs::directory_iterator(root, error)) {
        const auto owner = snapshot_owner(utf8_path(item.path().filename()));
        if (owner && *owner != self && !process_alive(*owner)) {
            std::error_code ignored;
            fs::remove_all(item.path(), ignored);
        }
    }
}

// Link each file into a private snapshot; copy when hard links are unavailable.
// Return nothing on failure and set missing when a source file disappeared.
std::optional<fs::path> make_snapshot(const fs::path& root, const fs::path& from,
                                      const std::vector<std::string>& files, bool& missing) {
    const fs::path directory = root / (std::string(snapshot_prefix) + host_tag() + "-" +
                                       std::to_string(process_id()) + "-" + unique_suffix());
    std::error_code error;
    if (!fs::create_directory(directory, error))
        return std::nullopt;
    for (const auto& file : files) {
        const fs::path source = from / local_path(file);
        const fs::path target = directory / local_path(file);
        fs::create_directories(target.parent_path(), error);
        fs::create_hard_link(source, target, error);
        if (error) {
            error.clear();
            fs::copy_file(source, target, error);
        }
        if (error) {
            missing = !fs::exists(source, error);
            fs::remove_all(directory, error);
            return std::nullopt;
        }
    }
    return directory;
}

// Exclude opened time: it changes when a cached entry is read.
std::string snapshot_key(const fs::path& entry, const CacheStamp& stamp) {
    return utf8_path(entry) + "\n" + stamp.container + "\n" + stamp.key + "\n" + std::to_string(stamp.size) +
           "\n" + stamp.created;
}

struct Snapshots;
Snapshots& snapshots();

// Keep snapshots by entry version until this process exits. No callback runs
// under the lock, so fork() takes it first, as glibc does for malloc: a child
// never inherits it held by a thread it lacks.
struct Snapshots {
    std::mutex mutex;
    std::uint64_t pid = process_id();
    std::map<std::string, fs::path> kept;

    Snapshots() {
#ifndef _WIN32
        pthread_atfork([] { snapshots().mutex.lock(); }, [] { snapshots().mutex.unlock(); },
                       [] { new (&snapshots().mutex) std::mutex; });
#endif
    }

    ~Snapshots() {
        if (pid != process_id())
            return;
        std::error_code ignored;
        for (const auto& [key, directory] : kept)
            fs::remove_all(directory, ignored);
    }

    void reset_after_fork() {
        if (pid == process_id())
            return;
        pid = process_id();
        kept.clear();
    }

    std::optional<fs::path> find(const std::string& key, const std::vector<std::string>& files) {
        const auto found = kept.find(key);
        if (found == kept.end())
            return std::nullopt;
        if (complete(found->second, files))
            return found->second;
        std::error_code ignored;
        fs::remove_all(found->second, ignored);
        kept.erase(found);
        return std::nullopt;
    }
};

Snapshots& snapshots() {
    static Snapshots kept;
    return kept;
}

} // namespace

std::string read_local(const fs::path& path) {
    std::ifstream stream(path, std::ios::binary);
    if (!stream)
        throw Error(TACO_ERR_IO, "could not read " + utf8_path(path));
    std::ostringstream out;
    out << stream.rdbuf();
    return out.str();
}

void write_atomically(const fs::path& target, std::string_view bytes) {
    fs::path temporary = target;
    temporary += ".tmp-" + unique_suffix();
    {
        std::ofstream stream(temporary, std::ios::binary | std::ios::trunc);
        stream.write(bytes.data(), static_cast<std::streamsize>(bytes.size()));
        if (!stream)
            throw Error(TACO_ERR_IO, "could not write the metadata cache: " + utf8_path(temporary));
    }
    std::error_code error;
    fs::rename(temporary, target, error);
    if (error) {
        std::error_code ignored;
        fs::remove(temporary, ignored);
        throw Error(TACO_ERR_IO, "could not write the metadata cache " + utf8_path(target) + ": " + error.message());
    }
}

std::string cache_label(std::string_view text) {
    std::string out;
    for (const char c : text.substr(0, 64)) {
        const bool plain = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '.' ||
                           c == '_';
        out += plain ? c : '-';
    }
    return out.empty() ? "dataset" : out;
}

std::string cache_origin(std::string_view source) {
    if (!has_uri_scheme(source))
        return "local";
    std::string scheme(source.substr(0, source.find("://")));
    std::transform(scheme.begin(), scheme.end(), scheme.begin(), [](unsigned char c) { return std::tolower(c); });
    return scheme;
}

CacheEntry::CacheEntry(const std::string& root, const std::string& identity)
    : root_(local_path(root)), hash_(hex64(fnv1a64(identity)).substr(4)) {}

std::optional<CacheStamp> CacheEntry::find(const std::vector<std::string>& files, std::string_view key) {
    if (refreshing())
        return std::nullopt;
    std::error_code error;
    const std::string suffix = "-" + hash_;
    for (const auto& item : fs::directory_iterator(root_, error)) {
        const std::string name = utf8_path(item.path().filename());
        if (!name.ends_with(suffix) || !item.is_directory(error))
            continue;
        auto stamp = read_stamp(item.path());
        if (!stamp || (!key.empty() && stamp->key != key) || !complete(item.path(), files))
            continue;
        directory_ = item.path();
        stamp->opened = now_utc();
        // A read-only cache still serves its entries.
        try {
            write_atomically(directory_ / stamp_name, stamp_json(*stamp));
        } catch (const Error&) {
        }
        stamp_ = *stamp;
        return stamp;
    }
    return std::nullopt;
}

std::optional<fs::path> CacheEntry::snapshot(const std::vector<std::string>& files) const {
    auto& registry = snapshots();
    const std::lock_guard lock(registry.mutex);
    registry.reset_after_fork();
    const std::string key = snapshot_key(directory_, stamp_);
    if (auto kept = registry.find(key, files))
        return kept;
    bool missing = false;
    const auto made = make_snapshot(root_, directory_, files, missing);
    if (!made) {
        if (missing)
            return std::nullopt;
        return directory_;
    }
    // Another process may have replaced the entry while it was linked.
    const auto stamp = read_stamp(directory_);
    if (!stamp || snapshot_key(directory_, *stamp) != key) {
        std::error_code ignored;
        fs::remove_all(*made, ignored);
        return std::nullopt;
    }
    registry.kept.emplace(key, *made);
    return made;
}

fs::path CacheEntry::store(const std::string& label, const std::vector<std::pair<std::string, std::string>>& files,
                           CacheStamp stamp) {
    std::error_code error;
    fs::create_directories(root_, error);
    if (error)
        throw Error(TACO_ERR_IO, "could not create the metadata cache " + utf8_path(root_) + ": " + error.message());
    if (!fs::exists(root_ / "CACHEDIR.TAG", error))
        write_atomically(root_ / "CACHEDIR.TAG", cachedir_tag);

    // Build aside so readers never see a partial entry.
    const fs::path staging = root_ / (std::string(staging_prefix) + unique_suffix());
    std::vector<std::string> names;
    try {
        fs::create_directories(staging, error);
        stamp.size = 0;
        for (const auto& [name, content] : files) {
            const fs::path target = staging / local_path(name);
            fs::create_directories(target.parent_path(), error);
            write_atomically(target, content);
            stamp.size += content.size();
            names.push_back(name);
        }
        stamp.created = now_utc();
        stamp.opened = stamp.created;
        write_atomically(staging / stamp_name, stamp_json(stamp));
    } catch (...) {
        fs::remove_all(staging, error);
        throw;
    }
    const std::string suffix = "-" + hash_;
    directory_ = root_ / (label + suffix);
    // The private staging copy gives this writer a consistent snapshot.
    const std::string version = snapshot_key(directory_, stamp);
    bool missing = false;
    const auto kept = make_snapshot(root_, staging, names, missing);

    // Reuse a concurrent writer's complete entry unless refresh is requested.
    const bool refresh = refreshing();
    const auto current = [&](const fs::path& directory) {
        const auto found = read_stamp(directory);
        return !refresh && found && found->key == stamp.key && found->container == stamp.container &&
               complete(directory, names);
    };
    std::vector<fs::path> replaced;
    for (int attempt = 1;; ++attempt) {
        std::error_code renamed;
        fs::rename(staging, directory_, renamed);
        if (!renamed)
            break;
        if (current(directory_)) {
            fs::remove_all(staging, error);
            break;
        }
        if (attempt == store_attempts) {
            fs::remove_all(staging, error);
            for (const auto& old : replaced)
                fs::remove_all(old, error);
            if (kept)
                fs::remove_all(*kept, error);
            throw Error(TACO_ERR_IO,
                        "could not write the metadata cache " + utf8_path(directory_) + ": " + renamed.message());
        }
        // Move the old entry aside if the platform cannot swap directories.
        if (exchange(staging, directory_)) {
            replaced.push_back(staging);
            break;
        }
        const fs::path trash = root_ / (std::string(trash_prefix) + unique_suffix());
        std::error_code moved;
        fs::rename(directory_, trash, moved);
        if (!moved)
            replaced.push_back(trash);
    }
    for (const auto& old : replaced)
        fs::remove_all(old, error);
    // Remove stale entries of this source after publishing the new one.
    for (const auto& item : fs::directory_iterator(root_, error)) {
        if (item.path() != directory_ && utf8_path(item.path().filename()).ends_with(suffix) &&
            item.is_directory(error) && !current(item.path()))
            discard(item.path());
    }
    remove_abandoned_snapshots(root_);
    fs::path result = directory_;
    if (kept) {
        auto& registry = snapshots();
        const std::lock_guard lock(registry.mutex);
        registry.reset_after_fork();
        // Stamps have one-second precision; reuse a snapshot with the same key.
        if (const auto earlier = registry.find(version, names)) {
            fs::remove_all(*kept, error);
            result = *earlier;
        } else {
            registry.kept.emplace(version, *kept);
            result = *kept;
        }
    }

    // Least recently opened entries make room, never the one just written.
    const std::uint64_t cap = cache_size_cap();
    if (cap == 0)
        return result;
    struct Known {
        fs::path directory;
        CacheStamp stamp;
    };
    std::vector<Known> entries;
    std::uint64_t total = 0;
    for (const auto& item : fs::directory_iterator(root_, error)) {
        const std::string name = utf8_path(item.path().filename());
        // Ignore temporary directories when applying the size cap.
        if (name.starts_with(staging_prefix) || name.starts_with(trash_prefix) || name.starts_with(snapshot_prefix) ||
            !item.is_directory(error))
            continue;
        if (auto known = read_stamp(item.path())) {
            total += known->size;
            entries.push_back(Known{item.path(), *known});
        }
    }
    std::sort(entries.begin(), entries.end(),
              [](const Known& a, const Known& b) { return a.stamp.opened < b.stamp.opened; });
    for (const auto& entry : entries) {
        if (total <= cap)
            break;
        if (entry.directory == directory_)
            continue;
        discard(entry.directory);
        total -= entry.stamp.size;
    }
    return result;
}

std::string CacheEntry::path(std::string_view file) const {
    return utf8_path(directory_ / local_path(file));
}

} // namespace taco
