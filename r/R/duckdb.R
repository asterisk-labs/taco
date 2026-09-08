.taco_stop <- function(fmt, ...) {
  stop(sprintf(paste0("taco: ", fmt), ...), call. = FALSE)
}


.check_source <- function(source) {
  if (!is.character(source) || !length(source) || anyNA(source) ||
      any(!nzchar(source))) {
    .taco_stop("`source` must contain one or more non-empty strings")
  }
  if (anyDuplicated(source)) {
    .taco_stop("`source` paths must be unique")
  }
  invisible(source)
}


.single_source <- function(source) {
  .check_source(source)
  if (length(source) != 1L) {
    .taco_stop("this operation needs a single source")
  }
  source
}


.reader <- new.env(parent = emptyenv())
.reader$con <- NULL


.has_reader <- function(con) {
  found <- DBI::dbGetQuery(
    con,
    "SELECT count(*) AS n FROM duckdb_functions() WHERE function_name = 'read_taco'"
  )
  found$n[[1L]] > 0
}


.require_reader <- function(con) {
  if (!.has_reader(con)) {
    .taco_stop(paste(
      "the loaded cozip extension has no TACO reader.",
      "Upgrade duckdb or point COZIP_EXTENSION at a local build"
    ))
  }
  invisible(con)
}


.open_reader <- function() {
  con <- .reader$con
  if (!is.null(con) && DBI::dbIsValid(con)) {
    return(con)
  }

  local_extension <- Sys.getenv("COZIP_EXTENSION", unset = "")
  driver <- if (nzchar(local_extension)) {
    duckdb::duckdb(
      config = list(allow_unsigned_extensions = "true"),
      shared_home = TRUE
    )
  } else {
    duckdb::duckdb(shared_home = TRUE)
  }
  con <- duckdb::dbConnect(driver)
  ready <- FALSE
  on.exit({
    if (!ready) {
      duckdb::dbDisconnect(con, shutdown = TRUE)
    }
  }, add = TRUE)

  DBI::dbExecute(con, "INSTALL httpfs")
  DBI::dbExecute(con, "LOAD httpfs")
  if (nzchar(local_extension)) {
    DBI::dbExecute(
      con,
      paste("LOAD", DBI::dbQuoteString(con, local_extension))
    )
  } else {
    DBI::dbExecute(con, "INSTALL cozip FROM community")
    DBI::dbExecute(con, "LOAD cozip")
  }
  .require_reader(con)

  .reader$con <- con
  ready <- TRUE
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
