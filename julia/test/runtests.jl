using Test
using DataFrames
import DuckDB
import JSON3
using Taco


const FIXTURE = joinpath(@__DIR__, "data", "taco.zip")
const NESTED_FIXTURE = joinpath(@__DIR__, "data", "taco_nested.zip")


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
        @test !hasproperty(dataset.contract, :derived)
        @test size(Taco.read(dataset), 1) == 3
        @test occursin("Taco.Dataset", sprint(show, dataset))
        @test Taco.sql(dataset, "SELECT id FROM dataset WHERE \"taco:sample_index\" = 1").id == ["sample-1"]
        @test size(Taco.sql(dataset, "SELECT * FROM dataset"), 1) == 3

        wide = Taco.read(FIXTURE)
        @test size(wide, 1) == 3
        @test issubset(["taco:sample_index", "split:split", "image.bin::location", "mask.bin::location"], names(wide))
        @test wide[!, "split:split"] == ["train", "train", "test"]

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

        nested = Taco.open_dataset(NESTED_FIXTURE)
        nested_wide = Taco.read(nested)
        @test issubset(["before/B02.bin::location", "after/B02.bin::location"], names(nested_wide))
        @test !any(name -> occursin("__", name), names(nested_wide))
        @test size(Taco.sql(nested, "SELECT * FROM \"children/before\""), 1) == 6

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
            @test dataset.collection["split:counts"] ==
                  Dict("train" => 4, "validation" => 0, "test" => 2, "excluded" => 0)

            # Two spellings of one file are one source.
            @test_throws "unique" Taco.read([part_path, joinpath(dir, ".", "part-1.zip")])
            @test_throws "unique" Taco.read([part_path, "file://" * part_path])
            upper = joinpath(dir, "PART-1.ZIP")
            isfile(upper) && @test_throws "unique" Taco.read([part_path, upper])

            if !Sys.iswindows()
                withenv("HOME" => dir) do
                    @test size(Taco.read("~/part-1.zip"), 1) == 3
                    @test Taco.open_dataset("~/part-1.zip").sources == [part_path]
                    @test Taco.inspect("~/part-1.zip", "levels") == ["sample", "children"]
                end
            end
        end
    end

    @testset "read_bytes" begin
        # Read a local file or ZIP range for the expected bytes.
        function location_bytes(location)
            m = match(r"^/vsisubfile/(\d+)_(\d+),(.+)$", location)
            m === nothing && return Base.read(location)
            return open(m[3]) do io
                seek(io, parse(Int, m[1]))
                Base.read(io, parse(Int, m[2]))
            end
        end

        mktempdir() do dir
            # Test the extracted fixture too when unzip is available.
            sources = Any[FIXTURE, NESTED_FIXTURE]
            if Sys.which("unzip") !== nothing
                folder = joinpath(dir, "folder")
                mkpath(folder)
                run(`unzip -q $FIXTURE -d $folder`)
                push!(sources, folder)
            end
            for source in sources
                wide = Taco.read(source)
                locations = String[]
                for name in names(wide)
                    endswith(name, "::location") && append!(locations, wide[!, name])
                end
                expected = location_bytes.(locations)
                @test Taco.read_bytes(locations) == expected
                @test Taco.read_bytes(first(locations)) == first(expected)
            end
            @test Taco.read_bytes(String[]) == Vector{UInt8}[]

            write(joinpath(dir, "payload.bin"), "payload")
            if !Sys.iswindows()
                withenv("HOME" => dir) do
                    @test Taco.read_bytes("~/payload.bin") == Vector{UInt8}("payload")
                end
            end
            @test_throws "non-empty" Taco.read_bytes("")
            @test_throws "NUL byte" Taco.read_bytes("bad\0.bin")
            @test_throws "must be strings" Taco.read_bytes([1])
            @test_throws "could not resolve" Taco.read_bytes("/vsisubfile/x_4,a.zip")
            @test_throws "could not" Taco.read_bytes(joinpath(dir, "missing.bin"))
        end
    end

    @testset "timestamps" begin
        dataset = Taco.open_dataset(FIXTURE)
        result = Taco.sql(dataset, """
            SELECT TIMESTAMPTZ '2024-01-01 12:00:00.000250+00' AS moment,
                   epoch_us(TIMESTAMPTZ '2024-01-01 12:00:00.000250+00') AS micros
            """)
        # A DateTime holds milliseconds; epoch_us returns the stored microseconds.
        @test string.(result.moment) == ["2024-01-01T12:00:00"]
        @test result.micros == [1704110400000250]
    end

    @testset "sql time zone" begin
        dataset = Taco.open_dataset(FIXTURE)
        setting = Taco.sql(dataset, "SELECT current_setting('autoinstall_known_extensions') AS autoinstall")
        @test setting.autoinstall == [false]

        icu = Taco.sql(dataset, "SELECT installed FROM duckdb_extensions() WHERE extension_name = 'icu'")
        if only(icu.installed)
            # ICU takes the time zone of the process when it loads, so a reader
            # that does not pin UTC answers in Lima time here.
            result = withenv("TZ" => "America/Lima") do
                Taco.sql(dataset, """
                    SELECT current_setting('TimeZone') AS zone,
                           CAST(TIMESTAMPTZ '2024-03-01 03:30:00+00' AS DATE) AS day,
                           extract(hour FROM TIMESTAMPTZ '2024-03-01 03:30:00+00') AS hour
                    """)
            end
            @test result.zone == ["UTC"]
            @test string.(result.day) == ["2024-03-01"]
            @test result.hour == [3]
        else
            @test_skip "ICU is not installed"
        end
    end

    @testset "first reads on many threads" begin
        # A first read extracts the archive and reports progress from inside the
        # core. Many at once while the GC is busy used to hang, so they run in a
        # child process that is killed if it does not finish.
        mktempdir() do dir
            paths = [joinpath(dir, "copy-$index.zip") for index in 1:48]
            foreach(path -> cp(FIXTURE, path), paths)
            script = """
                using Taco
                running = Threads.Atomic{Bool}(true)
                churn = Threads.@spawn while running[]
                    [rand(64) for _ in 1:2000]
                end
                rows = fetch.([Threads.@spawn size(Taco.read(path), 1) for path in $(repr(paths))])
                running[] = false
                wait(churn)
                exit(all(==(3), rows) ? 0 : 1)
                """
            command = `$(Base.julia_cmd()) --project=$(Base.active_project()) --threads=4 -e $script`
            child = run(pipeline(command; stdout=devnull); wait=false)
            timedwait(() -> process_exited(child), 120.0) == :ok || kill(child, Base.SIGKILL)
            @test success(child)
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

        # Times compare as instants, not as text.
        place = Any[1.0, 1.0, 2.0, 2.0]
        half = Dict("spatial" => place, "temporal" => Any["2024-05-01T00:00:00.500000Z", "2024-05-01T12:00:00.500000Z"])
        whole = Dict("spatial" => place, "temporal" => Any["2024-05-01T00:00:00Z", "2024-05-01T12:00:00Z"])
        @test Taco._extent_union([half, whole])["temporal"] ==
              Any["2024-05-01T00:00:00Z", "2024-05-01T12:00:00.500000Z"]
        shifted = Dict("spatial" => place, "temporal" => Any["2024-05-01T01:00:00+02:00", "2024-05-01T11:00:00-02:00"])
        @test Taco._extent_union([whole, shifted])["temporal"] ==
              Any["2024-05-01T01:00:00+02:00", "2024-05-01T11:00:00-02:00"]
        @test Taco._iso_micros("1969-12-31T23:59:59.999500Z") == -500
        @test Taco._iso_micros("2024-02-29T00:00:00") == 1709164800000000
        wrong = Dict("spatial" => place, "temporal" => Any["yesterday", "today"])
        @test_throws "ISO 8601" Taco._extent_union([whole, wrong])

        collection = Dict("id" => "same")
        @test_throws "same collection" Taco._merge_collections(
            [collection, copy(collection)],
            [["sample", "children"], ["children", "sample"]],
            ["first.zip", "second.zip"],
        )

        # Partitions of one build differ in their split counts, which add up.
        counts(train, test) = Dict{String,Any}(
            "id" => "same",
            "split:counts" => Dict{String,Any}("train" => train, "validation" => 0, "test" => test, "excluded" => 0),
        )
        levels = [["sample"], ["sample"]]
        merged = Taco._merge_collections([counts(2, 0), counts(0, 1)], levels, ["train.zip", "test.zip"])
        @test merged["split:counts"] == Dict("train" => 2, "validation" => 0, "test" => 1, "excluded" => 0)
        other = merge(counts(0, 1), Dict{String,Any}("id" => "other"))
        @test_throws "same collection" Taco._merge_collections([counts(2, 0), other], levels, ["a.zip", "b.zip"])
        broken = merge(counts(0, 1), Dict{String,Any}("split:counts" => "many"))
        @test_throws "split:counts" Taco._merge_collections([counts(2, 0), broken], levels, ["a.zip", "b.zip"])
    end
end
