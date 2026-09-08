using DataFrames: DataFrame
import DBInterface
import DuckDB


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


function _source(source)
    paths = _sources(source)
    length(paths) == 1 || throw(ArgumentError("taco: this operation needs a single source"))
    return only(paths)
end


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


function _contract(con, source)
    return _dataframe(con, "SELECT * FROM taco_contract(?)", Any[source])
end


function _same_contract(left, right)
    names(left) == names(right) || return false
    return all(name -> isequal(left[!, name], right[!, name]), names(left))
end


function _check_contracts(con, sources)
    expected = _contract(con, first(sources))
    for source in Iterators.drop(sources, 1)
        _same_contract(expected, _contract(con, source)) ||
            error("taco: sources do not share the same contract: $source")
    end
    return nothing
end


function _labels(sources)
    labels = basename.(rstrip.(sources, '/'))
    if all(!isempty, labels) && length(unique(labels)) == length(labels)
        return labels
    end
    return sources
end


const _READ_CALL = string(
    "read_taco(?, idx := ?::VARCHAR, level := ?::VARCHAR, ",
    "pivoted := ?::BOOLEAN, files := ?::VARCHAR[], gdal_vsi := ?::BOOLEAN)",
)


"""
    read(source; layout=:wide, idx=nothing, level=nothing, files=nothing,
         gdal_vsi=true) -> DataFrame

Read one TACO dataset or a vector of compatible partitions. Multiple
sources include a `source_file` column in the result.
"""
function read(
    source;
    layout::Symbol = :wide,
    idx = nothing,
    level::Union{Nothing,AbstractString} = nothing,
    files::Union{Nothing,AbstractVector{<:AbstractString}} = nothing,
    gdal_vsi::Bool = true,
)::DataFrame
    layout in (:wide, :long) ||
        throw(ArgumentError("taco: `layout` must be :wide or :long"))
    _check_idx(idx)
    level === nothing || !isempty(level) ||
        throw(ArgumentError("taco: `level` must be non-empty"))
    if files !== nothing && (isempty(files) || any(isempty, files))
        throw(ArgumentError("taco: `files` entries must be non-empty"))
    end

    sources = _sources(source)
    options = Any[
        _idx_text(idx),
        level === nothing ? nothing : String(level),
        layout === :wide,
        files === nothing ? nothing : String.(files),
        gdal_vsi,
    ]

    return _with_reader() do con
        if length(sources) == 1
            sql = "SELECT * FROM $_READ_CALL"
            return _dataframe(con, sql, Any[only(sources), options...])
        end

        _check_contracts(con, sources)
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


function _scalar(source, sql)
    path = _source(source)
    return _with_reader() do con
        first(first(collect(DBInterface.execute(con, sql, Any[path]))))
    end
end


"""Return the contract as `kind` and `value` rows."""
function contract(source)
    path = _source(source)
    return _with_reader() do con
        _contract(con, path)
    end
end


"""Return the metadata levels, parents before children."""
levels(source) = String.(_scalar(source, "SELECT taco_levels(?)"))


"""Return the structure leaves."""
structure(source) = String.(_scalar(source, "SELECT taco_structure(?)"))


"""Return `COLLECTION.json` as a string."""
collection(source) = String(_scalar(source, "SELECT taco_collection(?)"))


"""Return the serialized `taco:derived` declarations."""
function derived(source)
    values = _scalar(source, "SELECT taco_derived(?)")
    isempty(values) && return ""
    length(values) == 1 || error("taco: the dataset has invalid taco:derived metadata")
    return String(only(values))
end


"""Return the cozip profile of an archive."""
profile(source) = String(_scalar(source, "SELECT cozip_profile(?)"))
