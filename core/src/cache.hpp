#pragma once

// The metadata cache. Every entry is a directory named <label>-<hash> under
// the cache root, laid out like a TACO FOLDER without DATA/: COLLECTION.json,
// METADATA/ and a taco-cache.json stamp. The label names the dataset for
// people; the hash names the source for lookups.
//
// Cached remote metadata is revalidated against an origin-provided value.
// TACO_CACHE_REFRESH rebuilds every entry it touches. TACO_CACHE_SIZE caps
// the cache in bytes; the least recently opened entries go first.

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
    // How the entry stays valid: origin-reported remote sizes, or the size
    // and modification time of a local archive.
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

    // Writes the files and the stamp as <label>-<hash>, replacing any entry
    // of this source, then trims the cache to its size cap.
    void store(const std::string& label, const std::vector<std::pair<std::string, std::string>>& files,
               CacheStamp stamp);

    [[nodiscard]] std::string path(std::string_view file) const;
    // The METADATA Parquet files of the entry, by name.
    [[nodiscard]] std::vector<std::string> parquet_files() const;

  private:
    std::filesystem::path root_;
    std::string hash_;
    std::filesystem::path directory_;
};

// A file system friendly form of a name for use in entry labels.
std::string cache_label(std::string_view text);

// The scheme of a URI, or "local".
std::string cache_origin(std::string_view source);

std::string read_local(const std::filesystem::path& path);
void write_atomically(const std::filesystem::path& target, std::string_view bytes);

} // namespace taco
