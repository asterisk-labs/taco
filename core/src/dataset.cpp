#include "dataset.hpp"

#include "cache.hpp"
#include "cozip_index.hpp"
#include "error.hpp"
#include "json.hpp"
#include "paths.hpp"
#include "progress.hpp"
#include "rumi.hpp"
#include "transport.hpp"

#include <karu/karu.h>

#include <algorithm>
#include <charconv>
#include <exception>
#include <filesystem>
#include <fstream>
#include <mutex>
#include <new>
#include <optional>
#include <random>
#include <set>
#include <sstream>
#include <unordered_map>

#ifdef _WIN32
#include <process.h>
#else
#include <pthread.h>
#include <unistd.h>
#endif

namespace fs = std::filesystem;

namespace taco {
namespace {

constexpr std::string_view collection_name = "COLLECTION.json";
constexpr std::string_view metadata_prefix = "METADATA/";
constexpr std::string_view parquet_suffix = ".parquet";
constexpr std::string_view supported_version = "3.0.0";
constexpr std::uint64_t collection_limit = 64ULL * 1024 * 1024;
constexpr std::uint64_t max_sequence_bound = (1ULL << 53) - 1;

struct Level {
    std::string name;
    // Its Parquet file, under METADATA/ or beside COLLECTION.json in a TACOCAT.
    std::string file;
};

std::string utf8(const fs::path& path) {
    return utf8_path(path);
}

std::size_t level_depth(std::string_view level) {
    return level == "sample" ? 0 : 1 + static_cast<std::size_t>(std::count(level.begin(), level.end(), '/'));
}

void validate_structure_leaf(const std::string& declaration, const std::string& source) {
    const auto star = declaration.find('*');
    if (star == std::string::npos) {
        if (declaration.find_first_of("[]") != std::string::npos)
            fail("COLLECTION.json: malformed variable leaf '" + declaration + "': " + source);
        return;
    }

    const auto open = declaration.find('[', star);
    const auto comma = declaration.find(',', open == std::string::npos ? star : open);
    const auto close = declaration.find(']', comma == std::string::npos ? star : comma);
    const auto slash = declaration.rfind('/', star);
    const auto basename = slash == std::string::npos ? 0 : slash + 1;
    if (star == basename || declaration.find_first_of("*[]", basename) != star || open != star + 1 ||
        comma == std::string::npos || close == std::string::npos ||
        declaration.find_first_of("*[]", close + 1) != std::string::npos) {
        fail("COLLECTION.json: malformed variable leaf '" + declaration + "': " + source);
    }

    // Decimal digits without a sign, spaces or leading zeros, at most
    // 2^53 - 1 so that every reader holds the bound exactly.
    const auto parse_bound = [&](std::string_view text) {
        std::uint64_t value = 0;
        const auto [end, error] = std::from_chars(text.data(), text.data() + text.size(), value);
        if (text.empty() || error != std::errc() || end != text.data() + text.size() ||
            (text.size() > 1 && text.front() == '0') || value > max_sequence_bound)
            fail("COLLECTION.json: malformed variable leaf '" + declaration + "': " + source);
        return value;
    };

    const auto minimum = parse_bound(std::string_view(declaration).substr(open + 1, comma - open - 1));
    const auto maximum = parse_bound(std::string_view(declaration).substr(comma + 1, close - comma - 1));
    if (minimum < 1)
        fail("COLLECTION.json: variable leaf '" + declaration + "' must require at least one file: " + source);
    if (minimum > maximum)
        fail("COLLECTION.json: variable leaf '" + declaration + "' has min > max: " + source);
}

void sort_and_validate(std::vector<Level>& levels, const std::string& source) {
    if (levels.empty())
        fail("no METADATA Parquet files found: " + source);
    // Parents come before their children so the join chain resolves.
    std::sort(levels.begin(), levels.end(), [](const Level& a, const Level& b) {
        const auto da = level_depth(a.name);
        const auto db = level_depth(b.name);
        return da != db ? da < db : a.name < b.name;
    });
    if (levels[0].name != "sample")
        fail("TACO dataset has no sample.parquet: " + source);
    for (std::size_t i = 1; i < levels.size(); ++i) {
        const auto& name = levels[i].name;
        if (name != "children" && !name.starts_with("children/"))
            fail("METADATA level '" + name + "' is not 'children' or below it: " + source);
        const auto parent = parent_level(name);
        const auto found = std::find_if(levels.begin(), levels.begin() + static_cast<std::ptrdiff_t>(i),
                                        [&](const Level& level) { return level.name == parent; });
        if (found == levels.begin() + static_cast<std::ptrdiff_t>(i))
            fail("METADATA level '" + name + "' has no parent level '" + parent + "': " + source);
    }
    if (levels.size() > 1 && levels[1].name != "children")
        fail("TACO dataset has child levels but no children.parquet: " + source);
}

Contract read_contract(const std::string& collection, const std::string& source);

// The levels COLLECTION.json declares, parents first. A file name cannot be
// read back as a level: children/a_/b and children/a/_b share one.
std::vector<Level> declared_levels(const Contract& contract, const std::string& source) {
    const auto folded = [](std::string text) {
        for (char& c : text)
            c = c >= 'A' && c <= 'Z' ? static_cast<char>(c - 'A' + 'a') : c;
        return text;
    };
    std::vector<Level> levels;
    for (const auto& [name, fields] : contract.fields) {
        Level level{name, level_to_file(name)};
        // Names that differ only in case would share a file on most file systems.
        for (const auto& other : levels) {
            if (folded(other.file) == folded(level.file))
                fail("COLLECTION.json levels '" + other.name + "' and '" + name + "' would share the Parquet file " +
                     level.file + ": " + source);
        }
        levels.push_back(std::move(level));
    }
    sort_and_validate(levels, source);
    if (levels.size() < 2)
        fail("COLLECTION.json structure requires sample and children metadata levels: " + source);
    return levels;
}

// The Parquet files must be exactly those of the levels.
void match_files(const std::vector<Level>& levels, const std::vector<std::string>& files,
                 const std::string& source) {
    for (const auto& level : levels) {
        if (std::find(files.begin(), files.end(), level.file) == files.end())
            fail("TACO dataset has no " + level.file + " for metadata level '" + level.name + "': " + source);
    }
    for (const auto& file : files) {
        if (std::none_of(levels.begin(), levels.end(), [&](const Level& level) { return level.file == file; }))
            fail("Parquet file " + file + " matches no metadata level in COLLECTION.json: " + source);
    }
}

std::vector<std::string> read_partitions(const std::string& collection, const std::string& source);

// Reads the contract and levels of dataset.collection. files are the Parquet
// files found in directory.
void read_levels(Dataset& dataset, const fs::path& directory, const std::vector<std::string>& files) {
    dataset.contract = read_contract(dataset.collection, dataset.source);
    const auto levels = declared_levels(dataset.contract, dataset.source);
    match_files(levels, files, dataset.source);
    for (const auto& level : levels) {
        dataset.level_names.push_back(level.name);
        dataset.level_paths.push_back(utf8(directory / local_path(level.file)));
    }
    if (dataset.container == Container::tacocat)
        dataset.partitions = read_partitions(dataset.collection, dataset.source);
}

std::vector<std::string> parquet_names(const fs::path& directory) {
    std::vector<std::string> names;
    std::error_code error;
    for (const auto& entry : fs::directory_iterator(directory, error)) {
        const std::string name = utf8(entry.path().filename());
        if (entry.is_regular_file(error) && name.ends_with(parquet_suffix))
            names.push_back(name);
    }
    if (error)
        throw Error(TACO_ERR_IO, "could not list " + utf8(directory) + ": " + error.message());
    return names;
}

// Local paths stay as the caller wrote them, and a file:// URI becomes the
// path it names. Other URIs take karu's canonical form, and HTTP keeps the
// /vsicurl/ prefix that GDAL expects.
std::string location_base(const std::string& path) {
    if (!has_uri_scheme(path))
        return path;
    if (path.starts_with("file://"))
        return file_uri_path(path);
    std::string canonical = canonical_uri(path);
    if (canonical.starts_with("http://") || canonical.starts_with("https://"))
        return "/vsicurl/" + canonical;
    return canonical;
}

// Encode child paths for HTTP and file://, whose paths are percent-decoded;
// other remote schemes use the original path.
std::string remote_child(const std::string& directory, const std::string& file) {
    const bool encoded = is_http_url(directory) || directory.starts_with("file://");
    return child_path(directory, encoded ? encode_uri_path(file) : file);
}

// The CoZIP hash covers the metadata-bearing archive tail. Size alone cannot
// identify a version because CoZIP pads small archives to a fixed size.
std::string remote_zip_key(std::uint64_t integrity) {
    return "remote cozip " + hex64(integrity);
}

// Local keys use size and mtime; remote keys use the CoZIP integrity hash.
// A failed remote probe prevents opening with unverifiable cached metadata.
std::string cache_key(const std::string& source) {
    if (has_uri_scheme(source))
        return remote_zip_key(read_cozip_integrity(source));
    std::error_code error;
    const auto path = local_path(source);
    const auto size = fs::file_size(path, error);
    const auto written = fs::last_write_time(path, error);
    if (error)
        return "local unknown";
    return "local " + std::to_string(size) + " " +
           std::to_string(static_cast<long long>(written.time_since_epoch().count()));
}

std::string expected_key(const std::string& source) {
    return cache_key(source);
}

std::string remote_sizes_key(const std::vector<std::uint64_t>& sizes) {
    std::string key = "remote sizes";
    for (const auto size : sizes)
        key += " " + std::to_string(size);
    return key;
}

std::string remote_sizes_key(const std::vector<std::string>& sources) {
    return remote_sizes_key(object_sizes(sources));
}

std::optional<Container> parse_container(const std::string& name) {
    for (const auto container : {Container::zip, Container::folder, Container::tacocat}) {
        if (name == container_name(container))
            return container;
    }
    return std::nullopt;
}

CacheStamp make_stamp(const std::string& source, Container container, std::string key) {
    CacheStamp stamp;
    stamp.source = source;
    stamp.container = container_name(container);
    stamp.key = std::move(key);
    return stamp;
}

// Keep an open remote dataset independent of cache replacement or eviction.
Dataset cached_dataset(const std::string& source, Container container, std::string location_base,
                       const fs::path& snapshot) {
    Dataset dataset;
    dataset.source = source;
    dataset.container = container;
    dataset.location_base = std::move(location_base);
    dataset.collection = read_local(snapshot / collection_name);
    read_levels(dataset, snapshot / "METADATA", parquet_names(snapshot / "METADATA"));
    return dataset;
}

std::string cache_identity(const std::string& source) {
    if (has_uri_scheme(source))
        return canonical_uri(source);
    std::error_code error;
    const auto absolute = fs::absolute(local_path(source), error);
    return error ? source : utf8(absolute.lexically_normal());
}

std::string metadata_phase(const std::string& source) {
    std::string name = source_name(source);
    if (name == ".tacocat")
        name = source_name(parent_path(source));
    return "downloading " + name + " metadata";
}

std::string entry_label(const std::string& collection, Container container, const std::string& source);

std::uint64_t process_id() noexcept {
#ifdef _WIN32
    return static_cast<std::uint64_t>(_getpid());
#else
    return static_cast<std::uint64_t>(getpid());
#endif
}

fs::path temporary_directory() {
    std::error_code error;
    const fs::path base = fs::temp_directory_path(error);
    if (error)
        throw Error(TACO_ERR_IO, "could not find a temporary directory: " + error.message());
    std::random_device random;
    for (int attempt = 0; attempt < 16; ++attempt) {
        const fs::path candidate = base / ("taco-" + hex64((std::uint64_t{random()} << 32) | random()));
        if (fs::create_directory(candidate, error))
            return candidate;
    }
    throw Error(TACO_ERR_IO, "could not create a temporary directory in " + utf8(base));
}

void write_extracted(const fs::path& target, std::string_view bytes) {
    std::error_code error;
    fs::create_directories(target.parent_path(), error);
    std::ofstream stream(target, std::ios::binary | std::ios::trunc);
    stream.write(bytes.data(), static_cast<std::streamsize>(bytes.size()));
    if (error || !stream)
        throw Error(TACO_ERR_IO, "could not extract ZIP metadata to " + utf8(target));
}

Dataset extracted_dataset(const std::string& source, const fs::path& directory) {
    Dataset dataset;
    dataset.source = source;
    dataset.container = Container::zip;
    dataset.location_base = location_base(source);
    dataset.collection = read_local(directory / collection_name);
    read_levels(dataset, directory / "METADATA", parquet_names(directory / "METADATA"));
    return dataset;
}

void store_extracted(const fs::path& directory, const std::vector<std::string>& names,
                     const std::vector<std::string>& contents) {
    for (std::size_t i = 0; i < names.size(); ++i)
        write_extracted(directory / local_path(names[i]), contents[i]);
}

struct LocalExtractions;
LocalExtractions& local_extractions();

// The local ZIPs this process extracted. The lock guards short updates, never
// archive reads or progress callbacks, so fork() can take it first, as glibc
// does for malloc: a child never inherits it held by a thread it lacks.
struct LocalExtractions {
    std::mutex mutex;
    std::uint64_t pid = process_id();
    fs::path root;
    std::unordered_map<std::string, fs::path> entries;

    LocalExtractions() {
#ifndef _WIN32
        pthread_atfork([] { local_extractions().mutex.lock(); }, [] { local_extractions().mutex.unlock(); },
                       [] { new (&local_extractions().mutex) std::mutex; });
#endif
    }

    ~LocalExtractions() {
        if (!root.empty() && pid == process_id()) {
            std::error_code ignored;
            fs::remove_all(root, ignored);
        }
    }

    void reset_after_fork() {
        const auto current = process_id();
        if (pid == current)
            return;
        pid = current;
        root.clear();
        entries.clear();
    }

    std::optional<fs::path> find(const std::string& key) {
        const std::lock_guard lock(mutex);
        reset_after_fork();
        if (const auto found = entries.find(key); found != entries.end())
            return found->second;
        return std::nullopt;
    }

    // Keeps directory as the extraction of key, unless another thread kept
    // one first; then directory goes and that one is returned.
    fs::path add(const std::string& key, const fs::path& directory) {
        fs::path kept;
        {
            const std::lock_guard lock(mutex);
            reset_after_fork();
            kept = entries.emplace(key, directory).first->second;
        }
        if (kept != directory) {
            std::error_code ignored;
            fs::remove_all(directory, ignored);
        }
        return kept;
    }

    fs::path create_entry() {
        const std::lock_guard lock(mutex);
        reset_after_fork();
        if (root.empty())
            root = temporary_directory();
        std::random_device random;
        std::error_code error;
        for (int attempt = 0; attempt < 16; ++attempt) {
            const fs::path candidate = root / hex64((std::uint64_t{random()} << 32) | random());
            if (fs::create_directory(candidate, error))
                return candidate;
        }
        throw Error(TACO_ERR_IO, "could not create a temporary directory in " + utf8(root));
    }
};

LocalExtractions& local_extractions() {
    static LocalExtractions extractions;
    return extractions;
}

Dataset open_zip(const std::string& source, const std::string& cache_root) {
    const bool remote = has_uri_scheme(source);
    std::optional<CacheEntry> cache;
    LocalExtractions* local = nullptr;
    std::string local_key;
    if (remote) {
        cache.emplace(cache_root, cache_identity(source));
    } else {
        local = &local_extractions();
        local_key = cache_identity(source) + "\n" + expected_key(source);
        if (const auto found = local->find(local_key))
            return extracted_dataset(source, *found);
    }

    const CozipIndex index = read_cozip_index(source);
    if (index.profile != cozip_profile_taco)
        fail("taco needs a TACO-profile archive (profile=2). Got profile=" + profile_name(index.profile) +
             " in: " + source);
    const CozipEntry* collection = index.find(collection_name);
    if (!collection)
        fail("TACO archive has no COLLECTION.json in its cozip index: " + source);
    if (collection->size > collection_limit)
        fail("COLLECTION.json is larger than 64 MiB, refusing to read it: " + source);

    // Every METADATA Parquet file comes in one batch with COLLECTION.json,
    // which then says the levels they must be.
    std::vector<std::string> names = {std::string(collection_name)};
    std::vector<std::string> parquet;
    std::vector<Range> ranges = {Range{source, collection->offset, collection->size}};
    for (const auto& entry : index.entries) {
        if (entry.name.starts_with(metadata_prefix) && entry.name.ends_with(parquet_suffix)) {
            names.push_back(entry.name);
            parquet.push_back(entry.name.substr(metadata_prefix.size()));
            ranges.push_back(Range{source, entry.offset, entry.size});
        }
    }
    if (parquet.empty())
        fail("no METADATA Parquet files found: " + source);
    // Openers that missed the cache together use the entry the first one stored.
    if (remote) {
        if (const auto stamp = cache->find(names, remote_zip_key(index.integrity)); stamp && stamp->container == "zip") {
            if (const auto snapshot = cache->snapshot(names))
                return cached_dataset(source, Container::zip, location_base(source), *snapshot);
        }
    }
    const auto contents = download(ranges, metadata_phase(source));
    match_files(declared_levels(read_contract(contents[0], source), source), parquet, source);
    if (!remote) {
        const fs::path directory = local->create_entry();
        try {
            store_extracted(directory, names, contents);
        } catch (...) {
            std::error_code ignored;
            fs::remove_all(directory, ignored);
            throw;
        }
        return extracted_dataset(source, local->add(local_key, directory));
    }

    std::vector<std::pair<std::string, std::string>> files;
    for (std::size_t i = 0; i < names.size(); ++i)
        files.emplace_back(names[i], contents[i]);
    const auto snapshot = cache->store(entry_label(contents[0], Container::zip, source), files,
                                       make_stamp(source, Container::zip, remote_zip_key(index.integrity)));
    return cached_dataset(source, Container::zip, location_base(source), snapshot);
}

Dataset open_local_directory(const std::string& source) {
    std::string directory = trim_trailing_slashes(source);
    fs::path root = local_path(directory);
    std::error_code error;
    if (!fs::is_regular_file(root / collection_name, error)) {
        const fs::path catalog = root / ".tacocat";
        if (!fs::is_regular_file(catalog / collection_name, error))
            fail("directory has no COLLECTION.json or .tacocat/COLLECTION.json: " + directory);
        root = catalog;
        directory = utf8(root);
    }

    // Catalog source paths are relative to the directory that contains .tacocat.
    const bool folder = fs::is_directory(root / "METADATA", error);
    Dataset dataset;
    dataset.source = directory;
    dataset.container = folder ? Container::folder : Container::tacocat;
    dataset.location_base = folder ? directory : parent_path(directory);

    if (fs::file_size(root / collection_name, error) > collection_limit)
        fail("COLLECTION.json is larger than 64 MiB, refusing to read it: " + directory);
    dataset.collection = read_local(root / collection_name);
    const fs::path parquet_directory = folder ? root / "METADATA" : root;
    read_levels(dataset, parquet_directory, parquet_names(parquet_directory));
    return dataset;
}

json::Value parse_collection(const std::string& text, const std::string& source) {
    try {
        json::Value root = json::parse(text);
        if (!root.is_object())
            fail("COLLECTION.json must be a JSON object: " + source);
        return root;
    } catch (const json::InvalidJson& error) {
        fail("COLLECTION.json is not valid JSON (" + std::string(error.what()) + "): " + source);
    }
}

// Require taco:sources partitions to stay below the catalog directory (SPEC 7.5).
std::vector<std::string> read_partitions(const std::string& collection, const std::string& source) {
    const json::Value root = parse_collection(collection, source);
    const json::Value* sources = root.find("taco:sources");
    if (!sources || !sources->is_object())
        fail("COLLECTION.json has an invalid taco:sources object: " + source);
    const json::Value* partitions = sources->find("partitions");
    if (!partitions || !partitions->is_array() || partitions->items.empty())
        fail("COLLECTION.json: taco:sources.partitions must be a non-empty array: " + source);
    std::vector<std::string> files;
    std::set<std::string> seen;
    for (const auto& partition : partitions->items) {
        const json::Value* file = partition.is_object() ? partition.find("file") : nullptr;
        if (!file || !file->is_string())
            fail("COLLECTION.json: every taco:sources partition needs a file: " + source);
        if (!is_partition_path(file->string))
            fail("COLLECTION.json: taco:sources partition '" + file->string +
                 "' is not a normalized relative path ending in .zip: " + source);
        if (!seen.insert(file->string).second)
            fail("COLLECTION.json: taco:sources lists partition '" + file->string + "' twice: " + source);
        files.push_back(file->string);
    }
    return files;
}

// <id>-<container>-<origin>: what people see in the cache.
std::string entry_label(const std::string& collection, Container container, const std::string& source) {
    const json::Value root = parse_collection(collection, source);
    const json::Value* id = root.find("id");
    return cache_label(id && id->is_string() ? id->string : "dataset") + "-" + container_name(container) + "-" +
           cache_origin(source);
}

// Object stores cannot list directories reliably. The Parquet files come from
// the levels declared in COLLECTION.json, and taco:sources marks a TACOCAT.
Dataset open_uri_directory(const std::string& source, const std::string& cache_root) {
    const std::string directory = trim_trailing_slashes(without_query(source));
    const std::string location = remote_directory(directory);
    const std::string collection_source = child_path(directory, collection_name);
    CacheEntry cache(cache_root, location);

    // COLLECTION.json is the directory index: it declares every metadata
    // level, so opening an object-store dataset never requires listing it.
    const std::string collection = read_object(collection_source, collection_limit, "COLLECTION.json");
    const auto levels = declared_levels(read_contract(collection, directory), directory);
    const bool tacocat = parse_collection(collection, directory).find("taco:sources") != nullptr;
    // Partitions that would leave the catalog directory fail before any download.
    if (tacocat)
        read_partitions(collection, directory);
    const Container container = tacocat ? Container::tacocat : Container::folder;
    const std::string parquet_directory = tacocat ? directory : child_path(directory, "METADATA");

    std::vector<std::string> level_sources;
    level_sources.reserve(levels.size());
    for (const auto& level : levels)
        level_sources.push_back(remote_child(parquet_directory, level.file));
    const auto level_sizes = object_sizes(level_sources);

    std::vector<std::uint64_t> validation_sizes = {collection.size()};
    std::vector<Range> ranges;
    for (std::size_t index = 0; index < levels.size(); ++index) {
        validation_sizes.push_back(level_sizes[index]);
        ranges.push_back(Range{level_sources[index], 0, level_sizes[index]});
    }
    const auto contents = download(ranges, metadata_phase(directory));
    std::vector<std::pair<std::string, std::string>> files = {{std::string(collection_name), collection}};
    for (std::size_t i = 0; i < levels.size(); ++i)
        files.emplace_back(std::string(metadata_prefix) + levels[i].file, contents[i]);
    const auto snapshot = cache.store(entry_label(collection, container, directory), files,
                                      make_stamp(directory, container, remote_sizes_key(validation_sizes)));
    return cached_dataset(directory, container, tacocat ? parent_path(location) : location, snapshot);
}

std::optional<Dataset> cached_uri_dataset(const std::string& source, const std::string& cache_root);

Dataset open_uri_directory_or_catalog(const std::string& source, const std::string& cache_root) {
    try {
        return open_uri_directory(source, cache_root);
    } catch (const Error& error) {
        bool missing = error.status() == TACO_ERR_NOT_FOUND || error.transport() == KARU_ERR_AUTH;
        if (source.starts_with("file://")) {
            std::error_code ignored;
            missing = !fs::is_regular_file(local_path(file_uri_path(without_query(source))) / collection_name, ignored);
        }
        if (!missing || source_name(source) == ".tacocat")
            throw;
        const auto original = std::current_exception();
        const std::string catalog = child_path(trim_trailing_slashes(source), ".tacocat");
        try {
            if (auto cached = cached_uri_dataset(catalog, cache_root))
                return *cached;
            return open_uri_directory(catalog, cache_root);
        } catch (...) {
            if (error.transport() == KARU_ERR_AUTH)
                std::rethrow_exception(original);
            throw;
        }
    }
}

// A cached URI is reused only after its origin object has been revalidated.
std::optional<Dataset> cached_uri_dataset(const std::string& source, const std::string& cache_root) {
    const std::string directory = trim_trailing_slashes(without_query(source));
    const std::string location = remote_directory(directory);
    // An archive is filed under its own URI, a directory under its location.
    for (const std::string& identity : {cache_identity(source), location}) {
        CacheEntry entry(cache_root, identity);
        const auto stamp = entry.find({std::string(collection_name)}, "");
        if (!stamp)
            continue;
        try {
            const auto container = parse_container(stamp->container);
            if (!container)
                continue;
            const std::string collection = read_local(local_path(entry.path(collection_name)));
            const json::Value root = parse_collection(collection, directory);
            const json::Value* metadata = root.find("taco:metadata");
            if (!metadata || !metadata->is_object())
                continue;
            std::vector<std::string> files = {std::string(collection_name)};
            std::vector<std::string> validation_sources;
            if (*container == Container::zip) {
                validation_sources.push_back(source);
            } else {
                validation_sources.push_back(child_path(directory, collection_name));
            }
            const std::string parquet_directory =
                *container == Container::tacocat ? directory : child_path(directory, "METADATA");
            std::vector<std::string> levels;
            for (const auto& [name, value] : metadata->members)
                levels.push_back(name);
            std::sort(levels.begin(), levels.end(), [](const auto& left, const auto& right) {
                const auto left_depth = level_depth(left);
                const auto right_depth = level_depth(right);
                return left_depth != right_depth ? left_depth < right_depth : left < right;
            });
            for (const auto& level : levels) {
                files.push_back(std::string(metadata_prefix) + level_to_file(level));
                if (*container != Container::zip)
                    validation_sources.push_back(remote_child(parquet_directory, level_to_file(level)));
            }
            const std::string key = *container == Container::zip ? expected_key(source)
                                                                  : remote_sizes_key(validation_sources);
            if (!entry.find(files, key))
                continue;
            const auto snapshot = entry.snapshot(files);
            if (!snapshot)
                continue;
            if (*container == Container::zip)
                return cached_dataset(source, Container::zip, location_base(source), *snapshot);
            return cached_dataset(directory, *container,
                                  *container == Container::tacocat ? parent_path(location) : location, *snapshot);
        } catch (const Error&) {
            // A stale entry may name metadata that no longer exists. Treat any
            // failed validation as a miss and rebuild it from COLLECTION.json.
            continue;
        }
    }
    return std::nullopt;
}

bool is_remote_cozip(const std::string& source) {
    try {
        (void)read_cozip_profile(source);
        return true;
    } catch (const Error& error) {
        // A missing object may be a directory URL. Format failures mean the
        // object is not a CoZIP archive. Authentication, network, TLS and
        // server failures must keep their original diagnostic.
        if (error.transport() == KARU_ERR_NOT_FOUND || error.transport() == 0)
            return false;
        throw;
    }
}

Contract read_contract(const std::string& collection, const std::string& source) {
    const json::Value root = parse_collection(collection, source);

    Contract contract;
    const json::Value* version = root.find("taco:version");
    if (!version || !version->is_string())
        fail("COLLECTION.json has no valid taco:version: " + source);
    if (version->string != supported_version)
        fail("unsupported TACO version '" + version->string + "' in " + source + "; expected " +
             std::string(supported_version));

    const json::Value* structure = root.find("taco:structure");
    if (!structure)
        fail("COLLECTION.json has no taco:structure key: " + source);
    if (!structure->is_array() || structure->items.empty())
        fail("COLLECTION.json: taco:structure must be a non-empty array: " + source);
    for (const auto& item : structure->items) {
        if (!item.is_string())
            fail("COLLECTION.json: taco:structure must contain strings: " + source);
        validate_structure_leaf(item.string, source);
        contract.structure.push_back(item.string);
    }

    // taco:metadata names the user columns of every level. The reader needs
    // them to resolve a field declared at two levels of the same branch.
    const json::Value* metadata = root.find("taco:metadata");
    if (!metadata || !metadata->is_object())
        fail("COLLECTION.json has no valid taco:metadata object: " + source);
    for (const auto& [level, value] : metadata->members) {
        if (!value.is_object())
            fail("COLLECTION.json: metadata level must be an object: " + source);
        std::vector<std::string> names;
        for (const auto& member : value.members) {
            const auto suffix = rumi_file_suffix(member.first);
            if (member.first.starts_with("rumi:") && !suffix)
                fail("COLLECTION.json: invalid Rumi field '" + member.first + "': " + source);
            if (suffix) {
                const auto* type = member.second.is_object() ? member.second.find("type") : nullptr;
                const std::string_view expected = *suffix == "header" ? "binary" : "double";
                if (!type || !type->is_string() || type->string != expected)
                    fail("COLLECTION.json: field '" + member.first + "' must have type " +
                         std::string(expected) + ": " + source);
            }
            if (member.second.is_object()) {
                if (const auto* files = member.second.find("files")) {
                    if (!suffix || *suffix == "header")
                        fail("COLLECTION.json: field '" + member.first + "' cannot declare files: " + source);
                    if (!files->is_array() || files->items.empty())
                        fail("COLLECTION.json: files for field '" + member.first +
                             "' must be a non-empty array: " + source);
                    Contract::FileScope scope{level, member.first, {}};
                    for (const auto& file : files->items) {
                        if (!file.is_string())
                            fail("COLLECTION.json: files for field '" + member.first +
                                 "' must contain structure declarations: " + source);
                        if (std::find(contract.structure.begin(), contract.structure.end(), file.string) ==
                            contract.structure.end())
                            fail("COLLECTION.json: field '" + member.first +
                                 "' names an unknown structure declaration: " + source);
                        const auto slash = file.string.rfind('/');
                        const std::string file_level =
                            slash == std::string::npos ? "children" : "children/" + file.string.substr(0, slash);
                        if (file_level != level)
                            fail("COLLECTION.json: file for field '" + member.first +
                                 "' belongs to another level: " + source);
                        if (std::find(scope.files.begin(), scope.files.end(), file.string) != scope.files.end())
                            fail("COLLECTION.json: duplicate file for field '" + member.first + "': " + source);
                        scope.files.push_back(file.string);
                    }
                    contract.file_scopes.push_back(std::move(scope));
                }
            }
            names.push_back(member.first);
        }
        contract.fields.emplace_back(level, std::move(names));
    }
    return contract;
}

} // namespace

bool is_explicit_remote_directory(std::string_view source) {
    const auto object = without_query(source);
    if (object.ends_with('/') || source_name(source) == ".tacocat")
        return true;

    const auto marker = object.find("://");
    if (marker == std::string_view::npos)
        return false;
    const auto scheme = object.substr(0, marker);
    auto rest = object.substr(marker + 3);
    const auto components = [](std::string_view value) {
        std::size_t count = 0;
        for (std::size_t start = 0; start < value.size();) {
            const auto slash = value.find('/', start);
            const auto end = slash == std::string_view::npos ? value.size() : slash;
            if (end > start)
                ++count;
            if (slash == std::string_view::npos)
                break;
            start = slash + 1;
        }
        return count;
    };

    if (scheme == "s3" || scheme == "gs" || scheme == "az" || scheme == "abfs")
        return components(rest) == 1;
    if (scheme == "source")
        return components(rest) == 2;
    if (scheme == "hf") {
        if (rest.starts_with("datasets/"))
            rest.remove_prefix(9);
        else if (rest.starts_with("spaces/"))
            rest.remove_prefix(7);
        return components(rest) == 2;
    }
    if (scheme == "http" || scheme == "https")
        return components(rest) == 1;
    if (scheme != "file")
        return false;
    std::error_code error;
    return fs::is_directory(local_path(file_uri_path(object)), error);
}

const std::vector<std::string>* Contract::fields_of(std::string_view level) const {
    for (const auto& [name, names] : fields) {
        if (name == level)
            return &names;
    }
    return nullptr;
}

bool Contract::field_applies(std::string_view level, std::string_view field,
                             std::string_view declaration) const {
    for (const auto& scope : file_scopes) {
        if (scope.level == level && scope.field == field)
            return std::find(scope.files.begin(), scope.files.end(), declaration) != scope.files.end();
    }
    return true;
}

std::size_t Dataset::level_index(std::string_view name) const {
    for (std::size_t i = 0; i < level_names.size(); ++i) {
        if (level_names[i] == name)
            return i;
    }
    fail("TACO dataset has no level '" + std::string(name) + "': " + source);
}

std::string remote_directory(const std::string& directory) {
    return parent_path(location_base(child_path(directory, collection_name)));
}

Dataset open_dataset(const std::string& source, const std::string& cache_dir) {
    if (source.empty())
        fail("taco: path must not be empty");
    const std::string cache_root = cache_dir.empty() ? default_cache_dir() : cache_dir;

    Dataset dataset;
    std::error_code error;
    if (has_uri_scheme(source)) {
        // A complete cache entry settles the container type after revalidation.
        // Otherwise use URI shape first and probe only ambiguous URLs.
        if (auto cached = cached_uri_dataset(source, cache_root))
            return *cached;
        if (is_zip_name(source)) {
            dataset = open_zip(source, cache_root);
        } else if (is_explicit_remote_directory(source)) {
            dataset = open_uri_directory_or_catalog(source, cache_root);
        } else {
            bool archive = false;
            bool opened_as_directory = false;
            try {
                archive = is_remote_cozip(source);
            } catch (const Error& probe_error) {
                // Some object stores answer 403 when an object key is absent.
                // Try the directory form, but retain the authentication error
                // if that interpretation fails too.
                if (probe_error.transport() != KARU_ERR_AUTH)
                    throw;
                const auto original = std::current_exception();
                try {
                    dataset = open_uri_directory_or_catalog(source, cache_root);
                    opened_as_directory = true;
                } catch (...) {
                    std::rethrow_exception(original);
                }
            }
            if (!opened_as_directory)
                dataset = archive ? open_zip(source, cache_root)
                                  : open_uri_directory_or_catalog(source, cache_root);
        }
    }
    else if (fs::is_directory(local_path(source), error))
        dataset = open_local_directory(source);
    else
        dataset = open_zip(source, cache_root);
    return dataset;
}

const char* container_name(Container container) noexcept {
    switch (container) {
    case Container::zip:
        return "zip";
    case Container::folder:
        return "folder";
    case Container::tacocat:
        return "tacocat";
    }
    return "zip";
}

} // namespace taco
