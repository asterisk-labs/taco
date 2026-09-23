#pragma once

// Resolves a TACO container and gathers what the SQL builder needs: local
// copies of the metadata levels, the collection and its contract.

#include <cstddef>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace taco {

enum class Container { zip, folder, tacocat };

struct Contract {
    std::vector<std::string> structure;
    // Every level with the user fields it declares, in COLLECTION.json order.
    std::vector<std::pair<std::string, std::vector<std::string>>> fields;
    bool has_derived = false;
    std::string derived;

    [[nodiscard]] const std::vector<std::string>* fields_of(std::string_view level) const;
};

struct Dataset {
    std::string source;
    Container container = Container::zip;
    // Where file locations start: the archive of a ZIP, the directory of a
    // FOLDER and the directory holding the partitions of a TACOCAT.
    std::string location_base;
    std::string collection;
    // Parents before children, each with a local Parquet file.
    std::vector<std::string> level_names;
    std::vector<std::string> level_paths;
    Contract contract;

    [[nodiscard]] std::size_t level_index(std::string_view name) const;
};

// cache_dir empty selects the default cache.
Dataset open_dataset(const std::string& source, const std::string& cache_dir);

// True when the URI shape can only name a backend root or explicitly names a
// directory. Such sources must not pay an object probe first.
bool is_explicit_remote_directory(std::string_view source);

// Where a remote directory is. karu only resolves URIs that name an object, so
// a bucket or repository root is located through its COLLECTION.json.
std::string remote_directory(const std::string& directory);

const char* container_name(Container container) noexcept;

} // namespace taco
