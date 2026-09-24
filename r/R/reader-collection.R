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
    list(
      min(vapply(temporal, `[[`, character(1), 1L)),
      max(vapply(temporal, `[[`, character(1), 2L))
    )
  } else {
    NULL
  }
  list(spatial = as.list(c(west, south, east, north)), temporal = temporal)
}


.same_collection <- function(collection) {
  collection[["extent"]] <- NULL
  collection[["taco:sources"]] <- NULL
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
  collection
}
