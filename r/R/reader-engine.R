.taco_stop <- function(fmt, ...) {
  stop(sprintf(paste0("taco: ", fmt), ...), call. = FALSE)
}


.reader <- new.env(parent = emptyenv())
.reader$con <- NULL
.reader$shutdown <- FALSE


.open_reader <- function() {
  con <- .reader$con
  if (!is.null(con) && DBI::dbIsValid(con)) {
    return(con)
  }
  # Find installed extensions while keeping DuckDB's other files in tempdir().
  storage <- duckdb::duckdb_storage_status()
  config <- list(
    autoinstall_known_extensions = "false",
    extension_directory = storage$directory[storage$kind == "extensions"]
  )
  con <- DBI::dbConnect(duckdb::duckdb(config = config, shared_home = FALSE))
  # Use UTC for timestamp comparisons and display across host time zones.
  tryCatch(DBI::dbExecute(con, "SET TimeZone = 'UTC'"), error = function(error) NULL)
  .reader$con <- con
  con
}


.close_reader <- function() {
  con <- .reader$con
  if (!is.null(con) && DBI::dbIsValid(con)) {
    duckdb::dbDisconnect(con, shutdown = TRUE)
  }
  .reader$con <- NULL
  invisible(NULL)
}


.shutdown_reader <- function() {
  if (.reader$shutdown) {
    return(invisible(NULL))
  }
  .close_reader()
  .Call(taco_r_shutdown)
  .reader$shutdown <- TRUE
  invisible(NULL)
}
