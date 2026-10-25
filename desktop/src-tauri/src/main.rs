// Portable installer for the Spanish patch (Windows client).
// Copies the embedded payload, then edits only the patched lines of
// file_versions.dat and Persistent_Store/ss_win.mani (MD5, size, bundle CRC).
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::{Deserialize, Serialize};
use std::{
    collections::HashMap,
    fs,
    io::{Cursor, Read},
    path::{Path, PathBuf},
    process::Command,
    time::{SystemTime, UNIX_EPOCH},
};
use tauri::{Emitter, Window};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

const PATCH_ZIP: &[u8] = include_bytes!(concat!(env!("OUT_DIR"), "/patch.zip"));
const VERSION: &str = env!("CARGO_PKG_VERSION");
const MANI: &str = "Persistent_Store/ss_win.mani";
const VERSIONS: &str = "file_versions.dat";
const BACKUP_DIR: &str = "StellaSoraES_backup";
const RECORD: &str = "StellaSoraES_backup/last.json";

#[derive(Deserialize, Clone)]
struct PatchFile {
    path: String,
    md5: String,
    size: u64,
    crc: Option<u32>,
}

#[derive(Serialize, Deserialize)]
struct Record {
    backup: String,
    paths: Vec<String>,
    #[serde(default)]
    version: String,
}

#[derive(Serialize)]
struct GameInfo {
    valid: bool,
    path: String,
    client: String,
    resource: String,
    installed: Option<String>,
    running: bool,
    message: String,
}

#[derive(Serialize)]
struct PatchInfo {
    version: String,
    files: usize,
    bytes: u64,
}

#[derive(Serialize, Clone)]
struct Progress {
    done: usize,
    total: usize,
    label: String,
}

fn archive() -> Result<zip::ZipArchive<Cursor<&'static [u8]>>, String> {
    zip::ZipArchive::new(Cursor::new(PATCH_ZIP)).map_err(|e| e.to_string())
}

fn patch_files() -> Result<Vec<PatchFile>, String> {
    let mut zip = archive()?;
    let mut text = String::new();
    zip.by_name("patch-files.json")
        .map_err(|e| e.to_string())?
        .read_to_string(&mut text)
        .map_err(|e| e.to_string())?;
    serde_json::from_str(&text).map_err(|e| e.to_string())
}

fn game_running() -> bool {
    let mut cmd = Command::new("tasklist");
    cmd.args(["/FI", "IMAGENAME eq StellaSora.exe", "/NH"]);
    #[cfg(windows)]
    cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    cmd.output()
        .map(|o| String::from_utf8_lossy(&o.stdout).contains("StellaSora.exe"))
        .unwrap_or(false)
}

fn header_value(mani: &str, key: &str) -> String {
    mani.lines()
        .find_map(|l| l.strip_prefix(key))
        .unwrap_or("")
        .trim()
        .to_string()
}

fn read_record(root: &Path) -> Option<Record> {
    serde_json::from_str(&fs::read_to_string(root.join(RECORD)).ok()?).ok()
}

/// Rewrites matching lines, keeping the file's LF line endings intact.
fn edit_lines(path: &Path, update: impl Fn(&str) -> Option<String>) -> Result<usize, String> {
    let text = fs::read_to_string(path).map_err(|e| format!("{}: {e}", path.display()))?;
    let mut changed = 0;
    let lines: Vec<String> = text
        .split('\n')
        .map(|line| match update(line) {
            Some(new) if new != line => {
                changed += 1;
                new
            }
            _ => line.to_string(),
        })
        .collect();
    fs::write(path, lines.join("\n")).map_err(|e| e.to_string())?;
    Ok(changed)
}

#[tauri::command]
fn patch_info() -> Result<PatchInfo, String> {
    let files = patch_files()?;
    Ok(PatchInfo {
        version: VERSION.into(),
        bytes: files.iter().map(|f| f.size).sum(),
        files: files.len(),
    })
}

#[tauri::command]
fn inspect_game(path: String) -> GameInfo {
    let root = PathBuf::from(&path);
    let mut info = GameInfo {
        valid: false,
        path: path.clone(),
        client: String::new(),
        resource: String::new(),
        installed: None,
        running: game_running(),
        message: String::new(),
    };
    if !root.join("StellaSora.exe").is_file() {
        info.message = "No está StellaSora.exe en esta carpeta.".into();
        return info;
    }
    match fs::read_to_string(root.join(MANI)) {
        Ok(mani) => {
            info.client = header_value(&mani, "$CLIENT_VER:");
            info.resource = header_value(&mani, "$RES_VER:");
        }
        Err(_) => {
            info.message = "Abre el juego una vez para que descargue sus recursos.".into();
            return info;
        }
    }
    info.installed = read_record(&root).map(|r| if r.version.is_empty() { "anterior".into() } else { r.version });
    info.valid = true;
    info
}

#[tauri::command]
fn detect_game() -> Option<String> {
    let suffixes = [
        r"YostarGames\StellaSora_EN",
        r"Program Files\YostarGames\StellaSora_EN",
        r"Program Files (x86)\YostarGames\StellaSora_EN",
        r"Games\YostarGames\StellaSora_EN",
        r"Games\StellaSora_EN",
        r"StellaSora_EN",
    ];
    for drive in b'C'..=b'Z' {
        for suffix in suffixes {
            let candidate = format!("{}:\\{}", drive as char, suffix);
            if Path::new(&candidate).join("StellaSora.exe").is_file() {
                return Some(candidate);
            }
        }
    }
    None
}

fn restore_record(root: &Path) -> Result<usize, String> {
    let record = read_record(root).ok_or("No hay respaldo registrado.")?;
    let backup = PathBuf::from(&record.backup);
    for rel in &record.paths {
        fs::copy(backup.join(rel), root.join(rel)).map_err(|e| format!("{rel}: {e}"))?;
    }
    fs::remove_file(root.join(RECORD)).map_err(|e| e.to_string())?;
    Ok(record.paths.len())
}

#[tauri::command]
fn restore(path: String) -> Result<String, String> {
    if game_running() {
        return Err("Cierra Stella Sora antes de restaurar.".into());
    }
    let n = restore_record(Path::new(&path))?;
    Ok(format!("Se restauraron {n} archivos originales."))
}

#[tauri::command]
async fn install(window: Window, path: String) -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(move || install_blocking(&window, Path::new(&path)))
        .await
        .map_err(|e| e.to_string())?
}

fn install_blocking(window: &Window, root: &Path) -> Result<String, String> {
    if game_running() {
        return Err("Cierra Stella Sora antes de instalar.".into());
    }
    let emit = |done: usize, total: usize, label: &str| {
        let _ = window.emit("progress", Progress { done, total, label: label.into() });
    };
    // A previous install is undone first so the backup always holds the game's originals.
    if read_record(root).is_some() {
        emit(0, 1, "Restaurando la versión anterior del parche…");
        restore_record(root)?;
    }
    let files = patch_files()?;
    for f in &files {
        if !root.join(&f.path).is_file() {
            return Err(format!("Falta en el juego: {}. ¿Es la carpeta correcta?", f.path));
        }
    }
    let stamp = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs();
    let backup = root.join(BACKUP_DIR).join(stamp.to_string());
    let mut paths: Vec<String> = files.iter().map(|f| f.path.clone()).collect();
    paths.push(VERSIONS.into());
    paths.push(MANI.into());
    let total = paths.len() + files.len();
    for (i, rel) in paths.iter().enumerate() {
        emit(i, total, "Respaldando archivos originales…");
        let dst = backup.join(rel);
        fs::create_dir_all(dst.parent().unwrap()).map_err(|e| e.to_string())?;
        fs::copy(root.join(rel), &dst).map_err(|e| format!("{rel}: {e}"))?;
    }
    let record = Record { backup: backup.to_string_lossy().into(), paths: paths.clone(), version: VERSION.into() };
    fs::write(root.join(RECORD), serde_json::to_string(&record).unwrap()).map_err(|e| e.to_string())?;

    let result = (|| -> Result<(usize, usize), String> {
        let mut zip = archive()?;
        for (i, f) in files.iter().enumerate() {
            emit(paths.len() + i, total, "Copiando traducción…");
            let mut data = Vec::with_capacity(f.size as usize);
            zip.by_name(&f.path).map_err(|e| e.to_string())?.read_to_end(&mut data).map_err(|e| e.to_string())?;
            use md5::Digest;
            if format!("{:x}", md5::Md5::digest(&data)) != f.md5 {
                return Err(format!("{} está dañado dentro del instalador", f.path));
            }
            fs::write(root.join(&f.path), &data).map_err(|e| format!("{}: {e}", f.path))?;
        }
        let by_key: HashMap<String, &PatchFile> =
            files.iter().map(|f| (format!("\\{}", f.path.replace('/', "\\")), f)).collect();
        let by_name: HashMap<&str, &PatchFile> =
            files.iter().map(|f| (f.path.rsplit('/').next().unwrap(), f)).collect();
        emit(total, total, "Actualizando versiones del juego…");
        let v = edit_lines(&root.join(VERSIONS), |line| {
            let key = line.rsplit_once('|')?.0;
            by_key.get(key).map(|f| format!("{key}|{}", f.md5))
        })?;
        let m = edit_lines(&root.join(MANI), |line| {
            let mut fields: Vec<String> = line.split('|').map(String::from).collect();
            let f = by_name.get(fields.first()?.as_str())?;
            if fields.len() < 4 {
                return None;
            }
            fields[1] = f.md5.clone();
            fields[2] = f.size.to_string();
            if let Some(crc) = f.crc {
                fields[3] = crc.to_string();
            }
            Some(fields.join("|"))
        })?;
        Ok((v, m))
    })();

    match result {
        Ok((v, m)) if v == files.len() && m == files.len() => {
            Ok(format!("Parche {VERSION} instalado: {} archivos traducidos.", files.len()))
        }
        Ok((v, m)) => Ok(format!(
            "Instalado, pero faltaron entradas (versiones {v}/{n}, mani {m}/{n}); el juego podría volver a descargar algunos archivos.",
            n = files.len()
        )),
        Err(e) => {
            let _ = restore_record(root);
            Err(format!("Falló la instalación y se restauraron los originales: {e}"))
        }
    }
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![patch_info, inspect_game, detect_game, install, restore])
        .run(tauri::generate_context!())
        .expect("error al iniciar la app");
}
