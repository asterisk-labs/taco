#include "cozip_index.hpp"
#include "dataset.hpp"
#include "error.hpp"
#include "json.hpp"
#include "manifest.hpp"
#include "paths.hpp"
#include "sql.hpp"
#include "transport.hpp"

#include <taco/taco.h>

#include <cstdio>
#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <functional>
#include <sstream>
#include <string>
#include <vector>

namespace fs = std::filesystem;

namespace {

int checks = 0;
int failures = 0;

void check(bool condition, const char* expression, const char* file, int line) {
    ++checks;
    if (!condition) {
        ++failures;
        std::fprintf(stderr, "%s:%d: CHECK(%s) failed\n", file, line, expression);
    }
}

void check_throws(const std::function<void()>& body, const std::string& fragment, const char* file,
                  int line) {
    ++checks;
    try {
        body();
    } catch (const std::exception& error) {
        if (std::string(error.what()).find(fragment) != std::string::npos)
            return;
        ++failures;
        std::fprintf(stderr, "%s:%d: expected an error containing '%s', got '%s'\n", file, line,
                     fragment.c_str(), error.what());
        return;
    }
    ++failures;
    std::fprintf(stderr, "%s:%d: expected an error containing '%s'\n", file, line, fragment.c_str());
}

#define CHECK(expression) check(static_cast<bool>(expression), #expression, __FILE__, __LINE__)
#define CHECK_THROWS(body, fragment) check_throws([&] { body; }, fragment, __FILE__, __LINE__)

using Strings = std::vector<std::string>;

std::string data(const std::string& name) {
    return std::string(TACO_TEST_DATA) + "/" + name;
}

bool remote_tests() {
    const char* value = std::getenv("TACO_TEST_REMOTE");
    return value && std::string(value) == "1";
}

fs::path scratch(const std::string& name) {
    const fs::path path = fs::temp_directory_path() / ("taco-core-tests-" + name);
    fs::remove_all(path);
    fs::create_directories(path);
    return path;
}

std::string read_file(const fs::path& path) {
    std::ifstream stream(path, std::ios::binary);
    std::ostringstream out;
    out << stream.rdbuf();
    return out.str();
}

void write_file(const fs::path& path, const std::string& text) {
    fs::create_directories(path.parent_path());
    std::ofstream(path, std::ios::binary) << text;
}

bool contains(const std::string& text, const std::string& fragment) {
    return text.find(fragment) != std::string::npos;
}

void test_json() {
    using taco::json::Kind;
    const std::string text =
        R"({"a": [1, 2.5e3, -0.1, true, false, null], "b": "x\u00e9\ud83d\ude00\n", "a": 2})";
    const auto value = taco::json::parse(text);
    CHECK(value.is_object());
    CHECK(value.members.size() == 3);
    const auto* array = value.find("a");
    CHECK(array && array->is_array() && array->items.size() == 6);
    CHECK(array && array->raw == "[1, 2.5e3, -0.1, true, false, null]");
    CHECK(array && array->items[3].kind == Kind::boolean && array->items[3].boolean);
    CHECK(array && array->items[5].is_null());
    CHECK(value.find("b") && value.find("b")->string == "x\xc3\xa9\xf0\x9f\x98\x80\n");
    CHECK(value.find("missing") == nullptr);
    CHECK(taco::json::parse(" [ ] ").items.empty());

    const Strings invalid = {
        "", "{", "{\"a\":1,}", "[01]", "[1.]", "[-]", "\"\x01\"", "\"\\ud800\"", "\"\\udc00\"",
        "{\"a\" 1}", "tru", "\"\xff\"", "\"\xc0\xaf\"", "{} x", "[\"\\x\"]", std::string(300, '[')};
    for (const auto& document : invalid)
        CHECK_THROWS(taco::json::parse(document), "at byte");

    CHECK(taco::json::quote("a\"b\\\n\xc3\xa9") == "\"a\\\"b\\\\\\u000a\xc3\xa9\"");
}

void test_paths() {
    CHECK(taco::has_uri_scheme("hf://datasets/a/b"));
    CHECK(taco::has_uri_scheme("s3+x.y-z://bucket"));
    CHECK(!taco::has_uri_scheme("C:/data/x.zip"));
    CHECK(!taco::has_uri_scheme("/tmp/x.zip"));
    CHECK(!taco::has_uri_scheme("1ab://x"));

    CHECK(taco::is_zip_name("https://host/a.ZIP?download=1"));
    CHECK(taco::is_zip_name("a.zip"));
    CHECK(!taco::is_zip_name("https://host/.tacocat"));
    CHECK(!taco::is_zip_name("folder"));
    CHECK(taco::redact_uri("https://user:password@host/data?token=secret") ==
          "https://<redacted>@host/data?<redacted>");
    CHECK(taco::redact_uri("s3://bucket/key#credentials") ==
          "s3://bucket/key#<redacted>");

    CHECK(taco::is_explicit_remote_directory("s3://bucket"));
    CHECK(taco::is_explicit_remote_directory("gs://bucket"));
    CHECK(taco::is_explicit_remote_directory("az://container"));
    CHECK(taco::is_explicit_remote_directory("abfs://container"));
    CHECK(taco::is_explicit_remote_directory("source://account/product"));
    CHECK(taco::is_explicit_remote_directory("hf://datasets/owner/repository"));
    CHECK(taco::is_explicit_remote_directory("hf://owner/repository"));
    CHECK(taco::is_explicit_remote_directory("https://example.test"));
    CHECK(!taco::is_explicit_remote_directory("s3://bucket/object"));
    CHECK(!taco::is_explicit_remote_directory("source://account/product/object"));
    CHECK(!taco::is_explicit_remote_directory("hf://datasets/owner/repository/object"));

    CHECK(taco::is_semver("1.2.3"));
    CHECK(taco::is_semver("1.2.3-beta.1+build.2"));
    CHECK(!taco::is_semver("01.2.3"));
    CHECK(!taco::is_semver("v1.2.3"));
    CHECK(!taco::is_semver("1.2"));

    CHECK(taco::source_name("https://host/a/b.zip?x=1") == "b.zip");
    CHECK(taco::source_name("/tmp/dir/") == "dir");
    CHECK(taco::source_name("C:\\data\\x.zip") == "x.zip");
    CHECK(taco::parent_path("hf://datasets/o/r/data/.tacocat") == "hf://datasets/o/r/data");
    CHECK(taco::parent_path("/tmp/cat/.tacocat/") == "/tmp/cat");
    CHECK(taco::parent_path("x") == ".");
    CHECK(taco::child_path("/tmp/dir/", "COLLECTION.json") == "/tmp/dir/COLLECTION.json");

    CHECK((taco::source_labels({"/a/x.zip", "/b/y.zip"}) == Strings{"x.zip", "y.zip"}));
    CHECK((taco::source_labels({"/a/x.zip", "/b/x.zip"}) == Strings{"/a/x.zip", "/b/x.zip"}));

    CHECK(taco::level_to_file("children/before") == "children__before.parquet");
    CHECK(taco::file_to_level("children__before.parquet") == "children/before");
    CHECK(taco::parent_level("children") == "sample");
    CHECK(taco::parent_level("children/before/red") == "children/before");
    CHECK(taco::last_segment("children/before") == "before");
    CHECK(taco::sql_literal("it's") == "'it''s'");
    CHECK(taco::sql_identifier("a\"b") == "\"a\"\"b\"");
    CHECK(taco::hex64(0x1234) == "0000000000001234");
}

void test_cozip_index() {
    const auto index = taco::read_cozip_index(data("taco_flat.zip"));
    CHECK(index.profile == taco::cozip_profile_taco);
    CHECK(index.find("COLLECTION.json") != nullptr);
    CHECK(index.find("METADATA/sample.parquet") != nullptr);
    CHECK(index.find("METADATA/children.parquet") != nullptr);
    CHECK(index.find("DATA/0/image.bin") == nullptr);
    CHECK(index.integrity != 0);

    CHECK(taco::profile_name(taco::read_cozip_profile(data("flat_simple.zip"))) == "flat");
    CHECK(taco::profile_name(taco::read_cozip_profile(data("unsupported_profile.zip"))).starts_with("unknown:"));
    CHECK_THROWS(taco::read_cozip_index(data("flat_bad_hash.zip")), "integrity hash mismatch");
    CHECK_THROWS(taco::read_cozip_index(data("does_not_exist.zip")), "could not open");
    CHECK_THROWS(taco::read_cozip_index(data("taco_folder/COLLECTION.json")), "cozip archive too small");
}

void test_open_archives() {
    const std::string cache = scratch("archives").generic_string();

    const auto flat = taco::open_dataset(data("taco_flat.zip"), cache);
    CHECK(flat.container == taco::Container::zip);
    CHECK(flat.source == data("taco_flat.zip"));
    CHECK(flat.location_base == data("taco_flat.zip"));
    CHECK((flat.level_names == Strings{"sample", "children"}));
    CHECK((flat.contract.structure == Strings{"image.bin", "label.bin"}));
    CHECK(!flat.contract.has_derived);
    CHECK(contains(flat.collection, "\"taco-flat\""));
    for (const auto& path : flat.level_paths)
        CHECK(path.starts_with(cache) && fs::is_regular_file(path));

    // A second open finds a complete cache entry and leaves its files alone.
    const auto written = fs::last_write_time(flat.level_paths[0]);
    const auto again = taco::open_dataset(data("taco_flat.zip"), cache);
    CHECK(again.level_paths == flat.level_paths);
    CHECK(fs::last_write_time(again.level_paths[0]) == written);

    // A changed archive at the same path rebuilds its entry in place.
    const fs::path moving = scratch("moving") / "dataset.zip";
    fs::copy_file(data("taco_flat.zip"), moving);
    const auto first = taco::open_dataset(moving.string(), cache);
    fs::copy_file(data("taco_nested.zip"), moving, fs::copy_options::overwrite_existing);
    fs::last_write_time(moving, fs::file_time_type::clock::now() + std::chrono::seconds(5));
    const auto second = taco::open_dataset(moving.string(), cache);
    CHECK(first.level_paths[0] != second.level_paths[0]);
    CHECK(contains(second.level_paths[0], "taco-nested-zip-local-"));
    CHECK(!fs::exists(fs::path(first.level_paths[0]).parent_path()));
    CHECK((second.level_names == Strings{"sample", "children", "children/after", "children/before"}));

    const auto nested = taco::open_dataset(data("taco_nested.zip"), cache);
    CHECK((nested.level_names == Strings{"sample", "children", "children/after", "children/before"}));
    CHECK(nested.contract.structure.size() == 4);
    CHECK(nested.contract.fields_of("children/before") &&
          *nested.contract.fields_of("children/before") == Strings{"raster:resolution"});

    CHECK_THROWS(taco::open_dataset(data("taco_null.zip"), cache), "taco:structure must be a non-empty array");

    CHECK_THROWS(taco::open_dataset(data("taco_variable.zip"), cache), "must require at least one file");

    CHECK_THROWS(taco::open_dataset(data("flat_simple.zip"), cache), "Got profile=flat");
    CHECK_THROWS(taco::open_dataset(data("does_not_exist.zip"), cache), "could not open");
    CHECK_THROWS(taco::open_dataset("", cache), "must not be empty");

    const fs::path unnamed = scratch("unnamed-archive") / "dataset";
    fs::copy_file(data("taco_flat.zip"), unnamed);
    const auto unnamed_uri = taco::open_dataset("file://" + unnamed.string(), cache);
    CHECK(unnamed_uri.container == taco::Container::zip);
    CHECK((unnamed_uri.level_names == Strings{"sample", "children"}));

    const fs::path unnamed_flat = scratch("unnamed-flat") / "dataset";
    fs::copy_file(data("flat_simple.zip"), unnamed_flat);
    CHECK_THROWS(taco::open_dataset("file://" + unnamed_flat.string(), cache), "Got profile=flat");
}

void test_open_directories() {
    const std::string cache = scratch("directories").generic_string();

    const auto folder = taco::open_dataset(data("taco_folder"), cache);
    CHECK(folder.container == taco::Container::folder);
    CHECK(folder.location_base == data("taco_folder"));
    CHECK(folder.level_paths[0] == data("taco_folder") + "/METADATA/sample.parquet");
    CHECK(taco::open_dataset(data("taco_folder") + "/", cache).source == data("taco_folder"));

    const auto catalog = taco::open_dataset(data("taco_cat/.tacocat"), cache);
    CHECK(catalog.container == taco::Container::tacocat);
    CHECK(catalog.location_base == data("taco_cat"));
    CHECK((catalog.level_names == Strings{"sample", "children"}));

    CHECK_THROWS(taco::open_dataset(data("taco_badjson"), cache), "COLLECTION.json is not valid JSON");
    CHECK_THROWS(taco::open_dataset(data("taco_badversion"), cache), "unsupported TACO version '2.0.0'");
    CHECK_THROWS(taco::open_dataset(data(""), cache), "directory has no COLLECTION.json");

    const auto derived = taco::open_dataset(data("taco_derived"), cache);
    CHECK(derived.contract.has_derived && contains(derived.contract.derived, "majortom"));

    // file:// URIs take the object-store path: levels come from COLLECTION.json.
    const std::string root = "file://" + fs::absolute(TACO_TEST_DATA).generic_string();
    const auto uri_folder = taco::open_dataset(root + "/taco_folder", cache);
    CHECK(uri_folder.container == taco::Container::folder);
    CHECK((uri_folder.level_names == Strings{"sample", "children"}));
    CHECK(uri_folder.level_paths[0].starts_with(cache));
    const auto uri_catalog = taco::open_dataset(root + "/taco_cat/.tacocat/", cache);
    CHECK(uri_catalog.container == taco::Container::tacocat);
    CHECK(taco::open_dataset(root + "/taco_flat.zip", cache).container == taco::Container::zip);

    CHECK(taco::remote_directory("hf://datasets/org/repo") == "/vsihf/datasets/org/repo");
    CHECK(taco::remote_directory("s3://bucket") == "/vsis3/bucket");
    CHECK(taco::remote_directory("source://account/product") == "/vsisource/account/product");
    CHECK(taco::remote_directory("https://host/data") == "/vsicurl/https://host/data");
}

void test_sql() {
    const std::string cache = scratch("sql").generic_string();
    const auto nested = taco::open_dataset(data("taco_nested.zip"), cache);

    const auto wide = taco::build_sql(nested, taco::ReadOptions{});
    CHECK(contains(wide, "read_parquet(" + taco::sql_literal(nested.level_paths[0]) + ")"));
    CHECK(contains(wide, "'/vsisubfile/'"));
    CHECK(contains(wide, taco::sql_literal(data("taco_nested.zip"))));
    CHECK(contains(wide, "\"before__B02.bin::location\""));
    CHECK(!contains(wide, "AS \"before/B02.bin"));
    CHECK(!contains(wide, "::header"));

    // A level that declares rumi:header carries it next to every location.
    auto rumi = nested;
    for (auto& [name, fields] : rumi.contract.fields) {
        if (name == "children/before")
            fields.push_back("rumi:header");
    }
    const auto rumi_wide = taco::build_sql(rumi, taco::ReadOptions{});
    CHECK(contains(rumi_wide, "AS \"before__B02.bin::header\""));
    CHECK(contains(rumi_wide, "AS \"before__B02.bin::location\""));
    CHECK(!contains(rumi_wide, "AS \"change.bin::header\""));
    taco::ReadOptions rumi_quiet;
    rumi_quiet.location = false;
    CHECK(contains(taco::build_sql(rumi, rumi_quiet), "NULL::BLOB AS \"before__B02.bin::header\""));

    taco::ReadOptions long_quiet;
    long_quiet.pivot = false;
    long_quiet.location = false;
    CHECK(!contains(taco::build_sql(nested, long_quiet), "\"taco:location\""));
    long_quiet.location = true;
    CHECK(contains(taco::build_sql(nested, long_quiet), "\"taco:location\""));

    taco::ReadOptions level;
    level.level = "children/before";
    CHECK(taco::build_sql(nested, level) ==
          "SELECT COLUMNS(lambda c: c != 'cozip:location' AND c != 'taco:location') FROM read_parquet(" +
              taco::sql_literal(nested.level_paths[3]) + ")");

    const auto error = [&](std::function<void(taco::ReadOptions&)> change) {
        taco::ReadOptions options;
        change(options);
        taco::build_sql(nested, options);
    };
    CHECK_THROWS(error([](auto& o) { o.level = "nope"; }), "has no level 'nope'");
    CHECK_THROWS(error([](auto& o) { o.idx = "x"; }), "idx must contain non-negative integers");
    CHECK_THROWS(error([](auto& o) { o.idx = "1junk"; }), "idx must contain non-negative integers");
    CHECK_THROWS(error([](auto& o) { o.idx = "[-1, 2]"; }), "idx must contain non-negative integers");
    CHECK_THROWS(error([](auto& o) { o.idx = "[3, 1]"; }), "must not exceed its end");
    CHECK_THROWS(error([](auto& o) { o.idx = "[1, 2, 3]"; }), "two-element list");
    CHECK_THROWS(error([](auto& o) {
                     o.files = {"change.bin", "nope.bin"};
                     o.has_files = true;
                 }),
                 "unknown structure leaf: nope.bin");
    CHECK_THROWS(error([](auto& o) {
                     o.level = "children";
                     o.files = {"change.bin"};
                     o.has_files = true;
                 }),
                 "files does not apply when level is set");

    const auto folder = taco::open_dataset(data("taco_folder"), cache);
    CHECK(contains(taco::build_sql(folder, taco::ReadOptions{}), taco::sql_literal(data("taco_folder") + "/DATA/")));
    const auto catalog = taco::open_dataset(data("taco_cat/.tacocat"), cache);
    const auto catalog_sql = taco::build_sql(catalog, taco::ReadOptions{});
    CHECK(contains(catalog_sql, taco::sql_literal(data("taco_cat") + "/")));
    CHECK(contains(catalog_sql, "source_file"));

    const auto flat = taco::open_dataset(data("taco_flat.zip"), cache);
    const auto parts = taco::build_union_sql({&flat, &folder}, taco::ReadOptions{});
    CHECK(contains(parts, "'taco_flat.zip' AS source_file"));
    CHECK(contains(parts, "'taco_folder' AS source_file"));
    CHECK(contains(parts, "UNION ALL BY NAME"));
    taco::ReadOptions raw;
    raw.level = "sample";
    CHECK(contains(taco::build_union_sql({&flat, &folder}, raw), "SELECT 'taco_flat.zip' AS source_file, taco.*"));
}

std::string collection_json(const std::string& version) {
    return R"({"taco:version":"3.0.0","id":"versioned","dataset_version":")" + version +
           R"(","description":"Versioned","licenses":["MIT"],"providers":[{"name":"Asterisk Labs"}],)"
           R"("tasks":["other"],"taco:structure":null,"taco:metadata":{"sample":{}}})";
}

std::string manifest_json(const std::string& first_collection = collection_json("1.0.0"),
                          const std::string& second_collection = collection_json("2.0.0")) {
    return R"({"taco:container":"versioned","taco:default_version":"2.0.0","taco:versions":{)"
           R"("1.0.0":{"href":"1.0.0/","collection":)" + first_collection + "},"
           R"("2.0.0":{"href":"2.0.0/","collection":)" + second_collection + "}}}";
}

std::string replace(std::string text, const std::string& from, const std::string& to) {
    const auto at = text.find(from);
    if (at != std::string::npos)
        text.replace(at, from.size(), to);
    return text;
}

void test_manifest() {
    const auto cases = taco::json::parse(read_file(TACO_TEST_CONFORMANCE));
    for (const auto& item : cases.find("manifest_candidates")->items) {
        const auto candidate = taco::manifest_candidate(item.find("source")->string);
        const auto* expected = item.find("expected");
        CHECK(expected->is_null() ? !candidate : candidate && *candidate == expected->string);
    }
    for (const auto& item : cases.find("manifest_hrefs")->items)
        CHECK(taco::join_manifest_href(item.find("candidate")->string, item.find("href")->string) ==
              item.find("expected")->string);

    CHECK(taco::join_manifest_href("hf://datasets/org/repo/taco.json", "1.0.0/") == "hf://datasets/org/repo/1.0.0/");
    CHECK(taco::join_manifest_href("/data/root/taco.json", "../2.0.0/") == "/data/root/../2.0.0");
    CHECK(taco::join_manifest_href("/data/root/taco.json", "/elsewhere/part.zip") == "/elsewhere/part.zip");

    const fs::path root = scratch("versioned");
    write_file(root / "taco.json", manifest_json());
    const auto resolved = taco::resolve_dataset(root.string());
    const auto value = taco::json::parse(resolved);
    CHECK(value.find("source")->string == (root / "2.0.0").generic_string());
    CHECK(value.find("version")->string == "2.0.0");
    CHECK(value.find("versions")->items.size() == 2);
    CHECK(value.find("versions")->items[0].string == "1.0.0");
    CHECK(value.find("manifest")->string == (root / "taco.json").generic_string());
    CHECK(value.find("collection")->find("dataset_version")->string == "2.0.0");
    CHECK(taco::json::parse(taco::resolve_dataset((root / "taco.json").string())).find("manifest")->string ==
          (root / "taco.json").string());

    const auto direct = taco::json::parse(taco::resolve_dataset(data("taco_flat.zip")));
    CHECK(direct.find("source")->string == data("taco_flat.zip"));
    CHECK(direct.find("collection")->is_null());
    CHECK(direct.find("manifest")->is_null());
    CHECK(taco::json::parse(taco::resolve_dataset(data("taco_folder"))).find("version")->is_null());
    const std::string folder_uri = "file://" + fs::absolute(data("taco_folder")).generic_string();
    const auto direct_uri = taco::json::parse(taco::resolve_dataset(folder_uri));
    CHECK(direct_uri.find("source")->string == folder_uri);
    CHECK(direct_uri.find("manifest")->is_null());

    const std::vector<std::pair<std::string, std::string>> invalid = {
        {replace(manifest_json(), "\"versioned\"", "\"zip\""), "taco:container must be 'versioned'"},
        {R"({"taco:container":"versioned","taco:default_version":"2.0.0","taco:versions":{}})", "at least one"},
        {replace(manifest_json(), "\"taco:default_version\":\"2.0.0\"", "\"taco:default_version\":\"3.0.0\""),
         "is not present in taco:versions"},
        {replace(manifest_json(), "\"1.0.0\":{", "\"latest\":{"), "must follow Semantic Versioning"},
        {replace(manifest_json(), "\"href\":\"1.0.0/\"", "\"href\":\"\""), "needs a non-empty href"},
        {manifest_json(collection_json("1.0.0"), collection_json("1.0.0")), "embeds collection dataset_version '1.0.0'"},
        {manifest_json(collection_json("1.0.0"), replace(collection_json("2.0.0"), "\"description\":\"Versioned\",", "")),
         "embeds an invalid collection: missing required fields description"},
        {"not JSON", "not valid JSON"},
    };
    for (const auto& [text, message] : invalid) {
        write_file(root / "taco.json", text);
        CHECK_THROWS(taco::resolve_dataset((root / "taco.json").string()), message);
    }
    const std::string no_tasks = "\"tasks\":[\"other\"],";
    write_file(root / "taco.json",
               manifest_json(replace(collection_json("1.0.0"), no_tasks, ""), replace(collection_json("2.0.0"), no_tasks, "")));
    CHECK(taco::json::parse(taco::resolve_dataset((root / "taco.json").string())).find("version")->string == "2.0.0");
    CHECK_THROWS(taco::resolve_dataset((root / "missing" / "taco.json").string()), "does not exist");
    CHECK_THROWS(taco::resolve_dataset("file://" + (root / "missing" / "taco.json").generic_string()),
                 "does not exist");
}

void set_environment(const char* name, const char* value) {
#ifdef _WIN32
    _putenv_s(name, value);
#else
    if (*value)
        setenv(name, value, 1);
    else
        unsetenv(name);
#endif
}

void test_cache() {
    const std::string cache = scratch("cache").generic_string();
    const fs::path copies = scratch("cache-sources");

    // A remote entry is trusted: the source can disappear and the dataset
    // still opens without a request. TACO_CACHE_REFRESH asks again.
    const fs::path archive = copies / "dataset.zip";
    fs::copy_file(data("taco_flat.zip"), archive);
    const std::string uri = "file://" + archive.generic_string();
    const auto first = taco::open_dataset(uri, cache);
    fs::remove(archive);
    const auto again = taco::open_dataset(uri, cache);
    CHECK(again.level_paths == first.level_paths);
    CHECK((again.level_names == Strings{"sample", "children"}));
    CHECK(again.collection == first.collection);
    set_environment("TACO_CACHE_REFRESH", "1");
    CHECK_THROWS(taco::open_dataset(uri, cache), "could not");
    set_environment("TACO_CACHE_REFRESH", "");

    fs::copy_options overwrite = fs::copy_options::overwrite_existing;
    fs::create_directories(copies / "folder");
    fs::copy(data("taco_folder"), copies / "folder", fs::copy_options::recursive | overwrite);
    const std::string folder = "file://" + (copies / "folder").generic_string();
    const auto cached_folder = taco::open_dataset(folder, cache);
    fs::remove_all(copies / "folder");
    CHECK(taco::open_dataset(folder, cache).container == taco::Container::folder);
    CHECK(taco::open_dataset(folder, cache).level_paths == cached_folder.level_paths);

    // Entries are named for people and the root carries CACHEDIR.TAG.
    Strings names;
    for (const auto& entry : fs::directory_iterator(cache))
        names.push_back(entry.path().filename().string());
    std::sort(names.begin(), names.end());
    CHECK(names.size() == 3);
    CHECK(names[0] == "CACHEDIR.TAG");
    CHECK(names[1].starts_with("taco-flat-folder-file-"));
    CHECK(names[2].starts_with("taco-flat-zip-file-"));
    const auto stamp = taco::json::parse(read_file(fs::path(cache) / names[2] / "taco-cache.json"));
    CHECK(stamp.find("source")->string == uri);
    CHECK(stamp.find("key")->string == "trusted");

    // Over the size cap, the least recently opened entries go first.
    set_environment("TACO_CACHE_SIZE", "1");
    fs::copy_file(data("taco_nested.zip"), copies / "nested.zip");
    taco::open_dataset("file://" + (copies / "nested.zip").generic_string(), cache);
    names.clear();
    for (const auto& entry : fs::directory_iterator(cache))
        names.push_back(entry.path().filename().string());
    std::sort(names.begin(), names.end());
    CHECK(names.size() == 2);
    CHECK(names[1].starts_with("taco-nested-zip-file-"));
    set_environment("TACO_CACHE_SIZE", "");

    // A local archive is checked against its size and modification time.
    const fs::path local = copies / "local.zip";
    fs::copy_file(data("taco_flat.zip"), local);
    CHECK((taco::open_dataset(local.string(), cache).level_names == Strings{"sample", "children"}));
    fs::copy_file(data("taco_nested.zip"), local, overwrite);
    fs::last_write_time(local, fs::file_time_type::clock::now() + std::chrono::seconds(5));
    CHECK((taco::open_dataset(local.string(), cache).level_names ==
           Strings{"sample", "children", "children/after", "children/before"}));
}

struct Event {
    std::string phase;
    std::uint64_t done;
    std::uint64_t total;
};

void record_progress(const char* phase, uint64_t done, uint64_t total, void* user) {
    static_cast<std::vector<Event>*>(user)->push_back(Event{phase, done, total});
}

void test_progress() {
    std::vector<Event> events;
    taco_set_progress(record_progress, &events);
    const fs::path out = scratch("progress");
    const std::string archive = data("taco_flat.zip");
    taco::fetch_files({taco::Fetch{archive, 0, 0, (out / "copy.zip").generic_string()},
                       taco::Fetch{archive, 0, 100, (out / "head.bin").generic_string()}},
                      4096);
    CHECK(events.size() >= 3);
    CHECK(events.front().phase == "downloading 2 files");
    CHECK(events.front().done == 0);
    CHECK(events.front().total == fs::file_size(archive) + 100);
    CHECK(events.back().done == events.back().total);
    for (std::size_t i = 1; i < events.size(); ++i)
        CHECK(events[i].done > events[i - 1].done);

    // A cache miss reports the metadata download under the dataset name.
    events.clear();
    const std::string cache = scratch("progress-cache").generic_string();
    taco::open_dataset(archive, cache);
    CHECK(!events.empty());
    CHECK(events.front().phase == "downloading taco_flat.zip metadata");
    CHECK(events.back().done == events.back().total && events.back().total > 0);
    events.clear();
    taco::open_dataset(archive, cache);
    CHECK(events.empty());
    taco::fetch_files({});
    CHECK(events.empty());
    taco_set_progress(nullptr, nullptr);
}

void test_fetch() {
    const fs::path out = scratch("fetch");
    const std::string archive = data("taco_flat.zip");
    const std::string whole = read_file(archive);
    const auto index = taco::read_cozip_index(archive);
    const taco::CozipEntry* collection = index.find("COLLECTION.json");
    const auto target = [&](const char* name) { return (out / name).generic_string(); };

    // A range, a whole object and a tail, into directories that do not exist yet.
    taco::fetch_files({taco::Fetch{archive, collection->offset, collection->size, target("a/COLLECTION.json")},
                       taco::Fetch{data("taco_folder/COLLECTION.json"), 0, 0, target("b/c/whole.json")},
                       taco::Fetch{archive, 10, 0, target("tail.bin")}});
    CHECK(read_file(out / "a" / "COLLECTION.json") == whole.substr(collection->offset, collection->size));
    CHECK(read_file(out / "b" / "c" / "whole.json") == read_file(data("taco_folder/COLLECTION.json")));
    CHECK(read_file(out / "tail.bin") == whole.substr(10));

    // Small chunks exercise the batches that keep large objects out of memory.
    taco::fetch_files({taco::Fetch{archive, 0, 0, target("chunked.zip")}, taco::Fetch{archive, 7, 100, target("piece.bin")}},
                      1000);
    CHECK(read_file(out / "chunked.zip") == whole);
    CHECK(read_file(out / "piece.bin") == whole.substr(7, 100));

    // A second copy replaces the first instead of appending to it.
    taco::fetch_files({taco::Fetch{archive, 0, 4, target("piece.bin")}});
    CHECK(read_file(out / "piece.bin") == "PK\x03\x04");

    CHECK_THROWS(taco::fetch_files({taco::Fetch{archive, index.file_size + 1, 0, target("x")}}), "past the end");
    CHECK_THROWS(taco::fetch_files({taco::Fetch{data("does_not_exist.zip"), 0, 4, target("y")}}), "could not");
    CHECK_THROWS(taco::fetch_files({taco::Fetch{archive, 0, 4, target("")}}), "could not write");
}

void test_c_api() {
    CHECK(taco_api_version() == TACO_API_VERSION);
    char* name = nullptr;
    CHECK(taco_profile(data("taco_flat.zip").c_str(), &name) == TACO_OK);
    CHECK(name && std::string(name) == "taco");
    taco_free(name);
    CHECK(taco_profile(nullptr, &name) == TACO_ERR_INVALID);
    CHECK(name == nullptr);
    CHECK(contains(taco_last_error(), "must not be NULL"));

    const std::string cache = scratch("capi").generic_string();
    taco_dataset* dataset = nullptr;
    CHECK(taco_open(data("taco_nested.zip").c_str(), cache.c_str(), &dataset) == TACO_OK);
    CHECK(dataset != nullptr);
    CHECK(std::string(taco_dataset_container(dataset)) == "zip");
    CHECK(std::string(taco_dataset_source(dataset)) == data("taco_nested.zip"));
    CHECK(taco_dataset_level_count(dataset) == 4);
    CHECK(std::string(taco_dataset_level(dataset, 3)) == "children/before");
    CHECK(taco_dataset_level(dataset, 4) == nullptr);
    CHECK(taco_dataset_structure_count(dataset) == 4);
    CHECK(std::string(taco_dataset_structure(dataset, 0)) == "before/B02.bin");
    CHECK(taco_dataset_derived(dataset) == nullptr);
    CHECK(contains(taco_dataset_collection(dataset), "taco-nested"));

    const char* files[] = {"change.bin"};
    taco_read_options options = {nullptr, nullptr, 0, files, 1, 1};
    char* sql = nullptr;
    const taco_dataset* datasets[] = {dataset};
    CHECK(taco_sql(datasets, 1, &options, &sql) == TACO_OK);
    CHECK(sql && contains(sql, "change.bin"));
    taco_free(sql);
    options.level = "nope";
    options.files = nullptr;
    CHECK(taco_sql(datasets, 1, &options, &sql) == TACO_ERR_INVALID);
    CHECK(sql == nullptr);
    CHECK(contains(taco_last_error(), "has no level 'nope'"));
    taco_close(dataset);

    CHECK(taco_open(data("taco_badversion").c_str(), cache.c_str(), &dataset) == TACO_ERR_INVALID);
    CHECK(dataset == nullptr);
    CHECK(taco_open(data("does_not_exist.zip").c_str(), cache.c_str(), &dataset) != TACO_OK);

    char* candidate = nullptr;
    CHECK(taco_manifest_candidate("https://example.test/data.zip", &candidate) == TACO_OK);
    CHECK(candidate == nullptr);
    CHECK(taco_manifest_candidate("https://example.test/data", &candidate) == TACO_OK);
    CHECK(candidate && std::string(candidate) == "https://example.test/data/taco.json");
    taco_free(candidate);
    char* resolved = nullptr;
    CHECK(taco_resolve(data("taco_flat.zip").c_str(), &resolved) == TACO_OK);
    CHECK(resolved && contains(resolved, "\"manifest\":null"));
    taco_free(resolved);

    const std::string archive = data("taco_flat.zip");
    const std::string head = cache + "/head.bin";
    const taco_fetch_item item = {archive.c_str(), 0, 4, head.c_str()};
    CHECK(taco_fetch(&item, 1) == TACO_OK);
    CHECK(read_file(head) == "PK\x03\x04");
    CHECK(taco_fetch(nullptr, 0) == TACO_OK);
    CHECK(taco_fetch(nullptr, 1) == TACO_ERR_INVALID);
    const taco_fetch_item unnamed = {nullptr, 0, 4, head.c_str()};
    CHECK(taco_fetch(&unnamed, 1) == TACO_ERR_INVALID);
    CHECK(contains(taco_last_error(), "must not be NULL"));
    taco_shutdown();
    taco_shutdown();
}

void test_remote() {
    const std::string cache = scratch("remote").generic_string();
    const std::string base = "hf://datasets/asterisk-labs/taco-api-fixtures/data/04-change-detection";

    const auto archive = taco::open_dataset(base + "/single-zip/dataset.zip", cache);
    CHECK(archive.container == taco::Container::zip);
    CHECK(archive.location_base ==
          "/vsihf/datasets/asterisk-labs/taco-api-fixtures/data/04-change-detection/single-zip/dataset.zip");
    CHECK(archive.level_names.size() == 4);

    const auto catalog = taco::open_dataset(base + "/by-split/.tacocat", cache);
    CHECK(catalog.container == taco::Container::tacocat);
    CHECK(catalog.location_base == "/vsihf/datasets/asterisk-labs/taco-api-fixtures/data/04-change-detection/by-split");

    const auto folder = taco::open_dataset(base + "/folder", cache);
    CHECK(folder.container == taco::Container::folder);
    CHECK(folder.level_paths[0].starts_with(cache));

    const auto https = taco::open_dataset(
        "https://huggingface.co/datasets/asterisk-labs/taco-api-fixtures/resolve/main/data/04-change-detection/single-zip/dataset.zip",
        cache);
    CHECK(https.location_base.starts_with("/vsicurl/https://huggingface.co/"));

    const auto source_coop = taco::open_dataset(
        "source://asterisk-labs/taco-api-fixtures/data/04-change-detection/by-split/.tacocat", cache);
    CHECK(source_coop.container == taco::Container::tacocat);
    CHECK(source_coop.location_base == "/vsisource/asterisk-labs/taco-api-fixtures/data/04-change-detection/by-split");

    const auto resolved = taco::json::parse(taco::resolve_dataset(base + "/folder"));
    CHECK(resolved.find("manifest")->is_null());

    const fs::path out = scratch("remote-fetch");
    taco::fetch_files({taco::Fetch{base + "/single-zip/dataset.zip", 0, 4, (out / "head.bin").generic_string()},
                       taco::Fetch{base + "/folder/COLLECTION.json", 0, 0, (out / "COLLECTION.json").generic_string()}});
    CHECK(read_file(out / "head.bin") == "PK\x03\x04");
    CHECK(contains(read_file(out / "COLLECTION.json"), "\"taco:version\""));
}

} // namespace

int main() {
    test_json();
    test_paths();
    test_cozip_index();
    test_open_archives();
    test_open_directories();
    test_sql();
    test_manifest();
    test_cache();
    test_progress();
    test_fetch();
    test_c_api();
    if (remote_tests())
        test_remote();
    taco_shutdown();
    std::printf("%d checks, %d failures\n", checks, failures);
    return failures == 0 ? 0 : 1;
}
