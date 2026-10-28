cmake_minimum_required(VERSION 3.20)

project(launch C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)

if(MSVC)
    add_compile_options(/W3 /utf-8)
    add_compile_definitions(_CRT_SECURE_NO_WARNINGS UNICODE _UNICODE)
endif()

if(WIN32)
    set(EXESOURCE shlaunchWin/shlaunch)
    set(MYDEFINES UNICODE _UNICODE _WIN32)
    set(MYLIBS psapi netapi32)

    add_library(launch SHARED launch.c)
    target_compile_definitions(launch PRIVATE ${MYDEFINES})
    target_include_directories(launch PUBLIC ${EXESOURCE})
    target_link_libraries(launch PRIVATE ${MYLIBS})

    add_executable(shlaunch ${EXESOURCE}/shlaunch.c)
    target_include_directories(shlaunch PUBLIC ${EXESOURCE})
    target_compile_definitions(shlaunch PRIVATE ${MYDEFINES})
    target_link_libraries(shlaunch PRIVATE ${MYLIBS})

    install(TARGETS shlaunch launch
            RUNTIME DESTINATION bin
            LIBRARY DESTINATION lib)
elseif(APPLE)
    set(REV_HEADER ${CMAKE_CURRENT_BINARY_DIR}/shlaunch_build.h)
    add_custom_command(
        OUTPUT  ${REV_HEADER}
        COMMAND ${CMAKE_CURRENT_LIST_DIR}/../scripts/generateShsudoHeader.sh ${REV_HEADER}
        COMMENT "Generating ${REV_HEADER}"
    )
    set(SHLAUNCH_SUDO SighthoundVideoLauncher)
    add_executable(${SHLAUNCH_SUDO} shsudoMac/shsudoMac/shsudoMac/shsudo.m ${REV_HEADER})
    target_include_directories(${SHLAUNCH_SUDO} PRIVATE ${CMAKE_CURRENT_BINARY_DIR})
    target_link_libraries(${SHLAUNCH_SUDO} "-framework Foundation" "-framework Security")
    target_link_options(${SHLAUNCH_SUDO} PRIVATE -lobjc)

    set(EXESOURCE shlaunchMac/shlaunchMac)
    add_library(launch SHARED launch.c ${REV_HEADER})
    target_include_directories(launch PUBLIC ${EXESOURCE} ${CMAKE_CURRENT_BINARY_DIR})
    target_link_libraries(launch PRIVATE iconv)

    add_executable(shlaunch ${EXESOURCE}/shlaunch.c ${REV_HEADER})
    target_include_directories(shlaunch PUBLIC ${EXESOURCE} ${CMAKE_CURRENT_BINARY_DIR})
    target_link_libraries(shlaunch PRIVATE iconv)

    install(TARGETS shlaunch launch ${SHLAUNCH_SUDO}
            RUNTIME DESTINATION bin
            LIBRARY DESTINATION lib)
else()
    # Linux: the Windows launcher includes windows.h and the Mac launcher
    # includes Foundation. The shared library itself is portable POSIX.
    add_library(launch SHARED launch.c)
    target_compile_definitions(launch PRIVATE _GNU_SOURCE)
    target_include_directories(launch PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}/shlaunchMac/shlaunchMac)
    find_library(LIBRT rt)
    if(LIBRT)
        target_link_libraries(launch PRIVATE ${LIBRT})
    endif()
    install(TARGETS launch LIBRARY DESTINATION lib)
endif()
