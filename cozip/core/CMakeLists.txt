cmake_minimum_required(VERSION 3.21)

# ----------------------------------------------------------------------
# Single source of truth for the version.
#
# cozip/VERSION lives one level up from this CMakeLists. ALL bindings
# (this C library, the Python wheel, future R/Julia packages) read the
# exact same file so they cannot drift apart.
#
# CalVer of the form YYYY.M.D parses fine into CMake's MAJOR.MINOR.PATCH.
# ----------------------------------------------------------------------

file(READ "${CMAKE_CURRENT_SOURCE_DIR}/../VERSION" COZIP_VERSION_RAW)
string(STRIP "${COZIP_VERSION_RAW}" COZIP_VERSION)

project(cozip
    VERSION      ${COZIP_VERSION}
    DESCRIPTION  "Cloud-Optimized ZIP core"
    LANGUAGES    C)

# Settings that are SAFE to apply globally (i.e. that vendored deps
# can also use without breaking).
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "" FORCE)
endif()

if(APPLE AND NOT CMAKE_OSX_DEPLOYMENT_TARGET)
    set(CMAKE_OSX_DEPLOYMENT_TARGET "11.0" CACHE STRING "" FORCE)
endif()

# IMPORTANT: do NOT set CMAKE_C_STANDARD / CMAKE_C_EXTENSIONS / warning
# flags / visibility presets globally. Those propagate to libzip and
# zlib (brought in via FetchContent), and:
#
#   * CMAKE_C_EXTENSIONS OFF forces -std=c11 (strict ISO) instead of
#     -std=gnu11. Under strict ISO, glibc hides off_t unless
#     _POSIX_C_SOURCE or similar is defined. libzip's compat.h:146
#     uses off_t bare and fails to compile.
#
#   * -Wall -Wextra -Wpedantic -Wshadow turn libzip's own warnings
#     into noise we don't control.
#
#   * C_VISIBILITY_PRESET=hidden hides every symbol from dlsym, which
#     would also affect how libzip is linked into us (already
#     internal, but still propagating it globally is wrong-shaped).
#
# These options are applied to the cozip target only, further down.

option(COZIP_INSTALL     "Generate install / export targets" ON)
option(COZIP_BUILD_TESTS "Build the test suite"              OFF)


# ----------------------------------------------------------------------
# Generate version.h from version.h.in
#
# version.h is committed-via-template. CMake fills in the @PROJECT_*@
# placeholders with the values it parsed from cozip/VERSION above.
# The output sits in the binary dir so source tree stays clean.
#
# Consumers include it as `#include "version.h"` (the BINARY_DIR is
# added to PUBLIC include paths below for BUILD_INTERFACE).
# ----------------------------------------------------------------------

configure_file(
    "${CMAKE_CURRENT_SOURCE_DIR}/version.h.in"
    "${CMAKE_CURRENT_BINARY_DIR}/version.h"
    @ONLY)


# ----------------------------------------------------------------------
# Vendored deps via FetchContent.
#
# zlib and libzip are downloaded from upstream and built statically as
# part of cozip. This produces a self-contained shared library that
# only depends on libc on each platform, which is what the Python, R,
# Julia and standalone C bindings need for redistribution.
#
# First build downloads ~10 MB of source and adds ~60 seconds of
# compile time. Subsequent builds reuse the FetchContent cache.
# ----------------------------------------------------------------------

include(FetchContent)


# ---- zlib v1.3.1 ----

set(ZLIB_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(SKIP_INSTALL_ALL    ON  CACHE BOOL "" FORCE)

FetchContent_Declare(zlib
    GIT_REPOSITORY https://github.com/madler/zlib.git
    GIT_TAG        v1.3.1
    GIT_SHALLOW    TRUE)
FetchContent_MakeAvailable(zlib)

# zlib's CMakeLists builds both shared (target `zlib`) and static
# (target `zlibstatic`). Populate the variables and alias that
# libzip's find_package(ZLIB) consumes so it picks up the static one.
set(ZLIB_FOUND TRUE)
set(ZLIB_INCLUDE_DIRS "${zlib_SOURCE_DIR};${zlib_BINARY_DIR}")
set(ZLIB_INCLUDE_DIR  "${ZLIB_INCLUDE_DIRS}")
set(ZLIB_LIBRARIES    zlibstatic)
set(ZLIB_LIBRARY      zlibstatic)
set(ZLIB_VERSION_STRING "1.3.1")
set(ZLIB_VERSION_MAJOR 1)
set(ZLIB_VERSION_MINOR 3)
set(ZLIB_VERSION_PATCH 1)

if(NOT TARGET ZLIB::ZLIB)
    add_library(ZLIB::ZLIB ALIAS zlibstatic)
endif()


# ---- libzip v1.11.4 ----

# Build static, no tools, no docs, no tests. Disable every codec other
# than deflate (which lives in zlib) and every crypto backend. cozip
# only uses STORE so the only piece of libzip we actually exercise is
# its LFH and Central Directory writer.
set(BUILD_SHARED_LIBS     OFF CACHE BOOL "" FORCE)
set(BUILD_TOOLS           OFF CACHE BOOL "" FORCE)
set(BUILD_REGRESS         OFF CACHE BOOL "" FORCE)
set(BUILD_EXAMPLES        OFF CACHE BOOL "" FORCE)
set(BUILD_DOC             OFF CACHE BOOL "" FORCE)
set(ENABLE_BZIP2          OFF CACHE BOOL "" FORCE)
set(ENABLE_LZMA           OFF CACHE BOOL "" FORCE)
set(ENABLE_ZSTD           OFF CACHE BOOL "" FORCE)
set(ENABLE_OPENSSL        OFF CACHE BOOL "" FORCE)
set(ENABLE_COMMONCRYPTO   OFF CACHE BOOL "" FORCE)
set(ENABLE_WINDOWS_CRYPTO OFF CACHE BOOL "" FORCE)
set(ENABLE_MBEDTLS        OFF CACHE BOOL "" FORCE)
set(ENABLE_GNUTLS         OFF CACHE BOOL "" FORCE)
set(LIBZIP_DO_INSTALL     OFF CACHE BOOL "" FORCE)

FetchContent_Declare(libzip
    GIT_REPOSITORY https://github.com/nih-at/libzip.git
    GIT_TAG        v1.11.4
    GIT_SHALLOW    TRUE)
FetchContent_MakeAvailable(libzip)


# ----------------------------------------------------------------------
# cozip itself
# ----------------------------------------------------------------------

add_library(cozip SHARED cozip.c)
add_library(cozip::cozip ALIAS cozip)

# COZIP_BUILDING toggles cozip.h's COZIP_API macro from "import" to
# "export". Set as PRIVATE so it only applies to the cozip target,
# not to anyone consuming cozip via target_link_libraries.
target_compile_definitions(cozip PRIVATE COZIP_BUILDING)

# C standard — applied only to cozip, NOT propagated to vendored deps.
# c_std_11 is "want C11 if available, fall back to highest", which is
# what we need without breaking libzip's off_t expectations.
target_compile_features(cozip PRIVATE c_std_11)

# Strict warnings — only for our own code. libzip and zlib have their
# own warning hygiene; we don't impose ours on them.
if(MSVC)
    target_compile_options(cozip PRIVATE
        /W4 /permissive- /D_CRT_SECURE_NO_WARNINGS)
else()
    target_compile_options(cozip PRIVATE
        -Wall -Wextra -Wpedantic -Wshadow)
endif()

# Symbol visibility.
#
# C_VISIBILITY_PRESET=hidden hides every symbol by default; cozip.h's
# COZIP_API macro re-exports the public API surface explicitly via
# __attribute__((visibility("default"))) on Unix and __declspec(dllexport)
# on Windows.
#
# WINDOWS_EXPORT_ALL_SYMBOLS is OFF on purpose: with the COZIP_API
# annotations in place, we control exports manually. Leaving it ON
# would also export symbols from libzip and zlib (statically linked
# into us), polluting the ABI surface — the bug we're fixing.
#
# Result on every platform: the .so/.dylib/.dll exposes exactly the
# 8 cozip_* functions (the original 7 + cozip_version_string).
set_target_properties(cozip PROPERTIES
    C_VISIBILITY_PRESET        hidden
    VISIBILITY_INLINES_HIDDEN  ON
    WINDOWS_EXPORT_ALL_SYMBOLS OFF)

# Public include tree:
#   - SOURCE_DIR for cozip.h (and version.h.in's source twin if anyone
#     ever adds one to git, but today version.h is generate-only).
#   - BINARY_DIR for the generated version.h.
target_include_directories(cozip
    PUBLIC
        $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}>
        $<BUILD_INTERFACE:${CMAKE_CURRENT_BINARY_DIR}>
        $<INSTALL_INTERFACE:include>)

# libzip 1.11.x exposes its bare static target as `zip`. The
# transitive zlibstatic link comes through libzip's INTERFACE_LINK
# so we do not list it here.
target_link_libraries(cozip PRIVATE zip)

# Output naming.
# PREFIX "" disables the automatic "lib" prefix so the artifact is
# always cozip.<ext> across platforms (matches what the Makefile and
# Python loader expect).
set_target_properties(cozip PROPERTIES
    VERSION       ${PROJECT_VERSION}
    PUBLIC_HEADER cozip.h
    OUTPUT_NAME   cozip
    PREFIX        "")

# SOVERSION is a Linux/ELF concept that maps to install_name's
# compatibility_version on Apple. On a universal fat dylib produced by
# CMAKE_OSX_ARCHITECTURES="arm64;x86_64" it triggers symlink and lipo
# merge edge cases. Apple dylibs already encode VERSION in the
# install_name, so we just skip SOVERSION on Apple.
#
# NOTE: with CalVer, PROJECT_VERSION_MAJOR is the year (e.g. 2026), so
# every January the SOVERSION bumps. That is the correct behavior for
# "the ABI may break at any release" but worth knowing.
if(NOT APPLE)
    set_target_properties(cozip PROPERTIES
        SOVERSION ${PROJECT_VERSION_MAJOR})
endif()

# rpath setup so the binary finds bundled libs (none today, but ready
# for the day we add LICENSE/THIRD_PARTY notices alongside the .so).
if(APPLE)
    set_target_properties(cozip PROPERTIES
        INSTALL_NAME_DIR "@rpath"
        BUILD_WITH_INSTALL_RPATH TRUE)
elseif(UNIX)
    set_target_properties(cozip PROPERTIES
        INSTALL_RPATH "$ORIGIN"
        BUILD_WITH_INSTALL_RPATH TRUE)
endif()

if(COZIP_INSTALL)
    include(GNUInstallDirs)
    include(CMakePackageConfigHelpers)

    install(TARGETS cozip
        EXPORT        cozipTargets
        LIBRARY       DESTINATION ${CMAKE_INSTALL_LIBDIR}
        ARCHIVE       DESTINATION ${CMAKE_INSTALL_LIBDIR}
        RUNTIME       DESTINATION ${CMAKE_INSTALL_BINDIR}
        PUBLIC_HEADER DESTINATION ${CMAKE_INSTALL_INCLUDEDIR})

    # version.h is generated, so it cannot be a PUBLIC_HEADER attached
    # to the target (PUBLIC_HEADER files must exist at configure time).
    # Install it explicitly alongside cozip.h.
    install(FILES "${CMAKE_CURRENT_BINARY_DIR}/version.h"
        DESTINATION ${CMAKE_INSTALL_INCLUDEDIR})

    install(EXPORT cozipTargets
        FILE        cozipTargets.cmake
        NAMESPACE   cozip::
        DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/cozip)

    write_basic_package_version_file(
        "${CMAKE_CURRENT_BINARY_DIR}/cozipConfigVersion.cmake"
        VERSION       ${PROJECT_VERSION}
        COMPATIBILITY SameMajorVersion)

    file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/cozipConfig.cmake"
"include(\"\${CMAKE_CURRENT_LIST_DIR}/cozipTargets.cmake\")\n")

    install(FILES
        "${CMAKE_CURRENT_BINARY_DIR}/cozipConfig.cmake"
        "${CMAKE_CURRENT_BINARY_DIR}/cozipConfigVersion.cmake"
        DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/cozip)
endif()


# ----------------------------------------------------------------------
# Tests (OFF by default — flip COZIP_BUILD_TESTS=ON when you add them).
# Add a `tests/` subdirectory with its own CMakeLists.txt that calls
# add_test(...). The CI workflow already supports flipping this via
# -DCOZIP_BUILD_TESTS=ON in the Configure step.
# ----------------------------------------------------------------------

if(COZIP_BUILD_TESTS)
    enable_testing()
    add_subdirectory(tests)
endif()