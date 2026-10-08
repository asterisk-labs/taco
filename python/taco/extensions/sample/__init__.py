"""Sample extensions."""

from . import geoenrich, majortom, rumi, split, stac

# Built-in models by namespace, for contracts read without their models.
MODELS = {**stac.models.PROFILES, "split": split.Split}

__all__ = ["geoenrich", "majortom", "rumi", "split", "stac"]
