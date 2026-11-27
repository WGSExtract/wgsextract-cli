use std::{thread, time::Duration};

use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
use tauri_plugin_updater::UpdaterExt;

use crate::update_e2e_overlay;

use super::{env_flag, update_before_exit_hook, write_update_status, AUTO_ACCEPT_UPDATE_ENV};

#[tauri::command]
pub(crate) fn gfc_update_check_from_menu<R: tauri::Runtime>(app: tauri::AppHandle<R>) {
    tauri::async_runtime::spawn(async move {
        let current_version = app.package_info().version.to_string();
        write_update_status("checking");
        update_e2e_overlay::update(
            &app,
            "Checking for updates",
            &format!("Installed version: {current_version}"),
        );
        let updater = match app
            .updater_builder()
            .on_before_exit(update_before_exit_hook(&app))
            .build()
        {
            Ok(updater) => updater,
            Err(error) => {
                write_update_status(&format!("not-configured:{error}"));
                show_update_message(
                    &app,
                    "Updates Not Configured",
                    &format!("This build is not configured for updates: {error}"),
                    MessageDialogKind::Warning,
                );
                return;
            }
        };
        let update = match updater.check().await {
            Ok(update) => update,
            Err(error) => {
                write_update_status(&format!("check-failed:{error}"));
                show_update_message(
                    &app,
                    "Update Check Failed",
                    &format!("Could not check for updates: {error}"),
                    MessageDialogKind::Error,
                );
                return;
            }
        };

        let Some(update) = update else {
            write_update_status("none");
            update_e2e_overlay::update(
                &app,
                "No update available",
                &format!("Installed version: {current_version}"),
            );
            show_update_message(
                &app,
                "No Updates Available",
                "You are already running the latest version.",
                MessageDialogKind::Info,
            );
            return;
        };

        write_update_status(&format!("available:{}", update.version));
        update_e2e_overlay::update(
            &app,
            "Update available",
            &format!(
                "Installed {current_version} -> available {}",
                update.version
            ),
        );
        let prompt = format!(
            "You are running version {current_version}.\n\nVersion {} is available.\n\nDownload, install, and restart now?",
            update.version
        );
        let accepted = if env_flag(AUTO_ACCEPT_UPDATE_ENV) {
            write_update_status("accepted:auto");
            update_e2e_overlay::update(
                &app,
                "Update accepted",
                &format!("Downloading version {}", update.version),
            );
            true
        } else {
            let prompt_app = app.clone();
            match tauri::async_runtime::spawn_blocking(move || {
                prompt_app
                    .dialog()
                    .message(prompt)
                    .title("Update Available")
                    .kind(MessageDialogKind::Info)
                    .buttons(MessageDialogButtons::OkCancelCustom(
                        "Install and Restart".into(),
                        "Not Now".into(),
                    ))
                    .blocking_show()
            })
            .await
            {
                Ok(accepted) => {
                    write_update_status(if accepted {
                        "accepted:user"
                    } else {
                        "declined:user"
                    });
                    let overlay_detail = if accepted {
                        format!("Downloading version {}", update.version)
                    } else {
                        format!("Installed version: {current_version}")
                    };
                    update_e2e_overlay::update(
                        &app,
                        if accepted {
                            "Update accepted"
                        } else {
                            "Update declined"
                        },
                        &overlay_detail,
                    );
                    accepted
                }
                Err(error) => {
                    write_update_status(&format!("prompt-failed:{error}"));
                    show_update_message(
                        &app,
                        "Update Check Failed",
                        &format!("Could not show the update prompt: {error}"),
                        MessageDialogKind::Error,
                    );
                    return;
                }
            }
        };
        if !accepted {
            return;
        }

        update_e2e_overlay::pause_for_review();
        write_update_status("downloading");
        update_e2e_overlay::update(
            &app,
            "Downloading update",
            &format!("Downloading version {}", update.version),
        );
        let progress_app = app.clone();
        let progress_version = update.version.clone();
        let download_complete_app = app.clone();
        let download_complete_version = update.version.clone();
        let mut visible_progress_steps = 0_u8;
        let mut downloaded_bytes = 0_usize;
        match update
            .download_and_install(
                move |chunk_length, content_length| {
                    downloaded_bytes = downloaded_bytes.saturating_add(chunk_length);
                    let total = content_length
                        .map(|length| format!(" of {}", human_bytes(length)))
                        .unwrap_or_default();
                    let progress_percent = content_length
                        .filter(|length| *length > 0)
                        .map(|length| {
                            ((downloaded_bytes as f64 / length as f64) * 100.0).clamp(0.0, 100.0)
                        });
                    update_e2e_overlay::update_with_progress(
                        &progress_app,
                        "Downloading update",
                        &format!(
                            "Downloading version {progress_version}: {}{total}",
                            human_bytes(downloaded_bytes as u64)
                        ),
                        progress_percent,
                    );
                    if update_e2e_overlay::enabled() && visible_progress_steps < 12 {
                        visible_progress_steps += 1;
                        thread::sleep(Duration::from_millis(500));
                    }
                },
                move || {
                    write_update_status("downloaded");
                    update_e2e_overlay::update(
                        &download_complete_app,
                        "Download complete",
                        &format!(
                            "Starting installer for version {download_complete_version}; the old app will quit."
                        ),
                    );
                    update_e2e_overlay::pause_for_review();
                },
            )
            .await
        {
            Ok(()) => {
                write_update_status("installed:requesting-restart");
                app.request_restart();
            }
            Err(error) => {
                write_update_status(&format!("install-failed:{error}"));
                show_update_message(
                    &app,
                    "Update Failed",
                    &format!("Could not install the update: {error}"),
                    MessageDialogKind::Error,
                );
            }
        }
    });
}

fn show_update_message<R: tauri::Runtime>(
    app: &tauri::AppHandle<R>,
    title: &str,
    message: &str,
    kind: MessageDialogKind,
) {
    app.dialog()
        .message(message)
        .title(title)
        .kind(kind)
        .buttons(MessageDialogButtons::Ok)
        .show(|_| {});
}

fn human_bytes(bytes: u64) -> String {
    const MIB: u64 = 1024 * 1024;
    const KIB: u64 = 1024;
    if bytes >= MIB {
        format!("{:.1} MiB", bytes as f64 / MIB as f64)
    } else if bytes >= KIB {
        format!("{:.1} KiB", bytes as f64 / KIB as f64)
    } else {
        format!("{bytes} B")
    }
}
