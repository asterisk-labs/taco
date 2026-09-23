#' Inspect a TACO dataset
#'
#' Reads collection and contract information without reading samples.
#'
#' @param source One dataset path or URL.
#' @param query One of `"contract"`, `"structure"`, `"levels"`,
#'   `"collection"`, `"profile"`, or `"native_sql"`.
#' @return The requested dataset information.
#' @export
inspect <- function(source, query) {
  source <- .normalize_sources(source)
  if (length(source) != 1L) {
    .taco_stop("`inspect()` requires one source")
  }
  choices <- c("collection", "contract", "levels", "native_sql", "profile", "structure")
  if (!is.character(query) || length(query) != 1L || is.na(query) || !query %in% choices) {
    .taco_stop("`query` must be one of: %s", paste(choices, collapse = ", "))
  }

  native <- .Call(taco_r_dataset, .Call(taco_r_open, source))
  if (query == "collection") {
    return(.parse_collection(native[["collection"]]))
  }
  if (query == "structure") {
    return(native[["structure"]])
  }
  if (query == "levels") {
    return(native[["levels"]])
  }
  if (query == "profile") {
    return(.Call(taco_r_profile, source))
  }
  if (query == "native_sql") {
    dataset <- .Call(taco_r_open, source)
    return(.Call(taco_r_sql, list(dataset), NULL, NULL, TRUE, NULL, TRUE))
  }
  tibble::tibble(
    kind = c(rep("structure", length(native[["structure"]])), rep("level", length(native[["levels"]]))),
    value = c(native[["structure"]], native[["levels"]])
  )
}
