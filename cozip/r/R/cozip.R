# Profile selectors mirror the cozip_profile_t enum in cozip.h.
.PROFILE_NONE <- 0L
.PROFILE_FLAT <- 1L
.PROFILE_TACO <- 2L

# Names the writer reserves for itself; users cannot use them.
.RESERVED_NAMES <- c("__cozip__", "__metadata__")


#' Create a Cloud-Optimized ZIP archive (FLAT profile)
#'
#' Walks the libcozip pipeline end-to-end: plans offsets, drops a
#' `__metadata__.parquet` describing the indexed user files, serializes
#' the cozip index, writes the archive via libzip, and patches the
#' FNV-1a 64 integrity hash. The output file is overwritten if it
#' exists. The temporary metadata parquet is always cleaned up.
#'
#' The metadata parquet contains one row per indexed entry with
#' columns `name` (string), `offset` (uint64), `size` (uint64), plus
#' every additional column from `entries` other than `path` and
#' `in_index`. Rows with `in_index = FALSE` are excluded.
#'
#' @param out_path Destination file path. Resolved to an absolute path.
#'   Existing files are overwritten.
#' @param entries A data frame describing the entries. Must contain:
#'
#'   * `name` (character): archive-internal name in UTF-8.
#'   * `path` (character): filesystem path of the source file.
#'
#'   Optionally:
#'
#'   * `in_index` (logical, default `TRUE`): whether to list this entry
#'     in the cozip index and metadata parquet.
#'
#'   Any other columns flow through to the metadata parquet as extra
#'   metadata.
#' @param create_options Named list of arguments forwarded to
#'   [arrow::write_parquet()] (e.g. `list(compression = "zstd")`).
#' @param temp_dir Directory for the temporary metadata parquet.
#'   Created if missing. Defaults to [tempdir()].
#'
#' @return Absolute path of the created archive, as a character scalar.
#'
#' @examples
#' \dontrun{
#'   tmp <- tempfile(fileext = ".bin")
#'   writeBin(as.raw(0:255), tmp)
#'
#'   entries <- data.frame(
#'     name = "data.bin",
#'     path = tmp,
#'     description = "example payload",
#'     stringsAsFactors = FALSE
#'   )
#'
#'   archive <- cozip::create(file.path(tempdir(), "demo.cozip"), entries)
#' }
#'
#' @export
create <- function(out_path, entries,
                   create_options = NULL,
                   temp_dir = NULL) {

  # ---- 1. Validate inputs ----

  if (!is.character(out_path) || length(out_path) != 1L || is.na(out_path)) {
    stop("`out_path` must be a single non-NA string")
  }
  if (!is.data.frame(entries)) {
    stop("`entries` must be a data frame")
  }

  cols <- colnames(entries)
  missing <- setdiff(c("name", "path"), cols)
  if (length(missing) > 0L) {
    stop(sprintf("`entries` is missing required column(s): %s",
                 paste(shQuote(missing), collapse = ", ")))
  }

  n_users <- nrow(entries)
  if (n_users == 0L) {
    stop("`entries` is empty")
  }

  user_names  <- entries$name
  user_paths  <- entries$path
  user_in_idx <- if ("in_index" %in% cols) entries$in_index
                 else                      rep(TRUE, n_users)

  if (!is.character(user_names) || any(is.na(user_names))) {
    stop("`entries$name` must be character with no NAs")
  }
  if (!is.character(user_paths) || any(is.na(user_paths))) {
    stop("`entries$path` must be character with no NAs")
  }
  if (!is.logical(user_in_idx) || any(is.na(user_in_idx))
      || length(user_in_idx) != n_users) {
    stop("`entries$in_index` must be logical with no NAs")
  }

  # Reserved names (format-level rule; libcozip only protects memory)
  hits <- which(user_names %in% .RESERVED_NAMES)
  if (length(hits) > 0L) {
    stop(sprintf("row %d uses reserved name %s",
                 hits[1L], shQuote(user_names[hits[1L]])))
  }

  # Duplicates
  dups <- which(duplicated(user_names))
  if (length(dups) > 0L) {
    stop(sprintf("duplicate name %s at row %d",
                 shQuote(user_names[dups[1L]]), dups[1L]))
  }

  # File existence + sizes
  fi <- file.info(user_paths)
  bad <- which(is.na(fi$size))
  if (length(bad) > 0L) {
    i <- bad[1L]
    stop(sprintf("row %d (%s): source file not found: %s",
                 i, shQuote(user_names[i]), user_paths[i]))
  }
  user_sizes <- as.numeric(fi$size)

  out_path <- normalizePath(out_path, mustWork = FALSE)

  # ---- 2. Assemble entry vectors with __metadata__ placeholder ----

  meta_idx   <- n_users + 1L
  all_names  <- c(user_names,  "__metadata__")
  all_paths  <- c(user_paths,  NA_character_)  # patched after parquet write
  all_sizes  <- c(user_sizes,  0)              # patched after parquet write
  all_in_idx <- c(user_in_idx, TRUE)           # __metadata__ always in index

  # ---- 3. First plan: learn user payload offsets ----

  plan_res <- .Call(C_R_cozip_plan,
                    all_names, all_sizes, all_in_idx)

  # ---- 4. Build __metadata__.parquet ----

  parquet_path <- NULL
  on.exit(if (!is.null(parquet_path)) unlink(parquet_path, force = TRUE),
          add = TRUE)

  parquet_path <- .build_metadata_parquet(
    entries        = entries,
    user_names     = user_names,
    user_in_idx    = user_in_idx,
    offsets        = plan_res$offset[seq_len(n_users)],
    sizes          = plan_res$size[seq_len(n_users)],
    create_options = create_options,
    temp_dir       = temp_dir
  )

  # ---- 5. Patch metadata size + path, finalize ----

  all_sizes[meta_idx] <- as.numeric(file.size(parquet_path))
  all_paths[meta_idx] <- parquet_path

  .Call(C_R_cozip_finalize,
        out_path, all_names, all_paths, all_sizes, all_in_idx,
        .PROFILE_FLAT)

  out_path
}


# ---- Internal: build the __metadata__ parquet ---------------------------

.build_metadata_parquet <- function(entries, user_names, user_in_idx,
                                    offsets, sizes,
                                    create_options, temp_dir) {

  idx_rows <- which(user_in_idx)

  # Drop writer-private columns; preserve user column order.
  drop <- c("name", "path", "in_index")
  extra_cols <- setdiff(colnames(entries), drop)

  # Canonical order: name, offset, size, then every extra column in
  # input order. offset/size carried as uint64 to match the Python
  # writer.
  arrays <- list(
    name   = arrow::Array$create(user_names[idx_rows]),
    offset = arrow::Array$create(offsets[idx_rows], type = arrow::uint64()),
    size   = arrow::Array$create(sizes[idx_rows],   type = arrow::uint64())
  )
  for (col in extra_cols) {
    arrays[[col]] <- arrow::Array$create(entries[[col]][idx_rows])
  }

  meta_table <- do.call(arrow::arrow_table, arrays)

  if (is.null(temp_dir)) {
    temp_dir <- tempdir()
  } else if (!dir.exists(temp_dir)) {
    dir.create(temp_dir, recursive = TRUE)
  }

  parquet_path <- tempfile(tmpdir = temp_dir, fileext = ".parquet")

  args <- c(list(x = meta_table, sink = parquet_path),
            if (is.null(create_options)) list() else create_options)
  tryCatch(
    do.call(arrow::write_parquet, args),
    error = function(e) {
      unlink(parquet_path, force = TRUE)
      stop(e)
    }
  )

  parquet_path
}