using DataFrames
using DuckDB
using Tables

using .LibCozip:
    cozip_entry_t,
    cozip_error_t,
    entry_from_path,
    entry_from_buffer,
    plan!,
    index_payload_size,
    build_index_payload,
    write_archive!,
    patch_integrity_hash!,
    COZIP_PROFILE_FLAT,
    COZIP_SOURCE_PATH,
    COZIP_SOURCE_BUFFER

# Reserved names; users cannot use them.
const _PADDING_NAME = "__cozip_padding__"
const _RESERVED_NAMES = Set(["__cozip__", "__metadata__", _PADDING_NAME])

# Constants from cozip.h / ZIP APPNOTE for the small-archive padding logic.
const _COZIP_INDEX_OFFSET = 51
const _COZIP_HASH_WINDOW_SIZE = 32768
const _COZIP_MIN_ARCHIVE_SIZE = _COZIP_HASH_WINDOW_SIZE + _COZIP_INDEX_OFFSET
const _ZIP_CENTRAL_HEADER_BASE_SIZE = 46
const _ZIP_EOCD_SIZE = 22


"""
    metadata(out_path, table; create_options=nothing) -> String

Materialize the `__metadata__` parquet for a cozip archive.

Runs `cozip_plan` internally to compute offsets/sizes, then writes a
parquet at `out_path` with columns `name, offset, size, path, +extras`.
The `path` column stays in this parquet on purpose — it's consumed by
`Cozip.create(out, table::String)` and dropped right before the
parquet enters the ZIP.
"""
function metadata(out_path::AbstractString, table; create_options=nothing)
    df = _validate_and_normalize(table)
    n_users = nrow(df)

    names_v = String.(df.name)
    paths_v = String.(df.path)
    in_idx  = Vector{Bool}(df.in_index)

    user_entries, keepalive = _make_entries(names_v, paths_v, in_idx)

    # Include the __metadata__ slot in the plan so the cozip index size
    # accounts for it. The slot's payload_size is 0 here as a placeholder
    # — its real size is unknown until we materialize the parquet, but
    # since the slot sits last its size never shifts user offsets.
    name_meta_buf = Vector{UInt8}(codeunits("__metadata__")); push!(name_meta_buf, 0x00)
    push!(keepalive, name_meta_buf)

    entries = Vector{cozip_entry_t}(undef, n_users + 1)
    @inbounds for i in 1:n_users
        entries[i] = user_entries[i]
    end
    entries[n_users + 1] = entry_from_path(
        Ptr{Cchar}(pointer(name_meta_buf)),
        UInt64(0),
        true,
        Ptr{Cvoid}(C_NULL),
    )

    err = cozip_error_t()
    GC.@preserve keepalive plan!(entries, err)

    offsets = UInt64[entries[i].payload_offset for i in 1:n_users]
    sizes   = UInt64[entries[i].payload_size   for i in 1:n_users]

    # Keep all rows (incl. in_index=false) and keep `path` and `in_index`
    # columns — both are consumed by create() downstream. The final
    # __metadata__ that enters the ZIP is built by create() and only
    # then drops `path` and filters to in_index=true rows.
    df.offset = offsets
    df.size = sizes
    rest = setdiff(names(df), ["name", "offset", "size", "path", "in_index"])
    out = select(df, ["name", "offset", "size", "path", "in_index", rest...])

    _duckdb_write_parquet(out, out_path; create_options=create_options)
    return String(out_path)
end


"""
    create(out_path, table::String; temp_dir=nothing) -> String

Pack a cozip archive from a metadata parquet (output of `Cozip.metadata`).

Reads `name`, `path`, `offset`, `size` from the parquet, validates the
offsets against a fresh `cozip_plan`, rewrites the parquet without the
`path` column into `temp_dir`, and packs the archive.
"""
function create(out_path::AbstractString, table::AbstractString; temp_dir=nothing)
    out_path = abspath(out_path)
    temp_dir = something(temp_dir, tempdir())
    isdir(temp_dir) || mkpath(temp_dir)

    df = _read_parquet(String(table))
    _validate_metadata_parquet(df)

    n_users = nrow(df)
    names_v = String.(df.name)
    paths_v = String.(df.path)
    in_idx  = "in_index" in names(df) ? Vector{Bool}(df.in_index) : fill(true, n_users)

    user_entries, keepalive = _make_entries(names_v, paths_v, in_idx)

    # Replan with the __metadata__ slot to validate offsets match.
    name_meta_buf = Vector{UInt8}(codeunits("__metadata__")); push!(name_meta_buf, 0x00)
    push!(keepalive, name_meta_buf)

    entries = Vector{cozip_entry_t}(undef, n_users + 1)
    @inbounds for i in 1:n_users
        entries[i] = user_entries[i]
    end
    entries[n_users + 1] = entry_from_path(
        Ptr{Cchar}(pointer(name_meta_buf)),
        UInt64(0),
        true,
        Ptr{Cvoid}(C_NULL),
    )

    err = cozip_error_t()
    GC.@preserve keepalive plan!(entries, err)

    for i in 1:n_users
        if entries[i].payload_offset != UInt64(df.offset[i]) ||
           entries[i].payload_size   != UInt64(df.size[i])
            error("cozip.create: parquet offsets/sizes do not match cozip_plan " *
                  "for entry $(names_v[i]) (row $i)")
        end
    end

    # Write the metadata parquet without `path` and `in_index`, with
    # only in_index=true rows — this is what enters the ZIP as
    # __metadata__.
    meta_df = filter(:in_index => identity, df)
    meta_df = select(meta_df, Not([:path, :in_index]))
    meta_parquet = joinpath(temp_dir, "cozip_meta_$(getpid())_$(time_ns()).parquet")
    _duckdb_write_parquet(meta_df, meta_parquet)

    try
        GC.@preserve keepalive _pack_archive(out_path, user_entries, n_users, meta_parquet, temp_dir)
    finally
        rm(meta_parquet; force=true)
    end

    return out_path
end


"""
    create(out_path, table; temp_dir=nothing) -> String

All-in-one: build the metadata parquet, pack the archive, clean up.
Equivalent to calling `metadata` followed by `create(out, parquet_path)`.
"""
function create(out_path::AbstractString, table; temp_dir=nothing)
    temp_dir = something(temp_dir, tempdir())
    isdir(temp_dir) || mkpath(temp_dir)

    intermediate = joinpath(temp_dir, "cozip_full_$(getpid())_$(time_ns()).parquet")
    try
        metadata(intermediate, table)
        create(out_path, intermediate; temp_dir=temp_dir)
    finally
        rm(intermediate; force=true)
    end
    return String(out_path)
end


# Validate input table and normalize: required columns, reserved names,
# duplicate detection, missing source files. Adds in_index=true if absent.
function _validate_and_normalize(table)::DataFrame
    df = DataFrame(table; copycols=true)
    cols = names(df)

    missing_cols = setdiff(["name", "path"], cols)
    isempty(missing_cols) ||
        throw(ArgumentError("cozip.metadata: missing required column(s): $missing_cols"))

    nrow(df) > 0 || throw(ArgumentError("cozip.metadata: empty entry list"))

    if !("in_index" in cols)
        df.in_index = fill(true, nrow(df))
    end

    seen = Set{String}()
    for (i, name) in enumerate(df.name)
        name in _RESERVED_NAMES &&
            throw(ArgumentError("cozip.metadata: row $i uses reserved name $(repr(name))"))
        name in seen &&
            throw(ArgumentError("cozip.metadata: duplicate name $(repr(name)) at row $i"))
        push!(seen, name)
    end

    for (i, p) in enumerate(df.path)
        isfile(p) ||
            throw(SystemError("cozip.metadata: row $i ($(repr(df.name[i]))): " *
                              "source file not found: $p"))
    end

    return df
end


# Construct the cozip_entry_t array. Returns also a keepalive vector
# that holds the null-terminated UTF-8 buffers the entries point at —
# the caller must keep it alive across every ccall that reads them.
function _make_entries(
    names_v::Vector{String},
    paths_v::Vector{String},
    in_idx::AbstractVector{Bool},
)
    n = length(names_v)
    entries = Vector{cozip_entry_t}(undef, n)
    keepalive = Vector{Vector{UInt8}}(undef, 2n)
    for i in 1:n
        nb = Vector{UInt8}(codeunits(names_v[i])); push!(nb, 0x00)
        pb = Vector{UInt8}(codeunits(paths_v[i])); push!(pb, 0x00)
        keepalive[2i - 1] = nb
        keepalive[2i]     = pb

        entries[i] = entry_from_path(
            Ptr{Cchar}(pointer(nb)),
            UInt64(filesize(paths_v[i])),
            Bool(in_idx[i]),
            Ptr{Cvoid}(pointer(pb)),
        )
    end
    return entries, keepalive
end


function _read_parquet(path::String)::DataFrame
    isfile(path) || throw(SystemError("cozip.create: parquet not found: $path"))
    db = DuckDB.DB()
    con = DBInterface.connect(db)
    try
        result = DBInterface.execute(con, "SELECT * FROM read_parquet('$path')")
        return DataFrame(result)
    finally
        DBInterface.close!(con)
        DBInterface.close!(db)
    end
end


function _validate_metadata_parquet(df::DataFrame)
    required = ["name", "path", "offset", "size"]
    missing_cols = setdiff(required, names(df))
    isempty(missing_cols) ||
        error("cozip.create: metadata parquet is missing required column(s): $missing_cols")
    nrow(df) > 0 || error("cozip.create: metadata parquet is empty")
end


# Write a DataFrame to a parquet file via DuckDB. `create_options` is a
# raw SQL fragment passed inside the `COPY ... TO ... (FORMAT parquet,
# <create_options>)` parenthesis, e.g. "COMPRESSION 'zstd'".
function _duckdb_write_parquet(
    df::DataFrame,
    out_path::AbstractString;
    create_options=nothing,
)
    db = DuckDB.DB()
    con = DBInterface.connect(db)
    try
        DuckDB.register_data_frame(con, df, "tmp_cozip")
        opts = "FORMAT parquet"
        if create_options !== nothing && !isempty(strip(create_options))
            opts = "FORMAT parquet, $(create_options)"
        end
        escaped = replace(String(out_path), "'" => "''")
        DBInterface.execute(con, "COPY tmp_cozip TO '$escaped' ($opts)")
    finally
        DBInterface.close!(con)
        DBInterface.close!(db)
    end
end


# Pack the final archive: append the __metadata__ entry, optionally a
# padding entry to reach the minimum size, plan, build the index
# payload, write via libzip, and patch the integrity hash.
function _pack_archive(
    out_path::String,
    user_entries::Vector{cozip_entry_t},
    n_users::Int,
    meta_parquet::String,
    temp_dir::String,
)
    name_meta_buf = Vector{UInt8}(codeunits("__metadata__")); push!(name_meta_buf, 0x00)
    meta_path_buf = Vector{UInt8}(codeunits(meta_parquet));   push!(meta_path_buf, 0x00)
    pad_name_buf  = Vector{UInt8}(codeunits(_PADDING_NAME));  push!(pad_name_buf, 0x00)
    keepalive = Any[name_meta_buf, meta_path_buf, pad_name_buf]

    entries = Vector{cozip_entry_t}(undef, n_users + 2)
    @inbounds for i in 1:n_users
        entries[i] = user_entries[i]
    end
    meta_idx = n_users + 1
    pad_idx  = n_users + 2

    entries[meta_idx] = entry_from_path(
        Ptr{Cchar}(pointer(name_meta_buf)),
        UInt64(filesize(meta_parquet)),
        true,
        Ptr{Cvoid}(pointer(meta_path_buf)),
    )

    err = cozip_error_t()
    n_total = n_users + 1

    GC.@preserve keepalive begin
        plan!(view(entries, 1:n_total), err)
        idx_size = index_payload_size(view(entries, 1:n_total), err)

        # Optional padding entry: small archives need extra bytes so the
        # 32 KiB integrity suffix doesn't collide with the mutable hash
        # slot in the first LFH.
        pad_size = _required_padding_size(view(entries, 1:n_total), idx_size)

        if pad_size > 0
            padding_buf = fill(UInt8(0x5a), pad_size)
            push!(keepalive, padding_buf)
            entries[pad_idx] = entry_from_buffer(
                Ptr{Cchar}(pointer(pad_name_buf)),
                false,
                Ptr{Cvoid}(pointer(padding_buf)),
                pad_size,
            )

            n_total += 1
            plan!(view(entries, 1:n_total), err)
            idx_size = index_payload_size(view(entries, 1:n_total), err)
        end

        payload = build_index_payload(view(entries, 1:n_total), COZIP_PROFILE_FLAT, err)
        write_archive!(out_path, view(entries, 1:n_total), payload, err)
        patch_integrity_hash!(out_path, length(payload), err)
    end
end


function _required_padding_size(entries, index_payload_size::Int)::Int
    predicted = _predict_zip32_archive_size(entries, index_payload_size)
    missing_bytes = _COZIP_MIN_ARCHIVE_SIZE - predicted
    missing_bytes <= 0 && return 0

    name_len = length(_PADDING_NAME)
    overhead = 30 + name_len + _ZIP_CENTRAL_HEADER_BASE_SIZE + name_len
    return max(1, missing_bytes - overhead)
end


# Predict the on-disk size libzip will produce for a STORE-only ZIP32
# archive. Used to decide whether we need the padding entry.
function _predict_zip32_archive_size(entries, index_payload_size::Int)::Int
    total = _COZIP_INDEX_OFFSET + index_payload_size
    for e in entries
        total += Int(e.lfh_size) + Int(e.payload_size)
    end
    total += _ZIP_CENTRAL_HEADER_BASE_SIZE + length("__cozip__")
    for e in entries
        s = unsafe_string(e.arc_name)
        total += _ZIP_CENTRAL_HEADER_BASE_SIZE + length(s)
    end
    total += _ZIP_EOCD_SIZE
    return total
end