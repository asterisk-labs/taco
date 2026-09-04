# make python    install taco, lint, test and build the wheel
# make deck      assemble the deck into _site/deck
# make onepager  assemble the onepager into _site/onepager
# make site      deck + onepager + spec (what GitHub Pages deploys)
# make clean     remove build output and caches

PYTHON ?= python
SITE   := _site

# taco.reader forwards to the cozip DuckDB extension. Until it lands on the
# community registry, point at a local build of the sibling repository.
COZIP_EXTENSION ?= $(abspath ../cozip_reader/build/release/extension/cozip/cozip.duckdb_extension)
export COZIP_EXTENSION

.PHONY: python deck onepager site clean

python:
	$(PYTHON) -m pip install -q -e python --no-deps
	$(PYTHON) -m ruff check --config python/pyproject.toml python/taco python/tests python/examples demo.py tools
	$(PYTHON) -m mypy --config-file python/pyproject.toml python/taco
	$(PYTHON) -m pytest -q python
	rm -rf python/dist && cd python && (command -v uv >/dev/null && uv build -q || $(PYTHON) -m pip wheel -q --no-deps -w dist .) && ls dist

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
