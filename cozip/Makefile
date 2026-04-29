# cozip — top-level Makefile.
#
# Targets:
#   make lib       — build libcozip (via plain CMake + Ninja)
#   make python    — build lib + install Python package in editable mode
#   make test      — build + install + run pytest
#   make wheel     — build a distributable wheel into python/dist/
#   make sdist     — bundle source code (core + python + Makefile) into dist/
#   make clean     — remove all build artifacts
#
# The Python side does NOT invoke CMake. This Makefile is the only
# place where the two systems meet.

CORE_DIR   := core
BUILD_DIR  := build/lib
PY_DIR     := python
PY_LIB_DIR := $(PY_DIR)/cozip/_lib
DIST_DIR   := dist

VERSION := $(shell grep -E '^version' $(PY_DIR)/pyproject.toml | head -1 | sed -E 's/.*"([^"]+)".*/\1/')

# Detect the platform suffix.
UNAME_S := $(shell uname -s)
ifeq ($(UNAME_S),Darwin)
    LIB_NAME := libcozip.dylib
endif
ifeq ($(UNAME_S),Linux)
    LIB_NAME := libcozip.so
endif
ifeq ($(OS),Windows_NT)
    LIB_NAME := cozip.dll
    # vcpkg ships libzip on Windows but CMake doesn't auto-discover it.
    # Route through the vcpkg toolchain file (works in GH Actions and
    # locally if VCPKG_INSTALLATION_ROOT is set).
    VCPKG_ROOT ?= $(VCPKG_INSTALLATION_ROOT)
    CMAKE_EXTRA := -DCMAKE_TOOLCHAIN_FILE=$(VCPKG_ROOT)/scripts/buildsystems/vcpkg.cmake
endif

.PHONY: lib python test wheel sdist clean

# 1. Build libcozip with CMake + Ninja, then copy the artifact into the
#    Python package's _lib/ folder under the unversioned name.
lib:
	cmake -B $(BUILD_DIR) -S $(CORE_DIR) -G Ninja $(CMAKE_EXTRA)
	cmake --build $(BUILD_DIR)
	mkdir -p $(PY_LIB_DIR)
	cp $(BUILD_DIR)/$(LIB_NAME) $(PY_LIB_DIR)/$(LIB_NAME)

# 2. Editable install of the Python package. No CMake involved here —
#    hatchling just packages cozip/ as it sits on disk.
python: lib
	pip install -e $(PY_DIR)

# 3. Test.
test: python
	cd $(PY_DIR) && pytest tests/ -v

# 4. Build a distributable wheel. Run on each platform in CI.
wheel: lib
	cd $(PY_DIR) && python -m build --wheel

# 5. Source bundle: tarball of core + python + Makefile + docs.
#    Not a PEP 517 sdist (would require deeper hatchling config).
#    For now this is a "developer source archive" — extract and run
#    `make wheel` to rebuild from scratch.
sdist:
	mkdir -p $(DIST_DIR)
	rm -rf build/sdist
	mkdir -p build/sdist/cozip-$(VERSION)
	cp -r $(CORE_DIR) $(PY_DIR) Makefile build/sdist/cozip-$(VERSION)/
	-cp README.md LICENSE SPEC.md build/sdist/cozip-$(VERSION)/ 2>/dev/null
	# Strip generated stuff before tarballing
	rm -rf build/sdist/cozip-$(VERSION)/$(PY_DIR)/cozip/_lib/*.dylib
	rm -rf build/sdist/cozip-$(VERSION)/$(PY_DIR)/cozip/_lib/*.so
	rm -rf build/sdist/cozip-$(VERSION)/$(PY_DIR)/cozip/_lib/*.dll
	rm -rf build/sdist/cozip-$(VERSION)/$(PY_DIR)/build
	rm -rf build/sdist/cozip-$(VERSION)/$(PY_DIR)/dist
	find build/sdist/cozip-$(VERSION) -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find build/sdist/cozip-$(VERSION) -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find build/sdist/cozip-$(VERSION) -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	cd build/sdist && tar czf ../../$(DIST_DIR)/cozip-source-$(VERSION).tar.gz cozip-$(VERSION)
	@echo "Created: $(DIST_DIR)/cozip-source-$(VERSION).tar.gz"

# 6. Nuke everything stale, including leftovers from the old
#    scikit-build-core setup.
clean:
	# Build outputs
	rm -rf build/ $(DIST_DIR)/
	rm -rf $(PY_LIB_DIR)/*.dylib $(PY_LIB_DIR)/*.so $(PY_LIB_DIR)/*.dll
	# Python wheel/sdist outputs
	rm -rf $(PY_DIR)/dist $(PY_DIR)/build
	rm -rf $(PY_DIR)/*.egg-info
	rm -rf $(PY_DIR)/cozip.egg-info $(PY_DIR)/cozip/cozip.egg-info
	# Old scikit-build-core leftovers (if any)
	rm -rf $(PY_DIR)/_skbuild
	rm -rf $(PY_DIR)/CMakeFiles $(PY_DIR)/CMakeCache.txt
	rm -rf $(PY_DIR)/cmake_install.cmake $(PY_DIR)/build.ninja
	rm -rf $(PY_DIR)/.ninja_deps $(PY_DIR)/.ninja_log
	rm -rf $(PY_DIR)/core_build
	# Caches
	rm -rf $(PY_DIR)/.pytest_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true