#' @export
print.taco_dataset <- function(x, ...) {
  collection <- x[["collection"]]
  count <- length(x[["sources"]])
  cat(sprintf(
    "taco.Dataset(%s, sources=%d)\n",
    collection[["id"]], count
  ))
  invisible(x)
}
