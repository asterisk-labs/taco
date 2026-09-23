#include "dataset.hpp"

#include "cache.hpp"
#include "cozip_index.hpp"
#include "error.hpp"
#include "json.hpp"
#include "paths.hpp"
#include "progress.hpp"
#include "transport.hpp"

#include <karu/karu.h>

#include <algorithm>
#include <charconv>
#include <exception>
#include <filesystem>
#include <fstream>
#include <optional>
#include <sstream>

namespace fs = std::filesystem;

namespace taco {
namespace {

constexpr std::string_view collection_name = "COLLECTION.json";
constexpr std::string_view metadata_prefix = "METADATA/";
constexpr std::string_view parquet_suffix = ".parquet";
constexpr std::string_view supported_version = "3.0.0";
constexpr std::uint64_t collection_limit = 64ULL * 1024 * 1024;

struct Level {
    std::string name;
    // The index entry of a ZIP level, or where a directory level is read from.
    std::string origin;
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

    const auto parse_bound = [&](std::string_view text) {
        while (!text.empty() && text.front() == ' ')
            text.remove_prefix(1);
        while (!text.empty() && text.back() == ' ')
            text.remove_suffix(1);
        std::uint64_t value = 0;
        const auto [end, error] = std::from_chars(text.data(), text.data() + text.size(), value);
        if (text.empty() || error != std::errc() || end != text.data() + text.size())
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

// Local paths stay as the caller wrote them. URIs take karu's canonical form,
// and HTTP keeps the /vsicurl/ prefix that GDAL expects.
std::string location_base(const std::string& path) {
    if (!has_uri_scheme(path))
        return path;
    std::string canonical = canonical_uri(path);
    if (canonical.starts_with("http://") || canonical.starts_with("https://"))
        return "/vsicurl/" + canonical;
    return canonical;
}

// Local archives use size and modification time. Remote objects use the
// origin-reported content length. If the probe fails, opening fails instead
// of silently serving metadata that can no longer be validated.
std::string cache_key(const std::string& source) {
    if (has_uri_scheme(source))
        return "remote size " + std::to_string(object_size(source));
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
    std::vector<std::uint64_t> sizes;
    sizes.reserve(sources.size());
    for (const auto& source : sources)
        sizes.push_back(object_size(source));
    return remote_sizes_key(sizes);
}

std::optional<Container> parse_container(const std::string& name) {
    for (const auto container : {Container::zip, Container::folder, Container::tacocat}) {
        if (name == container_name(container))
            return container;
    }
    return std::nullopt;
}

CacheStamp make_stamp(const std::string& source, Container container, const std::string& validation_source = {}) {
    CacheStamp stamp;
    stamp.source = source;
    stamp.container = container_name(container);
    stamp.key = cache_key(validation_source.empty() ? source : validation_source);
    return stamp;
}

Dataset cached_dataset(const std::string& source, Container container, std::string location_base,
                       const CacheEntry& cache) {
    Dataset dataset;
    dataset.source = source;
    dataset.container = container;
    dataset.location_base = std::move(location_base);
    dataset.collection = read_local(local_path(cache.path(collection_name)));
    std::vector<Level> levels;
    for (const auto& file : cache.parquet_files())
        levels.push_back(Level{file_to_level(file), cache.path("METADATA/" + file)});
    sort_and_validate(levels, source);
    for (const auto& level : levels) {
        dataset.level_names.push_back(level.name);
        dataset.level_paths.push_back(level.origin);
    }
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

Dataset open_zip(const std::string& source, const std::string& cache_root) {
    CacheEntry cache(cache_root, cache_identity(source));
    if (const auto stamp = cache.find({std::string(collection_name)}, expected_key(source)); stamp && stamp->container == "zip")
        return cached_dataset(source, Container::zip, location_base(source), cache);

    const CozipIndex index = read_cozip_index(source);
    if (index.profile != cozip_profile_taco)
        fail("taco needs a TACO-profile archive (profile=2). Got profile=" + profile_name(index.profile) +
             " in: " + source);
    const CozipEntry* collection = index.find(collection_name);
    if (!collection)
        fail("TACO archive has no COLLECTION.json in its cozip index: " + source);
    if (collection->size > collection_limit)
        fail("COLLECTION.json is larger than 64 MiB, refusing to read it: " + source);

    std::vector<Level> levels;
    for (const auto& entry : index.entries) {
        if (entry.name.starts_with(metadata_prefix) && entry.name.ends_with(parquet_suffix))
            levels.push_back(Level{file_to_level(entry.name.substr(metadata_prefix.size())), entry.name});
    }
    sort_and_validate(levels, source);

    std::vector<std::string> names = {std::string(collection_name)};
    std::vector<Range> ranges = {Range{source, collection->offset, collection->size}};
    for (const auto& level : levels) {
        const CozipEntry* entry = index.find(level.origin);
        names.push_back(level.origin);
        ranges.push_back(Range{source, entry->offset, entry->size});
    }
    const auto contents = download(ranges, metadata_phase(source));
    std::vector<std::pair<std::string, std::string>> files;
    for (std::size_t i = 0; i < names.size(); ++i)
        files.emplace_back(names[i], contents[i]);
    cache.store(entry_label(contents[0], Container::zip, source), files, make_stamp(source, Container::zip));
    return cached_dataset(source, Container::zip, location_base(source), cache);
}

Dataset open_local_directory(const std::string& source) {
    const std::string directory = trim_trailing_slashes(source);
    const fs::path root = local_path(directory);
    std::error_code error;
    if (!fs::is_regular_file(root / collection_name, error))
        fail("directory has no COLLECTION.json: " + directory);

    // TACO spec 7.5: a catalog sits beside the archives it indexes, so
    // internal:source_file resolves against its parent directory.
    const bool folder = fs::is_directory(root / "METADATA", error);
    Dataset dataset;
    dataset.source = directory;
    dataset.container = folder ? Container::folder : Container::tacocat;
    dataset.location_base = folder ? directory : parent_path(directory);

    std::vector<Level> levels;
    const fs::path parquet_directory = folder ? root / "METADATA" : root;
    for (const auto& entry : fs::directory_iterator(parquet_directory, error)) {
        const std::string name = utf8(entry.path().filename());
        if (entry.is_regular_file(error) && name.ends_with(parquet_suffix))
            levels.push_back(Level{file_to_level(name), utf8(entry.path())});
    }
    if (error)
        throw Error(TACO_ERR_IO, "could not list " + utf8(parquet_directory) + ": " + error.message());
    sort_and_validate(levels, directory);

    if (fs::file_size(root / collection_name, error) > collection_limit)
        fail("COLLECTION.json is larger than 64 MiB, refusing to read it: " + directory);
    dataset.collection = read_local(root / collection_name);
    for (const auto& level : levels) {
        dataset.level_names.push_back(level.name);
        dataset.level_paths.push_back(level.origin);
    }
    return dataset;
}

json::Value parse_collection(const std::string& text, const std::string& source) {
    try {
        json::Value root = json::parse(text);
        if (!root.is_object())
            fail("COLLECTION.json must be a JSON object: " + source);
        return root;
    } catch (const json::InvalidJson&) {
        fail("COLLECTION.json is not valid JSON: " + source);
    }
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
    const json::Value root = parse_collection(collection, directory);
    const json::Value* metadata = root.find("taco:metadata");
    if (!metadata || !metadata->is_object())
        fail("COLLECTION.json has no valid taco:metadata object: " + directory);
    const json::Value* sources = root.find("taco:sources");
    if (sources && !sources->is_object())
        fail("COLLECTION.json has an invalid taco:sources object: " + directory);

    const bool tacocat = sources != nullptr;
    const Container container = tacocat ? Container::tacocat : Container::folder;
    const std::string parquet_directory = tacocat ? directory : child_path(directory, "METADATA");

    std::vector<Level> levels;
    for (const auto& [name, value] : metadata->members)
        levels.push_back(Level{name, child_path(parquet_directory, level_to_file(name))});
    sort_and_validate(levels, directory);

    std::vector<std::uint64_t> validation_sizes = {object_size(collection_source)};
    std::vector<Range> ranges;
    for (const auto& level : levels) {
        const auto size = object_size(level.origin);
        validation_sizes.push_back(size);
        ranges.push_back(Range{level.origin, 0, size});
    }
    const auto contents = download(ranges, metadata_phase(directory));
    std::vector<std::pair<std::string, std::string>> files = {{std::string(collection_name), collection}};
    for (std::size_t i = 0; i < levels.size(); ++i)
        files.emplace_back(std::string(metadata_prefix) + level_to_file(levels[i].name), contents[i]);
    auto stamp = make_stamp(directory, container, collection_source);
    stamp.key = remote_sizes_key(validation_sizes);
    cache.store(entry_label(collection, container, directory), files, std::move(stamp));
    return cached_dataset(directory, container, tacocat ? parent_path(location) : location, cache);
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
                validation_sources.push_back(child_path(parquet_directory, level_to_file(level)));
        }
        const std::string key = *container == Container::zip ? expected_key(source)
                                                              : remote_sizes_key(validation_sources);
        if (!entry.find(files, key))
            continue;
        if (*container == Container::zip)
            return cached_dataset(source, Container::zip, location_base(source), entry);
        return cached_dataset(directory, *container,
                              *container == Container::tacocat ? parent_path(location) : location, entry);
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

Contract read_contract(const Dataset& dataset) {
    const std::string& source = dataset.source;
    const json::Value root = parse_collection(dataset.collection, source);

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
        for (const auto& member : value.members)
            names.push_back(member.first);
        contract.fields.emplace_back(level, std::move(names));
    }
    for (const auto& level : dataset.level_names) {
        if (!contract.fields_of(level))
            fail("COLLECTION.json has no metadata declaration for level '" + level + "': " + source);
    }
    if (contract.fields.size() != dataset.level_names.size())
        fail("COLLECTION.json metadata levels do not match its Parquet files: " + source);
    if (dataset.level_names.size() < 2)
        fail("COLLECTION.json structure requires sample and children metadata levels: " + source);

    if (const json::Value* derived = root.find("taco:derived")) {
        if (!derived->is_object())
            fail("COLLECTION.json: taco:derived must be an object: " + source);
        contract.has_derived = true;
        contract.derived = std::string(derived->raw);
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

    auto path = rest;
#ifdef _WIN32
    if (path.size() >= 3 && path[0] == '/' && path[2] == ':')
        path.remove_prefix(1);
#endif
    std::error_code error;
    return fs::is_directory(local_path(path), error);
}

const std::vector<std::string>* Contract::fields_of(std::string_view level) const {
    for (const auto& [name, names] : fields) {
        if (name == level)
            return &names;
    }
    return nullptr;
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
        if (auto cached = cached_uri_dataset(source, cache_root)) {
            cached->contract = read_contract(*cached);
            return *cached;
        }
        if (is_zip_name(source)) {
            dataset = open_zip(source, cache_root);
        } else if (is_explicit_remote_directory(source)) {
            dataset = open_uri_directory(source, cache_root);
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
                    dataset = open_uri_directory(source, cache_root);
                    opened_as_directory = true;
                } catch (...) {
                    std::rethrow_exception(original);
                }
            }
            if (!opened_as_directory)
                dataset = archive ? open_zip(source, cache_root)
                                  : open_uri_directory(source, cache_root);
        }
    }
    else if (fs::is_directory(local_path(source), error))
        dataset = open_local_directory(source);
    else
        dataset = open_zip(source, cache_root);
    dataset.contract = read_contract(dataset);
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
