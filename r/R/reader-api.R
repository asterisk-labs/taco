#' Read a TACO dataset
#'
#' `source` is one or more `.zip` archives, a FOLDER directory or a
#' `.tacocat` catalog, local or remote. Multiple sources must share a contract.
#'
#' @param source A dataset, or local paths or http(s)/s3/gcs/azure/hf URLs.
#' @param files Restrict which structure leaves are read. `NULL` reads all.
#'
#' @return A tibble.
#' @export
read <- function(source, files = NULL) {
  UseMethod("read")
}


#' @rdname read
#' @export
read.character <- function(source, files = NULL) {
  sources <- .normalize_sources(source)
  if (length(sources) > 1L) {
    sources <- open_dataset(sources)[["sources"]]
  }
  .read_table(sources, files)
}


#' @rdname read
#' @export
read.taco_dataset <- function(source, files = NULL) {
  .read_table(source[["sources"]], files)
}
