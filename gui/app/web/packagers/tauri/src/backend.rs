//! Launches the Node WebUI backend and owns its lifetime.

use std::{
    env, fs,
    io::{Read, Write},
    net::TcpStream,
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{Arc, Mutex},
    thread,
    time::{Duration, Instant},
};

#[cfg(unix)]
use std::os::unix::process::CommandExt;
#[cfg(windows)]
use std::os::windows::process::CommandExt;

use crate::{child_process_path, AppPaths};

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;
const STARTUP_TIMEOUT: Duration = Duration::from_secs(15);
/// Node escalates its own process trees to SIGKILL after 3 s, so this leaves it time to finish.
const SHUTDOWN_TIMEOUT: Duration = Duration::from_secs(5);

pub(crate) struct BackendLaunch<'a> {
    pub paths: &'a AppPaths,
    pub picker_port: u16,
    pub application_name: &'a str,
    pub application_version: &'a str,
    pub api_token: &'a str,
}

struct BackendState {
    process: BackendChild,
    port: u16,
    api_token: String,
}

struct BackendChild {
    child: Child,
    /// Every process the backend starts inherits this job; closing it (even if this app crashes) kills them all.
    #[cfg(windows)]
    job: Option<job::KillOnCloseJob>,
}

impl BackendChild {
    fn spawn(mut command: Command) -> std::io::Result<Self> {
        #[cfg(unix)]
        command.process_group(0);
        #[cfg(windows)]
        command.creation_flags(CREATE_NO_WINDOW);
        let process = Self {
            child: command.spawn()?,
            #[cfg(windows)]
            job: None,
        };
        #[cfg(windows)]
        let process = {
            let mut process = process;
            process.job = Some(job::KillOnCloseJob::containing(&process.child).map_err(
                |error| {
                    std::io::Error::new(
                        error.kind(),
                        format!("Could not place the backend in a job object: {error}"),
                    )
                },
            )?);
            process
        };
        Ok(process)
    }

    #[cfg(unix)]
    fn has_exited(&mut self) -> std::io::Result<bool> {
        // Keep the leader's PID reserved until Drop signals its group.
        // SAFETY: waitid writes to local storage and only checks our child.
        unsafe {
            let mut info: libc::siginfo_t = std::mem::zeroed();
            if libc::waitid(
                libc::P_PID,
                self.child.id() as libc::id_t,
                &mut info,
                libc::WEXITED | libc::WNOHANG | libc::WNOWAIT,
            ) == -1
            {
                return Err(std::io::Error::last_os_error());
            }
            Ok(info.si_pid() != 0)
        }
    }

    #[cfg(not(unix))]
    fn has_exited(&mut self) -> std::io::Result<bool> {
        self.child.try_wait().map(|status| status.is_some())
    }
}

impl Drop for BackendChild {
    fn drop(&mut self) {
        #[cfg(unix)]
        // SAFETY: spawn made this unreaped child's PID its process-group ID.
        unsafe {
            libc::kill(-(self.child.id() as libc::pid_t), libc::SIGKILL);
        }
        #[cfg(windows)]
        drop(self.job.take());
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

struct StartupPortFile(PathBuf);

impl Drop for StartupPortFile {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.0);
    }
}

pub(crate) struct BackendProcess(Arc<Mutex<Option<BackendState>>>);

impl BackendProcess {
    pub(crate) fn new() -> Self {
        Self(Arc::new(Mutex::new(None)))
    }

    /// Starts Node on an OS-assigned port and waits until it reports that port.
    pub(crate) fn launch(
        &self,
        launch: BackendLaunch<'_>,
    ) -> Result<(u32, u16), Box<dyn std::error::Error>> {
        let mut state = self
            .0
            .lock()
            .map_err(|_| "Backend process lock was poisoned")?;
        if state.is_some() {
            return Err("WebUI backend is already running".into());
        }
        let port_file = StartupPortFile(env::temp_dir().join(format!(
            "gfc-backend-{}-{}.port",
            std::process::id(),
            crate::random_token(8)?
        )));
        let mut command = Command::new(&launch.paths.node_path);
        command
            .current_dir(child_process_path(&launch.paths.repo_root))
            .arg(child_process_path(&launch.paths.server_script))
            .args(["--port", "0", "--host", "127.0.0.1", "--bundle"])
            .arg(child_process_path(&launch.paths.bundle_root))
            // Secrets and ports go through the environment, never argv (which other users can list).
            .env("GFC_API_TOKEN", launch.api_token)
            .env("GFC_PORT_FILE", &port_file.0)
            .env("GFC_PARENT_PID", std::process::id().to_string())
            .env("WGSEXTRACT_GUI_APP_DATA_NAME", &launch.paths.app_data_name)
            .env("WGSEXTRACT_GUI_APPLICATION_NAME", launch.application_name)
            .env(
                "WGSEXTRACT_GUI_APPLICATION_VERSION",
                launch.application_version,
            )
            .env("GFC_NATIVE_PICKER_PORT", launch.picker_port.to_string())
            .stdin(Stdio::null());
        #[cfg(target_os = "macos")]
        command
            .arg("--network-cost-executable")
            .arg(env::current_exe()?);
        match backend_log(launch.paths.backend_log.as_deref()) {
            Some((stdout, stderr)) => command.stdout(stdout).stderr(stderr),
            None => command.stdout(Stdio::null()).stderr(Stdio::null()),
        };
        let (process, port) = start_backend(command, port_file)?;
        let pid = process.child.id();
        *state = Some(BackendState {
            process,
            port,
            api_token: launch.api_token.to_string(),
        });
        Ok((pid, port))
    }

    pub(crate) fn terminate(&self) {
        let Ok(mut state) = self.0.lock() else {
            return;
        };
        let Some(mut state) = state.take() else {
            return;
        };
        let _ = request_backend_shutdown(state.port, &state.api_token);
        let deadline = Instant::now() + SHUTDOWN_TIMEOUT;
        while Instant::now() < deadline {
            match state.process.has_exited() {
                Ok(false) => thread::sleep(Duration::from_millis(50)),
                _ => break,
            }
        }
    }
}

impl Drop for BackendProcess {
    fn drop(&mut self) {
        self.terminate();
    }
}

/// A new log each launch, holding the backend's stdout and stderr.
fn backend_log(path: Option<&std::path::Path>) -> Option<(Stdio, Stdio)> {
    let path = path?;
    fs::create_dir_all(path.parent()?).ok()?;
    let file = fs::File::create(path).ok()?;
    let stderr = file.try_clone().ok()?;
    Some((Stdio::from(file), Stdio::from(stderr)))
}

fn start_backend(
    command: Command,
    port_file: StartupPortFile,
) -> Result<(BackendChild, u16), Box<dyn std::error::Error>> {
    let mut process = BackendChild::spawn(command)?;
    let port = wait_for_port_file(&port_file.0, &mut process)?;
    Ok((process, port))
}

fn wait_for_port_file(
    port_file: &Path,
    process: &mut BackendChild,
) -> Result<u16, Box<dyn std::error::Error>> {
    let deadline = Instant::now() + STARTUP_TIMEOUT;
    while Instant::now() < deadline {
        if process.has_exited()? {
            return Err("WebUI backend exited during startup".into());
        }
        if let Ok(contents) = fs::read_to_string(port_file) {
            if let Ok(port) = contents.trim().parse::<u16>() {
                return Ok(port);
            }
        }
        thread::sleep(Duration::from_millis(25));
    }
    Err("Timed out waiting for the WebUI backend to report its port".into())
}

fn request_backend_shutdown(port: u16, api_token: &str) -> bool {
    let Ok(mut stream) = TcpStream::connect(("127.0.0.1", port)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(750)));
    let body = "{}";
    let request = format!(
        "POST /api/shutdown HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nX-GFC-Token: {api_token}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
        body.len()
    );
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }
    let mut response = [0; 64];
    let Ok(count) = stream.read(&mut response) else {
        return false;
    };
    response[..count].starts_with(b"HTTP/1.1 200")
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    use std::{io::BufRead, sync::mpsc};

    #[test]
    fn backend_cleanup_terminates_descendants_after_the_leader_exits() {
        let mut command = Command::new("/bin/sh");
        command
            .args(["-c", "sleep 10 & printf 'ready\\n'"])
            .stdout(Stdio::piped());
        let mut backend = BackendChild::spawn(command).unwrap();
        let mut output = std::io::BufReader::new(backend.child.stdout.take().unwrap());
        let mut ready = String::new();
        output.read_line(&mut ready).unwrap();
        assert_eq!(ready.trim(), "ready");
        let deadline = Instant::now() + Duration::from_secs(3);
        while !backend.has_exited().unwrap() {
            assert!(Instant::now() < deadline, "backend leader did not exit");
            thread::sleep(Duration::from_millis(10));
        }
        let (send, receive) = mpsc::channel();
        let reader = thread::spawn(move || send.send(output.read(&mut [0; 1])));
        drop(backend);
        assert_eq!(
            receive
                .recv_timeout(Duration::from_secs(3))
                .unwrap()
                .unwrap(),
            0
        );
        reader.join().unwrap().unwrap();
    }
}

#[cfg(windows)]
mod job {
    use std::{io, mem, os::windows::io::AsRawHandle, process::Child, ptr};

    use windows_sys::Win32::{
        Foundation::{CloseHandle, HANDLE},
        System::JobObjects::{
            AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
            SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
        },
    };

    pub(crate) struct KillOnCloseJob(HANDLE);

    // The handle is only used through thread-safe Win32 calls.
    unsafe impl Send for KillOnCloseJob {}

    impl KillOnCloseJob {
        pub(crate) fn containing(child: &Child) -> io::Result<Self> {
            // SAFETY: plain Win32 calls on handles we own; `info` outlives the call that reads it.
            unsafe {
                let handle = CreateJobObjectW(ptr::null(), ptr::null());
                if handle.is_null() {
                    return Err(io::Error::last_os_error());
                }
                let job = Self(handle);
                let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = mem::zeroed();
                info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
                if SetInformationJobObject(
                    handle,
                    JobObjectExtendedLimitInformation,
                    &info as *const JOBOBJECT_EXTENDED_LIMIT_INFORMATION as *const _,
                    mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
                ) == 0
                {
                    return Err(io::Error::last_os_error());
                }
                if AssignProcessToJobObject(handle, child.as_raw_handle() as HANDLE) == 0 {
                    return Err(io::Error::last_os_error());
                }
                Ok(job)
            }
        }
    }

    impl Drop for KillOnCloseJob {
        fn drop(&mut self) {
            // SAFETY: closing our own handle; with KILL_ON_JOB_CLOSE this ends every process in the job.
            unsafe {
                CloseHandle(self.0);
            }
        }
    }
}
