"""Open a TACO dataset and load its collection and contract."""
open_dataset(source) = _open_dataset(source)


"""
    read(source; files=nothing) -> DataFrame

Read a path, a vector of compatible partitions or an open `Dataset`.
"""
function read(source; files=nothing)
    sources = _normalize_sources(source)
    selected = files isa AbstractString ? [String(files)] : files
    length(sources) > 1 && return read(open_dataset(sources); files=selected)
    return _read_table(sources; files=selected)
end


read(dataset::Dataset; files=nothing) =
    _read_table(dataset.sources; files=files isa AbstractString ? [String(files)] : files, native=dataset._opened)


"""Run a SQL query over `dataset` or a raw metadata level."""
sql(dataset::Dataset, query::AbstractString) = _dataset_sql(dataset, query)


"""Inspect one part of a dataset without reading its samples."""
function inspect(source, query::AbstractString)
    sources = _normalize_sources(source)
    length(sources) == 1 || throw(ArgumentError("taco: `inspect()` requires one source"))
    choices = ("collection", "contract", "levels", "native_sql", "profile", "structure")
    query in choices || throw(ArgumentError("taco: `query` must be one of: $(join(choices, ", "))"))
    native = _open_native(only(sources))
    if query == "collection"
        return first(_parse_collection(_native_collection(native)))
    elseif query == "structure"
        return _native_structure(native)
    elseif query == "levels"
        return _native_levels(native)
    elseif query == "profile"
        return _native_profile(only(sources))
    elseif query == "native_sql"
        return _native_sql([native]; idx=nothing, level=nothing, pivoted=true, files=nothing, location=true)
    end
    structure = _native_structure(native)
    levels = _native_levels(native)
    return DataFrame(kind=vcat(fill("structure", length(structure)), fill("level", length(levels))),
                     value=vcat(structure, levels))
end


"""
    read_bytes(location) -> Vector{UInt8}
    read_bytes(locations) -> Vector{Vector{UInt8}}

Read complete files from `::location` values, local paths or URLs. A vector
returns one byte vector per location, in input order. `~` and `~/` are expanded.
"""
read_bytes(location::AbstractString) = only(_native_read_bytes([_check_location(location)]))
read_bytes(locations::AbstractVector) = _native_read_bytes(String[_check_location(location) for location in locations])


function _check_location(location)
    location isa AbstractString || throw(ArgumentError("taco: locations must be strings"))
    path = _c_text(location, "location")
    isempty(path) && throw(ArgumentError("taco: locations must be non-empty"))
    return path == "~" || startswith(path, "~/") ? expanduser(path) : path
end
