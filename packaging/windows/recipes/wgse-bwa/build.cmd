@echo off
setlocal
rem vs_win-64 activation finds the installed VS 2022 Build Tools and calls vcvars.
where cl.exe || exit /b 1
where nvcc.exe || exit /b 1
where clang-cl.exe || exit /b 1
where lld-link.exe || exit /b 1

rem Build our own static /MT zlib to match the executable's CRT.
cmake -S zlib -B zlib-build -G Ninja -DCMAKE_BUILD_TYPE=Release ^
    -DCMAKE_C_COMPILER=clang-cl -DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded ^
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5 -DZLIB_BUILD_EXAMPLES=OFF || exit /b 1
cmake --build zlib-build --target zlibstatic --parallel 8 || exit /b 1
copy /y "zlib\zlib.h" "zlib-build\zlib.h" >nul || exit /b 1

cmake -S native -B build -G Ninja -DCMAKE_BUILD_TYPE=Release ^
    -DCMAKE_C_COMPILER=clang-cl -DCMAKE_CXX_COMPILER=clang-cl -DCMAKE_LINKER=lld-link ^
    -DCMAKE_CUDA_COMPILER=nvcc.exe -DCMAKE_CUDA_HOST_COMPILER=cl.exe ^
    -DWGSE_CUDA=ON -DBUILD_TESTING=ON -DZLIB_LIBRARY="%SRC_DIR%\zlib-build\zlibstatic.lib" ^
    -DZLIB_INCLUDE_DIR="%SRC_DIR%\zlib-build" -DFETCHCONTENT_SOURCE_DIR_BWA="%SRC_DIR%\bwa" ^
    -DFETCHCONTENT_SOURCE_DIR_MEM3="%SRC_DIR%\mem3" -DFETCHCONTENT_FULLY_DISCONNECTED=ON || exit /b 1
cmake --build build --parallel 8 || exit /b 1

rem This is a CPU/no-driver smoke even if the build host has a GPU.
set "CUDA_VISIBLE_DEVICES=-1"
ctest --test-dir build -R "^bwa_(release_dense_sa|capabilities|smoke)$" --output-on-failure || exit /b 1

rem Don't ship the binary with the benchmark/failure-injection flags enabled.
cmake -S native -B build -DBUILD_TESTING=OFF || exit /b 1
cmake --build build --parallel 8 || exit /b 1
cmake --install build --prefix "%PREFIX%\Library" || exit /b 1
