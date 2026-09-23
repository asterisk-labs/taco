.sql_identifier <- function(value) {
  sprintf('"%s"', gsub('"', '""', value, fixed = TRUE))
}


.generated_columns <- function(contract) {
  result <- character()
  for (declaration in contract$structure) {
    variable <- grepl("*", declaration, fixed = TRUE)
    name <- if (variable) strsplit(declaration, "*", fixed = TRUE)[[1L]][1L] else declaration
    name <- gsub("/", "__", name, fixed = TRUE)
    result <- c(result, paste0(name, "::location"))

    path <- if (variable) strsplit(declaration, "*", fixed = TRUE)[[1L]][1L] else declaration
    parts <- strsplit(path, "/", fixed = TRUE)[[1L]]
    folder <- if (length(parts) == 1L) "children" else paste(c("children", head(parts, -1L)), collapse = "/")
    if ("rumi:header" %in% names(contract$metadata[[folder]])) {
      result <- c(result, paste0(name, "::header"))
    }
  }
  result
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
  datasets <- lapply(dataset$sources, function(path) .Call(taco_r_open, path))
  native_sql <- function(level = NULL, pivoted = TRUE, location = FALSE) {
    .Call(taco_r_sql, datasets, NULL, level, pivoted, NULL, location)
  }

  data <- native_sql()
  generated <- .generated_columns(dataset$contract)
  if (length(generated)) {
    excluded <- paste(vapply(generated, .sql_identifier, character(1)), collapse = ", ")
    data <- sprintf("SELECT * EXCLUDE (%s) FROM (%s) AS taco_data", excluded, data)
  }
  relations <- list(data = data, files = native_sql(pivoted = FALSE, location = TRUE))
  for (level in dataset$contract$levels) {
    relations[[gsub("/", "__", level, fixed = TRUE)]] <- native_sql(level = level)
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
#' @param query A SQL query over `data`, `files`, or a metadata level.
#' @return A tibble.
#' @export
sql <- function(dataset, query) {
  .dataset_sql(dataset, query)
}
