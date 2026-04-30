#' cozip: Cloud-Optimized ZIP
#'
#' R bindings to libcozip, the reference writer for the Cloud-Optimized
#' ZIP (cozip) format. A cozip archive is a regular ZIP whose first
#' entry is a binary index placed at byte 0, allowing readers to locate
#' any priority file in a single HTTP range request without scanning
#' the Central Directory.
#'
#' @section Package philosophy:
#' Two layers, mirroring the Python binding. A high-level [create()]
#' covers the FLAT profile (parquet manifest at the tail of the
#' archive). Low-level `.Call` shims to the libcozip C ABI cover
#' custom profiles and advanced workflows.
#'
#' @keywords internal
#' @useDynLib cozip, .registration = TRUE, .fixes = "C_"
"_PACKAGE"