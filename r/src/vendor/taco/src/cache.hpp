#pragma once

// The metadata cache. Every entry is a directory named <label>-<hash> under
// the cache root, laid out like a TACO FOLDER without DATA/: COLLECTION.json,
// METADATA/ and a taco-cache.json stamp. The label names the dataset for
// people; the hash names the source for lookups.
//
// Cached remote metadata is revalidated against an origin-provided value.
// TACO_CACHE_REFRESH rebuilds every entry it touches. TACO_CACHE_SIZE caps
// the cache in bytes; the least recently opened entries go first.
//
// An open dataset uses a private snapshot under .open-<host>-<pid>-<n>.
// Hard links, or copies when linking fails, keep its metadata available after
// cache replacement or eviction. The process retains snapshots until exit so
// generated SQL can outlive a Dataset object.

#include <cstdint>
#include <filesystem>
#include <optional>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace taco {

struct CacheStamp {
    std::string source;
    std::string container;
    // How the entry stays valid at its remote origin.
    std::string key;
    std::uint64_t size = 0;
    std::string created;
    std::string opened;
};

class CacheEntry {
  public:
    CacheEntry(const std::string& root, const std::string& identity);

    // The stamp of a complete entry, with its opened time refreshed. key
    // must match the stored one unless it is empty. Nothing while the entry
    // is missing, damaged, incomplete or being refreshed.
    std::optional<CacheStamp> find(const std::vector<std::string>& files, std::string_view key);

    // Writes the files and the stamp as <label>-<hash>, replacing any
    // outdated entry of this source, then trims the cache to its size cap.
    // Reuse a concurrent writer's complete entry unless refreshing. Return a
    // snapshot of the stored files.
    std::filesystem::path store(const std::string& label,
                                const std::vector<std::pair<std::string, std::string>>& files, CacheStamp stamp);

    // Snapshot the entry found by find(). Return nothing if it changed or
    // disappeared. Read it in place when the cache is read-only.
    [[nodiscard]] std::optional<std::filesystem::path> snapshot(const std::vector<std::string>& files) const;

    [[nodiscard]] std::string path(std::string_view file) const;

  private:
    std::filesystem::path root_;
    std::string hash_;
    std::filesystem::path directory_;
    // Compare with the current stamp before using a snapshot.
    CacheStamp stamp_;
};

// A file system friendly form of a name for use in entry labels.
std::string cache_label(std::string_view text);

// The scheme of a URI, or "local".
std::string cache_origin(std::string_view source);

std::string read_local(const std::filesystem::path& path);
void write_atomically(const std::filesystem::path& target, std::string_view bytes);

} // namespace taco
