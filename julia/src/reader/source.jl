function _check_path(source)
    source isa AbstractString ||
        throw(ArgumentError("taco: source paths must be strings"))
    path = String(source)
    isempty(path) && throw(ArgumentError("taco: source paths must be non-empty"))
    occursin('\0', path) && throw(ArgumentError("taco: a source path contains a NUL byte"))
    # A leading ~ is the home directory, as in Python and R. Julia cannot expand ~user.
    return path == "~" || startswith(path, "~/") ? expanduser(path) : path
end


# Local sources are the same when they are one file, as a path and its file://
# URI are, or two spellings on a case-insensitive file system. The core opens a
# file:// URI as the path that follows the prefix.
function _source_identity(source)
    occursin("://", source) && !startswith(source, "file://") && return source
    path = chopprefix(source, "file://")
    info = try
        stat(path)
    catch err
        err isa Base.IOError || rethrow()
        nothing
    end
    return info !== nothing && ispath(info) ? (info.device, info.inode) : abspath(path)
end


_normalize_sources(source::AbstractString) = [_check_path(source)]


function _normalize_sources(source::AbstractVector)
    isempty(source) &&
        throw(ArgumentError("taco: `source` must contain one or more paths"))
    paths = [_check_path(path) for path in source]
    allunique(_source_identity.(paths)) ||
        throw(ArgumentError("taco: source paths must be unique"))
    return paths
end


_normalize_sources(source) =
    throw(ArgumentError("taco: `source` must be a path or a vector of paths"))
