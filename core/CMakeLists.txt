cmake_minimum_required(VERSION 3.21)
cmake_policy(SET CMP0091 NEW)

file(STRINGS "${CMAKE_CURRENT_SOURCE_DIR}/include/taco/taco.h"
    TACO_API_VERSION_LINE REGEX "^#define TACO_API_VERSION [0-9]+$")
if(NOT TACO_API_VERSION_LINE)
    message(FATAL_ERROR "taco.h has no numeric TACO_API_VERSION")
endif()

project(taco
    VERSION     0.12.0
    DESCRIPTION "libtaco, the reference library for the TACO format."
    LANGUAGES   CXX C
)

# The release DLL and its vcpkg dependencies must use the same dynamic CRT.
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>DLL")

option(TACO_BUILD_TESTS "Build the core tests" OFF)
option(TACO_WERROR      "Treat warnings in the core as errors" OFF)
set(TACO_KARU_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../extern/karu"
    CACHE PATH "Path to the karu submodule source")

if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "" FORCE)
endif()

set(CMAKE_CXX_STANDARD              23)
set(CMAKE_CXX_STANDARD_REQUIRED     ON)
set(CMAKE_CXX_EXTENSIONS            OFF)
set(CMAKE_C_STANDARD                11)
set(CMAKE_C_STANDARD_REQUIRED       ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS   ON)

# Only TACO_API symbols leave the library. karu stays private to it.
set(CMAKE_CXX_VISIBILITY_PRESET     hidden)
set(CMAKE_C_VISIBILITY_PRESET       hidden)
set(CMAKE_VISIBILITY_INLINES_HIDDEN ON)

set(KARU_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(KARU_WERROR ${TACO_WERROR} CACHE BOOL "" FORCE)
add_subdirectory(${TACO_KARU_DIR} ${CMAKE_CURRENT_BINARY_DIR}/karu EXCLUDE_FROM_ALL)

set(TACO_SOURCES
    src/cache.cpp
    src/capi.cpp
    src/cozip_index.cpp
    src/dataset.cpp
    src/json.cpp
    src/paths.cpp
    src/progress.cpp
    src/sql.cpp
    src/transport.cpp
)

add_library(taco_objects OBJECT ${TACO_SOURCES})
target_include_directories(taco_objects
    PUBLIC  ${CMAKE_CURRENT_SOURCE_DIR}/include
    PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/src
)
target_link_libraries(taco_objects PUBLIC karu::internal)
target_compile_definitions(taco_objects PRIVATE
    TACO_BUILD
    TACO_VERSION_STRING="${PROJECT_VERSION}"
)
target_compile_options(taco_objects PRIVATE
    $<$<CXX_COMPILER_ID:GNU,Clang,AppleClang>:-Wall;-Wextra;-Wpedantic>
    $<$<CXX_COMPILER_ID:MSVC>:/W4;/permissive-;/utf-8>
)
if(TACO_WERROR)
    target_compile_options(taco_objects PRIVATE
        $<$<CXX_COMPILER_ID:GNU,Clang,AppleClang>:-Werror>
        $<$<CXX_COMPILER_ID:MSVC>:/WX>
    )
endif()

add_library(taco SHARED $<TARGET_OBJECTS:taco_objects>)
target_link_libraries(taco PRIVATE karu::internal)
target_include_directories(taco PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)

include(GNUInstallDirs)
install(TARGETS taco
    LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
    ARCHIVE DESTINATION ${CMAKE_INSTALL_LIBDIR}
    RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR}
)
install(FILES include/taco/taco.h DESTINATION ${CMAKE_INSTALL_INCLUDEDIR}/taco)

if(TACO_BUILD_TESTS)
    enable_testing()
    # R and Julia bind the C header, so it must compile as C11 too.
    add_library(taco_c_header_check OBJECT tests/test_c_header.c)
    target_include_directories(taco_c_header_check PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
    target_compile_options(taco_c_header_check PRIVATE
        $<$<C_COMPILER_ID:GNU,Clang,AppleClang>:-Wall;-Wextra;-Wpedantic>)

    add_executable(taco_tests tests/test_core.cpp)
    add_dependencies(taco_tests taco_c_header_check)
    target_link_libraries(taco_tests PRIVATE taco_objects)
    target_include_directories(taco_tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/src)
    target_compile_definitions(taco_tests PRIVATE
        TACO_TEST_DATA="${CMAKE_CURRENT_SOURCE_DIR}/tests/data")
    add_test(NAME core COMMAND taco_tests)
endif()
