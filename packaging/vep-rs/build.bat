@echo on
:: Portable CPU baseline (see build.sh).
if exist .cargo\config.toml del .cargo\config.toml
cargo build --release --locked --bin vep --bin vep-cache-builder
if errorlevel 1 exit 1
:: conda's Rust sets CARGO_BUILD_TARGET, which moves the output folder.
set "OUT=target\release"
if defined CARGO_BUILD_TARGET set "OUT=target\%CARGO_BUILD_TARGET%\release"
if not exist "%LIBRARY_BIN%" mkdir "%LIBRARY_BIN%"
copy "%OUT%\vep.exe" "%LIBRARY_BIN%\vep-rs.exe"
if errorlevel 1 exit 1
copy "%OUT%\vep-cache-builder.exe" "%LIBRARY_BIN%\vep-cache-builder.exe"
if errorlevel 1 exit 1
