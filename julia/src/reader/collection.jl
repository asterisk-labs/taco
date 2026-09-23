function _plain(value::JSON3.Object)
    result = Dict{String,Any}()
    for (key, item) in pairs(value)
        result[String(key)] = _plain(item)
    end
    return result
end


_plain(value::JSON3.Array) = Any[_plain(item) for item in value]
_plain(value) = value


function _collection_documents(sources)
    return [_native_collection(_open_native(source)) for source in sources]
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
