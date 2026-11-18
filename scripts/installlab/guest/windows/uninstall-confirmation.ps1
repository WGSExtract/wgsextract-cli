function Get-WorkspaceRemovalAnswer([string]$Text, [bool]$RemoveUsersWorkspace) {
    if ($Text -notmatch "(?s)Also delete\s+.*(?:users workspace|workspace folder)\?") {
        return $null
    }
    if ($RemoveUsersWorkspace) { return "Yes" }
    return "No"
}
