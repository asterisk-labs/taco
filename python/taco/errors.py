from __future__ import annotations


class TacoError(Exception):
    """Base class for every taco error."""


class ContractError(TacoError, ValueError):
    """The structure or metadata schema declaration is invalid."""


class SampleError(TacoError, ValueError):
    """A sample does not conform to its contract."""


class CollectionError(TacoError, ValueError):
    """Dataset-level metadata is invalid."""


class TypeSpecError(ContractError):
    """A Parquet/Arrow type specification cannot be parsed."""


class WriterError(TacoError, RuntimeError):
    """A writer was used outside its lifecycle or failed to publish."""


class ContainerError(TacoError, RuntimeError):
    """An on-disk container cannot be opened or is malformed."""


class ConsolidationError(TacoError, RuntimeError):
    """Several partitions cannot be merged into one TACOCAT."""


class ValidationFailed(TacoError, RuntimeError):
    """A validation report contains at least one error."""
