# make python    install taco, lint, test and build the wheel
# make r         check the R reader
# make julia     check the Julia reader
# make deck      assemble the deck into _site/deck
# make onepager  assemble the onepager into _site/onepager
# make site      deck + onepager + spec (what GitHub Pages deploys)
# make clean     remove build output and caches

PYTHON ?= python
SITE   := _site
DUCKDB_VERSION ?= 1.5.5
COZIP_PYTHON ?= ../cozip/python

# taco.reader forwards to the cozip DuckDB extension. Until it lands on the
# community registry, point at a local build of the sibling repository.
COZIP_EXTENSION ?= $(abspath ../cozip_reader/build/release/extension/cozip/cozip.duckdb_extension)
export COZIP_EXTENSION

.PHONY: python r julia deck onepager site clean

python:
	$(PYTHON) -m pip install -q "duckdb==$(DUCKDB_VERSION)" -e $(COZIP_PYTHON) -e python --no-deps
	$(PYTHON) -m ruff format --check --config python/pyproject.toml python/taco python/tests python/examples
	$(PYTHON) -m ruff check --config python/pyproject.toml python/taco python/tests python/examples tools
	$(PYTHON) -m mypy --config-file python/pyproject.toml python/taco
	$(PYTHON) -m pytest python --cov=taco --cov-config=python/pyproject.toml --cov-report=term-missing
	rm -rf python/dist && cd python && (command -v uv >/dev/null && uv build -q || $(PYTHON) -m pip wheel -q --no-deps -w dist .) && ls dist

r:
	Rscript -e 'roxygen2::roxygenise("r")'
	Rscript -e 'testthat::test_local("r", reporter = "summary", stop_on_failure = TRUE)'

julia:
	cd julia && julia --project=. -e 'using Pkg; Pkg.test()'

deck:
	rm -rf $(SITE)/deck && mkdir -p $(SITE) && cp -R deck $(SITE)/deck && touch $(SITE)/.nojekyll
	@echo "open $(SITE)/deck/overview/index.html"

onepager:
	rm -rf $(SITE)/onepager && mkdir -p $(SITE) && cp -R onepager $(SITE)/onepager && touch $(SITE)/.nojekyll
	@echo "open $(SITE)/onepager/index.html"

site:
	$(PYTHON) tools/build_site.py --output $(SITE) --clean

clean:
	rm -rf $(SITE) python/dist python/build python/*.egg-info .pytest_cache python/.pytest_cache \
	  .ruff_cache python/.ruff_cache .mypy_cache python/.mypy_cache numpy_demo.zip
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
