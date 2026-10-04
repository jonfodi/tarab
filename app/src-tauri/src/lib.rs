//! The rasa desktop shell: starts the Python engine (`rasa serve`), hands its address and token to the UI,
//! and stops it (and the slskd it manages) when the app quits.

use serde::{Deserialize, Serialize};
use std::fs::OpenOptions;
use std::io::{BufRead, BufReader};
use std::path::PathBuf;
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
/// - packaged app: Resources/engine/rasa-engine, with bundled slskd + ffmpeg in Resources/bin
/// - development: `uv run rasa serve` in ../core (override with RASA_ENGINE_CMD)
fn engine_command(resources: Option<PathBuf>) -> Command {
    if let Some(res) = resources {
        let bundled = res.join("engine").join("rasa-engine");
        if bundled.exists() {
            let mut c = Command::new(bundled);
            c.args(["serve", "--watch-stdin"]).env("RASA_BIN_DIR", res.join("bin"));
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

/// Engine stderr goes to ~/Library/Logs/rasa/engine.log (a packaged app has no terminal).
fn engine_log() -> Stdio {
    let dir = PathBuf::from(std::env::var("HOME").unwrap_or_default()).join("Library/Logs/rasa");
    let _ = std::fs::create_dir_all(&dir);
    match OpenOptions::new().create(true).append(true).open(dir.join("engine.log")) {
        Ok(f) => Stdio::from(f),
        Err(_) => Stdio::inherit(),
    }
}

fn start_engine(engine: &Engine, resources: Option<PathBuf>) -> Result<Backend, String> {
    let stderr = if cfg!(debug_assertions) { Stdio::inherit() } else { engine_log() };
    let mut child = engine_command(resources)
        .stdin(Stdio::piped()) // the engine exits when this pipe closes (if we crash)
        .stdout(Stdio::piped())
        .stderr(stderr)
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
            let resources = app.path().resource_dir().ok();
            std::thread::spawn(move || {
                let engine = handle.state::<Engine>();
                match start_engine(&engine, resources) {
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
