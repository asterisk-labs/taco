#pragma once

// Path, URI and SQL text helpers. Nothing here performs I/O.

#include <cstdint>
#include <filesystem>
#include <string>
#include <string_view>
#include <vector>

namespace taco {

// Paths cross the C API as UTF-8, whatever the platform's native encoding.
std::filesystem::path local_path(std::string_view utf8);
std::string utf8_path(const std::filesystem::path& path);

// scheme://, as opposed to a local path such as C:/data or /data.
bool has_uri_scheme(std::string_view path) noexcept;
// http:// or https://. Paths appended to such a URL are percent-encoded.
bool is_http_url(std::string_view uri) noexcept;
// Every component of a relative path encoded like JavaScript's
// encodeURIComponent, with '/' kept between them.
std::string encode_uri_path(std::string_view path);
// The local path a file:// URI names: the rest of the URI, percent-decoded.
// On Windows, file:///C:/data names C:/data.
std::string file_uri_path(std::string_view uri);

// The object path of a URI, without its query or fragment.
std::string_view without_query(std::string_view uri) noexcept;
// Safe for diagnostics: hides URI userinfo, queries, and fragments.
std::string redact_uri(std::string_view uri);

bool is_zip_name(std::string_view path) noexcept;
bool is_semver(std::string_view text);

std::string trim_trailing_slashes(std::string_view path);
std::string child_path(std::string_view directory, std::string_view child);
// The directory holding path, for a local path or a URI.
std::string parent_path(std::string_view path);
// The last component of a local path or a URI.
std::string source_name(std::string_view source);
// Names that tell partitions apart: base names when unique, else the sources.
std::vector<std::string> source_labels(const std::vector<std::string>& sources);
// A TACOCAT partition file: a normalized relative POSIX path ending in .zip,
// without an empty, '.' or '..' component or a backslash (SPEC 7.5).
bool is_partition_path(std::string_view path);

// The METADATA file of a level. There is no inverse: children/a_/b and
// children/a/_b share a file, so levels come from the contract.
std::string level_to_file(std::string_view level);
// sample for children, children for children/before, and so on.
std::string parent_level(std::string_view level);
std::string last_segment(std::string_view level);

std::uint64_t fnv1a64(std::string_view data, std::uint64_t hash = 0xCBF29CE484222325ULL) noexcept;
std::string hex64(std::uint64_t value);

// TACO_CACHE_DIR, then the platform cache directory.
std::string default_cache_dir();

std::string sql_identifier(std::string_view name);
std::string sql_literal(std::string_view value);
// A local path that DuckDB's glob matching resolves to exactly that file.
std::string glob_escape(std::string_view path);

} // namespace taco
