# shellcheck shell=sh disable=SC2034,SC2154
# Sourced by the GPU parity tests: picks the GPU backend this build and machine
# can use, from `$aligner capabilities --json`, as $gpu (its option) and
# $gpu_name (its name in messages). Exits 77 (skip) on machines without a
# supported GPU, and fails if one is present but its backend does not start.
# Callers set $gpu_request to an option (--metal, --cuda, --vulkan) to test that
# backend only, and $gpu_needs=all when they test seeding or rescue, which the
# Vulkan backend leaves to the CPU.
"$aligner" capabilities --json > capabilities.json
if [ -n "${gpu_request:-}" ]; then
    gpu=$gpu_request
    backend=${gpu_request#--}
    case $backend in
        metal) gpu_name=Metal ;;
        cuda) gpu_name=CUDA ;;
        vulkan) gpu_name=Vulkan ;;
        *) exit 2 ;;
    esac
    [ "$backend" = vulkan ] && [ "${gpu_needs:-}" = all ] && exit 77
    if ! grep -q "\"${backend}_available\":true" capabilities.json; then
        grep -q "\"${backend}_hardware_supported\":true" capabilities.json && exit 1
        exit 77
    fi
elif grep -q '"metal_available":true' capabilities.json; then
    gpu=--metal
    gpu_name=Metal
elif grep -q '"cuda_available":true' capabilities.json; then
    gpu=--cuda
    gpu_name=CUDA
elif [ "${gpu_needs:-}" != all ] && grep -q '"vulkan_available":true' capabilities.json; then
    gpu=--vulkan
    gpu_name=Vulkan
else
    grep -Eq '"(metal|cuda)_hardware_supported":true' capabilities.json && exit 1
    [ "${gpu_needs:-}" != all ] && grep -q '"vulkan_hardware_supported":true' capabilities.json \
        && exit 1
    exit 77
fi
