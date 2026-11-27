# TACO site

`docs/` is the source of [asterisk.coop/taco](https://asterisk.coop/taco/). It shares
its layout, type and figures with the cozip, Rumi, GeoZL and Karu sites. Build it and
preview the result with:

```bash
python -m pip install -r docs/_build/requirements.txt
make site
python -m http.server 8000 --directory _site
```

The build copies `docs/`, adds the JavaScript reader the playground imports, renders
the specification and checks every local link and anchor. `_site/` is ignored by Git.

## Layout

`api.html` explains how TACO works across languages. Each language page covers its
own API; the specification defines the format. Link to those pages for shared rules.

```
index.html            landing page and its interactive figure
api.html              how TACO works and what every binding shares
api-*.html            one page per language
spec/                 SPEC.md, the extension documents, their schemas and examples
playground/           browser viewer for public datasets
onepager/             printable project summary
deck/                 presentation
assets/               styles, scripts and figures
api/                  redirects from the old API pages
```

`spec/index.html` and `spec/extensions/*.html` are generated from the Markdown beside
them. After changing the specification or an extension document, run
`python docs/_build/build.py --sync-spec --output _site --clean` so the pages in
`docs/` match. The schema URLs under `spec/extensions/<name>/v1.0.0/` are extension
identifiers: never move them.

## Logo

The TACO mark, `assets/svg/taco-mark.svg`, is the mark of the first TACO site: the
taco emoji turns into the Earth and back while a satellite orbits it.
`taco-mark-static.svg` is the taco alone, for the favicon. Both draw the emoji with
the system font, so they look like the reader's own emoji.

## Numbers

The API pages use measurements from
[Major TOM Core-DEM](https://source.coop/major-tom/core-dem), a public TACOCAT with
1,735,044 samples, read on a laptop with Python. The landing figure draws 40 licensed
datasets in a new order on each load. Dataset facts come from the
[TorchGeo docs](https://docs.torchgeo.org/en/stable/api/datasets.html) and, for
CloudSEN12, its Hugging Face card. The figure's TACO side (Rumi rasters,
Parquet tables and JSON documents) is illustrative and has not been published.
The smaller examples create their own datasets and run locally. Remeasure site
figures when the reader changes.
