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


.read_call <- paste0(
  "read_taco(?, idx := ?::VARCHAR, level := ?::VARCHAR, ",
  "pivoted := ?::BOOLEAN, files := ?::VARCHAR[], gdal_vsi := ?::BOOLEAN)"
)


.read_options <- function(idx, level, layout, files, gdal_vsi) {
  list(
    .idx_text(idx),
    if (is.null(level)) NA_character_ else level,
    layout == "wide",
    if (is.null(files)) NA_character_ else list(files),
    gdal_vsi
  )
}


.same_contract <- function(con, sources) {
  expected <- DBI::dbGetQuery(
    con,
    "SELECT * FROM taco_contract(?)",
    params = list(sources[[1L]])
  )
  for (source in sources[-1L]) {
    actual <- DBI::dbGetQuery(
      con,
      "SELECT * FROM taco_contract(?)",
      params = list(source)
    )
    if (!identical(actual, expected)) {
      .taco_stop("sources do not share the same contract: %s", source)
    }
  }
  invisible(NULL)
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
#' @param source Local paths or http(s)/s3/gcs/azure/hf URLs to the dataset.
#' @param layout `"wide"` gives one row per sample with a column per
#'   structure leaf; `"long"` gives one row per file.
#' @param idx One sample number, or a two-element half-open range.
#'   `NULL` reads every sample.
#' @param level Return one contract level raw, with its internal columns,
#'   instead of the joined view.
#' @param files Restrict which structure leaves are read. `NULL` reads all.
#' @param gdal_vsi Fill the `cozip:gdal_vsi` column with GDAL paths.
#'
#' @return A tibble.
#' @export
read <- function(source, layout = c("wide", "long"), idx = NULL,
                 level = NULL, files = NULL, gdal_vsi = TRUE) {
  .check_source(source)
  layout <- match.arg(layout)
  .check_idx(idx)
  .check_names(level, "level")
  if (!is.null(level) && length(level) != 1L) {
    .taco_stop("`level` must be a single level name")
  }
  .check_names(files, "files")
  if (!is.logical(gdal_vsi) || length(gdal_vsi) != 1L || is.na(gdal_vsi)) {
    .taco_stop("`gdal_vsi` must be TRUE or FALSE")
  }

  options <- .read_options(idx, level, layout, files, gdal_vsi)
  con <- .open_reader()
  if (length(source) == 1L) {
    sql <- paste("SELECT * FROM", .read_call)
    params <- c(list(source), options)
  } else {
    .same_contract(con, source)
    projection <- if (is.null(level)) {
      "taco.sample_id, ?::VARCHAR AS source_file, taco.* EXCLUDE (sample_id)"
    } else {
      "?::VARCHAR AS source_file, taco.*"
    }
    branch <- paste("SELECT", projection, "FROM", .read_call, "AS taco")
    sql <- paste(rep(branch, length(source)), collapse = " UNION ALL BY NAME ")
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
  tibble::as_tibble(DBI::dbGetQuery(con, sql, params = params))
}


.scalar <- function(source, sql) {
  source <- .single_source(source)
  con <- .open_reader()
  DBI::dbGetQuery(con, sql, params = list(source))[[1L]]
}


#' The contract of a TACO dataset
#'
#' @param source Local path or URL to the dataset.
#' @return A tibble of `kind` and `value` rows describing the structure,
#'   the metadata levels and the derived groups.
#' @export
contract <- function(source) {
  source <- .single_source(source)
  con <- .open_reader()
  tibble::as_tibble(
    DBI::dbGetQuery(con, "SELECT * FROM taco_contract(?)", params = list(source))
  )
}


#' Metadata levels of a TACO dataset
#'
#' Named `metadata_levels()` rather than `levels()` so it does not mask
#' the base R generic.
#'
#' @param source Local path or URL to the dataset.
#' @return A character vector, parents before children.
#' @export
metadata_levels <- function(source) {
  unlist(.scalar(source, "SELECT taco_levels(?)"), use.names = FALSE)
}


#' Sample structure of a TACO dataset
#'
#' Named `sample_structure()` rather than `structure()` so it does not
#' mask the base R function.
#'
#' @param source Local path or URL to the dataset.
#' @return A character vector of leaf declarations, empty when the
#'   contract declares no structure.
#' @export
sample_structure <- function(source) {
  unlist(.scalar(source, "SELECT taco_structure(?)"), use.names = FALSE)
}


#' COLLECTION.json of a TACO dataset
#'
#' Returned unparsed so the package stays free of a JSON dependency.
#' Pass it through `jsonlite::fromJSON()` for a list.
#'
#' @param source Local path or URL to the dataset.
#' @return A single JSON string.
#' @export
collection <- function(source) {
  .scalar(source, "SELECT taco_collection(?)")
}


#' Derived metadata declarations of a TACO dataset
#'
#' @param source Local path or URL to the dataset.
#' @return A single JSON string, empty when the contract declares none.
#' @export
derived <- function(source) {
  values <- unlist(.scalar(source, "SELECT taco_derived(?)"), use.names = FALSE)
  if (length(values)) values[[1L]] else ""
}


#' The cozip profile of an archive
#'
#' @param source Local path or URL to the dataset.
#' @return `"none"`, `"flat"` or `"taco"`.
#' @export
profile <- function(source) {
  .scalar(source, "SELECT cozip_profile(?)")
}
