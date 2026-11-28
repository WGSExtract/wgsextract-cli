## Added

- Setup's first step checks for an app update. The desktop app's startup
  check fills it in, and **Rerun Setup** checks again, so one click shows
  whether the app itself needs an update.

## Changed

- **Standard UI Options** can be collapsed and starts collapsed. **Users
  Workspace Settings** is now available in Basic mode too, collapsed by default.
- The app version under its name turns red when it is out of date. A
  development run shows the latest release's version followed by **Dev**, and
  always counts as out of date.
- **Network mode** moved to the bottom of **Setup**, with its explanation in
  a tooltip. **Off** is now the single switch for all network activity,
  including the update checks at startup and during setup, and the top-left
  badge reads **NETWORK OFF** while it is on. Below it, **Allow check for
  updates** (on by default, turned off by **Not now**) stops only the update
  checks, so **Low** can run without them.
- The first-launch dialog explains that **Not now** leaves WGS Extract unable
  to run commands until setup runs, and that only Network mode Off blocks all
  network activity.
- The users workspace location now heads **Users Workspace Settings** as
  **Current {{usersWorkspace}}** and **Change {{usersWorkspace}}**.
- **Output Directory** shows `{{inputs}}/WGSEv6` while it is empty, as a
  reminder of the default.

## Removed

- The **Privacy mode** checkbox and the **Network & Privacy** section.
  **Check for updates at launch** became **Allow check for updates** and keeps
  its saved choice; Privacy mode on reads as unchecked.
- The **Reference Genome**, **Input VCF**, **Mother VCF**, **Father VCF** and
  **Haplogrep JAR Path** settings. Choose inputs on each command's page;
  Haplogrep is part of the installed CLI.
