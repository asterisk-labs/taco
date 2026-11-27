#' Read files as raw bytes
#'
#' Reads complete files from `::location` values returned by `read()` or
#' `sql()`, local paths or URLs. This does not use GDAL.
#'
#' @param location A character vector of locations. A leading `~` is expanded.
#' @return A raw vector for one location. For any other length, a list of raw
#'   vectors in input order.
#' @export
read_bytes <- function(location) {
  if (!is.character(location) || anyNA(location) || any(!nzchar(location))) {
    .taco_stop("`location` must contain non-empty strings")
  }
  local <- !grepl("://", location, fixed = TRUE) & startsWith(location, "~")
  location[local] <- path.expand(location[local])
  files <- .Call(taco_r_read_bytes, location)
  if (length(location) == 1L) files[[1L]] else files
}
