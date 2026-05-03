/*
 * Small Emscripten-facing bridge for browser and Node writers.
 *
 * The public C ABI is intentionally low level: callers fill cozip_entry_t
 * records directly. That is awkward from JavaScript because struct layout
 * details depend on the wasm32 ABI. This bridge accepts simple pointer arrays
 * instead, builds cozip_entry_t records in C, and runs the core writer pipeline.
 */

#ifndef COZIP_BUILDING
#define COZIP_BUILDING
#endif

#include "../core/cozip.h"

#include <stdarg.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define COZIP_WASM_PADDING_NAME "__cozip_padding__"
#define ZIP_CENTRAL_HEADER_BASE_SIZE 46u
#define ZIP_EOCD_SIZE 22u

static cozip_status_t wasm_set_err(cozip_error_t *err, cozip_status_t code,
                                   const char *fmt, ...) {
    if (err) {
        err->code = code;
        if (fmt) {
            va_list ap;
            va_start(ap, fmt);
            vsnprintf(err->message, COZIP_ERROR_MESSAGE_SIZE, fmt, ap);
            va_end(ap);
        } else {
            err->message[0] = '\0';
        }
    }
    return code;
}

static int name_is_reserved(const char *name) {
    return strcmp(name, COZIP_INDEX_NAME) == 0 ||
           strcmp(name, COZIP_WASM_PADDING_NAME) == 0;
}

static cozip_status_t validate_inputs(const char *out_path,
                                      const char **names,
                                      const uint8_t **buffers,
                                      const size_t *sizes,
                                      size_t n,
                                      cozip_error_t *err) {
    if (!out_path || !names || !buffers || !sizes || n == 0) {
        return wasm_set_err(err, COZIP_ERR_INVALID_ARGUMENT,
                            "missing output path or input arrays");
    }

    for (size_t i = 0; i < n; i++) {
        if (!names[i] || names[i][0] == '\0') {
            return wasm_set_err(err, COZIP_ERR_INVALID_ARGUMENT,
                                "entry %zu has an empty name", i);
        }
        if (name_is_reserved(names[i])) {
            return wasm_set_err(err, COZIP_ERR_INVALID_ARGUMENT,
                                "entry %zu uses reserved name '%s'",
                                i, names[i]);
        }
        if (!buffers[i] || sizes[i] == 0) {
            return wasm_set_err(err, COZIP_ERR_INVALID_ARGUMENT,
                                "entry %zu has an empty payload", i);
        }
        for (size_t j = 0; j < i; j++) {
            if (strcmp(names[i], names[j]) == 0) {
                return wasm_set_err(err, COZIP_ERR_INVALID_ARGUMENT,
                                    "duplicate name '%s'", names[i]);
            }
        }
    }

    return COZIP_OK;
}

static uint64_t predict_zip32_archive_size(const cozip_entry_t *entries,
                                           size_t n,
                                           size_t index_payload_size) {
    uint64_t total = COZIP_INDEX_OFFSET + (uint64_t)index_payload_size;

    for (size_t i = 0; i < n; i++) {
        total += entries[i].lfh_size + entries[i].payload_size;
    }

    total += ZIP_CENTRAL_HEADER_BASE_SIZE + COZIP_INDEX_NAME_LEN;
    for (size_t i = 0; i < n; i++) {
        total += ZIP_CENTRAL_HEADER_BASE_SIZE + (uint64_t)strlen(entries[i].arc_name);
    }
    total += ZIP_EOCD_SIZE;

    return total;
}

static size_t required_padding_size(const cozip_entry_t *entries,
                                    size_t n,
                                    size_t index_payload_size) {
    uint64_t predicted = predict_zip32_archive_size(entries, n, index_payload_size);
    if (predicted >= COZIP_MIN_ARCHIVE_SIZE) {
        return 0;
    }

    uint64_t missing = COZIP_MIN_ARCHIVE_SIZE - predicted;
    uint64_t name_len = (uint64_t)strlen(COZIP_WASM_PADDING_NAME);
    uint64_t overhead = 30u + name_len + ZIP_CENTRAL_HEADER_BASE_SIZE + name_len;
    uint64_t payload = missing > overhead ? missing - overhead : 1u;
    return (size_t)payload;
}

static void fill_buffer_entry(cozip_entry_t *entry,
                              const char *name,
                              const uint8_t *buffer,
                              size_t size,
                              int in_index) {
    memset(entry, 0, sizeof(*entry));
    entry->arc_name = name;
    entry->payload_size = (uint64_t)size;
    entry->in_index = in_index != 0;
    entry->source.kind = COZIP_SOURCE_BUFFER;
    entry->source.u.buffer.data = buffer;
    entry->source.u.buffer.size = size;
}

COZIP_API size_t cozip_wasm_error_size(void) {
    return sizeof(cozip_error_t);
}

COZIP_API size_t cozip_wasm_error_message_offset(void) {
    return offsetof(cozip_error_t, message);
}

COZIP_API cozip_status_t
cozip_wasm_write_archive_from_buffers(const char *out_path,
                                      const char **names,
                                      const uint8_t **buffers,
                                      const size_t *sizes,
                                      const uint8_t *in_index,
                                      size_t n,
                                      cozip_profile_t profile,
                                      cozip_error_t *err) {
    cozip_status_t status = validate_inputs(out_path, names, buffers, sizes, n, err);
    if (status != COZIP_OK) {
        return status;
    }

    cozip_entry_t *entries = (cozip_entry_t *)calloc(n + 1, sizeof(cozip_entry_t));
    if (!entries) {
        return wasm_set_err(err, COZIP_ERR_IO, "allocating entries failed");
    }

    for (size_t i = 0; i < n; i++) {
        fill_buffer_entry(&entries[i], names[i], buffers[i], sizes[i],
                          in_index ? in_index[i] : 1);
    }

    size_t n_total = n;
    uint8_t *padding = NULL;
    uint8_t *index_payload = NULL;

    status = cozip_plan(entries, n_total, err);
    if (status != COZIP_OK) {
        goto done;
    }

    size_t index_payload_size = 0;
    status = cozip_index_payload_size(entries, n_total, &index_payload_size, err);
    if (status != COZIP_OK) {
        goto done;
    }

    size_t pad_size = required_padding_size(entries, n_total, index_payload_size);
    if (pad_size > 0) {
        padding = (uint8_t *)malloc(pad_size);
        if (!padding) {
            status = wasm_set_err(err, COZIP_ERR_IO, "allocating padding failed");
            goto done;
        }
        memset(padding, 0x5a, pad_size);

        fill_buffer_entry(&entries[n_total], COZIP_WASM_PADDING_NAME,
                          padding, pad_size, 0);
        n_total++;

        status = cozip_plan(entries, n_total, err);
        if (status != COZIP_OK) {
            goto done;
        }
        status = cozip_index_payload_size(entries, n_total, &index_payload_size, err);
        if (status != COZIP_OK) {
            goto done;
        }
    }

    index_payload = (uint8_t *)malloc(index_payload_size);
    if (!index_payload) {
        status = wasm_set_err(err, COZIP_ERR_IO, "allocating index payload failed");
        goto done;
    }

    status = cozip_build_index_payload(entries, n_total, profile,
                                       index_payload, index_payload_size, err);
    if (status != COZIP_OK) {
        goto done;
    }

    status = cozip_write_archive(out_path, entries, n_total,
                                 index_payload, index_payload_size, err);
    if (status != COZIP_OK) {
        goto done;
    }

    status = cozip_patch_integrity_hash(out_path, index_payload_size, err);

done:
    free(index_payload);
    free(padding);
    free(entries);
    return status;
}
