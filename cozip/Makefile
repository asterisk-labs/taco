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
# CMakeLists sets PREFIX "" so the artifact is always cozip.<ext> with
# no "lib" prefix, regardless of compiler.
UNAME_S := $(shell uname -s)
ifeq ($(UNAME_S),Darwin)
    LIB_NAME := cozip.dylib
endif
ifeq ($(UNAME_S),Linux)
    LIB_NAME := cozip.so
endif
ifeq ($(OS),Windows_NT)
    LIB_NAME := cozip.dll
    # The MSVC setup action (ilammy/msvc-dev-cmd) EXPORTS VCPKG_ROOT
    # pointing to Visual Studio's bundled vcpkg, where libzip is NOT
    # installed. We use ':=' (not '?=') so the Makefile assignment wins
    # over the environment variable. Command-line override still works:
    #     make VCPKG_ROOT=D:/path/vcpkg test
    VCPKG_ROOT := C:/vcpkg
    VCPKG_BIN  := $(VCPKG_ROOT)/installed/x64-windows/bin
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
ifeq ($(OS),Windows_NT)
	# Python 3.8+ on Windows only searches the DLL's own folder for
	# transitive deps. Bundle vcpkg's runtime DLLs (libzip + zlib +
	# friends) alongside cozip.dll. This is what delvewheel does for
	# production wheels.
	cp $(VCPKG_BIN)/*.dll $(PY_LIB_DIR)/
endif

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
sdist:
	mkdir -p $(DIST_DIR)
	rm -rf build/sdist
	mkdir -p build/sdist/cozip-$(VERSION)
	cp -r $(CORE_DIR) $(PY_DIR) Makefile build/sdist/cozip-$(VERSION)/
	-cp README.md LICENSE SPEC.md build/sdist/cozip-$(VERSION)/ 2>/dev/null
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

# 6. Nuke everything stale.
clean:
	rm -rf build/ $(DIST_DIR)/
	rm -rf $(PY_LIB_DIR)/*.dylib $(PY_LIB_DIR)/*.so $(PY_LIB_DIR)/*.dll
	rm -rf $(PY_LIB_DIR)/libcozip.dylib $(PY_LIB_DIR)/libcozip.so $(PY_LIB_DIR)/libcozip.dll
	rm -rf $(PY_DIR)/dist $(PY_DIR)/build
	rm -rf $(PY_DIR)/*.egg-info
	rm -rf $(PY_DIR)/cozip.egg-info $(PY_DIR)/cozip/cozip.egg-info
	rm -rf $(PY_DIR)/.pytest_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true