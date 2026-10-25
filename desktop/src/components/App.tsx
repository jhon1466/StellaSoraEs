import { createSignal, For, onCleanup, onMount, Show } from "solid-js";
import { invoke as tauriInvoke, isTauri } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { open } from "@tauri-apps/plugin-dialog";

type GameInfo = {
  valid: boolean;
  path: string;
  client: string;
  resource: string;
  installed: string | null;
  running: boolean;
  message: string;
};
type PatchInfo = { version: string; files: number; bytes: number };
type Progress = { done: number; total: number; label: string };
type Line = { kind: "ok" | "error" | "info"; text: string };

// In a plain browser (design preview) answer with sample data instead of the Rust backend.
const PREVIEW: Record<string, unknown> = {
  patch_info: { version: "0.2.8", files: 74, bytes: 104_000_000 },
  detect_game: "C:\\YostarGames\\StellaSora_EN",
  inspect_game: { valid: true, path: "C:\\YostarGames\\StellaSora_EN", client: "2.0.0", resource: "2610211926", installed: null, running: false, message: "" },
};
const invoke = <T,>(cmd: string, args?: Record<string, unknown>): Promise<T> =>
  isTauri() ? tauriInvoke<T>(cmd, args) : Promise.resolve(PREVIEW[cmd] as T);

// Story CGs extracted from the client (imageavg bundles).
const CG = ["/game/cg1.jpg", "/game/cg2.jpg", "/game/cg3.jpg", "/game/cg4.jpg", "/game/cg5.jpg"];

function Star(props: { class?: string }) {
  return (
    <svg class={props.class} viewBox="0 0 12 12" width="12" height="12" aria-hidden="true">
      <path d="M6 0 7.2 4.8 12 6 7.2 7.2 6 12 4.8 7.2 0 6 4.8 4.8z" fill="currentColor" />
    </svg>
  );
}

function SectionTitle(props: { children: string }) {
  return (
    <div class="flex items-center gap-2">
      <span class="h-4 w-1.5 rounded-sm bg-teal" />
      <h3 class="text-[15px] font-bold tracking-wide text-ink">{props.children}</h3>
      <span class="ml-1 h-px flex-1 bg-line" />
    </div>
  );
}

export default function App() {
  const win = isTauri() ? getCurrentWindow() : undefined;
  const [cg, setCg] = createSignal(0);
  const [patch, setPatch] = createSignal<PatchInfo>();
  const [game, setGame] = createSignal<GameInfo>();
  const [busy, setBusy] = createSignal(false);
  const [progress, setProgress] = createSignal<Progress>();
  const [line, setLine] = createSignal<Line>({ kind: "info", text: "Buscando la carpeta de Stella Sora en tus discos…" });

  const percent = () => {
    const p = progress();
    return p && p.total ? Math.min(100, Math.round((p.done / p.total) * 100)) : 0;
  };
  const canInstall = () => !busy() && !!game()?.valid && !game()?.running;

  async function inspect(path: string) {
    const info = await invoke<GameInfo>("inspect_game", { path });
    setGame(info);
    if (!info.valid) setLine({ kind: "error", text: info.message });
    else if (info.running) setLine({ kind: "error", text: "El juego está abierto. Ciérralo antes de instalar el parche." });
    else if (info.installed) setLine({ kind: "ok", text: `La traducción ${info.installed} ya está instalada. Puedes reinstalarla o volver al inglés.` });
    else setLine({ kind: "info", text: "Encontré el juego. Cuando quieras, pulsa «Instalar en español»." });
  }

  async function detect() {
    const found = await invoke<string | null>("detect_game");
    if (found) await inspect(found);
    else setLine({ kind: "info", text: "No encontré el juego automáticamente. Elige la carpeta donde está StellaSora.exe." });
  }

  async function choose() {
    if (!isTauri()) return;
    const picked = await open({ directory: true, title: "Carpeta de Stella Sora (donde está StellaSora.exe)" });
    if (typeof picked === "string") await inspect(picked);
  }

  async function run(command: "install" | "restore") {
    const g = game();
    if (!g) return;
    setBusy(true);
    setProgress({ done: 0, total: 1, label: command === "install" ? "Preparando…" : "Restaurando…" });
    try {
      setLine({ kind: "ok", text: await invoke<string>(command, { path: g.path }) });
    } catch (e) {
      setLine({ kind: "error", text: String(e) });
    } finally {
      setBusy(false);
      setProgress(undefined);
      await inspect(g.path);
    }
  }

  onMount(async () => {
    const timer = setInterval(() => setCg((i) => (i + 1) % CG.length), 8000);
    const unlisten = isTauri() ? await listen<Progress>("progress", (e) => setProgress(e.payload)) : () => {};
    onCleanup(() => {
      clearInterval(timer);
      unlisten();
    });
    setPatch(await invoke<PatchInfo>("patch_info"));
    await detect();
  });

  return (
    <div class="relative h-screen w-screen overflow-hidden text-white">
      {/* Story CG backdrop */}
      <For each={CG}>
        {(src, i) => (
          <img
            src={src}
            alt=""
            class="animate-pan absolute inset-0 h-full w-full object-cover transition-opacity duration-[1800ms] ease-in-out"
            classList={{ "opacity-100": cg() === i(), "opacity-0": cg() !== i() }}
          />
        )}
      </For>
      <div class="absolute inset-0 bg-[radial-gradient(ellipse_at_25%_20%,transparent_30%,rgb(8_12_26/0.45)_100%)]" />

      {/* Window bar */}
      <header data-tauri-drag-region class="absolute inset-x-0 top-0 z-30 flex h-9 items-center justify-end gap-1 px-2">
        <span data-tauri-drag-region class="mr-auto pl-3 text-[11px] font-semibold text-white/70 [text-shadow:0_1px_2px_#000]">
          {game()?.valid ? `${game()!.client}.${game()!.resource.slice(-3)}` : ""}
        </span>
        <button class="grid h-7 w-9 place-items-center rounded text-white/80 hover:bg-white/20" onClick={() => win?.minimize()} aria-label="Minimizar">
          <svg width="11" height="11" viewBox="0 0 12 12"><path d="M1 6h10" stroke="currentColor" stroke-width="1.6" /></svg>
        </button>
        <button class="grid h-7 w-9 place-items-center rounded text-white/80 hover:bg-coral" onClick={() => win?.close()} aria-label="Cerrar">
          <svg width="11" height="11" viewBox="0 0 12 12"><path d="M2 2l8 8M10 2l-8 8" stroke="currentColor" stroke-width="1.6" /></svg>
        </button>
      </header>

      {/* Logo */}
      <div class="absolute left-9 top-10 z-10">
        <img src="/game/logo.png" alt="Stella Sora" class="w-[300px] drop-shadow-[0_4px_18px_rgba(40,120,255,0.35)]" />
        <div class="-mt-3 ml-3 inline-flex items-center gap-2 rounded-sm bg-ink/85 px-3 py-1 text-[12px] font-bold tracking-[0.2em]">
          <Star class="text-teal" />
          EDICIÓN EN ESPAÑOL
          <span class="text-teal">v{patch()?.version ?? ""}</span>
        </div>
      </div>

      {/* Popup panel */}
      <section class="popup animate-in absolute right-9 top-12 z-20 w-[400px] p-6 text-ink">
        <SectionTitle>Carpeta del juego</SectionTitle>
        <div class="mt-3 rounded-lg border border-line bg-white/70 px-3 py-2.5">
          <div class="truncate font-mono text-[12.5px] text-ink" title={game()?.path}>
            {game()?.path || "Sin seleccionar"}
          </div>
          <div class="mt-1.5 flex flex-wrap gap-1.5 text-[11px] font-semibold">
            <Show when={game()?.valid} fallback={<span class="text-coral">{game()?.message || "Buscando…"}</span>}>
              <span class="rounded bg-ink/8 px-1.5 py-0.5 text-ink-soft">Cliente {game()!.client}</span>
              <span class="rounded bg-ink/8 px-1.5 py-0.5 text-ink-soft">Recursos {game()!.resource}</span>
              <Show when={game()!.installed}>
                <span class="rounded bg-teal/20 px-1.5 py-0.5 text-[#0f8f95]">{game()!.installed === "anterior" ? "Español instalado" : `Español ${game()!.installed}`}</span>
              </Show>
            </Show>
          </div>
        </div>
        <div class="mt-3 grid grid-cols-2 gap-2">
          <button class="btn-sprite btn-white h-[46px] text-[13px] font-bold" disabled={busy()} onClick={detect}>
            Detectar
          </button>
          <button class="btn-sprite btn-white h-[46px] text-[13px] font-bold" disabled={busy()} onClick={choose}>
            Elegir carpeta
          </button>
        </div>

        <div class="mt-6">
          <SectionTitle>Contenido</SectionTitle>
          <ul class="mt-3 space-y-1.5 text-[12.5px] text-ink-soft">
            <For each={["Historia principal, eventos e historias de personajes", "Chats del teléfono, citas y diálogos de combate", "Interfaz, misiones, objetos y subtítulos", "Nombres de personajes en su versión original"]}>
              {(item) => (
                <li class="flex items-center gap-2">
                  <Star class="shrink-0 text-teal" />
                  {item}
                </li>
              )}
            </For>
          </ul>
        </div>

        <div class="mt-6">
          <button class="btn-sprite btn-teal relative h-[62px] w-full text-[17px] font-bold tracking-wider" disabled={!canInstall()} onClick={() => run("install")}>
            {busy() ? `${progress()?.label ?? ""}` : game()?.installed ? "Reinstalar en español" : "Instalar en español"}
          </button>
          <Show when={busy()}>
            <div class="relative mt-3 h-1.5 rounded-full bg-line">
              <div class="h-full rounded-full bg-teal transition-[width] duration-300" style={{ width: `${percent()}%` }} />
              <span class="absolute -top-[3px] -ml-1.5 text-teal transition-[left] duration-300" style={{ left: `${percent()}%` }}>
                <Star />
              </span>
            </div>
          </Show>
          <div class="mt-2 flex items-center justify-between text-[11px] text-ink-soft">
            <span>{patch() ? `${patch()!.files} archivos · ${Math.round(patch()!.bytes / 1048576)} MB · con respaldo` : ""}</span>
            <Show when={game()?.installed && !busy()}>
              <button class="btn-sprite btn-coral h-[34px] text-[11.5px] font-bold [border-image-width:14px_16px]" onClick={() => run("restore")}>
                Volver al inglés
              </button>
            </Show>
          </div>
        </div>
      </section>

      {/* AVG dialogue box used as the status line */}
      <footer class="avg-box absolute inset-x-0 bottom-0 z-10 px-14 pb-7 pt-16">
        <div class="text-[17px] font-semibold [text-shadow:0_1px_3px_#000]">Stella Sora ES</div>
        <div class="relative mt-2 flex items-center">
          <Star class="-ml-1.5 text-white/80" />
          <div class="avg-rule flex-1" />
          <Star class="-mr-1.5 text-white/80" />
        </div>
        <p
          class="mt-3 min-h-[28px] text-[19px] leading-snug [text-shadow:0_1px_3px_#000]"
          classList={{ "text-[#ffd2dc]": line().kind === "error", "text-white": line().kind !== "error" }}
        >
          {line().text}
        </p>
        <div class="absolute bottom-7 right-14 flex gap-2">
          <For each={CG}>
            {(_, i) => (
              <button
                class="h-2 w-2 rotate-45 transition-colors"
                classList={{ "bg-teal": cg() === i(), "bg-white/40 hover:bg-white/70": cg() !== i() }}
                onClick={() => setCg(i())}
                aria-label={`Ilustración ${i() + 1}`}
              />
            )}
          </For>
        </div>
      </footer>
    </div>
  );
}
