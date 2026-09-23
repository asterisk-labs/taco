import JSON3
import Libdl
using LazyArtifacts
using ProgressMeter: Progress, ProgressUnknown, update!, finish!


const _LIBRARY_ENV = "TACO_LIB"
const _API_VERSION = 2
const _HANDLE = Ref{Ptr{Cvoid}}(C_NULL)
const _SYMBOLS = Dict{Symbol,Ptr{Cvoid}}()
const _LIBRARY_LOCK = ReentrantLock()
const _CORE_FUNCTIONS = (
    :taco_api_version,
    :taco_last_error,
    :taco_free,
    :taco_set_progress,
    :taco_open,
    :taco_close,
    :taco_dataset_collection,
    :taco_dataset_level_count,
    :taco_dataset_level,
    :taco_dataset_structure_count,
    :taco_dataset_structure,
    :taco_sql,
    :taco_profile,
)


function _library_path()
    configured = get(ENV, _LIBRARY_ENV, "")
    isempty(configured) || return configured

    name = Sys.isapple() ? "libtaco.dylib" : Sys.iswindows() ? "taco.dll" : "libtaco.so"
    checkout = normpath(joinpath(@__DIR__, "..", "..", "..", "core", "build", name))
    isfile(checkout) && return checkout

    artifacts_toml = normpath(joinpath(@__DIR__, "..", "..", "Artifacts.toml"))
    base = try
        hash = artifact_hash("taco", artifacts_toml)
        hash === nothing && error("Artifacts.toml has no taco entry for this platform")
        LazyArtifacts.ensure_artifact_installed("taco", artifacts_toml)
        artifact_path(hash)
    catch err
        error(
            "taco: could not install the native TACO artifact: $(sprint(showerror, err)). " *
            "Set $_LIBRARY_ENV to an existing libtaco build to override it",
        )
    end
    entries = readdir(base)
    if length(entries) == 1 && isdir(joinpath(base, entries[1]))
        base = joinpath(base, entries[1])
    end
    return joinpath(base, Sys.iswindows() ? "bin" : "lib", name)
end


const _BARS = Dict{String,Any}()


# Native download progress, grouped by phase. C callbacks must not throw.
function _progress(phase::Cstring, done::UInt64, total::UInt64, user::Ptr{Cvoid})::Cvoid
    try
        name = unsafe_string(phase)
        enabled = stderr isa Base.TTY
        bar = get!(_BARS, name) do
            total > 0 ? Progress(Int(total); desc = name * " ", enabled = enabled) :
            ProgressUnknown(; desc = name * " ", enabled = enabled)
        end
        update!(bar, Int(done))
        if total > 0 && done >= total
            finish!(bar)
            delete!(_BARS, name)
        end
    catch
    end
    return nothing
end


function _load_core()
    lock(_LIBRARY_LOCK) do
        _HANDLE[] == C_NULL || return
        path = _library_path()
        handle = try
            Libdl.dlopen(path)
        catch err
            error(
                "taco: could not load the TACO core from $path: $(sprint(showerror, err)). " *
                "Build it with `make core` or set $_LIBRARY_ENV",
            )
        end
        for name in _CORE_FUNCTIONS
            _SYMBOLS[name] = Libdl.dlsym(handle, name)
        end
        version = ccall(_SYMBOLS[:taco_api_version], Cint, ())
        version == _API_VERSION ||
            error("taco: the TACO core at $path has C API $version, expected $_API_VERSION")
        callback = @cfunction(_progress, Cvoid, (Cstring, UInt64, UInt64, Ptr{Cvoid}))
        ccall(_SYMBOLS[:taco_set_progress], Cvoid, (Ptr{Cvoid}, Ptr{Cvoid}), callback, C_NULL)
        # Published last, so a reader never sees a handle without its symbols.
        _HANDLE[] = handle
    end
    return nothing
end


function _symbol(name::Symbol)
    _HANDLE[] == C_NULL && _load_core()
    return _SYMBOLS[name]
end


function _check_core(status::Cint)
    status == 0 && return nothing
    error(unsafe_string(ccall(_symbol(:taco_last_error), Cstring, ())))
end


function _take_string(pointer::Ptr{UInt8})
    pointer == C_NULL && return nothing
    try
        return unsafe_string(pointer)
    finally
        ccall(_symbol(:taco_free), Cvoid, (Ptr{UInt8},), pointer)
    end
end


function _c_text(value, argument)
    text = String(value)
    occursin('\0', text) && throw(ArgumentError("taco: `$argument` contains a NUL byte"))
    return text
end


mutable struct NativeDataset
    handle::Ptr{Cvoid}
end


function _open_native(source)
    out = Ref{Ptr{Cvoid}}(C_NULL)
    status = ccall(
        _symbol(:taco_open),
        Cint,
        (Cstring, Ptr{UInt8}, Ref{Ptr{Cvoid}}),
        _c_text(source, "source"),
        C_NULL,
        out,
    )
    _check_core(status)
    dataset = NativeDataset(out[])
    close = _symbol(:taco_close)
    return finalizer(value -> ccall(close, Cvoid, (Ptr{Cvoid},), value.handle), dataset)
end


function _native_collection(dataset::NativeDataset)
    return GC.@preserve dataset unsafe_string(
        ccall(_symbol(:taco_dataset_collection), Ptr{UInt8}, (Ptr{Cvoid},), dataset.handle),
    )
end


function _native_strings(dataset::NativeDataset, count::Symbol, item::Symbol)
    return GC.@preserve dataset begin
        total = ccall(_symbol(count), Csize_t, (Ptr{Cvoid},), dataset.handle)
        [
            unsafe_string(ccall(_symbol(item), Ptr{UInt8}, (Ptr{Cvoid}, Csize_t), dataset.handle, index - 1))
            for index in 1:total
        ]
    end
end


_native_levels(dataset) = _native_strings(dataset, :taco_dataset_level_count, :taco_dataset_level)
_native_structure(dataset) = _native_strings(dataset, :taco_dataset_structure_count, :taco_dataset_structure)


# Mirrors taco_read_options in taco.h.
struct _ReadOptions
    idx::Ptr{UInt8}
    level::Ptr{UInt8}
    pivoted::Cint
    files::Ptr{Ptr{UInt8}}
    file_count::Csize_t
    location::Cint
end


function _native_sql(datasets::Vector{NativeDataset}; idx, level, pivoted::Bool, files, location::Bool)
    idx_text = idx === nothing ? nothing : _c_text(idx, "idx")
    level_text = level === nothing ? nothing : _c_text(level, "level")
    file_texts = files === nothing ? nothing : [_c_text(name, "files") for name in files]
    handles = [dataset.handle for dataset in datasets]
    file_pointers = file_texts === nothing ? Ptr{UInt8}[] : [pointer(text) for text in file_texts]
    out = Ref{Ptr{UInt8}}(C_NULL)
    status = GC.@preserve datasets idx_text level_text file_texts handles file_pointers begin
        options = _ReadOptions(
            idx_text === nothing ? C_NULL : pointer(idx_text),
            level_text === nothing ? C_NULL : pointer(level_text),
            Cint(pivoted),
            file_texts === nothing ? C_NULL : pointer(file_pointers),
            Csize_t(length(file_pointers)),
            Cint(location),
        )
        ccall(
            _symbol(:taco_sql),
            Cint,
            (Ptr{Ptr{Cvoid}}, Csize_t, Ref{_ReadOptions}, Ref{Ptr{UInt8}}),
            handles,
            length(handles),
            options,
            out,
        )
    end
    _check_core(status)
    return _take_string(out[])
end


function _native_text(name::Symbol, text)
    out = Ref{Ptr{UInt8}}(C_NULL)
    status = ccall(_symbol(name), Cint, (Cstring, Ref{Ptr{UInt8}}), _c_text(text, "source"), out)
    _check_core(status)
    return _take_string(out[])
end


_native_profile(source) = _native_text(:taco_profile, source)
