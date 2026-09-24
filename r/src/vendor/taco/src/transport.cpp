#include "transport.hpp"

#include "error.hpp"
#include "paths.hpp"
#include "progress.hpp"

#include <karu/karu.h>

#include <algorithm>
#include <filesystem>
#include <fstream>
#include <functional>
#include <future>
#include <memory>

namespace fs = std::filesystem;

namespace taco {
namespace {

// Each thread reuses its connection pool while the transport configuration in
// the environment stays the same. A change is noticed on the next call.
thread_local std::shared_ptr<karu_client> cached_client;

struct LocatorDeleter {
    void operator()(karu_locator* locator) const noexcept { karu_locator_free(locator); }
};

struct ConfigDeleter {
    void operator()(karu_config* config) const noexcept { karu_config_free(config); }
};

using Locator = std::unique_ptr<karu_locator, LocatorDeleter>;

[[noreturn]] void transport_error(karu_status status, const std::string& action) {
    const char* detail = karu_last_error();
    std::string message = action + ": " + (detail && *detail ? detail : karu_status_string(status));
    switch (status) {
    case KARU_ERR_NOT_FOUND:
        throw Error(TACO_ERR_NOT_FOUND, message, status);
    case KARU_ERR_URI:
    case KARU_ERR_INVALID:
    case KARU_ERR_UNSUPPORTED:
        throw Error(TACO_ERR_INVALID, message, status);
    default:
        throw Error(TACO_ERR_IO, message, status);
    }
}

std::shared_ptr<karu_client> client() {
    karu_config* raw_config = nullptr;
    karu_status status = karu_config_create(&raw_config);
    if (status != KARU_OK)
        transport_error(status, "could not read the transport configuration");
    const std::unique_ptr<karu_config, ConfigDeleter> config(raw_config);

    if (cached_client) {
        int matches = 0;
        status = karu_client_matches_config(cached_client.get(), config.get(), &matches);
        if (status != KARU_OK)
            transport_error(status, "could not compare the transport configuration");
        if (matches)
            return cached_client;
    }

    karu_client* created = nullptr;
    status = karu_client_create(config.get(), &created);
    if (status != KARU_OK)
        transport_error(status, "could not create the transport");
    cached_client = std::shared_ptr<karu_client>(created, karu_client_free);
    return cached_client;
}

Locator resolve(const std::string& uri) {
    karu_locator* raw = nullptr;
    const karu_status status = karu_resolve(uri.c_str(), &raw);
    if (status != KARU_OK)
        transport_error(status, "could not resolve " + redact_uri(uri));
    return Locator(raw);
}

std::uint64_t size_with(const std::shared_ptr<karu_client>& transport, const std::string& uri) {
    const Locator locator = resolve(uri);
    std::uint64_t size = 0;
    const karu_status status = karu_client_size(transport.get(), locator.get(), &size);
    if (status != KARU_OK)
        transport_error(status, "could not open " + redact_uri(uri));
    return size;
}

} // namespace

std::string canonical_uri(const std::string& uri) {
    return karu_locator_uri(resolve(uri).get());
}

std::uint64_t object_size(const std::string& uri) {
    return size_with(client(), uri);
}

std::vector<std::uint64_t> object_sizes(const std::vector<std::string>& uris) {
    constexpr std::size_t batch_size = 8;
    std::vector<std::uint64_t> sizes(uris.size());
    if (uris.empty())
        return sizes;
    const auto transport = client();
    for (std::size_t start = 0; start < uris.size(); start += batch_size) {
        const auto stop = std::min(start + batch_size, uris.size());
        std::vector<std::future<std::uint64_t>> pending;
        pending.reserve(stop - start);
        for (std::size_t index = start; index < stop; ++index) {
            pending.push_back(std::async(std::launch::async, [transport, &uris, index] {
                return size_with(transport, uris[index]);
            }));
        }
        for (std::size_t index = start; index < stop; ++index)
            sizes[index] = pending[index - start].get();
    }
    return sizes;
}

std::vector<std::string> read_ranges(const std::vector<Range>& ranges) {
    std::vector<std::string> buffers(ranges.size());
    std::vector<Locator> locators;
    std::vector<karu_req> requests;
    for (std::size_t i = 0; i < ranges.size(); ++i) {
        if (ranges[i].length == 0)
            continue;
        locators.push_back(resolve(ranges[i].uri));
        buffers[i].resize(ranges[i].length);
        requests.push_back(karu_req{.locator = locators.back().get(),
                                    .offset = ranges[i].offset,
                                    .length = ranges[i].length,
                                    .buffer = buffers[i].data(),
                                    .tag = nullptr,
                                    .if_match = nullptr});
    }
    if (requests.empty())
        return buffers;
    const auto transport = client();
    const karu_status status = karu_client_fetch(transport.get(), requests.data(), requests.size());
    if (status != KARU_OK)
        transport_error(status, "could not read " + redact_uri(ranges.front().uri));
    return buffers;
}

std::string read_object(const std::string& uri, std::uint64_t limit, const std::string& what) {
    const std::uint64_t size = object_size(uri);
    if (size > limit)
        fail(what + " is larger than " + std::to_string(limit / (1024 * 1024)) +
             " MiB, refusing to read it: " + redact_uri(uri));
    return std::move(read_ranges({Range{uri, 0, size}}).front());
}

// Reads ranges in pieces of chunk_bytes, eight pieces per karu batch, handing
// each piece to sink in range order and reporting the bytes done under phase.
void read_in_batches(const std::vector<Range>& ranges, std::uint64_t chunk_bytes, const std::string& phase,
                     const std::function<void(std::size_t, std::string_view)>& sink) {
    if (chunk_bytes == 0)
        fail("read chunk size must be positive");
    struct Piece {
        std::size_t range;
        std::uint64_t offset;
        std::uint64_t length;
    };
    std::vector<Piece> pieces;
    std::uint64_t total = 0;
    for (std::size_t i = 0; i < ranges.size(); ++i) {
        total += ranges[i].length;
        for (std::uint64_t at = 0; at < ranges[i].length; at += chunk_bytes)
            pieces.push_back(Piece{i, ranges[i].offset + at, std::min(chunk_bytes, ranges[i].length - at)});
    }
    if (total == 0)
        return;
    const std::uint64_t batch_bytes = 8 * chunk_bytes;
    std::uint64_t done = 0;
    report_progress(phase, done, total);
    for (std::size_t start = 0; start < pieces.size();) {
        std::size_t stop = start;
        std::uint64_t bytes = 0;
        do {
            bytes += pieces[stop].length;
            ++stop;
        } while (stop < pieces.size() && bytes + pieces[stop].length <= batch_bytes);

        std::vector<Range> batch;
        for (std::size_t i = start; i < stop; ++i)
            batch.push_back(Range{ranges[pieces[i].range].uri, pieces[i].offset, pieces[i].length});
        const auto buffers = read_ranges(batch);
        for (std::size_t i = start; i < stop; ++i)
            sink(pieces[i].range, buffers[i - start]);
        done += bytes;
        report_progress(phase, done, total);
        start = stop;
    }
}

std::vector<std::string> download(const std::vector<Range>& ranges, const std::string& phase) {
    std::vector<std::string> buffers(ranges.size());
    for (std::size_t i = 0; i < ranges.size(); ++i)
        buffers[i].reserve(ranges[i].length);
    read_in_batches(ranges, fetch_chunk_bytes, phase,
                    [&](std::size_t range, std::string_view bytes) { buffers[range].append(bytes); });
    return buffers;
}

void fetch_files(const std::vector<Fetch>& fetches, std::uint64_t chunk_bytes) {
    std::vector<Range> ranges;
    for (const Fetch& fetch : fetches) {
        std::uint64_t length = fetch.length;
        if (length == 0) {
            const std::uint64_t size = object_size(fetch.uri);
            if (fetch.offset > size)
                fail("fetch offset " + std::to_string(fetch.offset) + " is past the end of " + redact_uri(fetch.uri));
            length = size - fetch.offset;
        }
        ranges.push_back(Range{fetch.uri, fetch.offset, length});

        const fs::path target = local_path(fetch.path);
        std::error_code error;
        fs::create_directories(target.parent_path(), error);
        if (!std::ofstream(target, std::ios::binary | std::ios::trunc))
            throw Error(TACO_ERR_IO, "could not write " + fetch.path);
    }

    // Pieces arrive in file order, so each file is appended from start to end.
    const std::string phase = "downloading " + std::to_string(fetches.size()) + (fetches.size() == 1 ? " file" : " files");
    read_in_batches(ranges, chunk_bytes, phase, [&](std::size_t index, std::string_view bytes) {
        const std::string& path = fetches[index].path;
        std::ofstream stream(local_path(path), std::ios::binary | std::ios::app);
        stream.write(bytes.data(), static_cast<std::streamsize>(bytes.size()));
        if (!stream)
            throw Error(TACO_ERR_IO, "could not write " + path);
    });
}

void shutdown_transport() noexcept {
    cached_client.reset();
}

} // namespace taco
