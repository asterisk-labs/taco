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


.read_table <- function(source, files) {
  .normalize_sources(source)
  .check_names(files, "files")

  datasets <- lapply(source, function(path) .Call(taco_r_open, path))
  sql <- .Call(taco_r_sql, datasets, NULL, NULL, TRUE, files, TRUE)
  tibble::as_tibble(DBI::dbGetQuery(.open_reader(), sql))
}
