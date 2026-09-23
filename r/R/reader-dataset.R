.build_contract <- function(collection) {
  metadata <- collection[["taco:metadata"]]
  structure <- collection[["taco:structure"]]
  derived <- collection[["taco:derived"]]
  structure <- unlist(structure, use.names = FALSE)
  if (is.null(derived)) {
    derived <- list()
  }
  base::structure(
    list(
      structure = structure,
      metadata = metadata,
      derived = derived,
      levels = names(metadata)
    ),
    class = c("taco_contract", "list")
  )
}


#' Open a TACO dataset
#'
#' @param source One or more dataset paths or URLs.
#' @return A `taco_dataset` with its resolved sources, collection and contract.
#' @export
open_dataset <- function(source) {
  sources <- .normalize_sources(source)
  collections <- lapply(.collection_documents(sources), .parse_collection)
  collection <- .merge_collections(collections, sources)
  base::structure(
    list(
      sources = sources,
      collection = collection,
      contract = .build_contract(collection)
    ),
    class = c("taco_dataset", "list")
  )
}
