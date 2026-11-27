function _plain(value::JSON3.Object)
    result = Dict{String,Any}()
    for (key, item) in pairs(value)
        result[String(key)] = _plain(item)
    end
    return result
end


_plain(value::JSON3.Array) = Any[_plain(item) for item in value]
_plain(value) = value


function _collection_documents(datasets::Vector{NativeDataset})
    return _native_collection.(datasets)
end


function _parse_collection(document)
    raw = JSON3.read(document)
    levels = String.(collect(keys(raw["taco:metadata"])))
    return _plain(raw), levels
end


function _add_counts(summaries)
    totals = Dict{String,Any}()
    for counts in summaries
        counts isa AbstractDict && all(count -> count isa Integer && !(count isa Bool), values(counts)) ||
            error("taco: split:counts must be an object of sample counts")
        for (name, count) in counts
            totals[name] = get(totals, name, 0) + count
        end
    end
    return totals
end


# Besides extent, the collection fields that summarize the rows of each source,
# so the partitions of one build differ in them (SPEC 7.5). A list combines them
# as taco.consolidate does.
const _SUMMARIES = Dict("split:counts" => _add_counts)


function _same_collection(collection)
    result = deepcopy(collection)
    for name in ("extent", "taco:sources", keys(_SUMMARIES)...)
        pop!(result, name, nothing)
    end
    return result
end


# Microseconds since 1970 of an ISO 8601 time. As text, 00:00:00.5Z sorts before
# 00:00:00Z. A time without an offset is UTC. Taco does not load Dates, so the
# days come from Howard Hinnant's days_from_civil.
function _iso_micros(value)
    pattern = r"^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]+))?(?:[Zz]|([+-])([0-9]{2}):?([0-9]{2}))?$"
    m = match(pattern, value)
    m === nothing && error("taco: extent.temporal must hold ISO 8601 times: $value")
    year, month, day, hour, minute, second = (parse(Int, m[index]) for index in 1:6)
    year -= month <= 2
    era = fld(year, 400)
    years = year - 400 * era
    days = 146097 * era + 365 * years + years ÷ 4 - years ÷ 100 + (153 * mod(month - 3, 12) + 2) ÷ 5 + day - 719469
    minutes = (24 * days + hour) * 60 + minute
    if m[8] !== nothing
        offset = 60 * parse(Int, m[9]) + parse(Int, m[10])
        minutes -= m[8] == "+" ? offset : -offset
    end
    fraction = m[7] === nothing ? 0 : parse(Int, rpad(first(m[7], 6), 6, '0'))
    return (60 * minutes + second) * 1_000_000 + fraction
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
            argmin(_iso_micros, String(value[1]) for value in temporals),
            argmax(_iso_micros, String(value[2]) for value in temporals),
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
    for (name, combine) in _SUMMARIES
        summaries = [value[name] for value in collections if get(value, name, nothing) !== nothing]
        isempty(summaries) || (collection[name] = combine(summaries))
    end
    return collection
end
