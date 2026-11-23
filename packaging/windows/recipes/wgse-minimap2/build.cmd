@echo off
setlocal
rem vs_win-64 activation calls vcvars; match the static CRT in zlib and the aligner.
cmake -S zlib -B zlib-build -G Ninja -DCMAKE_BUILD_TYPE=Release ^
    -DCMAKE_C_COMPILER=clang-cl -DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded ^
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5 -DZLIB_BUILD_EXAMPLES=OFF || exit /b 1
cmake --build zlib-build --target zlibstatic --parallel 2 || exit /b 1
copy /y "zlib\zlib.h" "zlib-build\zlib.h" >nul || exit /b 1

cmake -S native -B build -G Ninja -DCMAKE_BUILD_TYPE=Release ^
    -DCMAKE_C_COMPILER=clang-cl -DCMAKE_CXX_COMPILER=clang-cl -DCMAKE_LINKER=lld-link ^
    -DCMAKE_CUDA_COMPILER=nvcc.exe -DCMAKE_CUDA_HOST_COMPILER=cl.exe ^
    -DWGSE_CUDA=ON -DWGSE_BWA=OFF -DWGSE_MINIMAP2=ON -DBUILD_TESTING=ON ^
    -DZLIB_LIBRARY="%SRC_DIR%\zlib-build\zlibstatic.lib" -DZLIB_INCLUDE_DIR="%SRC_DIR%\zlib-build" ^
    -DFETCHCONTENT_SOURCE_DIR_MINIMAP2="%SRC_DIR%\minimap2" -DFETCHCONTENT_FULLY_DISCONNECTED=ON || exit /b 1
cmake --build build --parallel 2 || exit /b 1

rem Test with no visible GPU, including on hosted runners with no driver at all.
set "CUDA_VISIBLE_DEVICES=-1"
ctest --test-dir build --output-on-failure || exit /b 1

rem Exclude test-only failure injection from the shipped executable.
cmake -S native -B build -DBUILD_TESTING=OFF || exit /b 1
cmake --build build --parallel 2 || exit /b 1
rem A CUDA DLL in the import table would prevent CPU startup without the driver.
dumpbin /dependents "build\minimap2\wgse-minimap2.exe" > "build\minimap2\imports.txt" || exit /b 1
findstr /i /r /c:"nvcuda[.]dll" /c:"cudart.*[.]dll" "build\minimap2\imports.txt" && exit /b 1
cmake --install build --prefix "%PREFIX%\Library" || exit /b 1
