## Fixed

- The genome viewer's file endpoints answer a `HEAD` without a `Range` with `200` and the file's full size for files over 32 MiB too, instead of `416`; whole-file `GET`s keep their 32 MiB limit ([#1227](https://github.com/WGSExtract/wgsextract-cli/issues/1227)).
