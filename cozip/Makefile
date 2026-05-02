# cozip — top-level Makefile (lives at cozip/Makefile).
#
# Targets:
#   make lib            build libcozip standalone (CMake + Ninja)
#   make sync-version   write VERSION into r/DESCRIPTION
#   make sync-c         copy core/cozip.{c,h} into r/src/
#   make sync           sync-version + sync-c
#   make sync-check     fail if anything is out of sync (no writes)
#   make py-install     editable install of python package
#   make py-test        run python tests
#   make py-wheel       build python wheel
#   make r-install      R CMD INSTALL r/
#   make r-build        R CMD build r/ (produces .tar.gz)
#   make r-check        R CMD check on the built tarball
#   make clean          nuke build artifacts

VERSION    := $(shell tr -d '[:space:]' < VERSION)

CORE_DIR   := core
PY_DIR     := python
R_DIR      := r
BUILD_DIR  := build/lib
PY_LIB_DIR := $(PY_DIR)/cozip/_lib
DIST_DIR   := dist

UNAME_S := $(shell uname -s)
ifeq ($(UNAME_S),Darwin)
    LIB_NAME := cozip.dylib
endif
ifeq ($(UNAME_S),Linux)
    LIB_NAME := cozip.so
endif
ifeq ($(OS),Windows_NT)
    LIB_NAME := cozip.dll
endif

.PHONY: all lib sync sync-version sync-c sync-check \
        py-install py-test py-wheel \
        r-install r-build r-check r-clean \
        clean help

# Default target: full pre-push validation. Run this before commits
# to catch anything r-universe / CI would catch.
all: sync lib py-install r-build
	@echo ""
	@echo "✓ all green — sync, lib, python install, R build all passed"
	@echo "  cozip $(VERSION)"

help:
	@echo "cozip $(VERSION)"
	@echo ""
	@echo "Pipelines:"
	@echo "  all                full validation (sync + lib + python + R build)"
	@echo ""
	@echo "Sync:"
	@echo "  sync               sync-version + sync-c"
	@echo "  sync-version       VERSION → DESCRIPTION"
	@echo "  sync-c             core/cozip.{c,h} → r/src/"
	@echo "  sync-check         fail if out of sync (no writes)"
	@echo ""
	@echo "C library:"
	@echo "  lib                build libcozip + copy into python/_lib/"
	@echo ""
	@echo "Python:"
	@echo "  py-install         editable install"
	@echo "  py-test            run pytest"
	@echo "  py-wheel           build wheel"
	@echo ""
	@echo "R:"
	@echo "  r-install          R CMD INSTALL r/"
	@echo "  r-build            R CMD build r/ (produces dist/.tar.gz)"
	@echo "  r-check            R CMD check on the built tarball"
	@echo ""
	@echo "  clean              nuke build artifacts"

# --- C library ---

lib:
	cmake -B $(BUILD_DIR) -S $(CORE_DIR) -G Ninja
	cmake --build $(BUILD_DIR)
	mkdir -p $(PY_LIB_DIR)
	cp $(BUILD_DIR)/$(LIB_NAME) $(PY_LIB_DIR)/$(LIB_NAME)

# --- Sync ---

sync: sync-version sync-c

sync-version:
	@sed -i.bak -E 's/^Version:.*/Version: $(VERSION)/' $(R_DIR)/DESCRIPTION
	@rm -f $(R_DIR)/DESCRIPTION.bak
	@echo "DESCRIPTION → Version: $(VERSION)"

sync-c:
	@rm -f $(R_DIR)/src/cozip.c $(R_DIR)/src/cozip.h
	@cp $(CORE_DIR)/cozip.c $(R_DIR)/src/cozip.c
	@cp $(CORE_DIR)/cozip.h $(R_DIR)/src/cozip.h
	@echo "core/cozip.{c,h} → r/src/"

sync-check:
	@VFILE=$$(grep -E '^Version:' $(R_DIR)/DESCRIPTION | sed 's/Version:[[:space:]]*//'); \
	 if [ "$$VFILE" != "$(VERSION)" ]; then \
	   echo "VERSION=$(VERSION) but DESCRIPTION=$$VFILE — run 'make sync-version'"; \
	   exit 1; \
	 fi
	@diff -q $(CORE_DIR)/cozip.c $(R_DIR)/src/cozip.c >/dev/null || \
	  { echo "core/cozip.c differs from r/src/cozip.c — run 'make sync-c'"; exit 1; }
	@diff -q $(CORE_DIR)/cozip.h $(R_DIR)/src/cozip.h >/dev/null || \
	  { echo "core/cozip.h differs from r/src/cozip.h — run 'make sync-c'"; exit 1; }
	@echo "sync OK"

# --- Python ---

py-install: lib
	pip install -e $(PY_DIR)

py-test: py-install
	cd $(PY_DIR) && pytest tests/ -v

py-wheel: lib
	cd $(PY_DIR) && python -m build --wheel

# --- R ---

# Clean any leftover build artifacts in r/src/. Run before switching
# build environments (e.g. macOS → Docker/Linux) — make only checks
# timestamps, not binary format, so a stale Mach-O .so will silently
# survive an attempted Linux build and fail at load time with
# "invalid ELF header".
r-clean:
	rm -f $(R_DIR)/src/version.h
	rm -f $(R_DIR)/src/*.o $(R_DIR)/src/*.so
	rm -f $(R_DIR)/src/*.dylib $(R_DIR)/src/*.dll

r-install: sync-check r-clean
	R CMD INSTALL $(R_DIR)

r-build: sync-check r-clean
	mkdir -p $(DIST_DIR)
	cd $(DIST_DIR) && R CMD build ../$(R_DIR)

r-check: r-build
	cd $(DIST_DIR) && R CMD check cozip_$(VERSION).tar.gz

# --- Clean ---

clean:
	rm -rf build/ $(DIST_DIR)/
	rm -f $(PY_LIB_DIR)/*.dylib $(PY_LIB_DIR)/*.so $(PY_LIB_DIR)/*.dll
	rm -f $(R_DIR)/src/version.h
	rm -f $(R_DIR)/src/*.o $(R_DIR)/src/*.so $(R_DIR)/src/*.dylib $(R_DIR)/src/*.dll
	rm -f $(R_DIR)/src/.DS_Store
	rm -rf $(PY_DIR)/dist $(PY_DIR)/build $(PY_DIR)/*.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true