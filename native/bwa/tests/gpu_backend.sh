# shellcheck shell=sh disable=SC2034,SC2154
# Sourced by the GPU parity tests: picks the GPU backend this build and machine
# can use, from `$aligner capabilities --json`, as $gpu (its option) and
# $gpu_name (its name in messages). Exits 77 (skip) on machines without a
# supported GPU, and fails if one is present but its backend does not start.
"$aligner" capabilities --json > capabilities.json
if grep -q '"metal_available":true' capabilities.json; then
    gpu=--metal
    gpu_name=Metal
elif grep -q '"cuda_available":true' capabilities.json; then
    gpu=--cuda
    gpu_name=CUDA
else
    grep -Eq '"(metal|cuda)_hardware_supported":true' capabilities.json && exit 1
    exit 77
fi
