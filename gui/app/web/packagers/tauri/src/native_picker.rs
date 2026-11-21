//! A loopback endpoint the Node backend calls to show native file dialogs. Only requests that carry
//! the per-launch API token (which only the backend knows) are served.

use std::{
    collections::HashMap,
    io::{Read, Write},
    net::{TcpListener, TcpStream},
    path::Path,
    sync::{
        atomic::{AtomicUsize, Ordering},
        Arc,
    },
    thread,
    time::{Duration, Instant},
};

use tauri_plugin_dialog::DialogExt;

use crate::child_process_path;

const MAX_REQUEST_BYTES: usize = 16 * 1024;
const MAX_CONNECTIONS: usize = 8;
const MAX_TITLE_BYTES: usize = 512;
const MAX_DEFAULT_PATH_BYTES: usize = 4096;
const SOCKET_TIMEOUT: Duration = Duration::from_secs(5);
/// What the `bundle` picker offers: a bundle's manifest.json or a bundle archive.
/// Keep in sync with BUNDLE_FILE_EXTENSIONS in bundle-archive.ts.
const BUNDLE_EXTENSIONS: &[&str] = &["json", "zip", "tar", "tgz", "gz", "gzip"];

pub(crate) fn start_native_picker_listener<R: tauri::Runtime>(
    listener: TcpListener,
    app: tauri::AppHandle<R>,
    api_token: String,
) {
    thread::spawn(move || {
        let active = Arc::new(AtomicUsize::new(0));
        for stream in listener.incoming().flatten() {
            let Some(slot) = ConnectionSlot::acquire(&active) else {
                continue;
            };
            let deadline = Instant::now() + SOCKET_TIMEOUT;
            if stream.set_write_timeout(Some(SOCKET_TIMEOUT)).is_err() {
                continue;
            }
            let app = app.clone();
            let api_token = api_token.clone();
            let _ = thread::Builder::new().spawn(move || {
                let _slot = slot;
                handle_native_picker_request(stream, &app, &api_token, deadline);
            });
        }
    });
}

struct ConnectionSlot(Arc<AtomicUsize>);

impl ConnectionSlot {
    fn acquire(active: &Arc<AtomicUsize>) -> Option<Self> {
        let slot = Self(Arc::clone(active));
        let previous = active.fetch_add(1, Ordering::Relaxed);
        (previous < MAX_CONNECTIONS).then_some(slot)
    }
}

impl Drop for ConnectionSlot {
    fn drop(&mut self) {
        self.0.fetch_sub(1, Ordering::Relaxed);
    }
}

fn handle_native_picker_request<R: tauri::Runtime>(
    mut stream: TcpStream,
    app: &tauri::AppHandle<R>,
    api_token: &str,
    deadline: Instant,
) {
    let Some(request) = read_request_head(&mut stream, deadline) else {
        write_json_response(
            &mut stream,
            400,
            serde_json::json!({"error": "Invalid request"}),
        );
        return;
    };
    let mut lines = request.lines();
    let target = lines
        .next()
        .and_then(|line| line.split_whitespace().nth(1))
        .unwrap_or("");
    let token = lines
        .filter_map(|line| line.split_once(':'))
        .find(|(name, _)| name.trim().eq_ignore_ascii_case("x-gfc-token"))
        .map(|(_, value)| value.trim())
        .unwrap_or("");
    if !constant_time_eq(token.as_bytes(), api_token.as_bytes()) {
        write_json_response(
            &mut stream,
            401,
            serde_json::json!({"error": "Missing or invalid API token"}),
        );
        return;
    }
    let Some(query) = target
        .strip_prefix("/pick?")
        .or_else(|| (target == "/pick").then_some(""))
    else {
        write_json_response(&mut stream, 404, serde_json::json!({"error": "Not found"}));
        return;
    };
    let values = parse_query(query);
    let kind = values.get("kind").map(String::as_str).unwrap_or("file");
    let title = values
        .get("title")
        .map(String::as_str)
        .unwrap_or(if kind == "directory" {
            "Choose directory"
        } else {
            "Choose file"
        });
    let default_path = values.get("defaultPath").map(String::as_str).unwrap_or("");
    if !valid_dialog_options(title, default_path) {
        write_json_response(
            &mut stream,
            400,
            serde_json::json!({"error": "Invalid request"}),
        );
        return;
    }
    let mut dialog = app.dialog().file().set_title(title);
    if !default_path.is_empty() {
        dialog = dialog.set_directory(default_path);
    }
    // Blocking picks are fine here: this runs on a per-request thread, never the main thread.
    let picked = match kind {
        "directory" | "folder" => dialog.blocking_pick_folder(),
        "file" => dialog.blocking_pick_file(),
        "bundle" => dialog
            .add_filter("Bundle", BUNDLE_EXTENSIONS)
            .blocking_pick_file(),
        _ => {
            write_json_response(
                &mut stream,
                400,
                serde_json::json!({"error": "Path picker kind must be file, directory, or bundle."}),
            );
            return;
        }
    };
    match picked.map(|path| path.into_path()) {
        Some(Ok(path)) => write_json_response(
            &mut stream,
            200,
            serde_json::json!({"path": child_process_path(&path), "kind": kind, "cancelled": false}),
        ),
        Some(Err(error)) => write_json_response(
            &mut stream,
            400,
            serde_json::json!({"error": error.to_string()}),
        ),
        None => write_json_response(
            &mut stream,
            200,
            serde_json::json!({"kind": kind, "cancelled": true}),
        ),
    }
}

fn valid_dialog_options(title: &str, default_path: &str) -> bool {
    title.len() <= MAX_TITLE_BYTES
        && !title.contains('\0')
        && default_path.len() <= MAX_DEFAULT_PATH_BYTES
        && !default_path.contains('\0')
        && (default_path.is_empty() || Path::new(default_path).is_absolute())
}

fn read_request_head(stream: &mut TcpStream, deadline: Instant) -> Option<String> {
    let mut request = Vec::new();
    let mut buffer = [0; 4096];
    while !request.windows(4).any(|window| window == b"\r\n\r\n") {
        stream
            .set_read_timeout(Some(deadline.checked_duration_since(Instant::now())?))
            .ok()?;
        let count = match stream.read(&mut buffer) {
            Err(err) if err.kind() == std::io::ErrorKind::Interrupted => continue,
            result => result.ok()?,
        };
        if count == 0 || request.len() + count > MAX_REQUEST_BYTES {
            return None;
        }
        request.extend_from_slice(&buffer[..count]);
    }
    String::from_utf8(request).ok()
}

fn constant_time_eq(left: &[u8], right: &[u8]) -> bool {
    left.len() == right.len()
        && left
            .iter()
            .zip(right)
            .fold(0, |difference, (a, b)| difference | (a ^ b))
            == 0
}

fn parse_query(query: &str) -> HashMap<String, String> {
    query
        .split('&')
        .filter(|part| !part.is_empty())
        .filter_map(|part| {
            let (key, value) = part.split_once('=').unwrap_or((part, ""));
            Some((percent_decode(key)?, percent_decode(value)?))
        })
        .collect()
}

fn percent_decode(value: &str) -> Option<String> {
    let bytes = value.as_bytes();
    let mut output = Vec::with_capacity(bytes.len());
    let mut index = 0;
    while index < bytes.len() {
        match bytes[index] {
            b'+' => {
                output.push(b' ');
                index += 1;
            }
            b'%' if index + 2 < bytes.len() => {
                let hex = std::str::from_utf8(&bytes[index + 1..index + 3]).ok()?;
                output.push(u8::from_str_radix(hex, 16).ok()?);
                index += 3;
            }
            byte => {
                output.push(byte);
                index += 1;
            }
        }
    }
    String::from_utf8(output).ok()
}

fn write_json_response(stream: &mut TcpStream, status: u16, body: serde_json::Value) {
    let reason = match status {
        200 => "OK",
        400 => "Bad Request",
        401 => "Unauthorized",
        404 => "Not Found",
        _ => "Internal Server Error",
    };
    let payload = format!("{body}\n");
    let response = format!(
        "HTTP/1.1 {status} {reason}\r\nContent-Type: application/json; charset=utf-8\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{payload}",
        payload.len()
    );
    let _ = stream.write_all(response.as_bytes());
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stalled_picker_request_releases_the_socket() {
        let listener = TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let mut client = TcpStream::connect(listener.local_addr().unwrap()).unwrap();
        client
            .set_read_timeout(Some(Duration::from_secs(3)))
            .unwrap();
        let worker = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            assert!(
                read_request_head(&mut stream, Instant::now() + Duration::from_millis(50))
                    .is_none()
            );
        });
        assert_eq!(client.read(&mut [0; 1]).unwrap(), 0);
        worker.join().unwrap();
    }

    #[test]
    fn picker_rejects_excess_connections_until_a_slot_is_released() {
        let active = Arc::new(AtomicUsize::new(0));
        let mut slots: Vec<_> = (0..MAX_CONNECTIONS)
            .map(|_| ConnectionSlot::acquire(&active).unwrap())
            .collect();
        assert!(ConnectionSlot::acquire(&active).is_none());
        drop(slots.pop());
        assert!(ConnectionSlot::acquire(&active).is_some());
    }

    #[test]
    fn invalid_picker_options_are_not_passed_to_the_dialog() {
        let absolute = std::env::current_dir().unwrap();
        let absolute = absolute.to_str().unwrap();
        assert!(valid_dialog_options("Choose file", absolute));
        assert!(valid_dialog_options("Choose file", ""));
        assert!(!valid_dialog_options("Choose file", "relative/path"));
        assert!(!valid_dialog_options("a\0b", absolute));
        assert!(!valid_dialog_options(
            "Choose file",
            &format!("{absolute}\0")
        ));
        assert!(!valid_dialog_options(&"é".repeat(MAX_TITLE_BYTES), ""));
        let long_path = Path::new(absolute).join("x".repeat(MAX_DEFAULT_PATH_BYTES));
        assert!(!valid_dialog_options(
            "Choose file",
            long_path.to_str().unwrap()
        ));
    }

    #[test]
    fn decodes_query_components() {
        assert_eq!(
            percent_decode("C%3A%5CUsers+me").as_deref(),
            Some("C:\\Users me")
        );
        assert_eq!(percent_decode("100%25").as_deref(), Some("100%"));
        assert_eq!(percent_decode("bad%zz"), None);
    }

    #[test]
    fn compares_tokens_by_value() {
        assert!(constant_time_eq(b"secret", b"secret"));
        assert!(!constant_time_eq(b"secret", b"secreT"));
        assert!(!constant_time_eq(b"secret", b"secret2"));
    }
}
