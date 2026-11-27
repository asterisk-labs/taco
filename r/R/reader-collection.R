.collection_documents <- function(datasets) {
  vapply(
    datasets,
    function(dataset) .Call(taco_r_dataset, dataset)[["collection"]],
    character(1),
    USE.NAMES = FALSE
  )
}


.parse_collection <- function(document) {
  jsonlite::fromJSON(document, simplifyVector = FALSE)
}


.extent_union <- function(extents) {
  extents <- Filter(Negate(is.null), extents)
  if (!length(extents)) {
    return(NULL)
  }

  spatial <- lapply(extents, function(extent) as.numeric(extent[["spatial"]]))
  south <- min(vapply(spatial, `[[`, numeric(1), 2L))
  north <- max(vapply(spatial, `[[`, numeric(1), 4L))
  intervals <- do.call(rbind, lapply(spatial, function(bounds) {
    west <- bounds[[1L]]
    east <- bounds[[3L]]
    if (west <= east) {
      matrix(c(west + 180, east + 180), nrow = 1L)
    } else {
      matrix(c(0, east + 180, west + 180, 360), ncol = 2L, byrow = TRUE)
    }
  }))
  intervals <- intervals[order(intervals[, 1L]), , drop = FALSE]
  merged <- list()
  for (index in seq_len(nrow(intervals))) {
    interval <- intervals[index, ]
    last <- length(merged)
    if (last && interval[[1L]] <= merged[[last]][[2L]]) {
      merged[[last]][[2L]] <- max(merged[[last]][[2L]], interval[[2L]])
    } else {
      merged[[last + 1L]] <- interval
    }
  }
  gaps <- lapply(seq_len(length(merged) - 1L), function(index) {
    c(merged[[index]][[2L]], merged[[index + 1L]][[1L]])
  })
  gaps[[length(gaps) + 1L]] <- c(merged[[length(merged)]][[2L]], merged[[1L]][[1L]] + 360)
  sizes <- vapply(gaps, function(gap) gap[[2L]] - gap[[1L]], numeric(1))
  gap <- gaps[[which.max(sizes)]]
  if (gap[[1L]] == gap[[2L]]) {
    west <- -180
    east <- 180
  } else {
    start <- gap[[2L]] %% 360
    end <- gap[[1L]] %% 360
    west <- start - 180
    east <- if (end == 0 && start > 0) 180 else end - 180
  }

  temporal <- Filter(Negate(is.null), lapply(extents, `[[`, "temporal"))
  temporal <- if (length(temporal)) {
    starts <- vapply(temporal, `[[`, character(1), 1L)
    ends <- vapply(temporal, `[[`, character(1), 2L)
    list(starts[[which.min(.iso_seconds(starts))]], ends[[which.max(.iso_seconds(ends))]])
  } else {
    NULL
  }
  list(spatial = as.list(c(west, south, east, north)), temporal = temporal)
}


# Seconds since 1970 of ISO 8601 times. As text, 00:00:00.5Z sorts before
# 00:00:00Z. strptime reads an offset only as +hhmm, and a time without one is
# UTC.
.iso_seconds <- function(values) {
  values <- sub("([+-][0-9]{2}):([0-9]{2})$", "\\1\\2", sub("[Zz]$", "+0000", values))
  values <- ifelse(grepl("[+-][0-9]{4}$", values), values, paste0(values, "+0000"))
  seconds <- as.numeric(as.POSIXct(values, format = "%Y-%m-%dT%H:%M:%OS%z", tz = "UTC"))
  if (anyNA(seconds)) {
    .taco_stop("extent.temporal must hold ISO 8601 times")
  }
  seconds
}


.add_counts <- function(values) {
  totals <- list()
  for (counts in values) {
    if (!is.list(counts) || is.null(names(counts)) ||
        !all(vapply(counts, function(count) is.numeric(count) && length(count) == 1L, logical(1)))) {
      .taco_stop("split:counts must be an object of sample counts")
    }
    for (name in names(counts)) {
      totals[[name]] <- sum(totals[[name]], counts[[name]])
    }
  }
  totals
}


# Besides extent, the collection fields that summarize the rows of each source,
# so the partitions of one build differ in them (SPEC 7.5). A list combines them
# as taco.consolidate does.
.summaries <- list(`split:counts` = .add_counts)


.same_collection <- function(collection) {
  collection[c("extent", "taco:sources", names(.summaries))] <- NULL
  collection
}


.merge_collections <- function(collections, sources) {
  if (length(collections) == 1L) {
    return(collections[[1L]])
  }
  if (any(vapply(collections, function(x) !is.null(x[["taco:sources"]]), logical(1)))) {
    .taco_stop("a source list cannot contain TACOCAT datasets")
  }
  expected <- .same_collection(collections[[1L]])
  for (index in seq.int(2L, length(collections))) {
    if (!identical(.same_collection(collections[[index]]), expected)) {
      .taco_stop("source does not belong to the same collection: %s", sources[[index]])
    }
  }
  collection <- collections[[1L]]
  collection[["extent"]] <- .extent_union(lapply(collections, `[[`, "extent"))
  for (name in names(.summaries)) {
    values <- Filter(Negate(is.null), lapply(collections, `[[`, name))
    if (length(values)) {
      collection[[name]] <- .summaries[[name]](values)
    }
  }
  collection
}
