#pragma once

// The index stored at byte zero of a CoZIP archive.

#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

namespace taco {

inline constexpr std::uint8_t cozip_profile_none = 0;
inline constexpr std::uint8_t cozip_profile_flat = 1;
inline constexpr std::uint8_t cozip_profile_taco = 2;

struct CozipEntry {
    std::string name;
    std::uint64_t offset = 0;
    std::uint64_t size = 0;
};

struct CozipIndex {
    std::uint8_t profile = cozip_profile_none;
    std::uint64_t file_size = 0;
    // FNV-1a over the index and the trailing window; it changes with the archive.
    std::uint64_t integrity = 0;
    std::vector<CozipEntry> entries;

    [[nodiscard]] const CozipEntry* find(std::string_view name) const;
};

// Reads and verifies the index. Two range reads for a typical archive.
CozipIndex read_cozip_index(const std::string& uri);

// The same, for a caller that already knows the archive size.
CozipIndex read_cozip_index(const std::string& uri, std::uint64_t file_size);

// Reads only the fixed prefix that holds the profile byte.
std::uint8_t read_cozip_profile(const std::string& uri);

std::string profile_name(std::uint8_t profile);

} // namespace taco
