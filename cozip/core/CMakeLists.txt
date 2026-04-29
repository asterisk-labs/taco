cmake_minimum_required(VERSION 3.21)
project(cozip
    VERSION      1.0.0
    DESCRIPTION  "Cloud-Optimized ZIP core"
    LANGUAGES    C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS OFF)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
set(CMAKE_WINDOWS_EXPORT_ALL_SYMBOLS ON)

if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "" FORCE)
endif()

if(APPLE AND NOT CMAKE_OSX_DEPLOYMENT_TARGET)
    set(CMAKE_OSX_DEPLOYMENT_TARGET "11.0" CACHE STRING "" FORCE)
endif()

if(MSVC)
    add_compile_options(/W4 /permissive- /D_CRT_SECURE_NO_WARNINGS)
else()
    add_compile_options(-Wall -Wextra -Wpedantic -Wshadow)
endif()

option(COZIP_INSTALL "Generate install / export targets" ON)


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
set(SKIP_INSTALL_ALL ON CACHE BOOL "" FORCE)

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
set(BUILD_SHARED_LIBS    OFF CACHE BOOL "" FORCE)
set(BUILD_TOOLS          OFF CACHE BOOL "" FORCE)
set(BUILD_REGRESS        OFF CACHE BOOL "" FORCE)
set(BUILD_EXAMPLES       OFF CACHE BOOL "" FORCE)
set(BUILD_DOC            OFF CACHE BOOL "" FORCE)
set(ENABLE_BZIP2         OFF CACHE BOOL "" FORCE)
set(ENABLE_LZMA          OFF CACHE BOOL "" FORCE)
set(ENABLE_ZSTD          OFF CACHE BOOL "" FORCE)
set(ENABLE_OPENSSL       OFF CACHE BOOL "" FORCE)
set(ENABLE_COMMONCRYPTO  OFF CACHE BOOL "" FORCE)
set(ENABLE_WINDOWS_CRYPTO OFF CACHE BOOL "" FORCE)
set(ENABLE_MBEDTLS       OFF CACHE BOOL "" FORCE)
set(ENABLE_GNUTLS        OFF CACHE BOOL "" FORCE)
set(LIBZIP_DO_INSTALL    OFF CACHE BOOL "" FORCE)

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

target_include_directories(cozip
    PUBLIC
        $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}>
        $<INSTALL_INTERFACE:include>)

# libzip 1.11.x exposes its bare static target as `zip`. The
# transitive zlibstatic link comes through libzip's INTERFACE_LINK
# so we do not list it here.
target_link_libraries(cozip PRIVATE zip)

# PREFIX "" disables the automatic "lib" prefix that CMake adds on
# MinGW/GCC builds for Windows. Without it the artifact is libcozip.dll
# instead of cozip.dll, and the Makefile cp step fails.
set_target_properties(cozip PROPERTIES
    VERSION       ${PROJECT_VERSION}
    SOVERSION     ${PROJECT_VERSION_MAJOR}
    PUBLIC_HEADER cozip.h
    OUTPUT_NAME   cozip
    PREFIX        "")

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