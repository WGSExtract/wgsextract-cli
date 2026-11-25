"""Messages for the deps commands."""

CLI_HELP = {
    "cmd_check-deps": "Verify that all required bioinformatics tools (samtools, bcftools, etc.) are installed and accessible in your system path.",
    "cmd_deps": "Check, list, install and remove the external tools, and check whether a command can run offline.",
    "cmd_deps-list": "List every mandatory and optional tool with its status, and whether 'deps install' or 'deps uninstall' can act on it.",
    "cmd_deps-ready": "Report whether a command line can run on this computer without the network: its tools, images, GPUs, model weights, input, reference genome and indexes, checked offline without installing or downloading anything.",
    "cmd_deps-install": "Download and install the Pixi environment that provides an optional tool (e.g. gatk).",
    "cmd_deps-uninstall": "Remove the Pixi environment that provides an optional tool, and the other tools in it.",
}
