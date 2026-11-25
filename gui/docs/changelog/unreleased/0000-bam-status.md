## Changed

- In Basic mode, the BAM page's Sort and Index buttons stay visible but are
  disabled, with a tooltip saying why, when the file is already coordinate
  sorted or indexed. Sorted is now read from the file's header (`SO:coordinate`)
  rather than guessed from its name, and the status refreshes after a command
  finishes or the mode changes.
- Outputs go to a `WGSEv6/` folder beside the input, with manifests in
  `WGSEv6/manifests/`; existing `wgse/` folders keep being used.
