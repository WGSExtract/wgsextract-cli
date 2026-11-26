use std::{
    env, fs,
    io::{self, Write},
    path::PathBuf,
    process, thread,
    time::{Duration, SystemTime, UNIX_EPOCH},
};

use serde::Serialize;
use tauri::{Emitter, Manager, Resource, ResourceId, Webview};
use tauri_plugin_updater::UpdaterExt;

use crate::{update_e2e_overlay, BackendProcess};

pub(super) mod native_dialog;

pub(crate) const AUTO_UPDATE_ENV: &str = "GFC_TAURI_AUTO_UPDATE";
pub(crate) const AUTO_ACCEPT_UPDATE_ENV: &str = "GFC_TAURI_AUTO_ACCEPT_UPDATE";
pub(crate) const AUTO_UPDATE_DELAY_SECONDS_ENV: &str = "GFC_TAURI_AUTO_UPDATE_DELAY_SECONDS";
pub(crate) const AUTO_UPDATE_ACTION_DELAY_SECONDS_ENV: &str =
    "GFC_TAURI_AUTO_UPDATE_ACTION_DELAY_SECONDS";
pub(crate) const E2E_STEP_DELAY_SECONDS_ENV: &str = "GFC_TAURI_E2E_STEP_DELAY_SECONDS";
const UPDATE_STATUS_FILE_ENV: &str = "GFC_TAURI_UPDATE_STATUS_FILE";

struct DownloadedUpdate {
    path: PathBuf,
}

impl Resource for DownloadedUpdate {}

impl Drop for DownloadedUpdate {
    fn drop(&mut self) {
        match fs::remove_file(&self.path) {
            Ok(()) => {}
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => eprintln!(
                "Could not remove cached update file {}: {error}",
                self.path.display()
            ),
        }
    }
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct UpdateCheckResponse {
    current_version: String,
    available_version: Option<String>,
    update_rid: Option<ResourceId>,
    body: Option<String>,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct UpdateDownloadResponse {
    bytes_rid: ResourceId,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct UpdateProgressEvent {
    event: String,
    version: String,
    downloaded_bytes: u64,
    content_length: Option<u64>,
    percent: Option<f64>,
}

#[tauri::command]
pub(crate) async fn gfc_update_check<R: tauri::Runtime>(
    webview: Webview<R>,
    prior_update_rid: Option<ResourceId>,
    prior_bytes_rid: Option<ResourceId>,
) -> Result<UpdateCheckResponse, String> {
    let app = webview.app_handle().clone();
    let current_version = app.package_info().version.to_string();
    write_update_status("checking");
    update_e2e_overlay::update(
        &app,
        "Checking for updates",
        &format!("Installed version: {current_version}"),
    );

    let updater = webview
        .updater_builder()
        .on_before_exit(update_before_exit_hook(&app))
        .build()
        .map_err(|error| {
            write_update_status(&format!("not-configured:{error}"));
            format!("This build is not configured for updates: {error}")
        })?;
    let update = updater.check().await.map_err(|error| {
        write_update_status(&format!("check-failed:{error}"));
        format!("Could not check for updates: {error}")
    })?;

    close_prior_update_resources(&webview, prior_update_rid, prior_bytes_rid);

    let Some(update) = update else {
        write_update_status("none");
        update_e2e_overlay::update(
            &app,
            "No update available",
            &format!("Installed version: {current_version}"),
        );
        return Ok(UpdateCheckResponse {
            current_version,
            available_version: None,
            update_rid: None,
            body: None,
        });
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
    let available_version = update.version.clone();
    let body = update.body.clone();
    let update_rid = webview.resources_table().add(update);
    Ok(UpdateCheckResponse {
        current_version,
        available_version: Some(available_version),
        update_rid: Some(update_rid),
        body,
    })
}

fn close_prior_update_resources<R: tauri::Runtime>(
    webview: &Webview<R>,
    prior_update_rid: Option<ResourceId>,
    prior_bytes_rid: Option<ResourceId>,
) {
    // Close any previously returned resources after the replacement check
    // succeeds so a failed check does not invalidate a retryable install.
    if let Some(rid) = prior_update_rid {
        let _ = webview.resources_table().close(rid);
    }
    if let Some(rid) = prior_bytes_rid {
        let _ = webview.resources_table().close(rid);
    }
}

#[tauri::command]
pub(crate) async fn gfc_update_download<R: tauri::Runtime>(
    webview: Webview<R>,
    update_rid: ResourceId,
    accepted_by: Option<String>,
) -> Result<UpdateDownloadResponse, String> {
    let app = webview.app_handle().clone();
    let update = webview
        .resources_table()
        .get::<tauri_plugin_updater::Update>(update_rid)
        .map_err(|error| format!("Could not find the pending update: {error}"))?;
    let update = (*update).clone();
    let version = update.version.clone();
    let accepted_by = accepted_by.unwrap_or_else(|| "user".to_string());
    write_update_status(&format!("accepted:{accepted_by}"));
    write_update_status("downloading");
    update_e2e_overlay::pause_for_review();
    update_e2e_overlay::update(
        &app,
        "Downloading update",
        &format!("Downloading version {version}"),
    );

    let progress_app = app.clone();
    let progress_version = version.clone();
    let mut visible_progress_steps = 0_u8;
    let mut downloaded_bytes = 0_u64;
    let bytes = update
        .download(
            move |chunk_length, content_length| {
                downloaded_bytes = downloaded_bytes.saturating_add(chunk_length as u64);
                let percent = content_length.filter(|length| *length > 0).map(|length| {
                    ((downloaded_bytes as f64 / length as f64) * 100.0).clamp(0.0, 100.0)
                });
                emit_update_progress(
                    &progress_app,
                    UpdateProgressEvent {
                        event: "progress".into(),
                        version: progress_version.clone(),
                        downloaded_bytes,
                        content_length,
                        percent,
                    },
                );
                update_e2e_overlay::update_with_progress(
                    &progress_app,
                    "Downloading update",
                    &format!("Downloading version {progress_version}"),
                    percent,
                );
                if update_e2e_overlay::enabled() && visible_progress_steps < 12 {
                    visible_progress_steps += 1;
                    thread::sleep(Duration::from_millis(500));
                }
            },
            {
                let app = app.clone();
                let version = version.clone();
                move || {
                    write_update_status("downloaded");
                    emit_update_progress(
                        &app,
                        UpdateProgressEvent {
                            event: "finished".into(),
                            version: version.clone(),
                            downloaded_bytes: 0,
                            content_length: None,
                            percent: Some(100.0),
                        },
                    );
                    update_e2e_overlay::update(
                        &app,
                        "Download complete",
                        &format!("Ready to install version {version}"),
                    );
                    update_e2e_overlay::pause_for_review();
                }
            },
        )
        .await
        .map_err(|error| {
            write_update_status(&format!("download-failed:{error}"));
            format!("Could not download the update: {error}")
        })?;

    let downloaded_update = write_downloaded_update(&bytes).map_err(|error| {
        write_update_status(&format!("download-failed:{error}"));
        error
    })?;

    Ok(UpdateDownloadResponse {
        bytes_rid: webview.resources_table().add(downloaded_update),
    })
}

#[tauri::command]
pub(crate) async fn gfc_update_install<R: tauri::Runtime>(
    webview: Webview<R>,
    update_rid: ResourceId,
    bytes_rid: ResourceId,
) -> Result<(), String> {
    let app = webview.app_handle().clone();
    let update = webview
        .resources_table()
        .get::<tauri_plugin_updater::Update>(update_rid)
        .map_err(|error| format!("Could not find the pending update: {error}"))?;
    let downloaded_update = webview
        .resources_table()
        .get::<DownloadedUpdate>(bytes_rid)
        .map_err(|error| format!("Could not find the downloaded update: {error}"))?;
    let downloaded_update_path = downloaded_update.path.clone();
    drop(downloaded_update);
    let bytes = fs::read(&downloaded_update_path).map_err(|error| {
        let message = format!("Could not read the downloaded update: {error}");
        write_update_status(&format!("install-failed:{message}"));
        message
    })?;
    write_update_status("installing");
    update_e2e_overlay::update(
        &app,
        "Starting installer",
        &format!("Installing version {}", update.version),
    );
    update_e2e_overlay::pause_for_review();
    update.install(&bytes).map_err(|error| {
        write_update_status(&format!("install-failed:{error}"));
        format!("Could not install the update: {error}")
    })?;
    let _ = webview.resources_table().close(update_rid);
    let _ = webview.resources_table().close(bytes_rid);
    write_update_status("installed:requesting-restart");
    app.request_restart();
    Ok(())
}

fn update_before_exit_hook<R: tauri::Runtime>(
    app: &tauri::AppHandle<R>,
) -> impl Fn() + Send + Sync + 'static {
    let app = app.clone();
    move || {
        write_update_status("installer-launched:exiting");
        update_e2e_overlay::update(
            &app,
            "Installer launched",
            "The old app is quitting so Windows can replace it.",
        );
        app.state::<BackendProcess>().terminate();
        app.cleanup_before_exit();
    }
}

fn emit_update_progress<R: tauri::Runtime>(app: &tauri::AppHandle<R>, event: UpdateProgressEvent) {
    if let Err(error) = app.emit("gfc-update-progress", event) {
        eprintln!("Could not emit update progress event: {error}");
    }
}

fn write_downloaded_update(bytes: &[u8]) -> Result<DownloadedUpdate, String> {
    let directory = env::temp_dir().join("wgsextract-gui-updates");
    fs::create_dir_all(&directory)
        .map_err(|error| format!("Could not create update download cache: {error}"))?;
    let timestamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_nanos())
        .unwrap_or(0);

    for attempt in 0..100 {
        let path = directory.join(format!(
            "update-{}-{timestamp}-{attempt}.bin",
            process::id()
        ));
        match fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&path)
        {
            Ok(mut file) => {
                if let Err(error) = file.write_all(bytes) {
                    let _ = fs::remove_file(&path);
                    return Err(format!("Could not write downloaded update: {error}"));
                }
                return Ok(DownloadedUpdate { path });
            }
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => {}
            Err(error) => return Err(format!("Could not create downloaded update file: {error}")),
        }
    }

    Err("Could not create a unique downloaded update file.".into())
}

pub(crate) fn env_flag(name: &str) -> bool {
    env_flag_or(name, false)
}

/// Reads a yes/no variable, or `default` when it is unset or says neither.
pub(crate) fn env_flag_or(name: &str, default: bool) -> bool {
    parse_flag(&env::var(name).unwrap_or_default()).unwrap_or(default)
}

fn parse_flag(value: &str) -> Option<bool> {
    match value.trim().to_ascii_lowercase().as_str() {
        "1" | "true" | "yes" | "on" => Some(true),
        "0" | "false" | "no" | "off" => Some(false),
        _ => None,
    }
}

pub(crate) fn env_duration_seconds(name: &str) -> Duration {
    env::var(name)
        .ok()
        .and_then(|value| value.trim().parse::<u64>().ok())
        .map(Duration::from_secs)
        .unwrap_or(Duration::ZERO)
}

fn write_update_status(event: &str) {
    let Some(path) = env::var_os(UPDATE_STATUS_FILE_ENV).map(PathBuf::from) else {
        return;
    };
    if let Some(parent) = path.parent() {
        if let Err(error) = fs::create_dir_all(parent) {
            eprintln!("Could not create update status directory: {error}");
            return;
        }
    }
    match fs::OpenOptions::new().create(true).append(true).open(&path) {
        Ok(mut file) => {
            if let Err(error) = writeln!(file, "{event}") {
                eprintln!(
                    "Could not write update status to {}: {error}",
                    path.display()
                );
            }
        }
        Err(error) => eprintln!(
            "Could not open update status file {}: {error}",
            path.display()
        ),
    }
}

#[cfg(test)]
mod tests {
    use super::parse_flag;

    #[test]
    fn startup_update_check_flag_turns_off_only_when_said_so() {
        // GFC_TAURI_AUTO_UPDATE unset or unreadable keeps the default (on); tests pass 0 to stay offline.
        assert_eq!(parse_flag(""), None);
        assert_eq!(parse_flag("maybe"), None);
        for off in ["0", "false", " No ", "OFF"] {
            assert_eq!(parse_flag(off), Some(false), "{off}");
        }
        for on in ["1", "true", "Yes", "on"] {
            assert_eq!(parse_flag(on), Some(true), "{on}");
        }
    }
}
