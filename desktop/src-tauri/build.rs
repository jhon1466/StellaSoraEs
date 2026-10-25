// Packs translations/pc-v<version> into the executable so the app is a single portable .exe.
use std::{env, fs, io::Write, path::Path};

fn add_dir(zip: &mut zip::ZipWriter<fs::File>, root: &Path, dir: &Path) {
    for entry in fs::read_dir(dir).expect("read patch dir") {
        let path = entry.expect("dir entry").path();
        if path.is_dir() {
            add_dir(zip, root, &path);
            continue;
        }
        let name = path.strip_prefix(root).unwrap().to_string_lossy().replace('\\', "/");
        let options = zip::write::SimpleFileOptions::default()
            .compression_method(zip::CompressionMethod::Deflated)
            .large_file(true);
        zip.start_file(name, options).expect("zip entry");
        zip.write_all(&fs::read(&path).expect("read payload")).expect("zip write");
    }
}

fn main() {
    let version = env::var("CARGO_PKG_VERSION").unwrap();
    let patch = Path::new("../../translations").join(format!("pc-v{version}"));
    if !patch.join("patch-files.json").is_file() {
        panic!("Falta {}; ejecuta tools/build_all.sh {version} primero", patch.display());
    }
    println!("cargo:rerun-if-changed={}", patch.join("patch-files.json").display());
    let out = Path::new(&env::var("OUT_DIR").unwrap()).join("patch.zip");
    let mut zip = zip::ZipWriter::new(fs::File::create(&out).expect("create patch.zip"));
    add_dir(&mut zip, &patch, &patch);
    zip.finish().expect("finish zip");
    tauri_build::build()
}
