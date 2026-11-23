#include "cozip_index.hpp"

#include "error.hpp"
#include "transport.hpp"

#include <algorithm>
#include <cstring>
#include <set>

namespace taco {
namespace {

// The __cozip__ payload starts after its fixed 51-byte Local File Header.
constexpr std::uint64_t lfh_size = 51;
constexpr std::uint64_t index_header_size = 11;
// name length (2) + offset (8) + size (8) + at least one name byte
constexpr std::uint64_t entry_min_size = 19;
constexpr std::uint64_t hash_window_size = 32768;
constexpr std::uint64_t minimum_size = lfh_size + hash_window_size;
constexpr std::uint64_t bootstrap_size = 65536;
constexpr std::uint64_t fnv_offset_basis = 0xCBF29CE484222325ULL;
constexpr std::uint32_t lfh_signature = 0x04034B50U;
constexpr std::uint16_t integrity_header_id = 0xCA0C;
constexpr std::uint16_t format_version = 1;
constexpr std::string_view index_name = "__cozip__";
constexpr std::string_view padding_name = "__cozip_padding__";

std::uint16_t read_u16(const std::string& data, std::uint64_t at) {
    return static_cast<std::uint16_t>(static_cast<unsigned char>(data[at]) |
                                      (static_cast<unsigned char>(data[at + 1]) << 8));
}

std::uint32_t read_u32(const std::string& data, std::uint64_t at) {
    return static_cast<std::uint32_t>(read_u16(data, at)) |
           (static_cast<std::uint32_t>(read_u16(data, at + 2)) << 16);
}

std::uint64_t read_u64(const std::string& data, std::uint64_t at) {
    return static_cast<std::uint64_t>(read_u32(data, at)) |
           (static_cast<std::uint64_t>(read_u32(data, at + 4)) << 32);
}

std::uint64_t fnv1a(std::string_view data, std::uint64_t hash) noexcept {
    for (const char c : data) {
        hash ^= static_cast<unsigned char>(c);
        hash *= 0x100000001B3ULL;
    }
    return hash;
}

void check_minimum_size(std::uint64_t file_size, const std::string& uri) {
    if (file_size < minimum_size)
        fail("cozip archive too small (minimum is " + std::to_string(minimum_size) +
             " bytes): " + uri);
}

// Validate the fixed portion of the __cozip__ local file header.
std::uint8_t parse_profile_prefix(const std::string& head, const std::string& uri) {
    if (head.size() < lfh_size + 7)
        fail("cozip profile prefix is truncated: " + uri);
    if (read_u32(head, 0) != lfh_signature)
        fail("byte 0 is not a ZIP Local File Header: " + uri);
    // Encryption, data descriptor, strong encryption and masked headers all
    // make the fast path unreadable.
    if (read_u16(head, 6) & 0x2049)
        fail("__cozip__ has a forbidden general purpose bit set: " + uri);
    if (read_u16(head, 8) != 0)
        fail("__cozip__ compression method is not STORE: " + uri);
    const auto compressed = read_u32(head, 18);
    const auto uncompressed = read_u32(head, 22);
    if (compressed != uncompressed || compressed == 0 || compressed == 0xFFFFFFFFU)
        fail("__cozip__ sizes are not an equal, non-zero ZIP32 pair: " + uri);
    if (read_u16(head, 26) != index_name.size() || read_u16(head, 28) != 12)
        fail("LFH does not match cozip layout: " + uri);
    if (std::string_view(head).substr(30, index_name.size()) != index_name)
        fail("first ZIP entry is not __cozip__: " + uri);
    if (read_u16(head, 39) != integrity_header_id || read_u16(head, 41) != 8)
        fail("cozip integrity extra field (0xCA0C) missing: " + uri);
    if (std::string_view(head).substr(lfh_size, 4) != "CZIP")
        fail("index payload magic is not 'CZIP': " + uri);
    const auto version = read_u16(head, lfh_size + 4);
    if (version != format_version)
        fail("unsupported cozip format version " + std::to_string(version) + ": " + uri);
    return static_cast<std::uint8_t>(head[lfh_size + 6]);
}

void validate_name(const std::string& name, const std::string& uri) {
    if (name.empty())
        fail("cozip index has an empty name: " + uri);
    for (const char c : name) {
        const auto byte = static_cast<unsigned char>(c);
        if (byte < 0x01 || byte > 0x7F)
            fail("cozip index name '" + name + "' is not ASCII: " + uri);
    }
    if (name == index_name || name == padding_name)
        fail("cozip index lists the reserved name '" + name + "': " + uri);
    if (name.front() == '/' || name.back() == '/')
        fail("cozip index name '" + name + "' starts or ends with '/': " + uri);
    if (name.size() > 1 && std::isalpha(static_cast<unsigned char>(name[0])) && name[1] == ':')
        fail("cozip index name '" + name + "' has a drive letter: " + uri);
    if (name.find('\\') != std::string::npos)
        fail("cozip index name '" + name + "' contains a backslash: " + uri);
    std::size_t start = 0;
    for (;;) {
        const auto stop = name.find('/', start);
        const auto component = name.substr(start, stop == std::string::npos ? stop : stop - start);
        if (component == "." || component == "..")
            fail("cozip index name '" + name + "' has a '" + component + "' component: " + uri);
        if (stop == std::string::npos)
            break;
        start = stop + 1;
    }
}

} // namespace

const CozipEntry* CozipIndex::find(std::string_view name) const {
    for (const auto& entry : entries) {
        if (entry.name == name)
            return &entry;
    }
    return nullptr;
}

std::uint8_t read_cozip_profile(const std::string& uri) {
    check_minimum_size(object_size(uri), uri);
    return parse_profile_prefix(read_ranges({Range{uri, 0, lfh_size + 7}}).front(), uri);
}

CozipIndex read_cozip_index(const std::string& uri) {
    return read_cozip_index(uri, object_size(uri));
}

CozipIndex read_cozip_index(const std::string& uri, std::uint64_t file_size) {
    CozipIndex index;
    index.file_size = file_size;
    check_minimum_size(index.file_size, uri);

    // The prefix and the trailing window covered by the integrity hash arrive
    // in one batch.
    const auto bootstrap = std::min(bootstrap_size, index.file_size);
    const auto suffix_start = index.file_size - hash_window_size;
    auto parts = read_ranges({Range{uri, 0, bootstrap}, Range{uri, suffix_start, hash_window_size}});
    std::string head = std::move(parts[0]);
    const std::string suffix = std::move(parts[1]);

    index.profile = parse_profile_prefix(head, uri);
    const std::uint64_t payload_size = read_u32(head, 18);
    if (payload_size < index_header_size)
        fail("cozip index payload is smaller than its own header: " + uri);
    const std::uint64_t payload_end = lfh_size + payload_size;
    if (payload_end > index.file_size)
        fail("cozip index payload is truncated: " + uri);
    if (payload_end > head.size())
        head += read_ranges({Range{uri, head.size(), payload_end - head.size()}}).front();

    // Hash the index payload and trailing 32 KiB, counting their overlap once.
    const std::string_view payload = std::string_view(head).substr(lfh_size, payload_size);
    std::uint64_t hash = fnv1a(payload, fnv_offset_basis);
    if (payload_end <= suffix_start)
        hash = fnv1a(suffix, hash);
    else
        hash = fnv1a(std::string_view(suffix).substr(payload_end - suffix_start), hash);
    index.integrity = read_u64(head, 43);
    if (hash != index.integrity)
        fail("cozip integrity hash mismatch: " + uri);

    const std::uint64_t count = read_u32(head, lfh_size + 7);
    if (count > payload_size / entry_min_size)
        fail("cozip index declares more entries than fit in its payload: " + uri);

    std::uint64_t cursor = lfh_size + index_header_size;
    const auto need = [&](std::uint64_t bytes, const char* section) {
        if (cursor + bytes > payload_end)
            fail(std::string("cozip index is truncated in its ") + section + ": " + uri);
    };
    std::vector<std::uint16_t> lengths(count);
    for (auto& length : lengths) {
        need(2, "name lengths");
        length = read_u16(head, cursor);
        if (length == 0)
            fail("cozip index has an empty name: " + uri);
        cursor += 2;
    }
    index.entries.resize(count);
    for (std::uint64_t i = 0; i < count; ++i) {
        need(lengths[i], "names");
        index.entries[i].name = head.substr(cursor, lengths[i]);
        cursor += lengths[i];
    }
    for (auto& entry : index.entries) {
        need(8, "offsets");
        entry.offset = read_u64(head, cursor);
        cursor += 8;
    }
    for (auto& entry : index.entries) {
        need(8, "sizes");
        entry.size = read_u64(head, cursor);
        cursor += 8;
    }
    if (cursor != payload_end)
        fail("cozip index sections do not match its declared size: " + uri);

    std::set<std::string> seen;
    for (const auto& entry : index.entries) {
        validate_name(entry.name, uri);
        if (!seen.insert(entry.name).second)
            fail("cozip index lists '" + entry.name + "' twice: " + uri);
        // Reject entries whose byte range leaves the archive.
        if (entry.size == 0 || entry.offset < lfh_size || entry.offset > index.file_size ||
            entry.size > index.file_size - entry.offset)
            fail("cozip index entry '" + entry.name + "' has a range outside the archive: " + uri);
    }
    return index;
}

std::string profile_name(std::uint8_t profile) {
    switch (profile) {
    case cozip_profile_none:
        return "none";
    case cozip_profile_flat:
        return "flat";
    case cozip_profile_taco:
        return "taco";
    default:
        return "unknown:" + std::to_string(profile);
    }
}

} // namespace taco
