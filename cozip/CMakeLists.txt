cmake_minimum_required(VERSION 3.16)
project(coz LANGUAGES C VERSION 0.0.1)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

option(COZ_BUILD_SHARED "Build shared library" ON)

if (COZ_BUILD_SHARED)
    add_library(coz SHARED src/coz.c)
else()
    add_library(coz STATIC src/coz.c)
endif()

target_include_directories(coz PUBLIC
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
    $<INSTALL_INTERFACE:include>
)

if (MSVC)
    target_compile_options(coz PRIVATE /W4)
else()
    target_compile_options(coz PRIVATE -Wall -Wextra -Wpedantic)
endif()

install(TARGETS coz
    LIBRARY DESTINATION lib
    ARCHIVE DESTINATION lib
    RUNTIME DESTINATION bin
)
install(FILES include/coz.h DESTINATION include)
