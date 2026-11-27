#include "cache.hpp"
#include "cozip_index.hpp"
#include "dataset.hpp"
#include "error.hpp"
#include "json.hpp"
#include "paths.hpp"
#include "rumi.hpp"
#include "sql.hpp"
#include "transport.hpp"

#include <taco/taco.h>

#include <array>
#include <cstdio>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <functional>
#include <optional>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

#ifndef _WIN32
#include <sys/wait.h>
#include <unistd.h>
#endif

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

// The snapshot directory a remote dataset reads its levels from.
fs::path snapshot_of(const taco::Dataset& dataset) {
    return fs::path(dataset.level_paths.front()).parent_path().parent_path();
}

// The names under a cache root, without the snapshots of open datasets.
Strings stored(const fs::path& root) {
    Strings names;
    for (const auto& entry : fs::directory_iterator(root)) {
        if (!entry.path().filename().string().starts_with(".open-"))
            names.push_back(entry.path().filename().string());
    }
    std::sort(names.begin(), names.end());
    return names;
}

void test_json() {
    using taco::json::Kind;
    const std::string text = R"({"a": [1, 2.5e3, -0.1, true, false, null], "b": "x\u00e9\ud83d\ude00\n"})";
    const auto value = taco::json::parse(text);
    CHECK(value.is_object());
    CHECK(value.members.size() == 2);
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

    // JSON readers keep different values of a repeated key, so none is read.
    CHECK_THROWS(taco::json::parse(R"({"a": 1, "b": 2, "a": 3})"), "duplicate key \"a\" at byte 20");
    CHECK_THROWS(taco::json::parse(R"([{"x": {"ab": 1, "ab": 2}}])"), "duplicate key \"ab\"");
    CHECK(taco::json::parse(R"([{"a": 1}, {"a": 2}, {"b": {"a": 3}}])").items.size() == 3);

    // A number beyond the range of a double is refused, as NaN is.
    const std::string largest = "1" + std::string(308, '0');
    for (const auto& number : {std::string("1e400"), std::string("-1e400"), std::string("1.8e308"), largest + "0",
                               std::string("0.1e310"), std::string("1e99999999999999999999")})
        CHECK_THROWS(taco::json::parse("[" + number + "]"), "number does not fit a double");
    for (const auto& number : {std::string("1.7976931348623157e308"), std::string("-1.7976931348623157E+308"), largest,
                               std::string("1e-400"), std::string("0e99999999999999999999"), std::string("123.456e-7")})
        CHECK(taco::json::parse(number).kind == Kind::number);

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

    // Components appended to an HTTP URL are encoded like encodeURIComponent.
    CHECK(taco::is_http_url("http://host/a") && taco::is_http_url("https://host/a"));
    CHECK(!taco::is_http_url("s3://bucket/a") && !taco::is_http_url("/vsicurl/https://host/a"));
    CHECK(taco::encode_uri_path("0/my dir/hash#1 pct%41.tif") == "0/my%20dir/hash%231%20pct%2541.tif");
    CHECK(taco::encode_uri_path("A-z_0.9!~*'()/+,;=:@&$[]{}\"\\") ==
          "A-z_0.9!~*'()/%2B%2C%3B%3D%3A%40%26%24%5B%5D%7B%7D%22%5C");
    CHECK(taco::encode_uri_path("\xc3\xa9t\xc3\xa9.tif") == "%C3%A9t%C3%A9.tif");

    // A file:// URI names its path percent-decoded. A '%' that starts no
    // escape stays, and so does %00.
    CHECK(taco::file_uri_path("file:///data/my%20dir/a%23b%2541%c3%A9.zip") == "/data/my dir/a#b%41\xc3\xa9.zip");
    CHECK(taco::file_uri_path("file:///data/100%/x%2/y%zz%00") == "/data/100%/x%2/y%zz%00");
    CHECK(taco::file_uri_path("file://relative/a%2Fb") == "relative/a/b");
    CHECK(taco::file_uri_path("file://").empty());
#ifdef _WIN32
    CHECK(taco::file_uri_path("file:///C:/my%20data") == "C:/my data");
#endif

    // A TACOCAT partition is a normalized relative POSIX path ending in .zip.
    for (const char* valid : {"a.zip", "parts/a b.zip", "x/y/z.zip", "..a.zip"})
        CHECK(taco::is_partition_path(valid));
    for (const char* invalid : {"", ".zip/", "a", "a.ZIP", "../a.zip", "x/../a.zip", "./a.zip", "x/./a.zip", "/a.zip",
                                "x//a.zip", "x/", "parts\\a.zip", "../../../../etc/hosts.zip"})
        CHECK(!taco::is_partition_path(invalid));
    CHECK(!taco::is_partition_path(std::string("a\0b.zip", 7)));

    CHECK(taco::level_to_file("children/before") == "children__before.parquet");
    CHECK(taco::level_to_file("children/a_/b") == taco::level_to_file("children/a/_b"));
    CHECK(taco::parent_level("children") == "sample");
    CHECK(taco::parent_level("children/before/red") == "children/before");
    CHECK(taco::last_segment("children/before") == "before");
    CHECK(taco::sql_literal("it's") == "'it''s'");
    CHECK(taco::sql_identifier("a\"b") == "\"a\"\"b\"");
    CHECK(taco::glob_escape("/d/run[1]/a?b*c.parquet") == "/d/run[[]1[]]/a[?]b[*]c.parquet");
    CHECK(taco::glob_escape("/d/it's {x}\\y") == "/d/it's {x}\\y");
    CHECK(taco::hex64(0x1234) == "0000000000001234");

    CHECK(taco::rumi_file_suffix("rumi:header") == "header");
    CHECK(taco::rumi_file_suffix("rumi:mean_t0_b3") == "mean_t0_b3");
    CHECK(!taco::rumi_file_suffix("rumi:stats"));
    CHECK(!taco::rumi_file_suffix("rumi:mean_b01"));
}

void test_cozip_index() {
    const auto index = taco::read_cozip_index(data("taco_flat.zip"));
    CHECK(index.profile == taco::cozip_profile_taco);
    CHECK(index.find("COLLECTION.json") != nullptr);
    CHECK(index.find("METADATA/sample.parquet") != nullptr);
    CHECK(index.find("METADATA/children.parquet") != nullptr);
    CHECK(index.find("DATA/0/image.bin") == nullptr);
    CHECK(index.integrity != 0);
    CHECK(taco::read_cozip_integrity(data("taco_flat.zip")) == index.integrity);

    CHECK(taco::profile_name(taco::read_cozip_profile(data("flat_simple.zip"))) == "flat");
    CHECK(taco::profile_name(taco::read_cozip_profile(data("unsupported_profile.zip"))).starts_with("unknown:"));
    CHECK_THROWS(taco::read_cozip_index(data("flat_bad_hash.zip")), "integrity hash mismatch");
    CHECK_THROWS(taco::read_cozip_index(data("does_not_exist.zip")), "could not open");
    CHECK_THROWS(taco::read_cozip_index(data("taco_folder/COLLECTION.json")), "cozip archive too small");

    // Only CoZIP binary version 1 is read, so older and newer versions both fail.
    const fs::path versions = scratch("cozip-versions");
    for (const unsigned char version : {0, 2}) {
        auto bytes = read_file(data("taco_flat.zip"));
        bytes[51 + 4] = static_cast<char>(version);
        bytes[51 + 5] = 0;
        const fs::path archive = versions / ("v" + std::to_string(version) + ".zip");
        std::ofstream(archive, std::ios::binary) << bytes;
        CHECK_THROWS(taco::read_cozip_index(archive.generic_string()), "unsupported cozip format version");
    }
}

void test_open_archives() {
    const std::string cache = scratch("archives").generic_string();

    const auto flat = taco::open_dataset(data("taco_flat.zip"), cache);
    CHECK(flat.container == taco::Container::zip);
    CHECK(flat.source == data("taco_flat.zip"));
    CHECK(flat.location_base == data("taco_flat.zip"));
    CHECK((flat.level_names == Strings{"sample", "children"}));
    CHECK((flat.contract.structure == Strings{"image.bin", "label.bin"}));
    CHECK(contains(flat.collection, "\"taco-flat\""));
    // Local archives are extracted to a temporary directory, never to the cache.
    for (const auto& path : flat.level_paths)
        CHECK(!path.starts_with(cache) && fs::is_regular_file(path));
    CHECK(!fs::exists(cache) || fs::is_empty(cache));

    // Open handles for the same archive share one extraction.
    const auto again = taco::open_dataset(data("taco_flat.zip"), cache);
    CHECK(again.level_paths == flat.level_paths);

    const fs::path reused_archive = scratch("reused-archive") / "dataset.zip";
    fs::copy_file(data("taco_flat.zip"), reused_archive);
    const auto reused = taco::open_dataset(reused_archive.string(), cache);
    const auto reused_time = fs::last_write_time(reused_archive);
    {
        std::fstream stream(reused_archive, std::ios::in | std::ios::out | std::ios::binary);
        stream.write("bad!", 4);
    }
    fs::last_write_time(reused_archive, reused_time);
    CHECK(taco::open_dataset(reused_archive.string(), cache).level_paths == reused.level_paths);

    // A changed archive at the same path is extracted again.
    const fs::path moving = scratch("moving") / "dataset.zip";
    fs::copy_file(data("taco_flat.zip"), moving);
    const auto first = taco::open_dataset(moving.string(), cache);
    fs::copy_file(data("taco_nested.zip"), moving, fs::copy_options::overwrite_existing);
    fs::last_write_time(moving, fs::file_time_type::clock::now() + std::chrono::seconds(5));
    const auto second = taco::open_dataset(moving.string(), cache);
    CHECK(first.level_paths[0] != second.level_paths[0]);
    CHECK((second.level_names == Strings{"sample", "children", "children/after", "children/before"}));

    const auto nested = taco::open_dataset(data("taco_nested.zip"), cache);
    CHECK((nested.level_names == Strings{"sample", "children", "children/after", "children/before"}));
    CHECK(nested.contract.structure.size() == 4);
    CHECK(nested.contract.fields_of("children/before") &&
          *nested.contract.fields_of("children/before") == Strings{"raster:resolution"});

    const fs::path process_directory = fs::path(flat.level_paths[0]).parent_path().parent_path();
    CHECK(fs::is_directory(process_directory));

#ifndef _WIN32
    std::optional<taco::Dataset> inherited = taco::open_dataset(data("taco_flat.zip"), cache);
    const pid_t child = fork();
    CHECK(child >= 0);
    if (child == 0) {
        inherited.reset();
        std::exit(0);
    }
    int status = 0;
    CHECK(waitpid(child, &status, 0) == child);
    CHECK(WIFEXITED(status) && WEXITSTATUS(status) == 0);
    CHECK(fs::is_directory(process_directory));
    for (const auto& path : flat.level_paths)
        CHECK(fs::is_regular_file(path));

    int descriptors[2] = {-1, -1};
    CHECK(pipe(descriptors) == 0);
    const pid_t owner = fork();
    CHECK(owner >= 0);
    if (owner == 0) {
        close(descriptors[0]);
        const auto owned = taco::open_dataset(data("taco_nested.zip"), cache);
        const std::string directory = fs::path(owned.level_paths[0]).parent_path().parent_path().string();
        static_cast<void>(write(descriptors[1], directory.data(), directory.size()));
        close(descriptors[1]);
        std::exit(0);
    }
    close(descriptors[1]);
    std::array<char, 4096> directory{};
    const auto length = read(descriptors[0], directory.data(), directory.size());
    close(descriptors[0]);
    CHECK(waitpid(owner, &status, 0) == owner);
    CHECK(WIFEXITED(status) && WEXITSTATUS(status) == 0);
    CHECK(length > 0);
    if (length > 0)
        CHECK(!fs::exists(fs::path(std::string(directory.data(), static_cast<std::size_t>(length)))));
#endif

    CHECK_THROWS(taco::open_dataset(data("taco_badstructure"), cache), "taco:structure must be a non-empty array");

    const auto variable = taco::open_dataset(data("taco_variable.zip"), cache);
    CHECK((variable.contract.structure == Strings{"img*[1,3].bin", "mask.bin"}));

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

    const auto catalog_root = taco::open_dataset(data("taco_cat"), cache);
    CHECK(catalog_root.container == taco::Container::tacocat);
    CHECK(catalog_root.source == data("taco_cat/.tacocat"));
    CHECK(catalog_root.location_base == data("taco_cat"));

    CHECK_THROWS(taco::open_dataset(data("taco_badjson"), cache), "COLLECTION.json is not valid JSON");
    CHECK_THROWS(taco::open_dataset(data("taco_badversion"), cache), "unsupported TACO version '2.0.0'");
    CHECK_THROWS(taco::open_dataset(data(""), cache), "directory has no COLLECTION.json");

    // file:// URIs take the object-store path: levels come from COLLECTION.json.
    const std::string root = "file://" + fs::absolute(TACO_TEST_DATA).generic_string();
    const auto uri_folder = taco::open_dataset(root + "/taco_folder", cache);
    CHECK(uri_folder.container == taco::Container::folder);
    CHECK((uri_folder.level_names == Strings{"sample", "children"}));
    CHECK(uri_folder.level_paths[0].starts_with(cache));
    const auto uri_catalog = taco::open_dataset(root + "/taco_cat/.tacocat/", cache);
    CHECK(uri_catalog.container == taco::Container::tacocat);
    const auto uri_catalog_root = taco::open_dataset(root + "/taco_cat/", cache);
    CHECK(uri_catalog_root.container == taco::Container::tacocat);
    CHECK(uri_catalog_root.source == root + "/taco_cat/.tacocat");
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
    CHECK(contains(wide, "read_parquet(" + taco::sql_literal(nested.level_paths[0]) + ", hive_partitioning = false)"));
    CHECK(contains(wide, "'/vsisubfile/'"));
    CHECK(contains(wide, taco::sql_literal(data("taco_nested.zip"))));
    CHECK(contains(wide, "AS \"taco:sample_index\""));
    CHECK(contains(wide, "c != 'taco:sample_index'"));
    CHECK(contains(wide, "\"id\" AS id"));
    CHECK(contains(wide, "\"before/B02.bin::location\""));
    CHECK(!contains(wide, "before__B02.bin"));
    CHECK(!contains(wide, "::header"));
    CHECK(contains(wide, "LEFT JOIN LATERAL"));
    CHECK(contains(wide, "l1.\"internal:parent_id\" = l0.\"internal:current_id\""));
    CHECK(!contains(wide, "pivoted AS"));

    // A level that declares rumi:header carries it next to every location.
    auto rumi = nested;
    for (auto& [name, fields] : rumi.contract.fields) {
        if (name == "children/before")
            fields.push_back("rumi:header");
    }
    const auto rumi_wide = taco::build_sql(rumi, taco::ReadOptions{});
    CHECK(contains(rumi_wide, "AS \"before/B02.bin::header\""));
    CHECK(contains(rumi_wide, "AS \"before/B02.bin::location\""));
    CHECK(!contains(rumi_wide, "AS \"change.bin::header\""));
    taco::ReadOptions rumi_quiet;
    rumi_quiet.location = false;
    CHECK(contains(taco::build_sql(rumi, rumi_quiet), "NULL::BLOB AS \"before/B02.bin::header\""));

    // Rumi statistics follow the header.
    const Strings statistics = {"mean", "p98_t0_b3", "minimum_t12", "stddev_b0"};
    for (auto& [name, fields] : rumi.contract.fields) {
        if (name != "children/before")
            continue;
        for (const auto& field : statistics)
            fields.push_back("rumi:" + field);
    }
    const auto stats_wide = taco::build_sql(rumi, taco::ReadOptions{});
    for (const auto& field : statistics)
        CHECK(contains(stats_wide, "AS \"before/B02.bin::" + field + "\""));
    CHECK(stats_wide.find("AS \"before/B02.bin::header\"") < stats_wide.find("AS \"before/B02.bin::mean\""));
    const auto stats_quiet = taco::build_sql(rumi, rumi_quiet);
    CHECK(contains(stats_quiet, "NULL::DOUBLE AS \"before/B02.bin::mean\""));

    const auto selected_stats = taco::open_dataset(data("taco_rumi.zip"), cache);
    const auto selected_wide = taco::build_sql(selected_stats, taco::ReadOptions{});
    CHECK(contains(selected_wide, "AS \"scene/image.rumi::mean\""));
    CHECK(!contains(selected_wide, "scene/image.rumi::maximum_b1"));
    CHECK(contains(selected_wide, "AS \"scene/series::maximum_b1\""));
    CHECK(!contains(selected_wide, "scene/series::mean"));

    taco::ReadOptions long_quiet;
    long_quiet.pivot = false;
    long_quiet.location = false;
    CHECK(!contains(taco::build_sql(nested, long_quiet), "\"taco:location\""));
    long_quiet.location = true;
    CHECK(contains(taco::build_sql(nested, long_quiet), "\"taco:location\""));

    taco::ReadOptions level;
    level.level = "children/before";
    CHECK(taco::build_sql(nested, level) ==
          "SELECT COLUMNS(lambda c: c != 'cozip:location' AND c != 'taco:location' AND c != "
          "'taco:sample_index') FROM read_parquet(" +
              taco::sql_literal(nested.level_paths[3]) + ", hive_partitioning = false)");

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
    CHECK(contains(catalog_sql, "source_file"));
    // A row is read from a partition taco:sources lists, and a row naming any
    // other file fails the query.
    CHECK((catalog.partitions == Strings{"part_train.zip", "part_validation.zip"}));
    CHECK(contains(catalog_sql, "CASE l1.\"internal:source_file\" WHEN 'part_train.zip' THEN " +
                                    taco::sql_literal(data("taco_cat") + "/part_train.zip") + " WHEN 'part_validation.zip'"));
    CHECK(contains(catalog_sql, " ELSE error(concat('TACOCAT row names a partition outside taco:sources: ', "
                                "l1.\"internal:source_file\")) END"));

    // read_parquet takes [ ] ? * as a glob, so run[1] must not also read run1.
    const fs::path globs = scratch("glob");
    fs::copy(data("taco_folder"), globs / "run[1]", fs::copy_options::recursive);
    fs::copy(data("taco_folder"), globs / "run1", fs::copy_options::recursive);
    const auto bracket = taco::open_dataset((globs / "run[1]").generic_string(), cache);
    const std::string scan = "read_parquet(" + taco::sql_literal(taco::glob_escape(bracket.level_paths[0])) +
                             ", hive_partitioning = false)";
    CHECK(contains(scan, "/run[[]1[]]/METADATA/sample.parquet'"));
    taco::ReadOptions long_form;
    long_form.pivot = false;
    taco::ReadOptions sample_level;
    sample_level.level = "sample";
    for (const auto& options : {taco::ReadOptions{}, long_form, rumi_quiet, sample_level}) {
        const auto sql = taco::build_sql(bracket, options);
        CHECK(contains(sql, scan));
        CHECK(!contains(sql, "run[1]/METADATA"));
    }

    const auto flat = taco::open_dataset(data("taco_flat.zip"), cache);
    const auto parts = taco::build_union_sql({&flat, &folder}, taco::ReadOptions{});
    CHECK(contains(parts, "'taco_flat.zip' AS source_file"));
    CHECK(contains(parts, "'taco_folder' AS source_file"));
    CHECK(contains(parts, "UNION ALL BY NAME"));
    taco::ReadOptions raw;
    raw.level = "sample";
    const auto raw_samples = taco::build_union_sql({&flat, &folder}, raw);
    CHECK(contains(raw_samples, "SELECT 'taco_flat.zip' AS source_file, taco.*"));

    // Raw level IDs continue across a source list, as in TACOCAT.
    const auto count = [](const std::string& path) {
        return "(SELECT count(*) FROM read_parquet(" + taco::sql_literal(taco::glob_escape(path)) +
               ", hive_partitioning = false))";
    };
    CHECK(contains(raw_samples, "SELECT 0 AS taco_source, " + count(flat.level_paths[0]) + " AS taco_rows UNION ALL "
                                "SELECT 1 AS taco_source, " + count(folder.level_paths[0]) + " AS taco_rows"));
    CHECK(contains(raw_samples, "taco.* REPLACE (taco.\"internal:current_id\" + o.taco_current AS "
                                "\"internal:current_id\") FROM ("));
    CHECK(contains(raw_samples, "JOIN taco_offsets AS o ON o.taco_source = 1"));
    CHECK(!contains(raw_samples, "internal:parent_id"));
    raw.level = "children";
    const auto raw_children = taco::build_union_sql({&flat, &folder}, raw);
    CHECK(contains(raw_children, "SELECT 1 AS taco_source, " + count(folder.level_paths[1]) + " AS taco_rows, " +
                                     count(folder.level_paths[0]) + " AS taco_parent_rows"));
    CHECK(contains(raw_children, ", taco.\"internal:parent_id\" + o.taco_parent AS \"internal:parent_id\")"));
    CHECK(taco::build_sql(flat, raw) == taco::build_union_sql({&flat}, raw));

    // The long view uses the deeper field when names differ only by case.
    taco::Dataset cased;
    cased.source = "/data/cased.zip";
    cased.location_base = cased.source;
    cased.level_names = {"sample", "children"};
    cased.level_paths = {"/data/sample.parquet", "/data/children.parquet"};
    cased.contract.structure = {"a.tif"};
    cased.contract.fields = {{"sample", {"q:Score", "q:keep"}}, {"children", {"q:score"}}};
    taco::ReadOptions long_cased;
    long_cased.pivot = false;
    const auto cased_sql = taco::build_sql(cased, long_cased);
    CHECK(contains(cased_sql, ", l0.\"q:keep\", l1.\"q:score\" FROM l1"));
    CHECK(!contains(cased_sql, "\"q:Score\""));
    CHECK(contains(cased_sql, ", \"q:keep\", \"q:score\" FROM ("));

    // img*[1,3].tif names img0.tif to img2.tif, so img10.tif is the fixed file
    // it is declared as, in both views.
    taco::Dataset bounded = cased;
    bounded.contract.structure = {"img*[1,3].tif", "img10.tif"};
    bounded.contract.fields = {{"sample", {}}, {"children", {}}};
    const std::string pattern = R"('^img(0|[1-9][0-9]*)\.tif$')";
    const std::string index = "TRY_CAST(regexp_extract(path, " + pattern + ", 1) AS UBIGINT)";
    const auto bounded_wide = taco::build_sql(bounded, taco::ReadOptions{});
    CHECK(contains(bounded_wide, "ORDER BY " + index + ") FILTER (WHERE " + index + " < 3) AS \"img::location\""));
    CHECK(contains(bounded_wide, "MAX(CASE WHEN path = 'img10.tif' THEN \"taco:location\" END) AS \"img10.tif::location\""));
    taco::ReadOptions sequence_files;
    sequence_files.pivot = false;
    sequence_files.has_files = true;
    sequence_files.files = {"img*[1,3].tif"};
    CHECK(contains(taco::build_sql(bounded, sequence_files),
                   " WHERE (TRY_CAST(regexp_extract(regexp_replace(l1.\"internal:relative_path\", '^[0-9]+/', ''), " +
                       pattern + ", 1) AS UBIGINT) < 3)"));
    sequence_files.pivot = true;
    CHECK(contains(taco::build_sql(bounded, sequence_files), "FILTER (WHERE " + index + " < 3)"));

    // Paths appended to an HTTP URL are percent-encoded; the URL is not.
    taco::Dataset web;
    web.source = "http://host/my%20data/folder";
    web.container = taco::Container::folder;
    web.location_base = "/vsicurl/" + web.source;
    web.level_names = cased.level_names;
    web.level_paths = cased.level_paths;
    web.contract.structure = {"with space.tif"};
    web.contract.fields = {{"sample", {}}, {"children", {}}};
    const auto web_sql = taco::build_sql(web, taco::ReadOptions{});
    CHECK(contains(web_sql, "'/vsicurl/http://host/my%20data/folder/DATA/' || replace(replace(replace(replace(replace("
                            "replace(url_encode(l1.\"internal:relative_path\"), '%2F', '/'), '%21', '!'), '%2A', '*'), "
                            "'%27', ''''), '%28', '('), '%29', ')')"));
    web.container = taco::Container::tacocat;
    web.partitions = {"parts/my part#1.zip"};
    CHECK(contains(taco::build_sql(web, taco::ReadOptions{}),
                   "WHEN 'parts/my part#1.zip' THEN '/vsicurl/http://host/my%20data/folder/parts/my%20part%231.zip'"));
    for (const char* base : {"/vsis3/bucket/data", "/vsihf/datasets/o/r", "/data/local"}) {
        web.location_base = base;
        web.container = taco::Container::folder;
        CHECK(!contains(taco::build_sql(web, taco::ReadOptions{}), "url_encode"));
        web.container = taco::Container::tacocat;
        CHECK(contains(taco::build_sql(web, taco::ReadOptions{}), "THEN '" + std::string(base) + "/parts/my part#1.zip'"));
    }

    // A FOLDER row whose path is not normalized fails the query in both views,
    // over HTTP too, so no location leaves DATA/.
    const std::string checked = R"(CASE WHEN regexp_full_match(l1."internal:relative_path", '[^/\\]+(/[^/\\]+)*') )"
                                R"(AND NOT regexp_matches(l1."internal:relative_path", '(^|/)\.\.?(/|$)') THEN )";
    const std::string refused = R"( ELSE error(concat('FOLDER row has a relative path that is not normalized: ', )"
                                R"(l1."internal:relative_path")) END)";
    web.container = taco::Container::folder;
    for (const char* base : {"/data/local", "/vsicurl/https://host/folder"}) {
        web.location_base = base;
        for (const bool pivot : {true, false}) {
            taco::ReadOptions view;
            view.pivot = pivot;
            const auto sql = taco::build_sql(web, view);
            CHECK(contains(sql, checked + taco::sql_literal(std::string(base) + "/DATA/") + " || "));
            CHECK(contains(sql, refused));
        }
    }
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

    // A remote entry is revalidated before reuse.
    const fs::path archive = copies / "dataset.zip";
    fs::copy_file(data("taco_flat.zip"), archive);
    const std::string uri = "file://" + archive.generic_string();
    const auto first = taco::open_dataset(uri, cache);
    const auto again = taco::open_dataset(uri, cache);
    // Datasets read a snapshot of the entry, one for each version of it.
    CHECK(snapshot_of(first).parent_path() == fs::path(cache));
    CHECK(snapshot_of(first).filename().string().starts_with(".open-"));
    CHECK(snapshot_of(again) == snapshot_of(first));
    CHECK((again.level_names == Strings{"sample", "children"}));
    CHECK(again.collection == first.collection);
    fs::remove(archive);
    CHECK_THROWS(taco::open_dataset(uri, cache), "could not");
    fs::copy_file(data("taco_flat.zip"), archive);
    set_environment("TACO_CACHE_REFRESH", "1");
    CHECK((taco::open_dataset(uri, cache).level_names == Strings{"sample", "children"}));
    set_environment("TACO_CACHE_REFRESH", "");

    fs::copy_options overwrite = fs::copy_options::overwrite_existing;
    fs::create_directories(copies / "folder");
    fs::copy(data("taco_folder"), copies / "folder", fs::copy_options::recursive | overwrite);
    const std::string folder = "file://" + (copies / "folder").generic_string();
    const auto cached_folder = taco::open_dataset(folder, cache);
    const auto cached_sample_size = fs::file_size(cached_folder.level_paths[0]);
    {
        std::ofstream stream(copies / "folder" / "METADATA" / "sample.parquet", std::ios::app | std::ios::binary);
        stream.put('\0');
    }
    const auto refreshed_folder = taco::open_dataset(folder, cache);
    CHECK(fs::file_size(refreshed_folder.level_paths[0]) == cached_sample_size + 1);
    CHECK(fs::file_size(cached_folder.level_paths[0]) == cached_sample_size);
    {
        std::ofstream stream(copies / "folder" / "COLLECTION.json", std::ios::app | std::ios::binary);
        stream << '\n';
    }
    CHECK(taco::open_dataset(folder, cache).collection == cached_folder.collection + "\n");
    CHECK(taco::open_dataset(folder, cache).container == taco::Container::folder);

    // Entries are named for people and the root carries CACHEDIR.TAG.
    Strings names = stored(cache);
    CHECK(names.size() == 3);
    CHECK(names[0] == "CACHEDIR.TAG");
    CHECK(names[1].starts_with("taco-flat-folder-file-"));
    CHECK(names[2].starts_with("taco-flat-zip-file-"));
    const auto stamp = taco::json::parse(read_file(fs::path(cache) / names[2] / "taco-cache.json"));
    CHECK(stamp.find("source")->string == uri);
    CHECK(stamp.find("key")->string ==
          "remote cozip " + taco::hex64(taco::read_cozip_integrity(archive.generic_string())));

    // Over the size cap, the least recently opened entries go first.
    set_environment("TACO_CACHE_SIZE", "1");
    fs::copy_file(data("taco_nested.zip"), copies / "nested.zip");
    taco::open_dataset("file://" + (copies / "nested.zip").generic_string(), cache);
    names = stored(cache);
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

using Files = std::vector<std::pair<std::string, std::string>>;

void put(std::string& out, std::uint64_t value, int bytes) {
    for (int i = 0; i < bytes; ++i)
        out += static_cast<char>((value >> (8 * i)) & 0xFF);
}

// A TACO-profile cozip archive holding entries, which is all the reader needs.
std::string cozip_archive(const Files& entries) {
    std::uint64_t payload_size = 11;
    for (const auto& [name, content] : entries)
        payload_size += 2 + name.size() + 16;
    std::string payload = "CZIP";
    put(payload, 1, 2);
    put(payload, taco::cozip_profile_taco, 1);
    put(payload, entries.size(), 4);
    for (const auto& [name, content] : entries)
        put(payload, name.size(), 2);
    for (const auto& [name, content] : entries)
        payload += name;
    std::uint64_t offset = 51 + payload_size;
    for (const auto& [name, content] : entries) {
        put(payload, offset, 8);
        offset += content.size();
    }
    for (const auto& [name, content] : entries)
        put(payload, content.size(), 8);

    std::string archive;
    put(archive, 0x04034B50, 4);
    put(archive, 20, 2);
    put(archive, 0, 12);
    put(archive, payload.size(), 4);
    put(archive, payload.size(), 4);
    put(archive, 9, 2);
    put(archive, 12, 2);
    archive += "__cozip__";
    put(archive, 0xCA0C, 2);
    put(archive, 8, 2);
    put(archive, 0, 8);
    archive += payload;
    for (const auto& [name, content] : entries)
        archive += content;
    archive.resize(std::max<std::size_t>(archive.size(), 51 + 32768), '\0');
    // The integrity hash covers the payload and the last 32 KiB once each.
    const std::size_t hashed = std::max<std::size_t>(51 + payload.size(), archive.size() - 32768);
    std::string integrity;
    put(integrity, taco::fnv1a64(std::string_view(archive).substr(hashed), taco::fnv1a64(payload)), 8);
    archive.replace(43, 8, integrity);
    return archive;
}

constexpr const char* one_partition = R"({"samples": 1, "partitions": [{"file": "part.zip", "samples": 1}]})";

// sources, when given, is the taco:sources of a catalog.
std::string collection_json(const Strings& structure, const Strings& levels, const std::string& sources = "") {
    std::string out = "{\"taco:version\": \"3.0.0\", \"id\": \"levels\", \"taco:structure\": [";
    for (std::size_t i = 0; i < structure.size(); ++i)
        out += (i ? ", " : "") + taco::json::quote(structure[i]);
    out += "], \"taco:metadata\": {";
    for (std::size_t i = 0; i < levels.size(); ++i)
        out += (i ? ", " : "") + taco::json::quote(levels[i]) + ": {}";
    out += "}";
    if (!sources.empty())
        out += ", \"taco:sources\": " + sources;
    return out + "}";
}

// COLLECTION.json and a placeholder Parquet file for every level.
Files dataset_files(const Strings& structure, const Strings& levels) {
    Files files = {{"COLLECTION.json", collection_json(structure, levels)}};
    for (const auto& level : levels)
        files.emplace_back("METADATA/" + taco::level_to_file(level), "PAR1");
    return files;
}

void write_folder(const fs::path& folder, const Files& files) {
    for (const auto& [name, content] : files)
        write_file(folder / name, content);
}

void test_level_names() {
    const std::string cache = scratch("levels-cache").generic_string();
    const fs::path root = scratch("levels");

    // children__a___b.parquet is the file of children/a_/b and of children/a/_b,
    // so only COLLECTION.json can tell which level it holds.
    const std::vector<std::pair<Strings, Strings>> contracts = {
        {{"a_/b/x.tif"}, {"sample", "children", "children/a_", "children/a_/b"}},
        {{"a/b_/c/x.tif"}, {"sample", "children", "children/a", "children/a/b_", "children/a/b_/c"}},
    };
    for (std::size_t i = 0; i < contracts.size(); ++i) {
        const auto& [structure, levels] = contracts[i];
        const auto files = dataset_files(structure, levels);
        const fs::path folder = root / ("folder" + std::to_string(i));
        write_folder(folder, files);
        const fs::path archive = root / ("archive" + std::to_string(i) + ".zip");
        std::ofstream(archive, std::ios::binary) << cozip_archive(files);
        const fs::path catalog = root / ("catalog" + std::to_string(i));
        write_file(catalog / ".tacocat" / "COLLECTION.json", collection_json(structure, levels, one_partition));
        for (const auto& level : levels)
            write_file(catalog / ".tacocat" / taco::level_to_file(level), "PAR1");

        for (const auto& path : {folder, archive, catalog}) {
            for (const auto& source : {path.generic_string(), "file://" + path.generic_string()}) {
                const auto dataset = taco::open_dataset(source, cache);
                CHECK(dataset.level_names == levels);
                CHECK(dataset.level_paths.back().ends_with("/" + taco::level_to_file(levels.back())));
                CHECK(fs::is_regular_file(dataset.level_paths.back()));
            }
        }
    }

    // Levels that would share a file are refused, whatever the case of their names.
    const fs::path shared = root / "shared";
    write_folder(shared, dataset_files({"a_/b/x.tif", "a/_b/y.tif"},
                                       {"sample", "children", "children/a_", "children/a", "children/a_/b",
                                        "children/a/_b"}));
    CHECK_THROWS(taco::open_dataset(shared.generic_string(), cache),
                 "levels 'children/a_/b' and 'children/a/_b' would share the Parquet file children__a___b.parquet");
    const fs::path cased = root / "cased";
    write_folder(cased, dataset_files({"A/x.tif", "a/y.tif"}, {"sample", "children", "children/A", "children/a"}));
    CHECK_THROWS(taco::open_dataset(cased.generic_string(), cache), "would share the Parquet file");
    const fs::path shared_archive = root / "shared.zip";
    Files colliding = dataset_files({"A/x.tif"}, {"sample", "children", "children/A"});
    colliding[0].second = collection_json({"A/x.tif", "a/y.tif"}, {"sample", "children", "children/A", "children/a"});
    std::ofstream(shared_archive, std::ios::binary) << cozip_archive(colliding);
    CHECK_THROWS(taco::open_dataset(shared_archive.generic_string(), cache), "would share the Parquet file");

    // The Parquet files must be exactly those of the levels.
    const Strings simple = {"sample", "children"};
    const fs::path missing = root / "missing";
    write_folder(missing, dataset_files({"x.tif"}, simple));
    fs::remove(missing / "METADATA" / "children.parquet");
    CHECK_THROWS(taco::open_dataset(missing.generic_string(), cache),
                 "has no children.parquet for metadata level 'children'");
    const fs::path extra = root / "extra";
    write_folder(extra, dataset_files({"x.tif"}, simple));
    write_file(extra / "METADATA" / "children__x.parquet", "PAR1");
    CHECK_THROWS(taco::open_dataset(extra.generic_string(), cache), "children__x.parquet matches no metadata level");
    Files nested_entry = dataset_files({"x.tif"}, simple);
    nested_entry.emplace_back("METADATA/children/x.parquet", "PAR1");
    const fs::path nested_archive = root / "nested.zip";
    std::ofstream(nested_archive, std::ios::binary) << cozip_archive(nested_entry);
    CHECK_THROWS(taco::open_dataset(nested_archive.generic_string(), cache),
                 "children/x.parquet matches no metadata level");
    CHECK_THROWS(taco::open_dataset("file://" + nested_archive.generic_string(), cache),
                 "children/x.parquet matches no metadata level");
}

std::string stamp_key(const std::string& entry) {
    return taco::json::parse(read_file(fs::path(entry) / "taco-cache.json")).find("key")->string;
}

void test_cache_entries() {
    const fs::path root = scratch("cache-entries");
    const auto files = [](const std::string& content) {
        return Files{{"COLLECTION.json", "{}"}, {"METADATA/sample.parquet", content}};
    };
    taco::CacheStamp stamp;
    stamp.source = "file:///dataset.zip";
    stamp.container = "zip";
    stamp.key = "remote size 1";

    // Openers that missed the cache together keep the first complete entry,
    // which another of them may already be reading.
    taco::CacheEntry first(root.generic_string(), "identity");
    first.store("label", files("first"), stamp);
    const std::string entry = fs::path(first.path("COLLECTION.json")).parent_path().generic_string();
    write_file(first.path("marker"), "");
    taco::CacheEntry second(root.generic_string(), "identity");
    second.store("label", files("second"), stamp);
    CHECK(second.path("marker") == first.path("marker"));
    CHECK(fs::exists(first.path("marker")));
    CHECK(read_file(second.path("METADATA/sample.parquet")) == "first");

    // An entry with another key is outdated and replaced.
    stamp.key = "remote size 2";
    taco::CacheEntry changed(root.generic_string(), "identity");
    changed.store("label", files("changed"), stamp);
    CHECK(!fs::exists(first.path("marker")));
    CHECK(read_file(changed.path("METADATA/sample.parquet")) == "changed");
    CHECK(stamp_key(entry) == "remote size 2");

    // A refresh rebuilds an entry even when its key still matches.
    write_file(changed.path("marker"), "");
    set_environment("TACO_CACHE_REFRESH", "1");
    taco::CacheEntry refreshed(root.generic_string(), "identity");
    refreshed.store("label", files("refreshed"), stamp);
    set_environment("TACO_CACHE_REFRESH", "");
    CHECK(!fs::exists(changed.path("marker")));
    CHECK(read_file(refreshed.path("METADATA/sample.parquet")) == "refreshed");

    // Older entries of the source go, a current one under another label stays.
    taco::CacheEntry relabelled(root.generic_string(), "identity");
    relabelled.store("other", files("relabelled"), stamp);
    CHECK(fs::exists(refreshed.path("COLLECTION.json")));
    stamp.key = "remote size 3";
    taco::CacheEntry newest(root.generic_string(), "identity");
    newest.store("label", files("newest"), stamp);
    CHECK(!fs::exists(relabelled.path("COLLECTION.json")));
    CHECK((stored(root) == Strings{"CACHEDIR.TAG", fs::path(entry).filename().string()}));

#ifndef _WIN32
    // An entry whose stamp vanishes or cannot be read is a miss, not an error,
    // and the size cap skips it.
    const fs::path stamp_path = fs::path(entry) / "taco-cache.json";
    fs::permissions(stamp_path, fs::perms::none);
    if (!std::ifstream(stamp_path)) {
        taco::CacheEntry reader(root.generic_string(), "identity");
        CHECK(!reader.find({"COLLECTION.json"}, ""));
        set_environment("TACO_CACHE_SIZE", "1");
        taco::CacheEntry other(root.generic_string(), "other source");
        other.store("label", files("other"), stamp);
        set_environment("TACO_CACHE_SIZE", "");
        CHECK(fs::is_regular_file(other.path("COLLECTION.json")));
    }
    fs::permissions(stamp_path, fs::perms::owner_read | fs::perms::owner_write);
#endif
}

void test_cache_races() {
#ifndef _WIN32
    // Data loader workers open one remote dataset at once with a cold cache.
    const fs::path archive = scratch("race-source") / "dataset.zip";
    fs::copy_file(data("taco_nested.zip"), archive);
    const std::string uri = "file://" + archive.generic_string();
    for (int round = 0; round < 6; ++round) {
        const fs::path cache = scratch("race-" + std::to_string(round));
        int gate[2] = {-1, -1};
        CHECK(pipe(gate) == 0);
        std::vector<pid_t> workers;
        for (int worker = 0; worker < 8; ++worker) {
            const pid_t child = fork();
            if (child == 0) {
                close(gate[1]);
                char ignored = 0;
                static_cast<void>(read(gate[0], &ignored, 1));
                try {
                    const auto opened = taco::open_dataset(uri, cache.generic_string());
                    const bool readable = std::all_of(opened.level_paths.begin(), opened.level_paths.end(),
                                                      [](const std::string& path) { return fs::is_regular_file(path); });
                    std::_Exit(readable && opened.level_names.size() == 4 ? 0 : 2);
                } catch (const std::exception& error) {
                    std::fprintf(stderr, "worker failed: %s\n", error.what());
                    std::_Exit(1);
                }
            }
            workers.push_back(child);
        }
        close(gate[0]);
        // Closing the gate releases every worker at once.
        close(gate[1]);
        for (const auto worker : workers) {
            int status = 0;
            CHECK(waitpid(worker, &status, 0) == worker);
            CHECK(WIFEXITED(status) && WEXITSTATUS(status) == 0);
        }
        const auto names = stored(cache);
        CHECK(names.size() == 2 && names[0] == "CACHEDIR.TAG" && names[1].starts_with("taco-nested-zip-file-"));
    }
#endif
}

// Open datasets retain metadata snapshots across cache replacement and eviction.
void test_snapshots() {
    const fs::path cache = scratch("snapshots");
    const std::string root = cache.generic_string();
    const fs::path sources = scratch("snapshot-sources");
    const fs::path archive = sources / "dataset.zip";
    fs::copy_file(data("taco_flat.zip"), archive);
    const std::string uri = "file://" + archive.generic_string();

    std::optional<taco::Dataset> opened = taco::open_dataset(uri, root);
    const fs::path snapshot = snapshot_of(*opened);
    const Strings paths = opened->level_paths;
    const std::string sample = read_file(paths[0]);
    const std::string collection = opened->collection;
    const auto unchanged = [&] {
        return read_file(paths[0]) == sample && fs::is_regular_file(paths[1]) &&
               read_file(snapshot / "COLLECTION.json") == collection;
    };
    const auto snapshot_count = [&] {
        return std::count_if(fs::directory_iterator(cache), fs::directory_iterator(), [](const auto& entry) {
            return entry.path().filename().string().starts_with(".open-");
        });
    };
    for (int round = 0; round < 3; ++round)
        CHECK(snapshot_of(taco::open_dataset(uri, root)) == snapshot);
    CHECK(snapshot_count() == 1);

    // SQL generated from a closed dataset still finds its files.
    opened.reset();
    CHECK(unchanged());

    fs::copy_file(data("taco_nested.zip"), archive, fs::copy_options::overwrite_existing);
    const auto rebuilt = taco::open_dataset(uri, root);
    CHECK(rebuilt.level_names.size() == 4);
    CHECK(unchanged());
    set_environment("TACO_CACHE_REFRESH", "1");
    CHECK(taco::open_dataset(uri, root).level_names.size() == 4);
    set_environment("TACO_CACHE_REFRESH", "");
    CHECK(unchanged());

    // Eviction skips snapshots and does not count them.
    set_environment("TACO_CACHE_SIZE", "1");
    fs::copy_file(data("taco_flat.zip"), sources / "other.zip");
    taco::open_dataset("file://" + (sources / "other.zip").generic_string(), root);
    set_environment("TACO_CACHE_SIZE", "");
    const auto names = stored(cache);
    CHECK(names.size() == 2 && names[1].starts_with("taco-flat-zip-file-"));
    CHECK(unchanged());
    CHECK(std::all_of(rebuilt.level_paths.begin(), rebuilt.level_paths.end(),
                      [](const std::string& path) { return fs::is_regular_file(path); }));

#ifndef _WIN32
    // A child cleans up its own snapshots, leaving the parent's intact.
    fs::copy_file(data("taco_nested.zip"), sources / "third.zip");
    const std::string third = "file://" + (sources / "third.zip").generic_string();
    int descriptors[2] = {-1, -1};
    CHECK(pipe(descriptors) == 0);
    const pid_t child = fork();
    CHECK(child >= 0);
    if (child == 0) {
        close(descriptors[0]);
        const std::string made = snapshot_of(taco::open_dataset(third, root)).string();
        static_cast<void>(write(descriptors[1], made.data(), made.size()));
        close(descriptors[1]);
        std::exit(0);
    }
    close(descriptors[1]);
    std::array<char, 4096> made{};
    const auto length = read(descriptors[0], made.data(), made.size());
    close(descriptors[0]);
    int status = 0;
    CHECK(waitpid(child, &status, 0) == child);
    CHECK(WIFEXITED(status) && WEXITSTATUS(status) == 0);
    CHECK(length > 0);
    if (length > 0)
        CHECK(!fs::exists(fs::path(std::string(made.data(), static_cast<std::size_t>(length)))));
    CHECK(unchanged());

    // Remove dead local owners' snapshots, but keep those from other hosts.
    const std::string host = snapshot.filename().string().substr(6, 8);
    std::string other_host = host;
    other_host[0] = other_host[0] == '0' ? '1' : '0';
    const fs::path abandoned = cache / (".open-" + host + "-" + std::to_string(child) + "-0");
    const fs::path foreign = cache / (".open-" + other_host + "-" + std::to_string(child) + "-0");
    write_file(abandoned / "METADATA" / "sample.parquet", "PAR1");
    write_file(foreign / "METADATA" / "sample.parquet", "PAR1");
    fs::copy_file(data("taco_flat.zip"), sources / "fourth.zip");
    taco::open_dataset("file://" + (sources / "fourth.zip").generic_string(), root);
    CHECK(!fs::exists(abandoned));
    CHECK(fs::exists(foreign));
    CHECK(unchanged());

    // A cache this process cannot write still serves an entry it has no
    // snapshot of, in place.
    std::error_code error;
    fs::permissions(cache, fs::perms::owner_read | fs::perms::owner_exec);
    if (!fs::create_directory(cache / "probe", error)) {
        const auto read_only = taco::open_dataset(third, root);
        CHECK(!snapshot_of(read_only).filename().string().starts_with(".open-"));
        CHECK(read_only.level_names.size() == 4);
    }
    fs::permissions(cache, fs::perms::owner_all);
#endif
}

// CoZIP padding gives small archives equal sizes; the hash distinguishes versions.
void test_republished_archive() {
    const std::string cache = scratch("republish-cache").generic_string();
    const fs::path archive = scratch("republish") / "dataset.zip";
    const std::string uri = "file://" + archive.generic_string();
    std::ofstream(archive, std::ios::binary) << cozip_archive(dataset_files({"x.tif"}, {"sample", "children"}));
    const auto size = fs::file_size(archive);
    CHECK((taco::open_dataset(uri, cache).contract.structure == Strings{"x.tif"}));

    std::ofstream(archive, std::ios::binary | std::ios::trunc)
        << cozip_archive(dataset_files({"y.tif"}, {"sample", "children"}));
    CHECK(fs::file_size(archive) == size);
    CHECK((taco::open_dataset(uri, cache).contract.structure == Strings{"y.tif"}));
    CHECK(stored(cache).size() == 2);
}

// TACOCAT partitions must stay below the catalog directory (SPEC 7.5).
void test_partitions() {
    const std::string cache = scratch("partitions-cache").generic_string();
    const fs::path root = scratch("partitions");
    const Strings levels = {"sample", "children"};
    const auto catalog = [&](const std::string& name, const std::string& sources) {
        const fs::path directory = root / name;
        write_file(directory / ".tacocat" / "COLLECTION.json", collection_json({"x.tif"}, levels, sources));
        for (const auto& level : levels)
            write_file(directory / ".tacocat" / taco::level_to_file(level), "PAR1");
        return directory.generic_string();
    };

    const std::string valid = catalog("valid", R"({"samples": 2, "partitions": [{"file": "a.zip", "samples": 1},
                                                   {"file": "parts/b c.zip", "samples": 1}]})");
    for (const auto& source : {valid, "file://" + valid})
        CHECK((taco::open_dataset(source, cache).partitions == Strings{"a.zip", "parts/b c.zip"}));

    const std::vector<std::pair<std::string, std::string>> invalid = {
        {R"({"partitions": [{"file": "../../../../etc/hosts.zip"}]})",
         "taco:sources partition '../../../../etc/hosts.zip' is not a normalized relative path ending in .zip"},
        {R"({"partitions": [{"file": "a.zip"}, {"file": "/data/b.zip"}]})", "partition '/data/b.zip' is not"},
        {R"({"partitions": [{"file": "parts\\a.zip"}]})", "is not a normalized relative path"},
        {R"({"partitions": [{"file": "parts/./a.zip"}]})", "is not a normalized relative path"},
        {R"({"partitions": [{"file": "a.tar"}]})", "is not a normalized relative path"},
        {R"({"partitions": [{"file": "a.zip"}, {"file": "a.zip"}]})", "lists partition 'a.zip' twice"},
        {R"({"partitions": [{"samples": 1}]})", "every taco:sources partition needs a file"},
        {R"({"partitions": []})", "taco:sources.partitions must be a non-empty array"},
        {R"([])", "invalid taco:sources object"},
    };
    for (std::size_t i = 0; i < invalid.size(); ++i) {
        const std::string path = catalog("invalid" + std::to_string(i), invalid[i].first);
        CHECK_THROWS(taco::open_dataset(path, cache), invalid[i].second);
        CHECK_THROWS(taco::open_dataset("file://" + path, cache), invalid[i].second);
    }
}

// A file:// URI gives the locations of the path it names, percent-decoded.
void test_file_uris() {
    const std::string cache = scratch("file-uri-cache").generic_string();
    const fs::path root = scratch("file-uris") / "a b%41#c";
    fs::create_directories(root);
    fs::copy_file(data("taco_flat.zip"), root / "flat.zip");
    fs::copy(data("taco_folder"), root / "folder", fs::copy_options::recursive);
    fs::copy(data("taco_cat"), root / "cat", fs::copy_options::recursive);
    // The URI of a level file escapes the '%' a folder name may hold.
    write_folder(root / "percent", dataset_files({"x%41/y.tif"}, {"sample", "children", "children/x%41"}));

    const std::string uri = "file://" + taco::encode_uri_path(root.generic_string());
    CHECK(uri.ends_with("/a%20b%2541%23c"));
    for (const char* name : {"flat.zip", "folder", "cat", "percent"}) {
        const auto local = taco::open_dataset((root / name).generic_string(), cache);
        const auto remote = taco::open_dataset(uri + "/" + name, cache);
        CHECK(remote.container == local.container);
        CHECK(remote.location_base == local.location_base);
        CHECK(remote.partitions == local.partitions);
        CHECK(remote.level_names == local.level_names);
        CHECK(remote.level_paths.front().starts_with(cache));
        CHECK(!contains(taco::build_sql(remote, taco::ReadOptions{}), "file:"));
    }
    CHECK(taco::open_dataset(uri + "/flat.zip", cache).location_base == (root / "flat.zip").generic_string());
}

// Sequence bounds are decimal integers from 1 to 2^53 - 1, without a sign,
// spaces or leading zeros (SPEC 5.2).
void test_sequence_bounds() {
    const std::string cache = scratch("sequence-cache").generic_string();
    const fs::path root = scratch("sequences");
    int count = 0;
    const auto open = [&](const std::string& leaf) {
        const fs::path folder = root / std::to_string(count++);
        write_folder(folder, dataset_files({leaf}, {"sample", "children"}));
        return taco::open_dataset(folder.generic_string(), cache);
    };
    for (const std::string leaf : {"img*[1,3].tif", "img.*[1,3].tif", "img *[1,3].tif", "img*[10,10]",
                                   "img*[1,9007199254740991].tif"})
        CHECK(open(leaf).contract.structure == Strings{leaf});
    for (const char* leaf : {"img*[ 1,3].tif", "img*[1 ,3].tif", "img*[1, 3].tif", "img*[1,3 ].tif", "img*[01,3].tif",
                             "img*[1,03].tif", "img*[+1,3].tif", "img*[1,-3].tif", "img*[1,3e2].tif", "img*[,3].tif",
                             "img*[1,9007199254740992].tif", "img*[1,18446744073709551616].tif"})
        CHECK_THROWS(open(leaf), std::string("malformed variable leaf '") + leaf + "'");
    CHECK_THROWS(open("img*[0,3].tif"), "must require at least one file");
    CHECK_THROWS(open("img*[4,3].tif"), "has min > max");
}

// A COLLECTION.json that repeats a key or holds a number beyond a double is
// not read, as JSON readers disagree on it.
void test_collection_json() {
    const std::string cache = scratch("collection-json-cache").generic_string();
    const fs::path root = scratch("collection-json");
    const std::string valid = collection_json({"x.tif"}, {"sample", "children"});
    const std::vector<std::pair<std::string, std::string>> invalid = {
        {"{\"id\": \"first\", " + valid.substr(1), "(duplicate key \"id\" at byte "},
        {valid.substr(0, valid.size() - 1) + ", \"taco:metadata\": {}}", "(duplicate key \"taco:metadata\" at byte "},
        {valid.substr(0, valid.size() - 1) + ", \"extent\": {\"spatial\": [1e400]}}", "(number does not fit a double"},
    };
    for (std::size_t i = 0; i < invalid.size(); ++i) {
        const fs::path folder = root / std::to_string(i);
        write_folder(folder, dataset_files({"x.tif"}, {"sample", "children"}));
        write_file(folder / "COLLECTION.json", invalid[i].first);
        for (const auto& source : {folder.generic_string(), "file://" + folder.generic_string()})
            CHECK_THROWS(taco::open_dataset(source, cache), "COLLECTION.json is not valid JSON " + invalid[i].second);
    }
}

#ifndef _WIN32
// Holds the first progress report of the process that set it until released.
struct Pause {
    pid_t owner = getpid();
    std::atomic<bool> entered{false};
    std::atomic<bool> released{false};
};

void pause_once(const char*, uint64_t, uint64_t, void* user) {
    auto& pause = *static_cast<Pause*>(user);
    if (getpid() != pause.owner || pause.entered.exchange(true))
        return;
    while (!pause.released)
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
}
#endif

// A child forked while another thread opens a local ZIP inherits no lock
// that thread held, so it opens datasets of its own.
void test_fork_while_opening() {
#ifndef _WIN32
    const std::string cache = scratch("fork-cache").generic_string();
    const fs::path sources = scratch("fork-sources");
    for (const char* name : {"busy.zip", "child.zip", "cached.zip"})
        fs::copy_file(data("taco_flat.zip"), sources / name);
    const std::string busy_zip = (sources / "busy.zip").generic_string();
    const std::string child_zip = (sources / "child.zip").generic_string();
    const std::string cached = "file://" + (sources / "cached.zip").generic_string();
    taco::open_dataset(cached, cache);

    Pause pause;
    taco_set_progress(pause_once, &pause);
    std::thread busy([&] { taco::open_dataset(busy_zip, cache); });
    while (!pause.entered)
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    // busy now waits in its progress callback, halfway through extracting.
    const pid_t child = fork();
    CHECK(child >= 0);
    if (child == 0) {
        alarm(20);
        try {
            const bool opened = taco::open_dataset(child_zip, cache).level_names.size() == 2 &&
                                taco::open_dataset(cached, cache).level_names.size() == 2;
            std::exit(opened ? 0 : 2);
        } catch (const std::exception& error) {
            std::fprintf(stderr, "forked child failed: %s\n", error.what());
            std::exit(1);
        }
    }
    int status = 0;
    CHECK(waitpid(child, &status, 0) == child);
    CHECK(WIFEXITED(status) && WEXITSTATUS(status) == 0);
    pause.released = true;
    busy.join();
    taco_set_progress(nullptr, nullptr);
    CHECK((taco::open_dataset(busy_zip, cache).level_names == Strings{"sample", "children"}));
#endif
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

    // Reading metadata is reported under the dataset name.
    const fs::path copy = scratch("progress-copy") / "taco_flat.zip";
    fs::copy_file(archive, copy);
    const std::string cache = scratch("progress-cache").generic_string();
    events.clear();
    taco::open_dataset(copy.string(), cache);
    CHECK(!events.empty());
    CHECK(events.front().phase == "downloading taco_flat.zip metadata");
    CHECK(events.back().done == events.back().total && events.back().total > 0);
    events.clear();
    taco::open_dataset(copy.string(), cache);
    CHECK(events.empty());
    events.clear();
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

    const auto sizes = taco::object_sizes({archive, data("taco_folder/COLLECTION.json")});
    CHECK(sizes.size() == 2);
    CHECK(sizes[0] == whole.size());
    CHECK(sizes[1] == read_file(data("taco_folder/COLLECTION.json")).size());
    CHECK(taco::object_sizes({}).empty());

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
    test_cache();
    test_level_names();
    test_cache_entries();
    test_cache_races();
    test_snapshots();
    test_republished_archive();
    test_partitions();
    test_file_uris();
    test_sequence_bounds();
    test_collection_json();
    test_progress();
    test_fork_while_opening();
    test_fetch();
    test_c_api();
    if (remote_tests())
        test_remote();
    taco_shutdown();
    std::printf("%d checks, %d failures\n", checks, failures);
    return failures == 0 ? 0 : 1;
}
