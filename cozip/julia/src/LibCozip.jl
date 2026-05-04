module LibCozip

using Artifacts

# Status codes. Must match cozip.h.
const COZIP_OK                    = Cint(0)
const COZIP_ERR_INVALID_LFH       = Cint(1)
const COZIP_ERR_ARCHIVE_TOO_SMALL = Cint(2)
const COZIP_ERR_INVALID_ARGUMENT  = Cint(100)
const COZIP_ERR_BUFFER_TOO_SMALL  = Cint(101)
const COZIP_ERR_IO                = Cint(102)

const COZIP_PROFILE_NONE = Cint(0)
const COZIP_PROFILE_FLAT = Cint(1)
const COZIP_PROFILE_TACO = Cint(2)

const COZIP_SOURCE_PATH   = Cint(1)
const COZIP_SOURCE_BUFFER = Cint(2)

const COZIP_ERROR_MESSAGE_SIZE = 192

mutable struct cozip_error_t
    code::Cint
    message::NTuple{COZIP_ERROR_MESSAGE_SIZE,Cchar}
    cozip_error_t() = new(Cint(0), ntuple(_ -> Cchar(0), COZIP_ERROR_MESSAGE_SIZE))
end

# Immutable, isbits → Vector{cozip_entry_t} stores them contiguously.
struct cozip_entry_t
    arc_name::Ptr{Cchar}
    payload_size::UInt64
    in_index::Bool
    _pad1::NTuple{7,UInt8}
    source_kind::Cint
    _source_pad::Cint
    source_path_or_data::Ptr{Cvoid}
    source_size::Csize_t
    lfh_offset::UInt64
    lfh_size::UInt64
    payload_offset::UInt64
end

function entry_from_path(arc_name::Ptr{Cchar}, payload_size::Integer,
                         in_index::Bool, path::Ptr{Cvoid})
    cozip_entry_t(
        arc_name, UInt64(payload_size), in_index, ntuple(_ -> UInt8(0), 7),
        COZIP_SOURCE_PATH, Cint(0), path, Csize_t(0),
        UInt64(0), UInt64(0), UInt64(0),
    )
end

function entry_from_buffer(arc_name::Ptr{Cchar}, in_index::Bool,
                           data::Ptr{Cvoid}, size::Integer)
    cozip_entry_t(
        arc_name, UInt64(size), in_index, ntuple(_ -> UInt8(0), 7),
        COZIP_SOURCE_BUFFER, Cint(0), data, Csize_t(size),
        UInt64(0), UInt64(0), UInt64(0),
    )
end

function _lib_filename()
    Sys.isapple()   && return "cozip.dylib"
    Sys.iswindows() && return "cozip.dll"
    return "cozip.so"
end

_lib_subdir() = Sys.iswindows() ? "bin" : "lib"

function _resolve_lib_path()
    env = get(ENV, "COZIP_LIB_PATH", "")
    if !isempty(env)
        isfile(env) || error("cozip: COZIP_LIB_PATH=$env does not exist")
        return env
    end

    # Runtime artifact lookup — deliberately NOT the @artifact_str
    # macro. The macro resolves at precompile time and breaks the build
    # whenever Artifacts.toml is empty, regenerated, or out of sync
    # with the current release. Resolving at module load instead lets
    # the package precompile cleanly even with a stale manifest.
    artifacts_toml = joinpath(@__DIR__, "..", "Artifacts.toml")
    isfile(artifacts_toml) || error("cozip: missing $artifacts_toml")
    hash = artifact_hash("cozip", artifacts_toml)
    hash === nothing && error(
        "cozip: no matching artifact for current platform in $artifacts_toml. " *
        "Set COZIP_LIB_PATH=/path/to/cozip.{so,dylib,dll} to override.",
    )
    artifact_exists(hash) || error(
        "cozip: artifact $hash is not installed. " *
        "Run `using Pkg; Pkg.instantiate()` from the active project.",
    )
    artifact_dir = artifact_path(hash)

    # Release archives wrap everything in a single libcozip-VERSION-PLAT/
    # folder. Descend into it if present; otherwise assume flat layout.
    base = artifact_dir
    entries = readdir(base)
    if length(entries) == 1 && isdir(joinpath(base, entries[1]))
        base = joinpath(base, entries[1])
    end

    candidate = joinpath(base, _lib_subdir(), _lib_filename())
    isfile(candidate) || error("cozip: native library not found at $candidate")
    return candidate
end

const libcozip = Ref{String}("")

__init__() = (libcozip[] = _resolve_lib_path())

struct CozipError <: Exception
    code::Int
    name::String
    message::String
end

Base.showerror(io::IO, e::CozipError) = print(io, "CozipError [$(e.name)] $(e.message)")

function CozipError(err::cozip_error_t)
    code = Int(err.code)
    msg_bytes = UInt8[UInt8(c) for c in err.message if c != 0]
    msg = String(msg_bytes)
    name_ptr = ccall((:cozip_status_string, libcozip[]), Cstring, (Cint,), err.code)
    name = unsafe_string(name_ptr)
    return CozipError(code, name, msg)
end

function cozip_version()
    ptr = ccall((:cozip_version_string, libcozip[]), Cstring, ())
    return unsafe_string(ptr)
end

function cozip_status_string(status::Integer)
    ptr = ccall((:cozip_status_string, libcozip[]), Cstring, (Cint,), Cint(status))
    return unsafe_string(ptr)
end

# `Ref(err)` for a mutable struct does NOT copy — RefValue's field
# aliases the original object, so C writes through the pointer to the
# same storage `err` references. That makes the copy-back of err.code
# / err.message that earlier versions did pure no-ops; reading `err`
# directly after the ccall already sees the new values.

function plan!(entries::AbstractVector{cozip_entry_t}, err::cozip_error_t)
    err_ref = Ref(err)
    GC.@preserve entries err_ref begin
        status = ccall((:cozip_plan, libcozip[]), Cint,
                       (Ptr{cozip_entry_t}, Csize_t, Ptr{cozip_error_t}),
                       pointer(entries), length(entries), err_ref)
    end
    status == 0 || throw(CozipError(err))
    return nothing
end

function index_payload_size(entries::AbstractVector{cozip_entry_t}, err::cozip_error_t)
    out = Ref{Csize_t}(0)
    err_ref = Ref(err)
    GC.@preserve entries err_ref begin
        status = ccall((:cozip_index_payload_size, libcozip[]), Cint,
                       (Ptr{cozip_entry_t}, Csize_t, Ptr{Csize_t}, Ptr{cozip_error_t}),
                       pointer(entries), length(entries), out, err_ref)
    end
    status == 0 || throw(CozipError(err))
    return Int(out[])
end

function build_index_payload(entries::AbstractVector{cozip_entry_t},
                             profile::Integer, err::cozip_error_t)
    sz = index_payload_size(entries, err)
    buf = Vector{UInt8}(undef, sz)
    err_ref = Ref(err)
    GC.@preserve entries buf err_ref begin
        status = ccall((:cozip_build_index_payload, libcozip[]), Cint,
                       (Ptr{cozip_entry_t}, Csize_t, Cint, Ptr{UInt8}, Csize_t,
                        Ptr{cozip_error_t}),
                       pointer(entries), length(entries), Cint(profile),
                       buf, Csize_t(sz), err_ref)
    end
    status == 0 || throw(CozipError(err))
    return buf
end

function write_archive!(out_path::AbstractString,
                        entries::AbstractVector{cozip_entry_t},
                        payload::Vector{UInt8}, err::cozip_error_t)
    err_ref = Ref(err)
    GC.@preserve entries payload err_ref begin
        status = ccall((:cozip_write_archive, libcozip[]), Cint,
                       (Cstring, Ptr{cozip_entry_t}, Csize_t,
                        Ptr{UInt8}, Csize_t, Ptr{cozip_error_t}),
                       out_path, pointer(entries), length(entries),
                       payload, Csize_t(length(payload)), err_ref)
    end
    status == 0 || throw(CozipError(err))
    return nothing
end

function patch_integrity_hash!(archive_path::AbstractString,
                               payload_size::Integer, err::cozip_error_t)
    err_ref = Ref(err)
    GC.@preserve err_ref begin
        status = ccall((:cozip_patch_integrity_hash, libcozip[]), Cint,
                       (Cstring, Csize_t, Ptr{cozip_error_t}),
                       archive_path, Csize_t(payload_size), err_ref)
    end
    status == 0 || throw(CozipError(err))
    return nothing
end

end # module