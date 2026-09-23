#include <taco/taco.h>

#include "cozip_index.hpp"
#include "dataset.hpp"
#include "error.hpp"
#include "progress.hpp"
#include "sql.hpp"
#include "transport.hpp"

#include <cstdlib>
#include <cstring>
#include <memory>
#include <new>
#include <string>
#include <vector>

struct taco_dataset {
    taco::Dataset value;
};

namespace {

thread_local std::string last_error;

char* copy_string(const std::string& text) {
    auto* out = static_cast<char*>(std::malloc(text.size() + 1));
    if (!out)
        throw std::bad_alloc();
    std::memcpy(out, text.c_str(), text.size() + 1);
    return out;
}

// Every exported call reports failures through its status and last_error.
template <typename Body>
taco_status guard(Body&& body) noexcept {
    try {
        body();
        return TACO_OK;
    } catch (const taco::Error& error) {
        last_error = error.what();
        return error.status();
    } catch (const std::bad_alloc&) {
        last_error = "out of memory";
        return TACO_ERR_INTERNAL;
    } catch (const std::exception& error) {
        last_error = error.what();
        return TACO_ERR_INTERNAL;
    }
}

void require(bool condition, const char* message) {
    if (!condition)
        taco::fail(message);
}

const char* item(const std::vector<std::string>& values, size_t index) {
    return index < values.size() ? values[index].c_str() : nullptr;
}

} // namespace

int taco_api_version(void) {
    return TACO_API_VERSION;
}

const char* taco_version_string(void) {
    return TACO_VERSION_STRING;
}

const char* taco_last_error(void) {
    return last_error.c_str();
}

void taco_free(char* text) {
    std::free(text);
}

void taco_shutdown(void) {
    taco::shutdown_transport();
}

void taco_set_progress(taco_progress_fn callback, void* user) {
    taco::set_progress(callback, user);
}

taco_status taco_open(const char* source, const char* cache_dir, taco_dataset** out) {
    if (out)
        *out = nullptr;
    return guard([&] {
        require(source && out, "taco_open: source and out must not be NULL");
        auto dataset = std::make_unique<taco_dataset>();
        dataset->value = taco::open_dataset(source, cache_dir ? cache_dir : "");
        *out = dataset.release();
    });
}

void taco_close(taco_dataset* dataset) {
    delete dataset;
}

const char* taco_dataset_source(const taco_dataset* dataset) {
    return dataset ? dataset->value.source.c_str() : nullptr;
}

const char* taco_dataset_container(const taco_dataset* dataset) {
    return dataset ? taco::container_name(dataset->value.container) : nullptr;
}

const char* taco_dataset_collection(const taco_dataset* dataset) {
    return dataset ? dataset->value.collection.c_str() : nullptr;
}

size_t taco_dataset_level_count(const taco_dataset* dataset) {
    return dataset ? dataset->value.level_names.size() : 0;
}

const char* taco_dataset_level(const taco_dataset* dataset, size_t index) {
    return dataset ? item(dataset->value.level_names, index) : nullptr;
}

size_t taco_dataset_structure_count(const taco_dataset* dataset) {
    return dataset ? dataset->value.contract.structure.size() : 0;
}

const char* taco_dataset_structure(const taco_dataset* dataset, size_t index) {
    return dataset ? item(dataset->value.contract.structure, index) : nullptr;
}

const char* taco_dataset_derived(const taco_dataset* dataset) {
    return dataset && dataset->value.contract.has_derived ? dataset->value.contract.derived.c_str() : nullptr;
}

taco_status taco_sql(const taco_dataset* const* datasets, size_t count, const taco_read_options* options,
                     char** out_sql) {
    if (out_sql)
        *out_sql = nullptr;
    return guard([&] {
        require(datasets && count > 0 && options && out_sql,
                "taco_sql: datasets, options and out_sql must not be NULL");
        taco::ReadOptions read;
        if (options->idx)
            read.idx = options->idx;
        if (options->level)
            read.level = options->level;
        read.pivot = options->pivoted != 0;
        read.location = options->location != 0;
        if (options->files) {
            read.has_files = true;
            for (size_t i = 0; i < options->file_count; ++i) {
                require(options->files[i] != nullptr, "taco_sql: files must not contain NULL");
                read.files.emplace_back(options->files[i]);
            }
        }
        std::vector<const taco::Dataset*> selected;
        for (size_t i = 0; i < count; ++i) {
            require(datasets[i] != nullptr, "taco_sql: datasets must not contain NULL");
            selected.push_back(&datasets[i]->value);
        }
        *out_sql = copy_string(taco::build_union_sql(selected, read));
    });
}

taco_status taco_profile(const char* source, char** out_name) {
    if (out_name)
        *out_name = nullptr;
    return guard([&] {
        require(source && out_name, "taco_profile: source and out_name must not be NULL");
        *out_name = copy_string(taco::profile_name(taco::read_cozip_profile(source)));
    });
}

taco_status taco_fetch(const taco_fetch_item* items, size_t count) {
    return guard([&] {
        require(items || count == 0, "taco_fetch: items must not be NULL");
        std::vector<taco::Fetch> fetches;
        for (size_t i = 0; i < count; ++i) {
            require(items[i].uri && items[i].path, "taco_fetch: uri and path must not be NULL");
            fetches.push_back(taco::Fetch{items[i].uri, items[i].offset, items[i].length, items[i].path});
        }
        taco::fetch_files(fetches);
    });
}
