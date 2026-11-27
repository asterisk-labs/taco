.normalize_sources <- function(source) {
  if (!is.character(source) || !length(source) || anyNA(source) ||
      any(!nzchar(source))) {
    .taco_stop("`source` must contain one or more non-empty strings")
  }
  local <- !grepl("://", source, fixed = TRUE)
  source[local] <- path.expand(source[local])
  if (anyDuplicated(.source_identity(source))) {
    .taco_stop("`source` paths must be unique")
  }
  source
}


# Local sources are the same when they are one file, as a path and its file://
# URI are. Base R cannot read an inode, so normalizePath() stands in: it resolves
# `.`, `..` and links, and the letter case on macOS and Windows, but not hard
# links. The core opens a file:// URI as the path that follows the prefix.
.source_identity <- function(source) {
  path <- sub("^file://", "", source)
  local <- !grepl("://", path, fixed = TRUE)
  path[local] <- normalizePath(path[local], winslash = "/", mustWork = FALSE)
  path
}
