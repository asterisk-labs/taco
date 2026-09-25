.sql_identifier <- function(value) {
  sprintf('"%s"', gsub('"', '""', value, fixed = TRUE))
}


.dataset_sql <- function(dataset, query) {
  if (!inherits(dataset, "taco_dataset")) {
    .taco_stop("`dataset` must be a taco_dataset")
  }
  if (!is.character(query) || length(query) != 1L || is.na(query)) {
    .taco_stop("`query` must be one string")
  }
  query <- trimws(query)
  query <- sub(";[[:space:]]*$", "", query)
  if (!nzchar(query)) {
    .taco_stop("`query` must not be empty")
  }
  native_sql <- function(level = NULL, pivoted = TRUE, location = FALSE) {
    .Call(taco_r_sql, dataset[["_opened"]], NULL, level, pivoted, NULL, location)
  }

  relations <- list(dataset = native_sql(location = TRUE))
  for (level in dataset$contract$levels) {
    relations[[level]] <- native_sql(level = level)
  }
  context <- paste(
    sprintf("%s AS (%s)", vapply(names(relations), .sql_identifier, character(1)), unlist(relations)),
    collapse = ",\n"
  )
  statement <- sprintf("WITH %s\nSELECT * FROM (\n%s\n) AS taco_query", context, query)
  tibble::as_tibble(DBI::dbGetQuery(.open_reader(), statement))
}


#' Query a TACO dataset
#'
#' @param dataset An object returned by [open_dataset()].
#' @param query A SQL query over `dataset` or a raw metadata level.
#' @return A tibble.
#' @export
sql <- function(dataset, query) {
  .dataset_sql(dataset, query)
}
