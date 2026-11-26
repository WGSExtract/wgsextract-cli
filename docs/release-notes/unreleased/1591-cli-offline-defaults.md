## Fixes

- `ref microarray-targets` now names any missing liftOver chain and asks for
  `--download` instead of fetching it automatically.
- DeepVariant and Parabricks no longer let Docker fetch an image that
  disappears between the dependency check and launch. Install missing images
  separately with `docker pull`.
- The interactive reference library resumes an interrupted download only
  when you choose Resume; pressing Enter cancels.
