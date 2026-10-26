# Stella Sora en español

Parche de traducción al español, hecho por fans, para **Stella Sora** (cliente global de Yostar, versión 2.0.0) en **PC (Windows)** y **Android**.

Traduce la historia principal, los eventos, las historias de personajes, los chats del teléfono, las citas, los diálogos de combate, la interfaz, las misiones, los objetos y los subtítulos de las cinemáticas. Los nombres de los personajes se mantienen en su versión original.

> Proyecto no oficial, sin relación con Yostar. El ejecutable del juego (`StellaSora.exe` / APK) no se modifica: solo se reemplazan archivos de recursos y siempre se guarda un respaldo para volver al inglés.

## Descargas

Todo está en la sección [**Releases**](../../releases/latest):

| Archivo | Para qué sirve |
|---|---|
| `StellaSoraES-<versión>-windows.exe` | Instalador portable para PC. No requiere instalación: ábrelo y listo. Trae el parche dentro. |
| `StellaSoraES-<versión>-android.apk` | App instaladora para Android (usa Shizuku). |
| `stella-sora-es-android-<versión>.zip` | Paquete del parche para Android; se elige desde la app. |

## Instalación en PC

1. Cierra Stella Sora.
2. Abre `StellaSoraES-<versión>-windows.exe`.
3. La app busca el juego en todos tus discos. Si lo tienes en otra ubicación, pulsa **Elegir carpeta** y selecciona la carpeta donde está `StellaSora.exe`.
4. Pulsa **Instalar en español**.

Para quitar el parche, pulsa **Volver al inglés**. Si una actualización del juego reemplaza los archivos, vuelve a instalar el parche (o espera una nueva versión si cambió el contenido).

## Instalación en Android

Requiere [Shizuku](https://github.com/RikkaApps/Shizuku/releases) para poder escribir en `Android/data`.

1. Instala y arranca Shizuku.
2. Instala `StellaSoraES-<versión>-android.apk` y ábrelo.
3. **Paso 1:** pulsa **Conectar** y concede el permiso de Shizuku.
4. **Paso 2:** elige el archivo `stella-sora-es-android-<versión>.zip`.
5. Cierra el juego y pulsa **Instalar en español**.

La app guarda un respaldo; **Volver al inglés** restaura los archivos originales.

## Cómo funciona

El cliente guarda el texto en tres sitios:

| Ubicación | Contenido | Herramienta |
|---|---|---|
| `Persistent_Store/Tables/data.arcx` | 247 tablas de idioma (JSON) y `BubbleData` (diálogos de combate) | `tools/arcx_v66.py`, `tools/patch_arcx_translations.py` |
| `Persistent_Store/Scripts/lua.arcx` | Scripts Lua 5.3 compilados de `Game/UI/Avg/_en/`: historia, eventos, historias de personajes, chats, citas | `tools/luac53.py`, `tools/translate_lua_avg.py` |
| `Persistent_Store/AssetBundles/*.en.unity3d` | Textos fijos de la interfaz | `tools/translate_game_resources.py`, `tools/unityfs.py` |
| `Persistent_Store/Videos/*_EN.srt` | Subtítulos de cinemáticas | `tools/retranslate_opus.py srt` |

Detalles importantes para que el juego acepte los archivos:

- **Bundles:** se reescriben con el mismo formato UnityFS del juego (flags `0x243`, LZ4HC). UnityPy los guarda con `0xC2` y el juego no puede abrirlos.
- **Manifiestos:** el juego verifica cada archivo en `Persistent_Store/ss_win.mani` / `ss_and.mani` (MD5, tamaño y CRC32 del contenido descomprimido) y en `file_versions.dat` (MD5). Los instaladores editan solo las líneas de los archivos traducidos y conservan los saltos de línea LF; con CRLF el juego descarta sus versiones y vuelve a descargarlo todo.
- **Lua:** solo se reescriben las constantes de texto del bytecode; los comandos (`SetTalk`, `avg1_103`…) quedan intactos.

## Traducción

La traducción es automática y local: [OPUS-MT tc-big en→es](https://huggingface.co/Helsinki-NLP/opus-mt-tc-big-en-es) (CC-BY 4.0) con CTranslate2 en una GPU CUDA. Todo el juego tarda unos minutos.

- Cada texto se divide en frases; las etiquetas (`<color>`, `<size>`), las variables (`{0}`, `&Param1&`, `==PLAYER_NAME==`) y los enlaces (`#Palabra#`) se protegen.
- Los nombres de [tools/character_names.json](tools/character_names.json) se sustituyen por seudónimos antes de traducir y se restauran después, para que no se traduzcan.
- Las letras que faltan en el vocabulario del modelo (`Ú`, `ü`…) se reconstruyen con un diccionario sacado de las demás traducciones.
- Algunos términos del juego se reescriben antes de traducir; por ejemplo, "Clear" (completar una fase) → "Complete".

Al ser traducción automática puede haber frases literales. Las correcciones son bienvenidas.

## Compilar desde el código

### Requisitos

- Python 3 con `ctranslate2`, `sentencepiece`, `lz4`, `UnityPy`, `Pillow`, `fonttools` y runtime CUDA (`nvidia-cublas-cu12`).
- Flutter estable (app Android).
- Node.js y Rust estable con la plantilla MSVC (app de PC).
- Archivos del juego: el cliente de PC instalado y una copia de la carpeta `files` de Android.

### 1. Modelo de traducción

Descarga `ooeoeo/opus-mt-tc-big-en-es-ct2-float16` de Hugging Face en `%LOCALAPPDATA%\StellaSoraESPatch\models\opus-mt-tc-big-en-es-ct2`.

### 2. Traducir y empaquetar

Las rutas al cliente y a los recursos en inglés están al principio de `tools/build_all.sh` y `tools/prepare_patch.py`; ajústalas a tu equipo.

```bash
bash tools/build_all.sh 0.2.8
```

Genera `translations/pc-v0.2.8/`, `translations/android-v0.2.8/` y `dist/stella-sora-es-android-0.2.8.zip`.

### 3. App de PC (Astro 5 · SolidJS · Tailwind CSS 4 · Tauri 2)

```bash
cd desktop
npm install
npx tauri build --no-bundle
```

El ejecutable portable queda en `desktop/src-tauri/target/release/stella-sora-es.exe` e incluye `translations/pc-v<versión>`. `npm run dev` abre la interfaz en el navegador con datos de ejemplo.

### 4. App Android (Flutter)

```bash
flutter pub get
flutter build apk --release
```

Para firmar en release crea `android/key.properties` (no se sube al repositorio):

```properties
storeFile=C:/ruta/release.jks
storePassword=...
keyAlias=...
keyPassword=...
```

`flutter test test/screenshot_test.dart` renderiza la pantalla en `build/android_preview.png`.

## Estructura

```
lib/main.dart            App Android (Shizuku)
desktop/                 App de PC (Astro + Solid + Tailwind + Tauri)
  src/components/App.tsx Interfaz
  src-tauri/src/main.rs  Detección del juego, instalación, respaldo y restauración
tools/                   Extracción, traducción y empaquetado
img/game, desktop/public/game   Arte e interfaz extraídos del cliente
fonts/                   MiSans (subconjunto latino) de la fuente del juego
```

## Aviso legal

Stella Sora, sus personajes, ilustraciones, logotipos y fuentes son propiedad de sus respectivos dueños (Yostar). Este proyecto es una traducción hecha por fans y sin ánimo de lucro. El código fuente no incluye archivos del juego; los paquetes de las releases contienen solo los recursos de texto modificados necesarios para aplicar la traducción sobre una instalación legítima. Modificar archivos del cliente puede ir contra los términos del juego: úsalo bajo tu responsabilidad.
