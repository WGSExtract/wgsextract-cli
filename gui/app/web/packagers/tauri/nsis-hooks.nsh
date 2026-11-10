!macro NSIS_HOOK_PREINSTALL
  ; Releases up to 0.8.6 named the app's executable gui-for-cli-webui-tauri.exe. Tauri deletes that file
  ; and retargets the shortcuts when installing over one, but only checks whether the new executable runs,
  ; so close the old one first or it stays behind, locked, next to the new one.
  !insertmacro CheckIfAppIsRunning "$INSTDIR\gui-for-cli-webui-tauri.exe" "${PRODUCTNAME}"

  ; Releases up to 0.8.6 had another identifier, so their install folder is recorded under another
  ; manufacturer key, which this installer doesn't read. Installed in another folder than this one, the old
  ; app would stay there: remove it as an update does (its setup's tools and the user's data stay; the
  ; app moves them itself), then its key.
  ReadRegStr $R9 SHCTX "Software\guiforcli\${PRODUCTNAME}" ""
  ${If} $R9 != ""
  ${AndIf} $R9 != $INSTDIR
  ${AndIf} ${FileExists} "$R9\uninstall.exe"
    !insertmacro CheckIfAppIsRunning "$R9\gui-for-cli-webui-tauri.exe" "${PRODUCTNAME}"
    DetailPrint "Removing the earlier version in $R9..."
    ExecWait '"$R9\uninstall.exe" /S /UPDATE _?=$R9'
    Delete "$R9\uninstall.exe"
    RmDir "$R9"
  ${EndIf}
  DeleteRegKey SHCTX "Software\guiforcli\${PRODUCTNAME}"
  DeleteRegKey /ifempty SHCTX "Software\guiforcli"
  DeleteRegKey HKCU "Software\guiforcli\${PRODUCTNAME}"
  DeleteRegKey /ifempty HKCU "Software\guiforcli"
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  ${If} $UpdateMode <> 1
    ; Remove what the app's setup installed (for WGSExtract: its CLI runtime, and the Pixi and MSYS2 it
    ; installed), with the bundle's own uninstall steps, as the app's uninstall runs them. The workspace,
    ; with the user's libraries and outputs, goes only if the user says so; a silent uninstall keeps it.
    ; Not while the app runs (Tauri checks this again right after the hook).
    !insertmacro CheckIfAppIsRunning "$INSTDIR\${MAINBINARYNAME}.exe" "${PRODUCTNAME}"
    nsExec::ExecToStack '"$INSTDIR\node\node.exe" "$INSTDIR\app\dist\web\src\server\bundle-uninstall-cli.js" --print-workspace "$INSTDIR\examples\EmbeddedBundle"'
    Pop $0
    Pop $1
    StrCpy $2 ""
    ${If} $0 == 0
    ${AndIf} $1 != ""
      ${If} ${Cmd} `MessageBox MB_ICONQUESTION|MB_YESNO|MB_DEFBUTTON2 "Also delete ${PRODUCTNAME}'s workspace folder?$\r$\n$\r$\n$1$\r$\nThis deletes the Reference and Genome Libraries, your outputs, logs and settings kept there, including any shared with other users. Choose No to keep them." /SD IDNO IDYES`
        StrCpy $2 "--remove-workspace"
      ${EndIf}
    ${EndIf}
    StrCpy $3 ""
    ${If} $DeleteAppDataCheckboxState = 1
      ; The app's data folder (%LOCALAPPDATA%\WGSExtract-gui: its settings, and any workspace the user keeps
      ; there). After this hook Tauri deletes the webview's and the logs' folders, named for the identifier.
      StrCpy $3 "--remove-app-data"
    ${EndIf}
    DetailPrint "Removing what the app's setup installed..."
    nsExec::ExecToLog '"$INSTDIR\node\node.exe" "$INSTDIR\app\dist\web\src\server\bundle-uninstall-cli.js" $2 $3 "$INSTDIR\examples\EmbeddedBundle"'
    Pop $0
    ${If} $0 != 0
      DetailPrint "Removing what the app's setup installed failed ($0)."
      MessageBox MB_ICONEXCLAMATION|MB_OK "Some of what ${PRODUCTNAME}'s setup installed could not be removed. Click Show details for what failed." /SD IDOK
      SetErrorLevel 2
    ${EndIf}
  ${EndIf}
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
  ${If} $UpdateMode <> 1
    SetShellVarContext current

    Delete /REBOOTOK "$INSTDIR\uninstall.exe"
    RmDir /REBOOTOK "$INSTDIR"
    RmDir /REBOOTOK "$LOCALAPPDATA\${PRODUCTNAME}"
    RmDir /REBOOTOK "$LOCALAPPDATA\Programs\${PRODUCTNAME}"
  ${EndIf}
!macroend
