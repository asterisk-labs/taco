describe("read input validation", {
  it("requires one or more paths", {
    expect_error(taco::read(character()), "one or more")
    expect_error(taco::read(""), "one or more")
    expect_error(taco::read(c("a.zip", "a.zip")), "unique")
  })

  it("checks layout and selections", {
    expect_error(taco::read("a.zip", layout = "tall"), "should be one of")
    expect_error(taco::read("a.zip", level = c("sample", "children")), "single level")
    expect_error(taco::read("a.zip", files = ""), "non-empty")
    expect_error(taco::read("a.zip", gdal_vsi = "yes"), "TRUE or FALSE")
  })

  it("checks sample indices", {
    expect_error(taco::read("a.zip", idx = 1.5), "whole, non-negative")
    expect_error(taco::read("a.zip", idx = -1), "whole, non-negative")
    expect_error(taco::read("a.zip", idx = Inf), "whole, non-negative")
    expect_error(taco::read("a.zip", idx = 2^53), "too large")
    expect_error(taco::read("a.zip", idx = c(1, 2, 3)), "one sample number")
    expect_error(taco::read("a.zip", idx = c(5, 2)), "must not exceed")
  })
})


describe("read a TACO dataset", {
  it("returns one row per sample", {
    result <- taco::read(taco_fixture())
    expect_s3_class(result, "tbl_df")
    expect_identical(nrow(result), 3L)
    expect_true(all(c("sample_id", "ml:split", "image.bin", "mask.bin") %in% names(result)))
    expect_identical(result[["ml:split"]], c("train", "train", "test"))
  })

  it("returns one row per file", {
    result <- taco::read(taco_fixture(), layout = "long")
    expect_identical(nrow(result), 6L)
    expect_identical(sort(unique(result[["path"]])), c("image.bin", "mask.bin"))
    expect_identical(sort(unique(result[["file:role"]])), c("image", "mask"))
  })

  it("selects samples", {
    expect_identical(nrow(taco::read(taco_fixture(), idx = 1)), 1L)
    expect_identical(nrow(taco::read(taco_fixture(), idx = c(0, 2))), 2L)
  })

  it("selects files", {
    wide <- taco::read(taco_fixture(), files = "mask.bin")
    expect_true("mask.bin" %in% names(wide))
    expect_false("image.bin" %in% names(wide))

    # Restore these checks when cozip 2.0.1 reaches the community repository.
    # long <- taco::read(taco_fixture(), layout = "long", files = "mask.bin")
    # expect_identical(nrow(long), 3L)
    # expect_identical(unique(long[["path"]]), "mask.bin")
    # expect_error(
    #   taco::read(taco_fixture(), files = c("mask.bin", "nope.bin")),
    #   "structure leaf"
    # )
  })

  it("reads one metadata level", {
    result <- taco::read(taco_fixture(), level = "children")
    expect_identical(nrow(result), 6L)
    expect_true("internal:current_id" %in% names(result))
  })

  it("can omit GDAL paths", {
    result <- taco::read(taco_fixture(), layout = "long", gdal_vsi = FALSE)
    expect_true(all(is.na(result[["cozip:gdal_vsi"]])))
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

    expect_error(taco::read(c(taco_fixture(), folder)), "same contract")
  })
})


describe("contract accessors", {
  it("lists metadata levels and structure leaves", {
    expect_identical(taco::metadata_levels(taco_fixture()), c("sample", "children"))
    expect_identical(taco::sample_structure(taco_fixture()), c("image.bin", "mask.bin"))
  })

  it("returns COLLECTION.json unparsed", {
    document <- taco::collection(taco_fixture())
    expect_type(document, "character")
    expect_match(document, '"id": ?"taco-fixture"')
  })

  it("describes the contract", {
    result <- taco::contract(taco_fixture())
    expect_true(all(c("kind", "value") %in% names(result)))
    expect_true("structure" %in% result[["kind"]])
  })

  it("reports the archive profile", {
    expect_identical(taco::profile(taco_fixture()), "taco")
  })

  it("returns an empty string when there is no derived metadata", {
    expect_identical(taco::derived(taco_fixture()), "")
  })

  it("requires one source", {
    expect_error(
      taco::collection(c(taco_fixture(), "part-1.zip")),
      "single source"
    )
  })
})
