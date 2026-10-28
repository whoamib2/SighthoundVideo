cmake_minimum_required(VERSION 3.20)

project(xnat C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)

if(MSVC)
    add_compile_options(/W3 /utf-8)
    add_compile_definitions(_CRT_SECURE_NO_WARNINGS UNICODE _UNICODE)
endif()

find_path(MINIUPNPC_INCLUDE_DIR miniupnpc/miniupnpc.h
    HINTS ${CONAN_INCLUDE_DIRS_MINIUPNPC})
find_path(NATPMP_INCLUDE_DIR natpmp.h
    HINTS ${CONAN_INCLUDE_DIRS_LIBNATPMP})
find_library(MINIUPNPC_LIBRARY NAMES miniupnpc libminiupnpc
    HINTS ${CONAN_LIB_DIRS_MINIUPNPC})
find_library(NATPMP_LIBRARY NAMES natpmp libnatpmp
    HINTS ${CONAN_LIB_DIRS_LIBNATPMP})

if(NOT MINIUPNPC_INCLUDE_DIR OR NOT NATPMP_INCLUDE_DIR
        OR NOT MINIUPNPC_LIBRARY OR NOT NATPMP_LIBRARY)
    message(STATUS
        "xnat skipped: miniupnpc and libnatpmp are required "
        "(install libminiupnpc-dev and libnatpmp-dev, or point CMake at a Conan prefix)")
    return()
endif()

if(WIN32)
    set(TARGET_NAME "SighthoundXNAT")
    add_executable(${TARGET_NAME} xnat.c minidump.c)
    set(WINLIB iphlpapi ws2_32)
else()
    set(TARGET_NAME "xnat")
    add_library(${TARGET_NAME} SHARED xnat.c)
    target_compile_definitions(${TARGET_NAME} PUBLIC __XNAT_DYLIB)
    set(WINLIB "")
endif()

target_compile_definitions(${TARGET_NAME} PUBLIC MINIUPNP_STATICLIB=1 NATPMP_STATICLIB=1)
target_include_directories(${TARGET_NAME} PRIVATE
    ${MINIUPNPC_INCLUDE_DIR} ${NATPMP_INCLUDE_DIR})
target_link_libraries(${TARGET_NAME} PRIVATE
    ${MINIUPNPC_LIBRARY} ${NATPMP_LIBRARY} ${WINLIB})

install(TARGETS ${TARGET_NAME}
        RUNTIME DESTINATION bin
        LIBRARY DESTINATION lib)
