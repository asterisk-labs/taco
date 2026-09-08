using Test
using DataFrames
using Taco


const FIXTURE = joinpath(@__DIR__, "data", "taco.zip")


@testset "taco" begin
    @testset "input validation" begin
        @test_throws "one or more paths" Taco.read(String[])
        @test_throws "non-empty" Taco.read("")
        @test_throws "unique" Taco.read(["a.zip", "a.zip"])
        @test_throws "NUL byte" Taco.read("bad\0.zip")
        @test_throws "must be \"wide\" or \"long\"" Taco.read(FIXTURE; layout="tall")
        @test_throws "whole, non-negative" Taco.read(FIXTURE; idx=1.5)
        @test_throws "whole, non-negative" Taco.read(FIXTURE; idx=true)
        @test_throws "whole, non-negative" Taco.read(FIXTURE; idx=-1)
        @test_throws "whole, non-negative" Taco.read(FIXTURE; idx=typemax(UInt64))
        @test_throws "one sample number" Taco.read(FIXTURE; idx=(1, 2, 3))
        @test_throws "must not exceed" Taco.read(FIXTURE; idx=(5, 2))
        @test_throws "non-empty" Taco.read(FIXTURE; level="")
        @test_throws "entries must be non-empty" Taco.read(FIXTURE; files=[""])
    end

    @testset "read a TACO dataset" begin
        first_reads = fetch.([Threads.@spawn Taco.read(FIXTURE; idx=0) for _ in 1:32])
        @test all(frame -> size(frame, 1) == 1, first_reads)

        dataset = Taco.open_dataset(FIXTURE)
        @test dataset isa Taco.Dataset
        @test dataset.sources == [FIXTURE]
        @test dataset.collection["id"] == "taco-fixture"
        @test dataset.contract.structure == ["image.bin", "mask.bin"]
        @test dataset.contract.levels == ["sample", "children"]
        @test isempty(dataset.contract.derived)
        @test size(Taco.read(dataset), 1) == 3
        @test occursin("Taco.Dataset", sprint(show, dataset))

        wide = Taco.read(FIXTURE)
        @test size(wide, 1) == 3
        @test issubset(["sample_id", "ml:split", "image.bin", "mask.bin"], names(wide))
        @test wide[!, "ml:split"] == ["train", "train", "test"]

        long = Taco.read(FIXTURE; layout="long")
        @test size(long, 1) == 6
        @test sort(unique(long.path)) == ["image.bin", "mask.bin"]
        @test sort(unique(long[!, "file:role"])) == ["image", "mask"]

        @test size(Taco.read(FIXTURE; idx=1), 1) == 1
        @test size(Taco.read(FIXTURE; idx=(0, 2)), 1) == 2

        narrowed = Taco.read(FIXTURE; files=["mask.bin"])
        @test "mask.bin" in names(narrowed)
        @test !("image.bin" in names(narrowed))

        long_narrowed = Taco.read(FIXTURE; layout="long", files=["mask.bin"])
        @test size(long_narrowed, 1) == 3
        @test unique(long_narrowed.path) == ["mask.bin"]
        @test_throws "structure leaf" Taco.read(
            FIXTURE;
            files=["mask.bin", "nope.bin"],
        )

        level = Taco.read(FIXTURE; level="children")
        @test size(level, 1) == 6
        @test "internal:current_id" in names(level)

        quiet = Taco.read(FIXTURE; layout="long", gdal_vsi=false)
        @test all(ismissing, quiet[!, "cozip:gdal_vsi"])
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
