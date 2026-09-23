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
    _read_table(dataset.sources; files=files isa AbstractString ? [String(files)] : files)


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
