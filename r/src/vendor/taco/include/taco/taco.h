// C API for TACO metadata access.
//
// The core opens local or remote containers, caches their metadata, and
// produces DuckDB SQL. Python, R, and Julia use this ABI.
#ifndef TACO_H
#define TACO_H

#include <stddef.h>
#include <stdint.h>

#if defined(_WIN32)
#  if defined(TACO_BUILD)
#    define TACO_API __declspec(dllexport)
#  else
#    define TACO_API __declspec(dllimport)
#  endif
#else
#  define TACO_API __attribute__((visibility("default")))
#endif

#ifdef __cplusplus
extern "C" {
#endif

#define TACO_API_VERSION 2

typedef enum {
    TACO_OK = 0,
    TACO_ERR_INVALID = 1,
    TACO_ERR_IO = 2,
    TACO_ERR_NOT_FOUND = 3,
    TACO_ERR_INTERNAL = 99
} taco_status;

TACO_API int taco_api_version(void);
TACO_API const char* taco_version_string(void);

// Error text of the latest failed call on this thread.
TACO_API const char* taco_last_error(void);

// Releases a string returned through a char** parameter.
TACO_API void taco_free(char* text);

// Releases transport resources cached by this thread. Call this before
// unloading the shared library. It is safe to call more than once.
TACO_API void taco_shutdown(void);

// Progress of downloads and copies. phase names the work, done and total
// count bytes; total is 0 while unknown. The callback runs on the thread
// doing the work. NULL restores the built-in bar, which writes to stderr
// when that is a terminal and stays silent otherwise.
typedef void (*taco_progress_fn)(const char* phase, uint64_t done, uint64_t total, void* user);
TACO_API void taco_set_progress(taco_progress_fn callback, void* user);

typedef struct taco_dataset taco_dataset;

// Opens a dataset. Remote metadata, and the metadata inside any ZIP, is
// copied into cache_dir, or into the default cache when it is NULL.
TACO_API taco_status taco_open(const char* source, const char* cache_dir, taco_dataset** out);
TACO_API void taco_close(taco_dataset* dataset);

// Views owned by the dataset. They stay valid until taco_close.
TACO_API const char* taco_dataset_source(const taco_dataset* dataset);
TACO_API const char* taco_dataset_container(const taco_dataset* dataset);
TACO_API const char* taco_dataset_collection(const taco_dataset* dataset);
TACO_API size_t taco_dataset_level_count(const taco_dataset* dataset);
TACO_API const char* taco_dataset_level(const taco_dataset* dataset, size_t index);
TACO_API size_t taco_dataset_structure_count(const taco_dataset* dataset);
TACO_API const char* taco_dataset_structure(const taco_dataset* dataset, size_t index);
// Serialized taco:derived object, or NULL when the collection has none.
TACO_API const char* taco_dataset_derived(const taco_dataset* dataset);

typedef struct {
    // NULL for every sample, "5" for one, "[0, 100]" for a half-open range.
    const char* idx;
    // NULL for the joined view, otherwise one contract level read raw.
    const char* level;
    // Nonzero for one row per sample, zero for one row per file.
    int pivoted;
    // Structure leaves to read, or NULL for every leaf.
    const char* const* files;
    size_t file_count;
    // Nonzero to calculate file locations.
    int location;
} taco_read_options;

// The query that reads one dataset, or the union of compatible partitions.
TACO_API taco_status taco_sql(const taco_dataset* const* datasets, size_t count,
                              const taco_read_options* options, char** out_sql);

// Profile of a CoZIP archive: "none", "flat", "taco" or "unknown:<n>".
TACO_API taco_status taco_profile(const char* source, char** out_name);

// A byte range of a local or remote object and the file it is copied to. A
// zero length copies from offset to the end of the object.
typedef struct {
    const char* uri;
    uint64_t offset;
    uint64_t length;
    const char* path;
} taco_fetch_item;

// Copies every item into its file, creating parent directories. The Python
// writer exports samples with it.
TACO_API taco_status taco_fetch(const taco_fetch_item* items, size_t count);

#ifdef __cplusplus
} // extern "C"
#endif

#endif // TACO_H
