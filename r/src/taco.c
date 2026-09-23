#include <R.h>
#include <Rinternals.h>
#include <R_ext/Rdynload.h>

#ifndef __EMSCRIPTEN__
#include <taco/taco.h>

static void core_error(void) {
    Rf_error("%s", taco_last_error());
}

static const char* scalar_string(SEXP value, const char* name) {
    if (!Rf_isString(value) || XLENGTH(value) != 1 || STRING_ELT(value, 0) == NA_STRING)
        Rf_error("`%s` must be a single string", name);
    return Rf_translateCharUTF8(STRING_ELT(value, 0));
}

static SEXP scalar_utf8(const char* text) {
    SEXP value = PROTECT(Rf_mkCharCE(text, CE_UTF8));
    SEXP result = Rf_ScalarString(value);
    UNPROTECT(1);
    return result;
}

static SEXP take_string(char* text) {
    if (!text)
        return R_NilValue;
    SEXP result = PROTECT(scalar_utf8(text));
    taco_free(text);
    UNPROTECT(1);
    return result;
}

SEXP taco_r_shutdown(void) {
    taco_shutdown();
    return R_NilValue;
}

// The R function that draws progress, kept alive while it is registered.
static SEXP progress_handler = NULL;

static void report_progress(const char* phase, uint64_t done, uint64_t total, void* user) {
    (void)user;
    if (!progress_handler)
        return;
    SEXP text = PROTECT(scalar_utf8(phase));
    SEXP done_value = PROTECT(Rf_ScalarReal((double)done));
    SEXP total_value = PROTECT(Rf_ScalarReal((double)total));
    SEXP call = PROTECT(Rf_lang4(progress_handler, text, done_value, total_value));
    // A failing bar must not unwind through the core, so errors are dropped.
    int failed = 0;
    R_tryEvalSilent(call, R_GlobalEnv, &failed);
    UNPROTECT(4);
}

SEXP taco_r_set_progress(SEXP handler) {
    if (progress_handler)
        R_ReleaseObject(progress_handler);
    progress_handler = Rf_isNull(handler) ? NULL : handler;
    if (progress_handler)
        R_PreserveObject(progress_handler);
    taco_set_progress(progress_handler ? report_progress : NULL, NULL);
    return R_NilValue;
}

static void close_dataset(SEXP pointer) {
    taco_dataset* dataset = R_ExternalPtrAddr(pointer);
    if (dataset) {
        taco_close(dataset);
        R_ClearExternalPtr(pointer);
    }
}

static taco_dataset* dataset_address(SEXP pointer) {
    if (TYPEOF(pointer) != EXTPTRSXP || !R_ExternalPtrAddr(pointer))
        Rf_error("invalid TACO dataset handle");
    return R_ExternalPtrAddr(pointer);
}

SEXP taco_r_open(SEXP source) {
    taco_dataset* dataset = NULL;
    if (taco_open(scalar_string(source, "source"), NULL, &dataset) != TACO_OK)
        core_error();
    SEXP pointer = PROTECT(R_MakeExternalPtr(dataset, R_NilValue, R_NilValue));
    R_RegisterCFinalizerEx(pointer, close_dataset, TRUE);
    UNPROTECT(1);
    return pointer;
}

static SEXP string_vector(const taco_dataset* dataset, size_t count,
                          const char* (*item)(const taco_dataset*, size_t)) {
    SEXP result = PROTECT(Rf_allocVector(STRSXP, (R_xlen_t)count));
    for (size_t i = 0; i < count; ++i)
        SET_STRING_ELT(result, (R_xlen_t)i, Rf_mkCharCE(item(dataset, i), CE_UTF8));
    UNPROTECT(1);
    return result;
}

SEXP taco_r_dataset(SEXP pointer) {
    const taco_dataset* dataset = dataset_address(pointer);
    const char* names[] = {"source", "container", "collection", "levels", "structure", "derived", ""};
    SEXP result = PROTECT(Rf_mkNamed(VECSXP, names));
    SET_VECTOR_ELT(result, 0, scalar_utf8(taco_dataset_source(dataset)));
    SET_VECTOR_ELT(result, 1, scalar_utf8(taco_dataset_container(dataset)));
    SET_VECTOR_ELT(result, 2, scalar_utf8(taco_dataset_collection(dataset)));
    SET_VECTOR_ELT(result, 3, string_vector(dataset, taco_dataset_level_count(dataset), taco_dataset_level));
    SET_VECTOR_ELT(result, 4, string_vector(dataset, taco_dataset_structure_count(dataset), taco_dataset_structure));
    const char* derived = taco_dataset_derived(dataset);
    SET_VECTOR_ELT(result, 5, derived ? scalar_utf8(derived) : R_NilValue);
    UNPROTECT(1);
    return result;
}

SEXP taco_r_sql(SEXP datasets, SEXP idx, SEXP level, SEXP pivoted, SEXP files, SEXP location) {
    if (TYPEOF(datasets) != VECSXP || XLENGTH(datasets) == 0)
        Rf_error("datasets must be a non-empty list");
    R_xlen_t count = XLENGTH(datasets);
    const taco_dataset** handles = (const taco_dataset**)R_alloc((size_t)count, sizeof(taco_dataset*));
    for (R_xlen_t i = 0; i < count; ++i)
        handles[i] = dataset_address(VECTOR_ELT(datasets, i));

    taco_read_options options = {0};
    options.idx = Rf_isNull(idx) ? NULL : scalar_string(idx, "idx");
    options.level = Rf_isNull(level) ? NULL : scalar_string(level, "level");
    options.pivoted = Rf_asLogical(pivoted) == TRUE;
    options.location = Rf_asLogical(location) == TRUE;
    if (!Rf_isNull(files)) {
        if (!Rf_isString(files))
            Rf_error("`files` must be a character vector");
        R_xlen_t file_count = XLENGTH(files);
        const char** names = (const char**)R_alloc((size_t)file_count, sizeof(char*));
        for (R_xlen_t i = 0; i < file_count; ++i)
            names[i] = Rf_translateCharUTF8(STRING_ELT(files, i));
        options.files = names;
        options.file_count = (size_t)file_count;
    }

    char* sql = NULL;
    if (taco_sql(handles, (size_t)count, &options, &sql) != TACO_OK)
        core_error();
    return take_string(sql);
}

SEXP taco_r_profile(SEXP source) {
    char* name = NULL;
    if (taco_profile(scalar_string(source, "source"), &name) != TACO_OK)
        core_error();
    return take_string(name);
}

#else
static SEXP wasm_unavailable(void) {
    Rf_error("the native TACO core is not available on WebAssembly");
    return R_NilValue;
}

SEXP taco_r_shutdown(void) {
    return R_NilValue;
}

SEXP taco_r_set_progress(SEXP handler) {
    (void)handler;
    return R_NilValue;
}

SEXP taco_r_open(SEXP source) {
    (void)source;
    return wasm_unavailable();
}

SEXP taco_r_dataset(SEXP pointer) {
    (void)pointer;
    return wasm_unavailable();
}

SEXP taco_r_sql(SEXP datasets, SEXP idx, SEXP level, SEXP pivoted, SEXP files, SEXP location) {
    (void)datasets;
    (void)idx;
    (void)level;
    (void)pivoted;
    (void)files;
    (void)location;
    return wasm_unavailable();
}

SEXP taco_r_profile(SEXP source) {
    (void)source;
    return wasm_unavailable();
}

#endif

static const R_CallMethodDef methods[] = {
    {"taco_r_shutdown", (DL_FUNC)&taco_r_shutdown, 0},
    {"taco_r_set_progress", (DL_FUNC)&taco_r_set_progress, 1},
    {"taco_r_open", (DL_FUNC)&taco_r_open, 1},
    {"taco_r_dataset", (DL_FUNC)&taco_r_dataset, 1},
    {"taco_r_sql", (DL_FUNC)&taco_r_sql, 6},
    {"taco_r_profile", (DL_FUNC)&taco_r_profile, 1},
    {NULL, NULL, 0}};

void R_init_taco(DllInfo* dll) {
#ifndef __EMSCRIPTEN__
    if (taco_api_version() != TACO_API_VERSION)
        Rf_error("libtaco has C API %d, this package needs %d", taco_api_version(), TACO_API_VERSION);
#endif
    R_registerRoutines(dll, NULL, methods, NULL, NULL);
    R_useDynamicSymbols(dll, FALSE);
    R_forceSymbols(dll, TRUE);
}
