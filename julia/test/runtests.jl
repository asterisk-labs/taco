using Test
using DataFrames
import DuckDB
import JSON3
using Taco


const FIXTURE = joinpath(@__DIR__, "data", "taco.zip")


@testset "taco" begin
    @testset "input validation" begin
        @test_throws "one or more paths" Taco.read(String[])
        @test_throws "non-empty" Taco.read("")
        @test_throws "unique" Taco.read(["a.zip", "a.zip"])
        @test_throws "NUL byte" Taco.read("bad\0.zip")
        @test_throws "entries must be non-empty" Taco.read(FIXTURE; files=[""])
    end

    @testset "read a TACO dataset" begin
        first_reads = fetch.([Threads.@spawn Taco.read(FIXTURE) for _ in 1:32])
        @test all(frame -> size(frame, 1) == 3, first_reads)

        dataset = Taco.open_dataset(FIXTURE)
        @test dataset isa Taco.Dataset
        @test dataset.sources == [FIXTURE]
        @test dataset.collection["id"] == "taco-fixture"
        @test dataset.contract.structure == ["image.bin", "mask.bin"]
        @test dataset.contract.levels == ["sample", "children"]
        @test isempty(dataset.contract.derived)
        @test size(Taco.read(dataset), 1) == 3
        @test occursin("Taco.Dataset", sprint(show, dataset))
        @test Taco.sql(dataset, "SELECT id FROM dataset WHERE \"taco:sample_index\" = 1").id == ["sample-1"]
        @test size(Taco.sql(dataset, "SELECT * FROM dataset"), 1) == 3

        wide = Taco.read(FIXTURE)
        @test size(wide, 1) == 3
        @test issubset(["taco:sample_index", "ml:split", "image.bin::location", "mask.bin::location"], names(wide))
        @test wide[!, "ml:split"] == ["train", "train", "test"]

        queried = Taco.sql(dataset, "SELECT * FROM dataset")
        @test size(queried, 1) == 3
        @test issubset(["image.bin::location", "mask.bin::location"], names(queried))
        @test !("cozip:location" in names(queried))
        @test !("cozip:gdal_vsi" in names(queried))
        @test_throws DuckDB.QueryException Taco.sql(dataset, "SELECT * FROM data")
        @test_throws DuckDB.QueryException Taco.sql(dataset, "SELECT * FROM files")

        @test size(Taco.sql(dataset, "SELECT * FROM dataset WHERE \"taco:sample_index\" = 1"), 1) == 1
        @test size(Taco.sql(dataset, "SELECT * FROM dataset WHERE \"taco:sample_index\" < 2"), 1) == 2

        narrowed = Taco.read(FIXTURE; files=["mask.bin"])
        @test "mask.bin::location" in names(narrowed)
        @test !("image.bin::location" in names(narrowed))
        @test Taco.read(FIXTURE; files="mask.bin") == narrowed

        @test_throws "structure leaf" Taco.read(
            FIXTURE;
            files=["mask.bin", "nope.bin"],
        )

        level = Taco.sql(dataset, "SELECT * FROM children")
        @test size(level, 1) == 6
        @test "internal:current_id" in names(level)
        @test isempty(intersect(
            ["cozip:location", "taco:location", "cozip:gdal_vsi"],
            names(level),
        ))

        @test Taco.inspect(FIXTURE, "structure") == ["image.bin", "mask.bin"]
        @test Taco.inspect(FIXTURE, "levels") == ["sample", "children"]
        @test Taco.inspect(FIXTURE, "collection")["id"] == "taco-fixture"
        @test Taco.inspect(FIXTURE, "profile") == "taco"
        @test Taco.inspect(FIXTURE, "contract") isa DataFrame
        @test occursin("read_parquet", Taco.inspect(FIXTURE, "native_sql"))
        @test_throws "must be one of" Taco.inspect(FIXTURE, "unknown")
    end

    @testset "paths and partitions" begin
        mktempdir() do dir
            unicode_path = joinpath(dir, "niño.zip")
            part_path = joinpath(dir, "part-1.zip")
            cp(FIXTURE, unicode_path)
            cp(FIXTURE, part_path)

            @test size(Taco.read(unicode_path), 1) == 3

            dataset = Taco.open_dataset([FIXTURE, part_path])
            parts = Taco.read(dataset)
            @test size(parts, 1) == 6
            @test sort(unique(parts.source_file)) == ["part-1.zip", "taco.zip"]
            @test parts[!, "taco:sample_index"] == 0:5
            @test Taco.read([FIXTURE, part_path]) == parts
        end
    end

    @testset "extent union" begin
        first = Dict(
            "spatial" => Any[-76.0, -12.0, -75.0, -11.0],
            "temporal" => Any["2024-01-01T00:00:00Z", "2024-01-02T00:00:00Z"],
        )
        second = Dict(
            "spatial" => Any[-74.0, -10.0, -73.0, -9.0],
            "temporal" => Any["2024-01-03T00:00:00Z", "2024-01-04T00:00:00Z"],
        )
        extent = Taco._extent_union([first, second])
        @test extent["spatial"] == Any[-76.0, -12.0, -73.0, -9.0]
        @test extent["temporal"] == Any[
            "2024-01-01T00:00:00Z",
            "2024-01-04T00:00:00Z",
        ]

        collection = Dict("id" => "same")
        @test_throws "same collection" Taco._merge_collections(
            [collection, copy(collection)],
            [["sample", "children"], ["children", "sample"]],
            ["first.zip", "second.zip"],
        )
    end
end
