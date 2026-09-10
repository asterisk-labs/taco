.check_idx <- function(idx) {
  if (is.null(idx)) {
    return(invisible(NULL))
  }
  if (!is.numeric(idx) || anyNA(idx) || !length(idx) %in% c(1L, 2L)) {
    .taco_stop("`idx` must be one sample number or a two-element range")
  }
  if (any(!is.finite(idx)) || any(idx != trunc(idx)) || any(idx < 0)) {
    .taco_stop("`idx` must contain whole, non-negative numbers")
  }
  if (any(idx > 2^53 - 1)) {
    .taco_stop("`idx` is too large to represent exactly in R")
  }
  if (length(idx) == 2L && idx[1L] > idx[2L]) {
    .taco_stop("`idx` range start must not exceed its end")
  }
  invisible(NULL)
}


.idx_text <- function(idx) {
  if (is.null(idx)) {
    return(NA_character_)
  }
  values <- format(idx, scientific = FALSE, trim = TRUE, digits = 22)
  if (length(values) == 1L) values else sprintf("[%s, %s]", values[1L], values[2L])
}


.check_names <- function(values, argument) {
  if (is.null(values)) {
    return(invisible(NULL))
  }
  if (!is.character(values) || anyNA(values) || !length(values)) {
    .taco_stop("`%s` must be a character vector with no NAs (or NULL)", argument)
  }
  if (any(!nzchar(values))) {
    .taco_stop("`%s` entries must be non-empty", argument)
  }
  invisible(NULL)
}


.LOCATION_COLUMN <- "taco:location"
.LEGACY_LOCATION_COLUMN <- "cozip:gdal_vsi"


.read_call <- function(location_argument) {
  paste0(
    "read_taco(?, idx := ?::VARCHAR, level := ?::VARCHAR, ",
    "pivoted := ?::BOOLEAN, files := ?::VARCHAR[], ", location_argument,
    " := ?::BOOLEAN)"
  )
}


.read_options <- function(idx, level, layout, files, location) {
  list(
    .idx_text(idx),
    if (is.null(level)) NA_character_ else level,
    layout == "wide",
    if (is.null(files)) NA_character_ else list(files),
    location
  )
}


.legacy_taco_signature <- function(message) {
  grepl("read_taco", message, fixed = TRUE) &&
    grepl("gdal_vsi", message, fixed = TRUE) &&
    grepl("does not support the supplied arguments", message, fixed = TRUE)
}


.normalize_locations <- function(result, location, level, layout, legacy) {
  preserve_current <- !legacy && location && is.null(level) && layout == "long"
  drop <- intersect("cozip:location", names(result))
  if (!preserve_current && .LOCATION_COLUMN %in% names(result)) {
    drop <- c(drop, .LOCATION_COLUMN)
  }
  preserve_legacy <- legacy && location && is.null(level) && layout == "long"
  if (!preserve_legacy && .LEGACY_LOCATION_COLUMN %in% names(result)) {
    drop <- c(drop, .LEGACY_LOCATION_COLUMN)
  }
  if (length(drop)) result <- result[setdiff(names(result), drop)]
  if (preserve_legacy && .LEGACY_LOCATION_COLUMN %in% names(result)) {
    names(result)[names(result) == .LEGACY_LOCATION_COLUMN] <- .LOCATION_COLUMN
  }
  tibble::as_tibble(result)
}


.source_labels <- function(sources) {
  labels <- basename(sub("/+$", "", sources))
  if (all(nzchar(labels)) && !anyDuplicated(labels)) labels else sources
}


#' Read a TACO dataset
#'
#' `source` is one or more `.zip` archives, a FOLDER directory or a
#' `.tacocat` catalog, local or remote. Multiple sources must share a contract.
#'
#' @param source A dataset, or local paths or http(s)/s3/gcs/azure/hf URLs.
#' @param layout `"wide"` gives one row per sample with a column per
#'   structure leaf; `"long"` gives one row per file.
#' @param idx One sample number, or a two-element half-open range.
#'   `NULL` reads every sample.
#' @param level Return one contract level raw, with its internal columns,
#'   instead of the joined view.
#' @param files Restrict which structure leaves are read. `NULL` reads all.
#' @param location Fill structure columns in a wide read, or include the
#'   calculated `taco:location` column in a long read. Raw level reads never
#'   synthesize a location column.
#'
#' @return A tibble.
#' @export
read <- function(source, layout = c("wide", "long"), idx = NULL,
                 level = NULL, files = NULL, location = TRUE) {
  UseMethod("read")
}


#' @rdname read
#' @export
read.character <- function(source, layout = c("wide", "long"), idx = NULL,
                           level = NULL, files = NULL, location = TRUE) {
  if (length(source) > 1L) {
    source <- open_dataset(source)[["sources"]]
  }
  .read_sources(source, layout, idx, level, files, location)
}


#' @rdname read
#' @export
read.taco_dataset <- function(source, layout = c("wide", "long"), idx = NULL,
                              level = NULL, files = NULL, location = TRUE) {
  .read_sources(source[["sources"]], layout, idx, level, files, location)
}


.read_sources <- function(source, layout, idx, level, files, location) {
  .check_source(source)
  layout <- match.arg(layout, c("wide", "long"))
  .check_idx(idx)
  .check_names(level, "level")
  if (!is.null(level) && length(level) != 1L) {
    .taco_stop("`level` must be a single level name")
  }
  .check_names(files, "files")
  if (!is.logical(location) || length(location) != 1L || is.na(location)) {
    .taco_stop("`location` must be TRUE or FALSE")
  }

  options <- .read_options(idx, level, layout, files, location)
  con <- .open_reader()
  build_query <- function(location_argument) {
    call <- .read_call(location_argument)
    if (length(source) == 1L) {
      return(paste("SELECT * FROM", call))
    }
    projection <- if (is.null(level)) {
      "taco.sample_id, ?::VARCHAR AS source_file, taco.* EXCLUDE (sample_id)"
    } else {
      "?::VARCHAR AS source_file, taco.*"
    }
    branch <- paste("SELECT", projection, "FROM", call, "AS taco")
    paste(rep(branch, length(source)), collapse = " UNION ALL BY NAME ")
  }
  if (length(source) == 1L) {
    params <- c(list(source), options)
  } else {
    params <- unname(
      unlist(
        Map(
          function(label, path) c(list(label, path), options),
          .source_labels(source),
          source
        ),
        recursive = FALSE
      )
    )
  }
  legacy <- FALSE
  result <- tryCatch(
    DBI::dbGetQuery(con, build_query("location"), params = params),
    error = function(err) {
      if (!.legacy_taco_signature(conditionMessage(err))) stop(err)
      legacy <<- TRUE
      DBI::dbGetQuery(con, build_query("gdal_vsi"), params = params)
    }
  )
  .normalize_locations(result, location, level, layout, legacy)
}
