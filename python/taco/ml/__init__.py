"""Typed samples and plots for datasets that declare an ``ml:contract``.

``taco.read`` returns metadata; this returns the arrays a model consumes. It is
installed separately (``pip install taco-eo[ml]``) because decoding and plotting
pull in rasterio, Pillow and matplotlib, which a reader of tables does not need.

    from taco.ml import Dataset, plot_sample

    ds = Dataset("scenes.tacozip")
    plot_sample(ds[0])
"""

from .dataset import Dataset, SlotValue
from .viz import plot_sample, plot_slot

__all__ = ["Dataset", "SlotValue", "plot_sample", "plot_slot"]
