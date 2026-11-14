# CI compiler caches

Expensive Tauri builds use `.github/actions/rust-cache` (SHA-pinned
Swatinem/rust-cache); native builds use `.github/actions/compiler-cache`.
Keep these steps in the compile job when moving a workflow to a reusable
workflow. Neither helper changes triggers or concurrency.

## Trust and release builds

Production caches are written only by successful main builds. Pull requests
restore only. Native caching splits `actions/cache/restore` and
`actions/cache/save`; Rust caching sets `save-if` in the shared wrapper.
The reusable GUI build additionally refuses to write when a caller requests
an arbitrary older commit or a release tag.

GitHub caches are scoped to the creating ref. Tagged release runs can restore
main's objects, **not** caches from a PR merge ref or a different branch.
ccache revalidates compiler contents, source, headers and flags; no sloppiness
is enabled. Rust retains dependencies only, not workspace binaries, installers,
Cargo-installed tools or signing credentials. Compilation/linking and all
existing artifact tests still run; a cache hit never skips those checks.

The owner-authorized measurement exception is limited to `workflow_dispatch`
on `ci/916-compiler-cache` by `theontho`. It uses `measure-916-*` keys, with no
production fallback prefix, so even main or tagged runs cannot consume it.
Remove those experimental entries through the Actions cache UI after measuring.

## Keys and budget

Native keys contain OS, architecture, compiler version/target (and macOS SDK),
build family and recipe hash. ccache also hashes the actual compiler binary,
including the conda compiler when a recipe selects one. Recipes include
toolchain variant manifests in their keys; changing a recipe forces a cold
build. Rattler uses `--no-build-id` to avoid timestamped work paths defeating
object reuse, without caching its build/host environments.

Each native family is capped and cleaned to **128 MB** before saving:
Linux CPU aligners (two architectures), macOS aligners, Linux/macOS DeepVariant,
Windows tools and GLIMPSE2: seven entries, at most about **0.9 GB** per recipe
generation before archive compression. Typical Tauri dependency archives are
estimated at **150–400 MB/platform**, four platforms shared across distribution
and installer jobs (about **0.6–1.6 GB**). These are estimates, not measured
archive sizes; inspect the action summaries and Actions cache UI after seeding.
Rust's own pruning drops workspace crates and incremental output; its target
cache is not a hard size cap.

The repository was already at roughly 10 GiB / 76 entries during the inventory
on 2026-11-14. GitHub evicts least-recently-used caches at the repository's 10 GB
limit. This rollout reserves roughly 2.5 GB of that budget; it does not claim
extra quota or silently remove other workflows' caches. Monitor eviction and
hit rates before widening coverage or retaining multiple recipe generations.
There are no run-id/SHA-per-push native keys or broad restore prefixes.

## Inventory exclusions

The full dated main-run inventory and raw job timings are in `out/916/baseline.md`
(gitignored evidence). These workflows intentionally do not get new caches:

| Workflow | Reason |
|---|---|
| `gui-distribution.yml` | Compilation already delegates to cached `gui-build.yml`. |
| `gatk_windows.yml` | Uses installed GATK/GKL; only compiles a small Java parity probe, not native kernels. |
| `linux_bwa_mem3.yml` | Tests installed Bioconda binaries, no compilation. |
| `gkl_windows.yml` | Native package step 31s; installing/restoring a cache is unlikely to pay. |
| `mimalloc.yml` | Package step 17s; cache setup would cost more than compilation. |
| `linux_aligners.yml` package/CUDA jobs | Package steps 17–87s; conda/CUDA setup dominates. First cache the CPU development jobs (45–140s). |
| `aligner_bench.yml` | Arbitrary refs, some predate CI helpers; successful compile step 50s. Benchmark execution dwarfs compilation. |
| `windows_arm64.yml` | Mixed MSVC/vcpkg/source builds; latest main run never reached native packaging. Requires separate toolchain-specific cache qualification. |
| `vep_rs.yml` | No recent main run; isolated conda Rust workspace/toolchain is created after setup, unlike Tauri. Needs separate target-cache integration and measurements before enabling. |

The rollout does not promise a 40% end-to-end improvement for workflows whose
tests, source downloads or toolchain installation dominate. Compare the same
ref on a cold and warm dispatch, build-step wall time separately from total
job time, and report actual hits/bytes rather than assuming a restored archive
was useful. All scientific smoke/parity tests remain unchanged.
