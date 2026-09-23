using DataFrames: DataFrame


_sql_identifier(value) = "\"" * replace(value, "\"" => "\"\"") * "\""


function _generated_columns(contract)
    result = String[]
    for declaration in contract.structure
        variable = occursin('*', declaration)
        path = variable ? first(split(declaration, '*'; limit=2)) : declaration
        name = replace(path, "/" => "__")
        push!(result, "$name::location")
        parts = split(path, '/')
        level = length(parts) == 1 ? "children" : join(["children"; parts[1:end-1]], "/")
        haskey(contract.metadata[level], "rumi:header") && push!(result, "$name::header")
    end
    return result
end


function _dataset_sql(dataset::Dataset, query::AbstractString)::DataFrame
    statement = strip(query)
    endswith(statement, ';') && (statement = rstrip(chop(statement)))
    isempty(statement) && throw(ArgumentError("taco: query must not be empty"))
    occursin('\0', statement) && throw(ArgumentError("taco: query must not contain NUL"))

    datasets = [_open_native(source) for source in dataset.sources]
    native_sql(; level=nothing, pivoted=true, location=false) = _native_sql(
        datasets;
        idx=nothing,
        level=level,
        pivoted=pivoted,
        files=nothing,
        location=location,
    )

    data = native_sql()
    generated = _generated_columns(dataset.contract)
    if !isempty(generated)
        excluded = join(_sql_identifier.(generated), ", ")
        data = "SELECT * EXCLUDE ($excluded) FROM ($data) AS taco_data"
    end
    relations = Pair{String,String}[
        "data" => data,
        "files" => native_sql(; pivoted=false, location=true),
    ]
    for level in dataset.contract.levels
        push!(relations, replace(level, "/" => "__") => native_sql(; level=level))
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
)::DataFrame
    if files !== nothing && (isempty(files) || any(isempty, files))
        throw(ArgumentError("taco: `files` entries must be non-empty"))
    end

    datasets = [_open_native(source) for source in sources]
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
