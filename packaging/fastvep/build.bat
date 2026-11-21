@echo on
:: Use the conda compiler's portable baseline, not host-specific CPU features.
if exist .cargo\config.toml del .cargo\config.toml
set "CARGO_PROFILE_RELEASE_STRIP=none"
cargo-bundle-licenses --format yaml --output THIRDPARTY.yml
if errorlevel 1 exit 1
cargo build --release --locked --bin fastvep
if errorlevel 1 exit 1
set "OUT=target\release"
if defined CARGO_BUILD_TARGET set "OUT=target\%CARGO_BUILD_TARGET%\release"
if not exist "%LIBRARY_BIN%" mkdir "%LIBRARY_BIN%"
copy "%OUT%\fastvep.exe" "%LIBRARY_BIN%\fastvep.exe"
if errorlevel 1 exit 1
