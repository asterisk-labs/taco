describe("read input validation", {
  it("requires one or more paths", {
    expect_error(taco::read(character()), "one or more")
    expect_error(taco::read(""), "one or more")
    expect_error(taco::read(c("a.zip", "a.zip")), "unique")
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
    expect_true(all(c("sample_index", "ml:split", "image.bin::location", "mask.bin::location") %in% names(result)))
    expect_identical(result[["ml:split"]], c("train", "train", "test"))
  })

  it("reads an open dataset", {
    dataset <- taco::open_dataset(taco_fixture())
    expect_s3_class(dataset, "taco_dataset")
    expect_identical(dataset$sources, taco_fixture())
    expect_identical(dataset$collection$id, "taco-fixture")
    expect_identical(dataset$contract$structure, c("image.bin", "mask.bin"))
    expect_identical(dataset$contract$levels, c("sample", "children"))
    expect_identical(dataset$contract$derived, list())
    expect_identical(nrow(taco::read(dataset)), 3L)
    expect_match(capture.output(print(dataset)), "taco.Dataset")
    selected <- taco::sql(dataset, "SELECT id FROM data WHERE sample_index = 1")
    expect_identical(selected$id, "sample-1")
    expect_identical(nrow(taco::sql(dataset, "SELECT * FROM files WHERE path = 'mask.bin'")), 3L)
  })

  it("queries one row per file", {
    dataset <- taco::open_dataset(taco_fixture())
    result <- taco::sql(dataset, "SELECT * FROM files")
    expect_identical(nrow(result), 6L)
    expect_identical(sort(unique(result[["path"]])), c("image.bin", "mask.bin"))
    expect_identical(sort(unique(result[["file:role"]])), c("image", "mask"))
    expect_true("taco:location" %in% names(result))
    expect_false(any(c("cozip:location", "cozip:gdal_vsi") %in% names(result)))
  })

  it("selects samples with SQL", {
    dataset <- taco::open_dataset(taco_fixture())
    expect_identical(nrow(taco::sql(dataset, "SELECT * FROM data WHERE sample_index = 1")), 1L)
    expect_identical(nrow(taco::sql(dataset, "SELECT * FROM data WHERE sample_index < 2")), 2L)
  })

  it("selects files", {
    wide <- taco::read(taco_fixture(), files = "mask.bin")
    expect_true("mask.bin::location" %in% names(wide))
    expect_false("image.bin::location" %in% names(wide))

    dataset <- taco::open_dataset(taco_fixture())
    long <- taco::sql(dataset, "SELECT * FROM files WHERE path = 'mask.bin'")
    expect_identical(nrow(long), 3L)
    expect_identical(unique(long[["path"]]), "mask.bin")
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

  it("combines compatible sources", {
    copy <- file.path(tempdir(), "part-1.zip")
    expect_true(file.copy(taco_fixture(), copy, overwrite = TRUE))
    on.exit(unlink(copy), add = TRUE)

    result <- taco::read(c(taco_fixture(), copy))
    expect_identical(nrow(result), 6L)
    expect_identical(sort(unique(result[["source_file"]])), c("part-1.zip", "taco.zip"))
    expect_equal(as.numeric(result[["sample_index"]]), 0:5)
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
  it("combines partition extents", {
    make_partition <- function(path, spatial, temporal) {
      dir.create(path)
      utils::unzip(taco_fixture(), exdir = path)
      collection_path <- file.path(path, "COLLECTION.json")
      collection <- jsonlite::read_json(collection_path, simplifyVector = FALSE)
      collection$extent <- list(spatial = as.list(spatial), temporal = as.list(temporal))
      jsonlite::write_json(collection, collection_path, auto_unbox = TRUE)
    }

    first <- tempfile("taco-part-")
    second <- tempfile("taco-part-")
    on.exit(unlink(c(first, second), recursive = TRUE), add = TRUE)
    make_partition(first, c(-76, -12, -75, -11), c("2024-01-02T00:00:00Z", "2024-01-03T00:00:00Z"))
    make_partition(second, c(-74, -10, -73, -9), c("2024-01-01T00:00:00Z", "2024-01-04T00:00:00Z"))

    dataset <- taco::open_dataset(c(first, second))
    expect_equal(unlist(dataset$collection$extent$spatial), c(-76, -12, -73, -9))
    expect_identical(
      unlist(dataset$collection$extent$temporal),
      c("2024-01-01T00:00:00Z", "2024-01-04T00:00:00Z")
    )
  })
})
