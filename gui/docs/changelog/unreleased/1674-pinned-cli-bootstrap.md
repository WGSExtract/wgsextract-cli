## Fixed

- Setup can initialize the reference library with the bundled v0.8.10 CLI,
  which predates the new network-mode options. Local commands still run in
  Low Data and Off modes; the bundled CLI uses its own download consent
  ([#1674, bundled CLI setup](https://github.com/WGSExtract/wgsextract-cli/issues/1674)).
