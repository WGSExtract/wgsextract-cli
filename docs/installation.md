# CLI installation and removal

For the platform walkthrough, see the [site install guide](https://wgsextract.github.io/wgsextract-cli/install.html).
For source development, use [developer setup](developer_setup.md) instead.

## Install

macOS and Linux x86_64 or native ARM64 (`linux-aarch64`):

```bash
curl -fsSL https://raw.githubusercontent.com/WGSExtract/wgsextract-cli/main/install.sh | sh
```

The bootstrap script comes from `main`, but installs the latest published
GitHub release, not `main`. It creates `wgsextract-cli/` in the current
directory (or beside a downloaded `install.sh`) with the app in `app/`,
Pixi files in `.pixi/`, and the `wgsextract` launcher and `uninstall.sh`.
On macOS it checks for Xcode Command Line Tools first.

Linux ARM64 phase 1 supports default alignment/CRAM/calling/export, VEP,
native Yleaf-derived Y-DNA analysis and Haplogrep 3. GATK fails before
execution because its native kernels are not yet qualified. DeepVariant has
an explicit [native CPU source-build path](deepvariant-arm64.md); there is
no silent Java or x86-emulation fallback. See [Linux ARM64](linux-arm64.md).
Releases v0.6.5 and earlier lack ARM64 support. To test unreleased support
instead of a release, select `main` explicitly:

```bash
curl -fsSL https://raw.githubusercontent.com/WGSExtract/wgsextract-cli/main/install.sh | WGSEXTRACT_REF=main sh
```

On Windows, clone or download the repository and run `install_windows.bat`
from PowerShell. It installs Pixi if missing, then the CLI and WGS Extract's
native builds of samtools, bcftools, BWA, bwa-mem2, minimap2, fastp and
samblaster into Pixi. No WSL or MSYS2 is needed. See [Windows](windows.md).

## Installer options

These are installer inputs, not runtime CLI configuration. Set them before
running `install.sh`:

| Variable | Default | Purpose |
| :--- | :--- | :--- |
| `WGSEXTRACT_INSTALL_DIR` | `wgsextract-cli` next to `install.sh`, or `./wgsextract-cli` for `curl \| sh` | Install location |
| `WGSEXTRACT_RELEASE_TAG` | latest GitHub release | Release tag, or `latest` |
| `WGSEXTRACT_REF` | `WGSEXTRACT_RELEASE_TAG` | Git ref, or `latest` |
| `WGSEXTRACT_ARCHIVE_URL` | GitHub source archive for `WGSEXTRACT_REF` | Exact source archive URL |
| `WGSEXTRACT_BIN_DIR` | `$WGSEXTRACT_INSTALL_DIR` | CLI launcher directory |
| `WGSEXTRACT_PIXI_HOME` | `$WGSEXTRACT_INSTALL_DIR/.pixi` when Pixi is missing | Install SHA-256-verified pinned Pixi here; an existing Pixi on `PATH` or in `~/.pixi` is reused |
| `WGSEXTRACT_PIXI_CACHE_DIR` | `$WGSEXTRACT_INSTALL_DIR/.pixi/cache` | Pixi package cache |
| `WGSEXTRACT_PIXI_ENV_DIR` | `$WGSEXTRACT_INSTALL_DIR/app/.pixi/envs` | Pixi environments, kept across updates |
| `WGSEXTRACT_NO_OPEN` | unset | `1` skips opening Finder on macOS |

Leave `WGSEXTRACT_BIN_DIR`, `WGSEXTRACT_PIXI_HOME`,
`WGSEXTRACT_PIXI_CACHE_DIR` and `WGSEXTRACT_PIXI_ENV_DIR` unset to keep the
installation together. Pin `WGSEXTRACT_RELEASE_TAG=vX.Y.Z` for reproducible
installs; use `WGSEXTRACT_REF=main` or `WGSEXTRACT_ARCHIVE_URL=<url>` for testing.

When Pixi is missing, installers verify the pinned release's SHA-256 before
running it; they do not execute unsigned upstream installer scripts.
Updating the Pixi pin also requires updating its trusted release digests.
Reference installation has separate [sequence-dictionary checks](reference-genomes.md#verification-and-gene-maps).

## Running

| Install type | Command |
| :--- | :--- |
| `install.sh` | `./wgsextract-cli/wgsextract`, or add `wgsextract-cli/` to `PATH` |
| Source checkout or Windows install | `pixi run wgsextract`, from the repository directory |

Guides write plain `wgsextract`; substitute your launcher.
[Optional environments](developer_setup.md#pixi-environments) require explicit
setup; the CLI never installs tools during a feature run.

## Uninstall

```bash
./wgsextract-cli/uninstall.sh --yes
```

This removes the installation, launcher, Pixi environments and cache under
it, **including reference data in the default `app/reference/` location**.
Updating by rerunning the installer instead keeps `app/reference/`,
`app/genomes/` and Pixi environments.

Configuration is kept unless `--remove-config` is given. Interactive runs
ask whether to remove Pixi from `~/.pixi`; `--yes` keeps it unless you add
`--remove-pixi`. That also removes Pixi's `PATH` line from shell profiles,
and deletes a profile such as `~/.zshrc` only if installation created it and
nothing else was added.

Custom `WGSEXTRACT_BIN_DIR`, `WGSEXTRACT_PIXI_CACHE_DIR` and
`WGSEXTRACT_PIXI_ENV_DIR` locations are recorded at install time.
`--bin-dir`, `--pixi-cache-dir` and `--pixi-env-dir` override those records.
**Recorded cache and environment directories outside the installation are
also deleted by default.** Use `--keep-pixi-cache` and `--keep-pixi-envs`
to preserve those external directories; these flags do not preserve files
inside the installation being removed.
Windows has its [own uninstaller](windows.md).

On every platform, `--remove-config` includes the owned default users
workspace's `~/wgs-extract/config.toml`
(`%USERPROFILE%\wgs-extract\config.toml` on Windows) and legacy per-user
config locations. An otherwise empty default users workspace loses its
ownership marker and directory too. Libraries, other files and their
workspace marker are preserved; unmarked user folders are left alone.
Without `--remove-config`, workspace configuration is unchanged.
