describe("read input validation", {
  it("requires one or more paths", {
    expect_error(taco::read(character()), "one or more")
    expect_error(taco::read(""), "one or more")
    expect_error(taco::read(c("a.zip", "a.zip")), "unique")
  })

  it("treats two spellings of one file as one source", {
    expect_error(taco::read(c(taco_fixture(), file.path(".", taco_fixture()))), "unique")
    expect_error(taco::read(c(taco_fixture(), paste0("file://", normalizePath(taco_fixture())))), "unique")
    upper <- file.path(dirname(taco_fixture()), "TACO.ZIP")
    if (file.exists(upper)) {
      expect_error(taco::read(c(taco_fixture(), upper)), "unique")
    }
  })

  it("checks file selections", {
    expect_error(taco::read("a.zip", files = ""), "non-empty")
  })
})


describe("read a TACO dataset", {
  it("returns one row per sample", {
    result <- taco::read(taco_fixture())
    expect_s3_class(result, "tbl_df")
    expect_identical(nrow(result), 3L)
    expect_true(all(c("taco:sample_index", "split:split", "image.bin::location", "mask.bin::location") %in% names(result)))
    expect_identical(result[["split:split"]], c("train", "train", "test"))
    expect_identical(as.numeric(result[["taco:sample_index"]]), c(0, 1, 2))
  })

  it("reads an open dataset", {
    dataset <- taco::open_dataset(taco_fixture())
    expect_s3_class(dataset, "taco_dataset")
    expect_identical(dataset$sources, taco_fixture())
    expect_identical(dataset$collection$id, "taco-fixture")
    expect_identical(dataset$contract$structure, c("image.bin", "mask.bin"))
    expect_identical(dataset$contract$levels, c("sample", "children"))
    expect_null(dataset$contract$derived)
    expect_identical(nrow(taco::read(dataset)), 3L)
    expect_match(capture.output(print(dataset)), "taco.Dataset")
    selected <- taco::sql(dataset, 'SELECT id FROM dataset WHERE "taco:sample_index" = 1')
    expect_identical(selected$id, "sample-1")
    expect_identical(nrow(taco::sql(dataset, "SELECT * FROM dataset")), 3L)
  })

  it("queries the complete sample table", {
    dataset <- taco::open_dataset(taco_fixture())
    result <- taco::sql(dataset, "SELECT * FROM dataset")
    expect_identical(nrow(result), 3L)
    expect_true(all(c("image.bin::location", "mask.bin::location") %in% names(result)))
    expect_false(any(c("cozip:location", "cozip:gdal_vsi") %in% names(result)))
    expect_error(taco::sql(dataset, "SELECT * FROM data"), "data.*does not exist")
    expect_error(taco::sql(dataset, "SELECT * FROM files"), "files.*does not exist")
  })

  it("selects samples with SQL", {
    dataset <- taco::open_dataset(taco_fixture())
    expect_identical(nrow(taco::sql(dataset, 'SELECT * FROM dataset WHERE "taco:sample_index" = 1')), 1L)
    expect_identical(nrow(taco::sql(dataset, 'SELECT * FROM dataset WHERE "taco:sample_index" < 2')), 2L)
  })

  it("selects files", {
    wide <- taco::read(taco_fixture(), files = "mask.bin")
    expect_true("mask.bin::location" %in% names(wide))
    expect_false("image.bin::location" %in% names(wide))

    expect_error(
      taco::read(taco_fixture(), files = c("mask.bin", "nope.bin")),
      "structure leaf"
    )
  })

  it("queries one metadata level", {
    dataset <- taco::open_dataset(taco_fixture())
    result <- taco::sql(dataset, "SELECT * FROM children")
    expect_identical(nrow(result), 6L)
    expect_true("internal:current_id" %in% names(result))
    expect_false(any(c("cozip:location", "taco:location", "cozip:gdal_vsi") %in% names(result)))
  })

  it("names nested files and levels after their paths", {
    dataset <- taco::open_dataset(taco_nested_fixture())
    wide <- taco::read(dataset)
    expect_true(all(c("before/B02.bin::location", "after/B02.bin::location") %in% names(wide)))
    expect_false(any(grepl("__", names(wide), fixed = TRUE)))
    expect_identical(nrow(taco::sql(dataset, 'SELECT * FROM "children/before"')), 6L)
  })

  it("inspects a dataset", {
    expect_identical(taco::inspect(taco_fixture(), "structure"), c("image.bin", "mask.bin"))
    expect_identical(taco::inspect(taco_fixture(), "levels"), c("sample", "children"))
    expect_identical(taco::inspect(taco_fixture(), "collection")$id, "taco-fixture")
    expect_identical(taco::inspect(taco_fixture(), "profile"), "taco")
    expect_s3_class(taco::inspect(taco_fixture(), "contract"), "tbl_df")
    expect_match(taco::inspect(taco_fixture(), "native_sql"), "read_parquet")
    expect_error(taco::inspect(taco_fixture(), "unknown"), "must be one of")
  })

  it("reads Unicode paths", {
    copy <- file.path(tempdir(), "niño.zip")
    expect_true(file.copy(taco_fixture(), copy, overwrite = TRUE))
    on.exit(unlink(copy), add = TRUE)
    expect_identical(nrow(taco::read(copy)), 3L)
  })

  it("expands a leading ~", {
    skip_on_os("windows")
    home <- tempfile("taco-home-")
    dir.create(home)
    on.exit(unlink(home, recursive = TRUE), add = TRUE)
    expect_true(file.copy(taco_fixture(), file.path(home, "taco.zip")))
    old <- Sys.getenv("HOME")
    Sys.setenv(HOME = home)
    on.exit(Sys.setenv(HOME = old), add = TRUE)

    expect_identical(nrow(taco::read("~/taco.zip")), 3L)
    expect_identical(taco::open_dataset("~/taco.zip")$sources, file.path(home, "taco.zip"))
    expect_identical(taco::inspect("~/taco.zip", "levels"), c("sample", "children"))
  })

  it("combines compatible sources", {
    copy <- file.path(tempdir(), "part-1.zip")
    expect_true(file.copy(taco_fixture(), copy, overwrite = TRUE))
    on.exit(unlink(copy), add = TRUE)

    result <- taco::read(c(taco_fixture(), copy))
    expect_identical(nrow(result), 6L)
    expect_identical(sort(unique(result[["source_file"]])), c("part-1.zip", "taco.zip"))
    expect_equal(as.numeric(result[["taco:sample_index"]]), 0:5)
    expect_identical(
      taco::open_dataset(c(taco_fixture(), copy))$collection[["split:counts"]],
      list(train = 4L, validation = 0L, test = 2L, excluded = 0L)
    )
  })

  it("rejects sources with different contracts", {
    folder <- tempfile("taco-contract-")
    dir.create(folder)
    on.exit(unlink(folder, recursive = TRUE), add = TRUE)
    utils::unzip(taco_fixture(), exdir = folder)

    collection_path <- file.path(folder, "COLLECTION.json")
    document <- readChar(collection_path, file.info(collection_path)$size)
    document <- sub('"mask.bin"', '"other.bin"', document, fixed = TRUE)
    writeLines(document, collection_path, useBytes = TRUE)

    expect_error(taco::read(c(taco_fixture(), folder)), "same collection")
  })
})


describe("open a TACO dataset", {
  extent <- function(spatial, temporal) {
    list(extent = list(spatial = as.list(spatial), temporal = as.list(temporal)))
  }
  counts <- function(train, test) {
    list(`split:counts` = list(train = train, validation = 0L, test = test, excluded = 0L))
  }

  it("combines partition extents", {
    first <- tempfile("taco-part-")
    second <- tempfile("taco-part-")
    on.exit(unlink(c(first, second), recursive = TRUE), add = TRUE)
    taco_partition(first, extent(c(-76, -12, -75, -11), c("2024-01-02T00:00:00Z", "2024-01-03T00:00:00Z")))
    taco_partition(second, extent(c(-74, -10, -73, -9), c("2024-01-01T00:00:00Z", "2024-01-04T00:00:00Z")))

    dataset <- taco::open_dataset(c(first, second))
    expect_equal(unlist(dataset$collection$extent$spatial), c(-76, -12, -73, -9))
    expect_identical(
      unlist(dataset$collection$extent$temporal),
      c("2024-01-01T00:00:00Z", "2024-01-04T00:00:00Z")
    )
  })

  it("compares partition times as instants", {
    paths <- c(tempfile("taco-part-"), tempfile("taco-part-"), tempfile("taco-part-"))
    on.exit(unlink(paths, recursive = TRUE), add = TRUE)
    taco_partition(paths[[1L]], extent(c(1, 1, 2, 2), c("2024-05-01T00:00:00.500000Z", "2024-05-01T12:00:00.500000Z")))
    taco_partition(paths[[2L]], extent(c(1, 1, 2, 2), c("2024-05-01T00:00:00Z", "2024-05-01T12:00:00Z")))
    taco_partition(paths[[3L]], extent(c(1, 1, 2, 2), c("2024-05-01T01:00:00+02:00", "2024-05-01T11:00:00-02:00")))

    expect_identical(
      unlist(taco::open_dataset(paths[1:2])$collection$extent$temporal),
      c("2024-05-01T00:00:00Z", "2024-05-01T12:00:00.500000Z")
    )
    expect_identical(
      unlist(taco::open_dataset(paths[2:3])$collection$extent$temporal),
      c("2024-05-01T01:00:00+02:00", "2024-05-01T11:00:00-02:00")
    )
  })

  it("adds up the split counts of partitions", {
    paths <- c(tempfile("taco-part-"), tempfile("taco-part-"), tempfile("taco-part-"))
    on.exit(unlink(paths, recursive = TRUE), add = TRUE)
    taco_partition(paths[[1L]], counts(2L, 0L))
    taco_partition(paths[[2L]], counts(0L, 1L))
    taco_partition(paths[[3L]], list(`split:counts` = "many"))

    dataset <- taco::open_dataset(paths[1:2])
    expect_identical(dataset$collection[["split:counts"]], list(train = 2L, validation = 0L, test = 1L, excluded = 0L))
    expect_identical(nrow(taco::read(dataset)), 6L)
    expect_error(taco::open_dataset(paths[c(1L, 3L)]), "split:counts")
  })
})


describe("query a TACO dataset", {
  it("never installs an extension", {
    dataset <- taco::open_dataset(taco_fixture())
    setting <- taco::sql(dataset, "SELECT current_setting('autoinstall_known_extensions') AS autoinstall")
    expect_false(setting$autoinstall)
  })

  it("answers time zone queries in UTC with an installed ICU", {
    skip_if_not(icu_installed(), "ICU is not installed")
    # ICU takes the time zone of the process when it loads, so a reader that
    # does not pin UTC answers in Lima time here.
    zone <- Sys.getenv("TZ", unset = NA)
    Sys.setenv(TZ = "America/Lima")
    on.exit(if (is.na(zone)) Sys.unsetenv("TZ") else Sys.setenv(TZ = zone), add = TRUE)

    dataset <- taco::open_dataset(taco_fixture())
    result <- taco::sql(dataset, paste(
      "SELECT current_setting('TimeZone') AS zone,",
      "CAST(TIMESTAMPTZ '2024-03-01 03:30:00+00' AS DATE) AS day,",
      "extract(hour FROM TIMESTAMPTZ '2024-03-01 03:30:00+00') AS hour"
    ))
    expect_identical(result$zone, "UTC")
    expect_identical(result$day, as.Date("2024-03-01"))
    expect_equal(result$hour, 3)
  })
})


# Read a local file or ZIP range for the expected bytes.
location_bytes <- function(location) {
  parts <- regmatches(location, regexec("^/vsisubfile/([0-9]+)_([0-9]+),(.+)$", location))[[1L]]
  if (!length(parts)) {
    return(readBin(location, "raw", file.size(location)))
  }
  con <- file(parts[[4L]], "rb")
  on.exit(close(con))
  seek(con, as.numeric(parts[[2L]]))
  readBin(con, "raw", as.numeric(parts[[3L]]))
}


describe("read_bytes", {
  it("reads ZIP and FOLDER locations singly or together", {
    folder <- taco_partition(tempfile("folder"), list())
    for (source in c(taco_fixture(), folder)) {
      wide <- taco::read(source)
      locations <- c(wide[["image.bin::location"]], wide[["mask.bin::location"]])
      expected <- lapply(locations, location_bytes)
      expect_identical(taco::read_bytes(locations), expected)
      expect_identical(taco::read_bytes(locations[[1L]]), expected[[1L]])
    }
    expect_identical(taco::read_bytes(character()), list())
  })

  it("expands ~ and reports bad locations", {
    home <- tempfile()
    dir.create(home)
    writeBin(charToRaw("payload"), file.path(home, "payload.bin"))
    old_home <- Sys.getenv("HOME")
    Sys.setenv(HOME = home)
    on.exit(Sys.setenv(HOME = old_home), add = TRUE)
    expect_identical(taco::read_bytes("~/payload.bin"), charToRaw("payload"))
    expect_error(taco::read_bytes(NA_character_), "non-empty")
    expect_error(taco::read_bytes(1), "non-empty")
    expect_error(taco::read_bytes("/vsisubfile/x_4,a.zip"), "could not resolve")
    expect_error(taco::read_bytes(file.path(home, "missing.bin")), "could not")
  })
})
