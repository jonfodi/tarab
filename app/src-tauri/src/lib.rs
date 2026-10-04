//! The rasa desktop shell: starts the Python engine (`rasa serve`), hands its address and token to the UI,
//! and stops it (and the slskd it manages) when the app quits.

use serde::{Deserialize, Serialize};
use std::io::{BufRead, BufReader};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use tauri::{Manager, RunEvent, State};

#[derive(Clone, Serialize, Deserialize)]
struct Backend {
    port: u16,
    token: String,
}

#[derive(Default)]
struct Engine {
    child: Mutex<Option<Child>>,
    backend: Mutex<Option<Backend>>,
    error: Mutex<Option<String>>,
}

/// How to launch the engine:
/// - packaged app: the bundled `rasa-engine` next to the executable (phase 4)
/// - development: `uv run rasa serve` in ../core (override with RASA_ENGINE_CMD)
fn engine_command() -> Command {
    if let Ok(exe) = std::env::current_exe() {
        let bundled = exe.with_file_name("rasa-engine");
        if bundled.exists() {
            let mut c = Command::new(bundled);
            c.args(["serve", "--watch-stdin"]);
            return c;
        }
    }
    if let Ok(cmd) = std::env::var("RASA_ENGINE_CMD") {
        let mut c = Command::new("/bin/sh");
        c.args(["-c", &cmd]);
        return c;
    }
    let core = concat!(env!("CARGO_MANIFEST_DIR"), "/../../core");
    let mut c = Command::new("/bin/sh");
    // GUI apps don't inherit the shell PATH: look for uv where Homebrew/installers put it.
    c.args(["-lc", "PATH=\"$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH\" exec uv run --directory \"$0\" rasa serve --watch-stdin", core]);
    c
}

fn start_engine(engine: &Engine) -> Result<Backend, String> {
    let mut child = engine_command()
        .stdin(Stdio::piped()) // the engine exits when this pipe closes (if we crash)
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .map_err(|e| format!("couldn't start the rasa engine: {e}"))?;
    let stdout = child.stdout.take().ok_or("engine has no stdout")?;
    let mut line = String::new();
    BufReader::new(stdout)
        .read_line(&mut line)
        .map_err(|e| format!("engine didn't start: {e}"))?;
    let backend: Backend = serde_json::from_str(line.trim())
        .map_err(|e| format!("engine said {line:?} instead of its address: {e}"))?;
    *engine.child.lock().unwrap() = Some(child);
    Ok(backend)
}

fn stop_engine(engine: &Engine) {
    if let Some(mut child) = engine.child.lock().unwrap().take() {
        drop(child.stdin.take()); // closing stdin asks the engine to shut down (it stops slskd)
        for _ in 0..50 {
            if let Ok(Some(_)) = child.try_wait() {
                return;
            }
            std::thread::sleep(std::time::Duration::from_millis(100));
        }
        let _ = child.kill();
    }
}

#[tauri::command]
fn backend(engine: State<Engine>) -> Result<Backend, String> {
    if let Some(b) = engine.backend.lock().unwrap().clone() {
        return Ok(b);
    }
    Err(engine
        .error
        .lock()
        .unwrap()
        .clone()
        .unwrap_or_else(|| "engine is starting".into()))
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .manage(Engine::default())
        .invoke_handler(tauri::generate_handler![backend])
        .setup(|app| {
            let handle = app.handle().clone();
            std::thread::spawn(move || {
                let engine = handle.state::<Engine>();
                match start_engine(&engine) {
                    Ok(b) => *engine.backend.lock().unwrap() = Some(b),
                    Err(e) => *engine.error.lock().unwrap() = Some(e),
                }
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building rasa");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            stop_engine(&handle.state::<Engine>());
        }
    });
}
