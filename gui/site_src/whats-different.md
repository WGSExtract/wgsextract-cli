---
output: 'whats-different.html'
title: 'WGSExtract v4/5 to v6 Major Changes'
description: 'The major changes between WGSExtract v4/v5 and the v6 desktop app and CLI.'
eyebrow: 'What''s Different'
heading: 'WGSExtract v4/5 to v6 Major Changes'
lede: 'What changed between the WGSExtract v4 and v5 releases and the new v6 desktop app and CLI.'
actions: 'Download v6|index.html|primary; WGSExtract CLI|https://wgsextract.github.io/wgsextract-cli/'
footer_title: 'What''s Different'
footer_text: 'Major changes from WGSExtract v4/5 to v6.'
---

::: section
::: wrap
## What we did

Key is, we started with the application that we all know and love. And greatly extended it for the better in many ways.

First, we polished off the started CLI interface to the old program.  Something we have been wanting for years so the tool could be run on a server driven by scripts. Then we stripped out and dropped the GUI which we had been slowly pulling apart. Finally, we used coding tools to rewrite the tool, check it into GitHub for revision control, and add test cases to verify everything was working.  Outputs were changed to JSON files in addition to writing to the terminal, for convenience.  This CLI-only backend is still called "wgsextract".

Next, we did experimentation with different front-ends and universal installers.  Settling on using WebUI and Tauri for the GUI and Pixi to replace the various package managers and environments on the different platforms (Macports, Homebrew, Bioconda, etc). The GUI is now consistent, modern and full featured.

As part of this, we overhauled the installer scripts to handle the change.  And split them into a CLI installer and GUI App installer.  The GUI App installer checks if the CLI is already installed. If not, it installs that as well. This provides dedicated installers for each platform as opposed to downloading a common .zip and learning how to start the installer script for your platform.

Then we started going "hog wild" as they say.  Doing Vibe coding to greatly expand the features available. Adding lots of new transformations and tools to the platform.  In some cases as alternate methods. In others to support new sequencers like Ultima Genomics. As part of this, the AI was trained to figure out how to optimize many of the common bioinformatic tools on the various platforms. Gaining significant performance with no change in behavior. Adding graphical engine accelerators for those with Nvidia graphic cards or Apple Mn processors with built in neural engines. This type of work continues. As a result, the features available vary a lot by platform now.  We tried to make all platforms the same but had to break this rule to enable new features.  For example, VEP is not yet available on Windows.

As all these new tools and options really crowded the GUI, so we added a Basic and Advanced mode to the GUI.  In Basic, you are back to the tool you knew and loved. With many automatic, behind the scene features to enable what seem like simple tasks.  Changing to Advanced exposes all the new features -- new options to refine behavior as well as new features added to the tool.  It is complete with regard to the CLI capabilities.

Beside checking the validity of the files you received back from the testing lab, probably the next most used feature is the Microarray file generator.  Still so much of the market works off those files. We did a lot of cleanup there but most is only enabled by options in the Advanced mode.  More is coming.  But key is files generated should better match actual chip microarray tests coming back from the lab.

Probably the biggest operational change is we removed the blocking in the GUI.  Preventing the need to start multiple copies of the software. You can now execute commands in parallel directly.  Each firing off the CLI back-end separately and in parallel. We also removed the need to have the separate, continuous scroll command window showing the scripts of what is executing. Instead, each action in the GUI creates its own scroll window capture. These are hidden by default but easily brought back into view.  When there is a problem, the Settings pane now has an ability to automatically report a problem. It can capture the command screen and generate a GitHub issue directly. There are now AI agents watching and continuously working on the Issues page in the background.

:::
:::
