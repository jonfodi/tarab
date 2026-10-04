// tarab UI: talks to the local engine (tarab serve) over HTTP + server-sent events.
import { invoke } from "@tauri-apps/api/core";

type Backend = { port: number; token: string };
type Job = {
  id: number; query: string; status: string; message: string; group: string | null; buy_url: string | null;
  result: { tier: string | null; verdict: string | null; path: string | null; note: string; needs_listen: boolean } | null;
};

const $ = <T extends HTMLElement = HTMLElement>(sel: string) => document.querySelector(sel) as T;
let base = "";
let token = "";
const jobs = new Map<number, Job>();

// ------------------------------------------------------------------ backend

async function connect(): Promise<void> {
  for (;;) {
    try {
      const b = await invoke<Backend>("backend");
      base = `http://127.0.0.1:${b.port}`;
      token = b.token;
      return;
    } catch (e) {
      if (String(e) !== "engine is starting") return setConn(`engine error: ${e}`, "bad");
      await new Promise((r) => setTimeout(r, 300));
    }
  }
}

async function api<T = any>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(base + path, {
    method,
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await r.json();
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}

function listen(): void {
  const es = new EventSource(`${base}/events?token=${encodeURIComponent(token)}`);
  es.onmessage = (m) => {
    const msg = JSON.parse(m.data);
    if (msg.kind === "job") {
      jobs.set(msg.id, msg);
      renderJobs();
      if (["ok", "already"].includes(msg.status)) refreshListen();
    } else if (msg.kind === "event") {
      const j = jobs.get(msg.job);
      if (j && msg.event.kind !== "done") {
        j.message = msg.event.message;
        renderJobs();
      }
    } else if (msg.kind === "setup") {
      $("#setup-msg").textContent = msg.message;
    }
  };
  es.onerror = () => setConn("reconnecting…");
}

// ------------------------------------------------------------------ status

function setConn(text: string, cls = ""): void {
  const el = $("#conn");
  el.textContent = text;
  el.className = `pill ${cls}`;
}

async function refreshStatus(): Promise<any> {
  const s = await api("GET", "/status");
  if (!s.setup_done) setConn("not set up");
  else if (s.slskd.logged_in) setConn(`Soulseek: ${s.username}`, "ok");
  else if (s.slskd.error) setConn(s.slskd.error, "bad");
  else setConn(s.slskd.running ? "connecting to Soulseek…" : "Soulseek stopped", s.slskd.running ? "" : "bad");
  return s;
}

// ------------------------------------------------------------------ Get

const ICON: Record<string, string> = {
  queued: "·", running: "↓", ok: "✓", already: "=", queued_remote: "⏸", not_found: "✗", failed: "✗",
  low_only: "✗", error: "!", dry_run: "·",
};
const LABEL: Record<string, string> = {
  already: "already in your library", queued: "waiting in a peer's queue (added to wishlist)",
  not_found: "not on Soulseek right now (added to wishlist)", low_only: "only low quality available (added to wishlist)",
  failed: "no copy passed the checks",
};

function renderJobs(): void {
  const ul = $("#jobs");
  ul.innerHTML = "";
  for (const j of [...jobs.values()].reverse()) {
    const li = document.createElement("li");
    const status = j.status === "queued" && j.result ? "queued_remote" : j.status;
    const r = j.result;
    const listenFlag = r?.needs_listen ? ` <span class="listen">· listen</span>` : "";
    const detail = r ? (r.path ? `${r.tier ?? ""} · ${r.path}` : LABEL[j.status] ?? r.note) : j.message;
    const buy = j.buy_url && ["not_found", "failed", "low_only"].includes(j.status)
      ? ` · <a href="${j.buy_url}" target="_blank">buy it</a>` : "";
    li.innerHTML = `<span class="s-${j.status}">${ICON[status] ?? "·"}</span>
      <span class="q">${esc(j.query)}${listenFlag}</span><span class="s-${j.status}">${j.status.replace("_", " ")}</span>
      <span class="msg">${esc(detail ?? "")}${buy}</span>`;
    ul.append(li);
  }
}

async function getTracks(): Promise<void> {
  const text = $<HTMLTextAreaElement>("#tracks").value;
  try {
    const r = await api("POST", "/get", {
      text, title_first: $<HTMLInputElement>("#title-first").checked, upgrade: $<HTMLInputElement>("#upgrade").checked,
    });
    r.jobs.forEach((j: Job) => jobs.set(j.id, j));
    $<HTMLTextAreaElement>("#tracks").value = "";
    renderJobs();
  } catch (e) {
    alert(e);
  }
}

async function getRelease(): Promise<void> {
  const v = $<HTMLInputElement>("#release").value.trim();
  if (!v) return;
  const btn = $<HTMLButtonElement>("#get-release");
  btn.disabled = true;
  btn.textContent = "Looking up…";
  try {
    const r = await api("POST", "/get", v.startsWith("http") ? { url: v } : { release: v });
    r.jobs.forEach((j: Job) => jobs.set(j.id, j));
    $<HTMLInputElement>("#release").value = "";
    renderJobs();
  } catch (e) {
    alert(e);
  } finally {
    btn.disabled = false;
    btn.textContent = "Get release";
  }
}

// ------------------------------------------------------------------ Listen / Library / Wishlist

const fmt = (s: number | null) => (s ? `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}` : "");
const TIER = ["too low", "256", "320/V0", "lossless"];

async function refreshListen(): Promise<void> {
  const { tracks } = await api("GET", "/library");
  const unsure = tracks.filter((t: any) => t.verdict === "unsure" || t.verdict === "unknown");
  $("#listen-count").textContent = unsure.length ? `(${unsure.length})` : "";
  const ul = $("#listen-list");
  ul.innerHTML = unsure.length ? "" : `<li><span></span><span class="msg">Nothing to check.</span></li>`;
  for (const t of unsure) {
    const li = document.createElement("li");
    li.innerHTML = `<span class="listen">?</span><span class="q">${esc(t.artist)} - ${esc(t.title)}</span>
      <span><button data-v="ok">Sounds right</button> <button data-v="wrong">Wrong track</button></span>
      <span class="msg">${esc(t.note ?? "")} · ${fmt(t.length)} · ${esc(t.path)}</span>`;
    li.querySelectorAll("button").forEach((b) =>
      b.addEventListener("click", async () => {
        await api("POST", "/library/verdict", { key: t.key, verdict: b.dataset.v });
        refreshListen();
      }));
    ul.append(li);
  }
}

async function refreshLibrary(): Promise<void> {
  const { tracks } = await api("GET", "/library");
  const f = $<HTMLInputElement>("#lib-filter").value.toLowerCase();
  const rows = tracks.filter((t: any) => `${t.artist} ${t.title}`.toLowerCase().includes(f));
  $("#lib-count").textContent = `${rows.length} tracks`;
  $("#lib tbody").innerHTML = rows.map((t: any) => `<tr><td>${esc(t.artist)}</td><td>${esc(t.title)}</td>
    <td>${TIER[t.tier]}</td><td>${t.verdict}</td><td>${fmt(t.length)}</td></tr>`).join("");
}

async function refreshWishlist(): Promise<void> {
  const { wishes } = await api("GET", "/wishlist");
  const ul = $("#wish-list");
  ul.innerHTML = wishes.length ? "" : `<li><span></span><span class="msg">Empty.</span></li>`;
  for (const w of wishes) {
    const li = document.createElement("li");
    const when = w.last_check ? new Date(w.last_check * 1000).toLocaleString() : "not yet";
    li.innerHTML = `<span>⏳</span><span class="q">${esc(w.line)}</span><span><button>Remove</button></span>
      <span class="msg">last checked ${when}${w.status ? ` · ${w.status.replace("_", " ")}` : ""}</span>`;
    li.querySelector("button")!.addEventListener("click", async () => {
      await api("DELETE", "/wishlist", { key: w.key });
      refreshWishlist();
    });
    ul.append(li);
  }
}

// ------------------------------------------------------------------ Settings

const FIELDS: [string, string, "text" | "bool" | "int"][] = [
  ["hq_dir", "Music folder", "text"], ["make_lq", "Make 320k MP3 copies (old CDJs)", "bool"],
  ["lq_dir", "MP3 folder", "text"], ["share_hq", "Share music folder on Soulseek", "bool"],
  ["max_uploads", "Max simultaneous uploads", "int"], ["max_upload_kbps", "Upload speed limit (KiB/s, 0 = none)", "int"],
  ["min_tier", "Lowest quality to accept (1 = 256k, 2 = 320k, 3 = lossless only)", "int"],
  ["check_identity", "Check tracks against official audio", "bool"],
];

async function renderSettings(): Promise<void> {
  const s = await api("GET", "/settings");
  $("#settings-form").innerHTML = FIELDS.map(([k, label, t]) => t === "bool"
    ? `<label><span>${label}</span><input type="checkbox" name="${k}" ${s[k] ? "checked" : ""}></label>`
    : `<label><span>${label}</span><input name="${k}" value="${esc(String(s[k] ?? ""))}"></label>`).join("");
}

async function saveSettings(): Promise<void> {
  const form = $<HTMLFormElement>("#settings-form");
  const body: Record<string, unknown> = {};
  for (const [k, , t] of FIELDS) {
    const el = form.elements.namedItem(k) as HTMLInputElement;
    body[k] = t === "bool" ? el.checked : t === "int" ? Number(el.value) : el.value;
  }
  await api("PUT", "/settings", body);
  $("#settings-msg").textContent = "Saved.";
  setTimeout(() => ($("#settings-msg").textContent = ""), 2000);
}

// ------------------------------------------------------------------ setup

async function runSetup(): Promise<void> {
  const s = await api("GET", "/settings");
  const dlg = $<HTMLDialogElement>("#setup");
  const form = $<HTMLFormElement>("#setup-form");
  (form.elements.namedItem("hq_dir") as HTMLInputElement).value = s.hq_dir;
  (form.elements.namedItem("lq_dir") as HTMLInputElement).value = s.lq_dir;
  dlg.showModal();
  dlg.addEventListener("cancel", (e) => e.preventDefault());
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(form);
    const btn = $<HTMLButtonElement>("#setup-go");
    btn.disabled = true;
    $("#setup-msg").textContent = "Installing and connecting… (first time downloads slskd, ~60 MB)";
    try {
      const r = await api("POST", "/setup", {
        username: f.get("username"), password: f.get("password"), hq_dir: f.get("hq_dir"), lq_dir: f.get("lq_dir"),
        make_lq: f.get("make_lq") === "on", share_hq: f.get("share_hq") === "on",
      });
      if (r.logged_in) {
        dlg.close();
        refreshStatus();
      } else {
        $("#setup-msg").textContent = `Couldn't log in: ${r.error || r.state || "unknown error"}`;
      }
    } catch (err) {
      $("#setup-msg").textContent = String(err);
    } finally {
      btn.disabled = false;
    }
  });
}

// ------------------------------------------------------------------ boot

function esc(s: string): string {
  return s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);
}

function tabs(): void {
  document.querySelectorAll<HTMLButtonElement>("#tabs button").forEach((b) =>
    b.addEventListener("click", () => {
      document.querySelectorAll("#tabs button, .tab").forEach((el) => el.classList.remove("on"));
      b.classList.add("on");
      $(`#${b.dataset.tab}`).classList.add("on");
      ({ listen: refreshListen, wishlist: refreshWishlist, library: refreshLibrary, settings: renderSettings } as
        Record<string, () => void>)[b.dataset.tab!]?.();
    }));
}

async function boot(): Promise<void> {
  tabs();
  await connect();
  if (!base) return;
  listen();
  const s = await refreshStatus();
  (await api("GET", "/jobs")).jobs.forEach((j: Job) => jobs.set(j.id, j));
  renderJobs();
  refreshListen();
  if (!s.setup_done) runSetup();
  setInterval(refreshStatus, 5000);

  $("#get-tracks").addEventListener("click", getTracks);
  $("#get-release").addEventListener("click", getRelease);
  $("#clear-done").addEventListener("click", async () => {
    jobs.clear();
    (await api("POST", "/jobs/clear")).jobs.forEach((j: Job) => jobs.set(j.id, j));
    renderJobs();
  });
  $("#wish-add").addEventListener("click", async () => {
    await api("POST", "/wishlist", { text: $<HTMLInputElement>("#wish-line").value });
    $<HTMLInputElement>("#wish-line").value = "";
    refreshWishlist();
  });
  $("#lib-filter").addEventListener("input", refreshLibrary);
  $("#save-settings").addEventListener("click", saveSettings);
  $("#import").addEventListener("click", async () => {
    $("#settings-msg").textContent = "Indexing…";
    const r = await api("POST", "/library/import", {});
    $("#settings-msg").textContent = `Indexed ${r.imported} tracks from ${r.folder}: tarab won't download them again.`;
  });
  $("#make-lq").addEventListener("click", async () => {
    $("#settings-msg").textContent = "Converting…";
    const r = await api("POST", "/lq");
    $("#settings-msg").textContent = `${r.files.length} tracks in the MP3 folder.`;
  });
}

boot();
