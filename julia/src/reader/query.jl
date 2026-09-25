using DataFrames: DataFrame


_sql_identifier(value) = "\"" * replace(value, "\"" => "\"\"") * "\""


function _dataset_sql(dataset::Dataset, query::AbstractString)::DataFrame
    statement = strip(query)
    endswith(statement, ';') && (statement = rstrip(chop(statement)))
    isempty(statement) && throw(ArgumentError("taco: query must not be empty"))
    occursin('\0', statement) && throw(ArgumentError("taco: query must not contain NUL"))

    native_sql(; level=nothing, pivoted=true, location=false) = _native_sql(
        dataset._opened;
        idx=nothing,
        level=level,
        pivoted=pivoted,
        files=nothing,
        location=location,
    )

    relations = Pair{String,String}["dataset" => native_sql(; location=true)]
    for level in dataset.contract.levels
        push!(relations, level => native_sql(; level=level))
    end
    context = join(["$(_sql_identifier(name)) AS ($sql)" for (name, sql) in relations], ",\n")
    sql = "WITH $context\nSELECT * FROM (\n$statement\n) AS taco_query"
    return _with_reader() do con
        _dataframe(con, sql)
    end
end


function _read_table(
    sources;
    files::Union{Nothing,AbstractVector{<:AbstractString}} = nothing,
    native::Union{Nothing,Vector{NativeDataset}} = nothing,
)::DataFrame
    if files !== nothing && (isempty(files) || any(isempty, files))
        throw(ArgumentError("taco: `files` entries must be non-empty"))
    end

    datasets = isnothing(native) ? _open_native.(sources) : native
    sql = _native_sql(
        datasets;
        idx=nothing,
        level=nothing,
        pivoted=true,
        files=files,
        location=true,
    )
    return _with_reader() do con
        _dataframe(con, sql)
    end
end
