import JSON3
import Libdl
using LazyArtifacts
using ProgressMeter: Progress, ProgressUnknown, update!, finish!


const _LIBRARY_ENV = "TACO_LIB"
const _API_VERSION = 3
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


# Core calls can wait on locks or call back into Julia; mark them GC-safe to
# avoid deadlocks. Before Julia 1.12, switch GC state manually and pass values
# that convert without allocating.
macro _gc_safe_ccall(call)
    @static if VERSION >= v"1.12"
        return esc(:(@ccall gc_safe = true $call))
    else
        return quote
            local state = ccall(:jl_gc_safe_enter, Int8, ())
            local result = $(esc(:(@ccall $call)))
            ccall(:jl_gc_safe_leave, Cvoid, (Int8,), state)
            result
        end
    end
end


# The core reports progress on the working thread; keep one bar per thread.
const _BARS = Union{Nothing,Pair{String,Any}}[]
const _PROGRESS_OUTPUT = Ref{IO}(devnull)


# The callback runs under core locks: do not yield or throw. Write directly to
# terminal stderr because Julia's stderr may wait on the event loop.
function _progress(phase::Cstring, done::UInt64, total::UInt64, user::Ptr{Cvoid})::Cvoid
    try
        stderr isa Base.TTY || return nothing
        thread = Threads.threadid()
        thread <= length(_BARS) || return nothing
        name = unsafe_string(phase)
        current = _BARS[thread]
        if current === nothing || current.first != name
            output = _PROGRESS_OUTPUT[]
            bar = total > 0 ? Progress(Int(total); desc = name * " ", output = output) :
                  ProgressUnknown(; desc = name * " ", output = output)
            current = _BARS[thread] = name => bar
        end
        update!(current.second, Int(done))
        if total > 0 && done >= total
            finish!(current.second)
            _BARS[thread] = nothing
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
        version == _API_VERSION || error(_version_message(path, version))
        fill!(resize!(_BARS, Threads.maxthreadid()), nothing)
        _PROGRESS_OUTPUT[] = IOContext(
            Base.Filesystem.File(Base.RawFD(2)),
            :color => get(stderr, :color, false),
            :displaysize => displaysize(stderr),
        )
        set_progress = _SYMBOLS[:taco_set_progress]
        callback = @cfunction(_progress, Cvoid, (Cstring, UInt64, UInt64, Ptr{Cvoid}))
        @_gc_safe_ccall $set_progress(callback::Ptr{Cvoid}, C_NULL::Ptr{Cvoid})::Cvoid
        # Published last, so a reader never sees a handle without its symbols.
        _HANDLE[] = handle
    end
    return nothing
end


# A checkout of Taco.jl can be ahead of the released core that Artifacts.toml
# names, so say which side is older and how to pair them.
function _version_message(path, version)
    message = "taco: the TACO core at $path has C API $version, expected $_API_VERSION. "
    if version < _API_VERSION
        return message * "The core is older than this Taco.jl, as happens when Taco.jl comes from " *
               "a source checkout ahead of the last release. Build the core with `make core` and " *
               "set $_LIBRARY_ENV to it, or install a registered Taco release"
    end
    return message * "The core is newer than this Taco.jl; update Taco.jl or point $_LIBRARY_ENV at a matching core"
end


function _symbol(name::Symbol)
    _HANDLE[] == C_NULL && _load_core()
    return _SYMBOLS[name]
end


function _bytes_symbol(name::Symbol)
    _HANDLE[] == C_NULL && _load_core()
    symbol = Libdl.dlsym_e(_HANDLE[], name)
    symbol == C_NULL && error(
        "taco: the native core does not support read_bytes; build it with `make core` or set TACO_LIB to a newer libtaco",
    )
    return symbol
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
    path = _c_text(source, "source")
    out = Ref{Ptr{Cvoid}}(C_NULL)
    open = _symbol(:taco_open)
    status = @_gc_safe_ccall $open(path::Cstring, C_NULL::Ptr{UInt8}, out::Ref{Ptr{Cvoid}})::Cint
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
    source = _c_text(text, "source")
    out = Ref{Ptr{UInt8}}(C_NULL)
    native = _symbol(name)
    status = @_gc_safe_ccall $native(source::Cstring, out::Ref{Ptr{UInt8}})::Cint
    _check_core(status)
    return _take_string(out[])
end


_native_profile(source) = _native_text(:taco_profile, source)


function _native_read_bytes(locations::Vector{String})
    isempty(locations) && return Vector{UInt8}[]
    pointers = [pointer(location) for location in locations]
    count = Csize_t(length(locations))
    out = Ref{Ptr{Cvoid}}(C_NULL)
    native = _bytes_symbol(:taco_read_bytes)
    data_symbol = _bytes_symbol(:taco_bytes_data)
    size_symbol = _bytes_symbol(:taco_bytes_size)
    free_symbol = _bytes_symbol(:taco_bytes_free)
    status = GC.@preserve locations pointers begin
        array = pointer(pointers)
        @_gc_safe_ccall $native(array::Ptr{Ptr{UInt8}}, count::Csize_t, out::Ref{Ptr{Cvoid}})::Cint
    end
    _check_core(status)
    bytes = out[]
    try
        return map(0:length(locations)-1) do index
            nbytes = ccall(size_symbol, UInt64, (Ptr{Cvoid}, Csize_t), bytes, index)
            data = ccall(data_symbol, Ptr{UInt8}, (Ptr{Cvoid}, Csize_t), bytes, index)
            file = Vector{UInt8}(undef, nbytes)
            nbytes > 0 && unsafe_copyto!(pointer(file), data, nbytes)
            file
        end
    finally
        ccall(free_symbol, Cvoid, (Ptr{Cvoid},), bytes)
    end
end
