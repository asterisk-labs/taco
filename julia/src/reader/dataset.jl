struct Contract
    structure::Vector{String}
    metadata::Dict{String,Any}
    derived::Dict{String,Any}
    levels::Vector{String}
end


struct Dataset
    sources::Vector{String}
    collection::Dict{String,Any}
    contract::Contract
    _opened::Vector{NativeDataset}
end


function _build_contract(collection, levels)
    raw_structure = collection["taco:structure"]
    structure = String.(raw_structure)
    metadata = Dict{String,Any}(collection["taco:metadata"])
    derived = Dict{String,Any}(get(collection, "taco:derived", Dict{String,Any}()))
    return Contract(structure, metadata, derived, levels)
end


function _open_dataset(source)
    sources = _normalize_sources(source)
    native = _open_native.(sources)
    parsed = _parse_collection.(_collection_documents(native))
    collection = _merge_collections(first.(parsed), last.(parsed), sources)
    levels = last(first(parsed))
    return Dataset(sources, collection, _build_contract(collection, levels), native)
end


function Base.show(io::IO, dataset::Dataset)
    collection = dataset.collection
    print(io, "Taco.Dataset(")
    show(io, collection["id"])
    print(io, ", sources=", length(dataset.sources), ")")
end
