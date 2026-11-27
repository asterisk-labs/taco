taco_fixture <- function() testthat::test_path("data", "taco.zip")
taco_nested_fixture <- function() testthat::test_path("data", "taco_nested.zip")

# Whether a plain duckdb() connection finds an installed ICU.
icu_installed <- function() {
  con <- suppressMessages(DBI::dbConnect(duckdb::duckdb()))
  on.exit(DBI::dbDisconnect(con, shutdown = TRUE), add = TRUE)
  icu <- DBI::dbGetQuery(con, "SELECT installed FROM duckdb_extensions() WHERE extension_name = 'icu'")
  isTRUE(icu$installed)
}

# A FOLDER copy of the fixture with some COLLECTION.json fields replaced.
taco_partition <- function(path, fields) {
  dir.create(path)
  utils::unzip(taco_fixture(), exdir = path)
  collection_path <- file.path(path, "COLLECTION.json")
  collection <- jsonlite::read_json(collection_path, simplifyVector = FALSE)
  collection[names(fields)] <- fields
  jsonlite::write_json(collection, collection_path, auto_unbox = TRUE)
  path
}
