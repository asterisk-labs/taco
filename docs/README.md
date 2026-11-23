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

Each fact lives on one page. How TACO works and what every binding shares is on
`api.html`; a language page has only what differs in that language; the format is in
the specification. Link to a section instead of repeating it.

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

## Numbers

The API pages measure
[Major TOM Core-DEM](https://source.coop/major-tom/core-dem), a public TACOCAT of
1,735,044 samples on Source Cooperative, with the Python reader on a laptop. The
landing figure shows CloudSEN12 and four of 39 more datasets, drawn at random on each
load. Their facts come from the
[TorchGeo dataset docs](https://docs.torchgeo.org/en/stable/api/datasets.html), and
CloudSEN12's from its Hugging Face card. Only datasets with a license are listed. Its TACO side, rasters as Rumi, tables
as Parquet and JSON as JSON, is an example, not a published dataset. The small examples write their
own dataset, so they run anywhere. Keep every number on
the site measured; when the reader changes, measure again.
