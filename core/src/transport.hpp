#pragma once

// Byte access through karu. Local paths, URIs and VSI paths all go through
// the same calls.

#include <cstdint>
#include <string>
#include <vector>

namespace taco {

struct Range {
    std::string uri;
    std::uint64_t offset = 0;
    std::uint64_t length = 0;
};

// karu's canonical form of a URI, such as /vsihf/ for hf://.
std::string canonical_uri(const std::string& uri);

std::uint64_t object_size(const std::string& uri);

// Gets several sizes concurrently, preserving input order.
std::vector<std::uint64_t> object_sizes(const std::vector<std::string>& uris);

// Reads every range in one karu batch, in request order.
std::vector<std::string> read_ranges(const std::vector<Range>& ranges);

// A whole object, refusing anything larger than limit bytes.
std::string read_object(const std::string& uri, std::uint64_t limit, const std::string& what);

// Like read_ranges, in bounded batches that report their progress as phase.
std::vector<std::string> download(const std::vector<Range>& ranges, const std::string& phase);

// A byte range copied into a local file. A zero length copies from offset to
// the end of the object.
struct Fetch {
    std::string uri;
    std::uint64_t offset = 0;
    std::uint64_t length = 0;
    std::string path;
};

constexpr std::uint64_t fetch_chunk_bytes = 8ULL * 1024 * 1024;

// Writes every fetch to its file, creating parent directories. Objects are
// read at most chunk_bytes at a time, so memory stays bounded whatever their
// size.
void fetch_files(const std::vector<Fetch>& fetches, std::uint64_t chunk_bytes = fetch_chunk_bytes);

// Stops the transport client cached by this thread.
void shutdown_transport() noexcept;

} // namespace taco
