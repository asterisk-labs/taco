using DataFrames: DataFrame
import DBInterface
import DuckDB
import JSON3


const _DATABASE = Ref{Union{Nothing,DuckDB.DB}}(nothing)
const _CONNECTION = Ref{Union{Nothing,DuckDB.Connection}}(nothing)
const _LOCK = ReentrantLock()


function _has_reader(con)
    query = DBInterface.execute(
        con,
        "SELECT count(*) AS n FROM duckdb_functions() WHERE function_name = 'read_taco'",
    )
    return first(first(collect(query))) > 0
end


function _require_reader(con)
    _has_reader(con) || error(
        "taco: the loaded cozip extension has no TACO reader. " *
        "Upgrade DuckDB or point COZIP_EXTENSION at a local build",
    )
    return nothing
end


function _open_reader()
    con = _CONNECTION[]
    con === nothing || return con

    local_extension = get(ENV, "COZIP_EXTENSION", "")
    db = if isempty(local_extension)
        DuckDB.DB()
    else
        DuckDB.DB(config=Dict("allow_unsigned_extensions" => "true"))
    end
    con = DBInterface.connect(db)
    try
        DBInterface.execute(con, "INSTALL httpfs")
        DBInterface.execute(con, "LOAD httpfs")
        if isempty(local_extension)
            DBInterface.execute(con, "INSTALL cozip FROM community")
            DBInterface.execute(con, "LOAD cozip")
        else
            path = replace(local_extension, "'" => "''")
            DBInterface.execute(con, "LOAD '$path'")
        end
        _require_reader(con)
    catch
        DBInterface.close!(con)
        DBInterface.close!(db)
        rethrow()
    end

    _DATABASE[] = db
    _CONNECTION[] = con
    return con
end


function _close_reader!()
    lock(_LOCK) do
        con = _CONNECTION[]
        db = _DATABASE[]
        _CONNECTION[] = nothing
        _DATABASE[] = nothing
        con === nothing || DBInterface.close!(con)
        db === nothing || DBInterface.close!(db)
    end
    return nothing
end


function __init__()
    _DATABASE[] = nothing
    _CONNECTION[] = nothing
    atexit(_close_reader!)
end


function _check_platform()
    Sys.iswindows() && error(
        "taco: reading is not supported on Windows because DuckDB.jl " *
        "cannot load the cozip filesystem extension there",
    )
    return nothing
end


function _check_path(source)
    source isa AbstractString ||
        throw(ArgumentError("taco: source paths must be strings"))
    path = String(source)
    isempty(path) && throw(ArgumentError("taco: source paths must be non-empty"))
    occursin('\0', path) && throw(ArgumentError("taco: a source path contains a NUL byte"))
    return path
end


_sources(source::AbstractString) = [_check_path(source)]


function _sources(source::AbstractVector)
    isempty(source) &&
        throw(ArgumentError("taco: `source` must contain one or more paths"))
    paths = [_check_path(path) for path in source]
    length(unique(paths)) == length(paths) ||
        throw(ArgumentError("taco: source paths must be unique"))
    return paths
end


_sources(source) = throw(ArgumentError("taco: `source` must be a path or a vector of paths"))


function _check_idx(idx)
    idx === nothing && return nothing
    values = idx isa Tuple || idx isa AbstractVector ? collect(idx) : Any[idx]
    length(values) in (1, 2) ||
        throw(ArgumentError("taco: `idx` must be one sample number or a two-element range"))
    all(
        value -> value isa Integer && !(value isa Bool) &&
                 value >= 0 && value <= typemax(Int64),
        values,
    ) || throw(ArgumentError("taco: `idx` must contain whole, non-negative numbers"))
    length(values) == 2 && values[1] > values[2] &&
        throw(ArgumentError("taco: `idx` range start must not exceed its end"))
    return nothing
end


function _idx_text(idx)
    idx === nothing && return nothing
    values = idx isa Integer ? Any[idx] : collect(idx)
    length(values) == 1 && return string(only(values))
    return "[$(values[1]), $(values[2])]"
end


function _with_reader(body)
    _check_platform()
    return lock(_LOCK) do
        body(_open_reader())
    end
end


function _dataframe(con, sql, params)
    return DataFrame(DBInterface.execute(con, sql, params))
end


function _labels(sources)
    labels = basename.(rstrip.(sources, '/'))
    if all(!isempty, labels) && length(unique(labels)) == length(labels)
        return labels
    end
    return sources
end


struct Contract
    structure::Union{Nothing,Vector{String}}
    metadata::Dict{String,Any}
    derived::Dict{String,Any}
    levels::Vector{String}
end


struct Dataset
    sources::Vector{String}
    collection::Dict{String,Any}
    contract::Contract
end


function _plain(value::JSON3.Object)
    result = Dict{String,Any}()
    for (key, item) in pairs(value)
        result[String(key)] = _plain(item)
    end
    return result
end


_plain(value::JSON3.Array) = Any[_plain(item) for item in value]
_plain(value) = value


function _collection_documents(con, sources)
    branches = ["SELECT $index AS position, taco_collection(?) AS document" for index in eachindex(sources)]
    sql = join(branches, " UNION ALL ") * " ORDER BY position"
    frame = _dataframe(con, sql, Any[sources...])
    return String.(frame[!, "document"])
end


function _parse_collection(document)
    raw = JSON3.read(document)
    levels = String.(collect(keys(raw["taco:metadata"])))
    return _plain(raw), levels
end


function _same_collection(collection)
    result = deepcopy(collection)
    pop!(result, "extent", nothing)
    pop!(result, "taco:sources", nothing)
    return result
end


function _extent_union(values)
    extents = filter(!isnothing, values)
    isempty(extents) && return nothing

    spatial = [Float64.(extent["spatial"]) for extent in extents]
    south = minimum(bounds[2] for bounds in spatial)
    north = maximum(bounds[4] for bounds in spatial)
    intervals = Tuple{Float64,Float64}[]
    for bounds in spatial
        west, east = bounds[1], bounds[3]
        if west <= east
            push!(intervals, (west + 180.0, east + 180.0))
        else
            push!(intervals, (0.0, east + 180.0), (west + 180.0, 360.0))
        end
    end
    sort!(intervals)
    merged = Vector{Vector{Float64}}()
    for (start, stop) in intervals
        if !isempty(merged) && start <= merged[end][2]
            merged[end][2] = max(merged[end][2], stop)
        else
            push!(merged, [start, stop])
        end
    end
    gaps = [(merged[index][2], merged[index + 1][1]) for index in 1:length(merged)-1]
    push!(gaps, (merged[end][2], merged[1][1] + 360.0))
    gap = gaps[argmax([stop - start for (start, stop) in gaps])]
    if gap[1] == gap[2]
        west, east = -180.0, 180.0
    else
        start = mod(gap[2], 360.0)
        stop = mod(gap[1], 360.0)
        west = start - 180.0
        east = stop == 0.0 && start > 0.0 ? 180.0 : stop - 180.0
    end

    temporals = [extent["temporal"] for extent in extents if get(extent, "temporal", nothing) !== nothing]
    temporal = if isempty(temporals)
        nothing
    else
        Any[
            minimum(String(value[1]) for value in temporals),
            maximum(String(value[2]) for value in temporals),
        ]
    end
    return Dict{String,Any}(
        "spatial" => Any[west, south, east, north],
        "temporal" => temporal,
    )
end


function _merge_collections(collections, levels, sources)
    length(collections) == 1 && return only(collections)
    any(collection -> get(collection, "taco:sources", nothing) !== nothing, collections) &&
        error("taco: a source list cannot contain TACOCAT datasets")
    expected = _same_collection(first(collections))
    for index in 2:length(collections)
        (levels[index] == levels[1] && isequal(_same_collection(collections[index]), expected)) ||
            error("taco: source does not belong to the same collection: $(sources[index])")
    end
    collection = deepcopy(first(collections))
    collection["extent"] = _extent_union([get(value, "extent", nothing) for value in collections])
    collection["extent"] === nothing && delete!(collection, "extent")
    return collection
end


function _collection_contract(collection, levels)
    raw_structure = collection["taco:structure"]
    structure = raw_structure === nothing ? nothing : String.(raw_structure)
    metadata = Dict{String,Any}(collection["taco:metadata"])
    derived = Dict{String,Any}(get(collection, "taco:derived", Dict{String,Any}()))
    return Contract(structure, metadata, derived, levels)
end


"""Open a TACO dataset and load its collection and contract."""
function open_dataset(source)
    sources = _sources(source)
    return _with_reader() do con
        parsed = _parse_collection.(_collection_documents(con, sources))
        collections = first.(parsed)
        levels = last.(parsed)
        collection = _merge_collections(collections, levels, sources)
        Dataset(sources, collection, _collection_contract(collection, first(levels)))
    end
end


function Base.show(io::IO, dataset::Dataset)
    collection = dataset.collection
    print(io, "Taco.Dataset(")
    show(io, collection["id"])
    print(
        io,
        ", version=",
        collection["dataset_version"],
        ", sources=",
        length(dataset.sources),
        ")",
    )
end


const _READ_CALL = string(
    "read_taco(?, idx := ?::VARCHAR, level := ?::VARCHAR, ",
    "pivoted := ?::BOOLEAN, files := ?::VARCHAR[], gdal_vsi := ?::BOOLEAN)",
)


"""
    read(source; layout="wide", idx=nothing, level=nothing, files=nothing,
         gdal_vsi=true) -> DataFrame

Read a path, a vector of compatible partitions or an open `Dataset`.
"""
function read(source; kwargs...)
    sources = _sources(source)
    length(sources) > 1 && return read(open_dataset(sources); kwargs...)
    return _read_sources(sources; kwargs...)
end


read(dataset::Dataset; kwargs...) = _read_sources(dataset.sources; kwargs...)


function _read_sources(
    sources;
    layout::AbstractString = "wide",
    idx = nothing,
    level::Union{Nothing,AbstractString} = nothing,
    files::Union{Nothing,AbstractVector{<:AbstractString}} = nothing,
    gdal_vsi::Bool = true,
)::DataFrame
    layout in ("wide", "long") ||
        throw(ArgumentError("taco: `layout` must be \"wide\" or \"long\""))
    _check_idx(idx)
    level === nothing || !isempty(level) ||
        throw(ArgumentError("taco: `level` must be non-empty"))
    if files !== nothing && (isempty(files) || any(isempty, files))
        throw(ArgumentError("taco: `files` entries must be non-empty"))
    end

    options = Any[
        _idx_text(idx),
        level === nothing ? nothing : String(level),
        layout == "wide",
        files === nothing ? nothing : String.(files),
        gdal_vsi,
    ]

    return _with_reader() do con
        if length(sources) == 1
            sql = "SELECT * FROM $_READ_CALL"
            return _dataframe(con, sql, Any[only(sources), options...])
        end

        projection = if level === nothing
            "taco.sample_id, ?::VARCHAR AS source_file, taco.* EXCLUDE (sample_id)"
        else
            "?::VARCHAR AS source_file, taco.*"
        end
        branch = "SELECT $projection FROM $_READ_CALL AS taco"
        sql = join(fill(branch, length(sources)), " UNION ALL BY NAME ")
        params = Any[]
        for (label, path) in zip(_labels(sources), sources)
            append!(params, Any[label, path, options...])
        end
        return _dataframe(con, sql, params)
    end
end
