# Smoke + roundtrip tests for the cozip R binding.

# ---- helpers ----------------------------------------------------------

# Deterministic byte payload — same seed always yields same bytes.
make_payload <- function(seed, size) {
  as.raw((seed + seq_len(size) - 1L) %% 256L)
}

# Little-endian readers; offsets are 0-based to match the spec.
read_u16_le <- function(raw_data, offset) {
  bitwOr(
    as.integer(raw_data[offset + 1L]),
    bitwShiftL(as.integer(raw_data[offset + 2L]), 8L)
  )
}

read_u32_le <- function(raw_data, offset) {
  as.numeric(raw_data[offset + 1L]) +
    as.numeric(raw_data[offset + 2L]) * 0x100 +
    as.numeric(raw_data[offset + 3L]) * 0x10000 +
    as.numeric(raw_data[offset + 4L]) * 0x1000000
}


# ---- bindings ---------------------------------------------------------

# Removed: explicit getDLLRegisteredRoutines() check. If the C symbols
# weren't registered, every other test below would fail when calling
# C_R_cozip_plan / C_R_cozip_finalize — so the check is implicit.


# ---- roundtrip --------------------------------------------------------

test_that("end-to-end roundtrip: every layer matches the spec", {
  # Validates:
  #   1. archive exists and clears the spec minimum size (>= 32819)
  #   2. base R unzip sees __cozip__ first and __metadata__ present
  #   3. LFH layout for __cozip__ (filename len, extra len, 0xCA0C)
  #   4. integrity hash (bytes 43..50) is non-zero
  #   5. CZIP magic at byte 51
  #   6. cozip index lists 4 entries (3 users + __metadata__)
  #   7. user payloads round-trip byte-for-byte through unzip
  #   8. __metadata__.parquet has the expected schema and offsets
  tmp <- withr::local_tempdir()

  sizes <- c(12000L, 13000L, 14000L)
  n <- length(sizes)
  src_files    <- character(n)
  src_payloads <- vector("list", n)
  for (i in seq_len(n)) {
    p <- file.path(tmp, sprintf("src_%d.bin", i - 1L))
    payload <- make_payload(0xA0L + i - 1L, sizes[i])
    writeBin(payload, p)
    src_files[i]    <- p
    src_payloads[[i]] <- payload
  }

  arc_names <- sprintf("data/file_%d.bin", seq_len(n) - 1L)
  entries <- data.frame(
    name = arc_names,
    path = src_files,
    stringsAsFactors = FALSE
  )

  out <- file.path(tmp, "out.cozip")
  returned <- cozip::create(out, entries)

  # 1. exists + size >= minimum
  expect_true(file.exists(returned))
  expect_true(file.exists(out))
  archive_size <- file.size(out)
  expect_gte(archive_size, 32819)

  # 2. valid ZIP, first entry __cozip__, __metadata__ present
  zip_list <- utils::unzip(out, list = TRUE)
  expect_equal(zip_list$Name[1], "__cozip__")
  expect_true("__metadata__" %in% zip_list$Name)
  for (arc in arc_names) {
    expect_true(arc %in% zip_list$Name, info = paste("missing:", arc))
  }

  # 3. inspect raw bytes for the __cozip__ LFH
  raw <- readBin(out, "raw", n = archive_size)
  expect_identical(raw[1:4], as.raw(c(0x50, 0x4B, 0x03, 0x04)))

  fname_len <- read_u16_le(raw, 26)
  extra_len <- read_u16_le(raw, 28)
  expect_equal(fname_len, 9L)
  expect_equal(extra_len, 12L)
  expect_equal(rawToChar(raw[31:39]), "__cozip__")

  extra_id <- read_u16_le(raw, 39)
  expect_equal(extra_id, 0xCA0CL)
  expect_equal(read_u16_le(raw, 41), 8L)

  # 4. hash patched (non-zero)
  expect_true(any(raw[44:51] != as.raw(0)))

  # 5. CZIP magic at byte 51
  expect_equal(rawToChar(raw[52:55]), "CZIP")

  # 6. index header: version, profile, n_entries
  version <- read_u16_le(raw, 55)
  profile <- as.integer(raw[58])
  n_index <- read_u32_le(raw, 58)
  expect_equal(version, 1L)
  expect_equal(profile, 1L)
  expect_equal(n_index, 4)  # 3 users + __metadata__

  # 7. user payloads round-trip via unzip
  extracted <- file.path(tmp, "extracted")
  dir.create(extracted)
  utils::unzip(out, exdir = extracted)
  for (i in seq_len(n)) {
    extracted_path  <- file.path(extracted, arc_names[i])
    extracted_bytes <- readBin(extracted_path, "raw",
                               n = file.size(extracted_path))
    expect_identical(extracted_bytes, src_payloads[[i]])
  }

  # 8. __metadata__.parquet schema and content
  meta <- arrow::read_parquet(file.path(extracted, "__metadata__"))

  expect_equal(colnames(meta)[1:3], c("name", "offset", "size"))
  expect_false("path" %in% colnames(meta))
  expect_false("in_index" %in% colnames(meta))
  expect_equal(nrow(meta), 3L)

  # offsets/sizes in the parquet must point at the actual user payloads
  for (i in seq_len(n)) {
    row_idx <- which(meta$name == arc_names[i])
    off <- as.numeric(meta$offset[row_idx])
    sz  <- as.numeric(meta$size[row_idx])
    expect_identical(raw[(off + 1):(off + sz)], src_payloads[[i]])
  }
})


test_that("entries with in_index=FALSE are written but not indexed", {
  # The cozip index still gets the __metadata__ entry, so n_index is
  # 1 + 1 = 2 when only one user is in_index=TRUE.
  tmp <- withr::local_tempdir()

  src <- file.path(tmp, "small.bin")
  big <- file.path(tmp, "big.bin")
  writeBin(make_payload(0x10L, 100L),    src)
  writeBin(make_payload(0x20L, 33000L),  big)  # alone clears 32 KiB minimum

  entries <- data.frame(
    name     = c("a.bin", "b.bin"),
    path     = c(src, big),
    in_index = c(TRUE, FALSE),
    stringsAsFactors = FALSE
  )

  out <- file.path(tmp, "out.cozip")
  cozip::create(out, entries)

  raw <- readBin(out, "raw", n = file.size(out))
  expect_equal(read_u32_le(raw, 58), 2)  # a.bin + __metadata__

  zip_list <- utils::unzip(out, list = TRUE)
  expect_true("a.bin"        %in% zip_list$Name)
  expect_true("b.bin"        %in% zip_list$Name)
  expect_true("__metadata__" %in% zip_list$Name)

  # The metadata parquet must NOT list b.bin (in_index=FALSE).
  extracted <- file.path(tmp, "extracted")
  dir.create(extracted)
  utils::unzip(out, exdir = extracted)
  meta <- arrow::read_parquet(file.path(extracted, "__metadata__"))
  expect_equal(meta$name, "a.bin")
})

# ---- negatives --------------------------------------------------------

test_that("create() rejects empty entries", {
  tmp <- withr::local_tempdir()
  out <- file.path(tmp, "empty.cozip")
  expect_error(
    cozip::create(out, data.frame(name = character(), path = character())),
    "empty"
  )
})

test_that("create() rejects a missing source file", {
  tmp <- withr::local_tempdir()
  out <- file.path(tmp, "out.cozip")
  entries <- data.frame(
    name = "does/not/exist.bin",
    path = file.path(tmp, "nope.bin"),
    stringsAsFactors = FALSE
  )
  expect_error(cozip::create(out, entries), "not found")
})

test_that("create() rejects reserved names", {
  tmp <- withr::local_tempdir()
  src <- file.path(tmp, "src.bin")
  writeBin(make_payload(0x33L, 33000L), src)

  entries <- data.frame(
    name = "__metadata__",
    path = src,
    stringsAsFactors = FALSE
  )
  expect_error(
    cozip::create(file.path(tmp, "out.cozip"), entries),
    "reserved"
  )
})

test_that("create() rejects duplicate names", {
  tmp <- withr::local_tempdir()
  src1 <- file.path(tmp, "a.bin")
  src2 <- file.path(tmp, "b.bin")
  writeBin(make_payload(0x10L, 100L),   src1)
  writeBin(make_payload(0x20L, 33000L), src2)

  entries <- data.frame(
    name = c("dupe.bin", "dupe.bin"),
    path = c(src1, src2),
    stringsAsFactors = FALSE
  )
  expect_error(
    cozip::create(file.path(tmp, "out.cozip"), entries),
    "duplicate"
  )
})