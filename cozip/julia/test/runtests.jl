using Test
using Cozip
using Cozip.LibCozip
using DataFrames
using DuckDB
using DBInterface

# Deterministic byte payload — same seed always yields same bytes.
_make_payload(seed::Integer, size::Integer) =
    UInt8[(seed + i - 1) & 0xFF for i in 1:size]

# Read a __metadata__ entry from a cozip into a DataFrame, via DuckDB
# pointed at the zip-inside path with /vsisubfile-style offset+size.
function _read_metadata(cozip_path::String)
    raw = read(cozip_path)
    # Find the __metadata__ entry by scanning the cozip index payload.
    # Magic at byte 51 = "CZIP", header is 11 bytes, then name_lens (u16),
    # names, offsets (u64), sizes (u64).
    @assert raw[1:4] == b"PK\x03\x04"
    @assert raw[31:39] == b"__cozip__"
    n = reinterpret(UInt32, raw[59:62])[1]

    cur = 51 + 11 + 1  # 1-indexed
    name_lens = reinterpret(UInt16, raw[cur : cur + 2n - 1])
    cur += 2n
    names = String[]
    for nl in name_lens
        push!(names, String(raw[cur : cur + nl - 1]))
        cur += nl
    end
    offsets = reinterpret(UInt64, raw[cur : cur + 8n - 1])
    cur += 8n
    sizes = reinterpret(UInt64, raw[cur : cur + 8n - 1])

    idx = findfirst(==("__metadata__"), names)
    @assert idx !== nothing
    off = Int(offsets[idx])
    sz = Int(sizes[idx])

    # __metadata__ payload is bytes [off+1 : off+sz] (1-indexed)
    parquet_bytes = raw[off + 1 : off + sz]

    # Write to a temp file because DuckDB can't read from memory directly.
    tmp = tempname() * ".parquet"
    write(tmp, parquet_bytes)
    try
        db = DuckDB.DB()
        con = DBInterface.connect(db)
        try
            return DataFrame(DBInterface.execute(con, "SELECT * FROM read_parquet('$tmp')"))
        finally
            DBInterface.close!(con); DBInterface.close!(db)
        end
    finally
        rm(tmp; force=true)
    end
end

# Read a user entry (by arc name) out of the cozip via the metadata index.
function _read_entry_payload(cozip_path::String, arc_name::String)
    df = _read_metadata(cozip_path)
    raw = read(cozip_path)
    idx = findfirst(==(arc_name), df.name)
    @assert idx !== nothing
    off = Int(df.offset[idx])
    sz = Int(df.size[idx])
    return raw[off + 1 : off + sz]
end


@testset "library loads and version" begin
    v = LibCozip.cozip_version()
    @test !isempty(v)
    @test occursin('.', v)
    @test LibCozip.cozip_status_string(0) == "OK"
    @test LibCozip.cozip_status_string(1) == "INVALID_LFH"
end


@testset "roundtrip" begin
    mktempdir() do dir
        sizes = [12000, 13000, 14000]
        src_files = String[]
        src_payloads = Vector{UInt8}[]
        for (i, sz) in enumerate(sizes)
            p = joinpath(dir, "src_$(i-1).bin")
            payload = _make_payload(0xA0 + i - 1, sz)
            write(p, payload)
            push!(src_files, p)
            push!(src_payloads, payload)
        end

        arc_names = ["data/file_$(i-1).bin" for i in 1:3]
        table = DataFrame(name=arc_names, path=src_files)

        out = joinpath(dir, "out.cozip")
        Cozip.create(out, table)

        # 1. exists + size >= minimum
        @test isfile(out)
        @test filesize(out) >= 32819

        # 2. raw bytes: PK signature, __cozip__ first
        raw = read(out)
        @test raw[1:4] == b"PK\x03\x04"
        @test raw[31:39] == b"__cozip__"

        # 3. LFH layout
        fname_len = reinterpret(UInt16, raw[27:28])[1]
        extra_len = reinterpret(UInt16, raw[29:30])[1]
        @test fname_len == 9
        @test extra_len == 12

        extra_id = reinterpret(UInt16, raw[40:41])[1]
        @test extra_id == 0xCA0C
        @test reinterpret(UInt16, raw[42:43])[1] == 8

        # 4. integrity hash patched (non-zero)
        @test raw[44:51] != fill(UInt8(0), 8)

        # 5. CZIP magic at byte 52 (1-indexed)
        @test raw[52:55] == b"CZIP"

        # 6. index header: version, profile, n_entries
        version = reinterpret(UInt16, raw[56:57])[1]
        profile = raw[58]
        n_index = reinterpret(UInt32, raw[59:62])[1]
        @test version == 1
        @test profile == 1
        @test n_index == 4

        # 7. user payloads round-trip via the metadata index
        for (arc, expected) in zip(arc_names, src_payloads)
            @test _read_entry_payload(out, arc) == expected
        end

        # 8. metadata schema and content
        meta = _read_metadata(out)
        @test names(meta)[1:3] == ["name", "offset", "size"]
        @test !("path" in names(meta))
        @test !("in_index" in names(meta))
        @test nrow(meta) == 3

        for (arc, expected) in zip(arc_names, src_payloads)
            i = findfirst(==(arc), meta.name)
            @test i !== nothing
            off = Int(meta.offset[i])
            sz = Int(meta.size[i])
            @test raw[off + 1 : off + sz] == expected
        end
    end
end


@testset "in_index=false excludes from index" begin
    mktempdir() do dir
        small = joinpath(dir, "small.bin"); write(small, _make_payload(0x10, 100))
        big   = joinpath(dir, "big.bin");   write(big,   _make_payload(0x20, 33000))

        table = DataFrame(
            name=["a.bin", "b.bin"],
            path=[small, big],
            in_index=[true, false],
        )

        out = joinpath(dir, "out.cozip")
        Cozip.create(out, table)

        raw = read(out)
        n_index = reinterpret(UInt32, raw[59:62])[1]
        @test n_index == 2  # a.bin + __metadata__

        meta = _read_metadata(out)
        @test meta.name == ["a.bin"]
    end
end


@testset "small archive is padded" begin
    mktempdir() do dir
        src = joinpath(dir, "tiny.txt"); write(src, "tiny\n")

        table = DataFrame(name=["tiny.txt"], path=[src])
        out = joinpath(dir, "tiny.cozip")
        Cozip.create(out, table; temp_dir=dir)

        @test filesize(out) >= 32819

        raw = read(out)
        @test raw[44:51] != fill(UInt8(0), 8)

        @test _read_entry_payload(out, "tiny.txt") == b"tiny\n"

        meta = _read_metadata(out)
        @test meta.name == ["tiny.txt"]
    end
end


@testset "rejects empty table" begin
    mktempdir() do dir
        out = joinpath(dir, "empty.cozip")
        @test_throws ArgumentError Cozip.create(out, DataFrame(name=String[], path=String[]))
    end
end


@testset "rejects missing source" begin
    mktempdir() do dir
        out = joinpath(dir, "out.cozip")
        table = DataFrame(name=["nope.bin"], path=[joinpath(dir, "missing.bin")])
        @test_throws SystemError Cozip.create(out, table)
    end
end


@testset "rejects reserved name" begin
    mktempdir() do dir
        src = joinpath(dir, "src.bin"); write(src, _make_payload(0x33, 33000))
        table = DataFrame(name=["__metadata__"], path=[src])
        @test_throws ArgumentError Cozip.create(joinpath(dir, "out.cozip"), table)
    end
end


@testset "rejects duplicate names" begin
    mktempdir() do dir
        a = joinpath(dir, "a.bin"); write(a, _make_payload(0x10, 100))
        b = joinpath(dir, "b.bin"); write(b, _make_payload(0x20, 33000))
        table = DataFrame(name=["dupe.bin", "dupe.bin"], path=[a, b])
        @test_throws ArgumentError Cozip.create(joinpath(dir, "out.cozip"), table)
    end
end


@testset "metadata + create two-step" begin
    mktempdir() do dir
        src1 = joinpath(dir, "s1.bin"); write(src1, _make_payload(0x10, 12000))
        src2 = joinpath(dir, "s2.bin"); write(src2, _make_payload(0x20, 13000))

        table = DataFrame(
            name=["s1.bin", "s2.bin"],
            path=[src1, src2],
            cloud_pct=[0.1f0, 0.05f0],
        )

        meta_parquet = joinpath(dir, "meta.parquet")
        Cozip.metadata(meta_parquet, table)
        @test isfile(meta_parquet)

        out = joinpath(dir, "out.cozip")
        Cozip.create(out, meta_parquet)

        @test isfile(out)
        @test filesize(out) >= 32819

        # Metadata inside the cozip should NOT have `path`, but should
        # still have the user's extra column `cloud_pct`.
        meta = _read_metadata(out)
        @test !("path" in names(meta))
        @test "cloud_pct" in names(meta)
        @test sort(meta.name) == ["s1.bin", "s2.bin"]
    end
end