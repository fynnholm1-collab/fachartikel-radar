// Fachartikel-Radar: Oberfläche. Daten kommen aus data/articles.json (täglich per GitHub Actions erzeugt).

const $ = (sel) => document.querySelector(sel);
const STORE = "radar-status";

let articles = [];
let backlog = null; // wird erst beim Öffnen des Reiters geladen
let meta = {};
let offen = new Set(JSON.parse(localStorageGet("radar-offen") || "[]"));
let status = loadStatus();
let tab = localStorageGet("radar-tab") || "eingang";

function localStorageGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function localStorageSet(k, v) { try { localStorage.setItem(k, v); } catch { /* privat/blockiert */ } }
function loadStatus() { try { return JSON.parse(localStorage.getItem(STORE) || "{}"); } catch { return {}; } }
function saveStatus() { localStorageSet(STORE, JSON.stringify(status)); }
function st(id) { return status[id] || {}; }
function setSt(id, patch) { status[id] = { ...st(id), ...patch, t: new Date().toISOString() }; saveStatus(); }

const TABS = [
  { id: "eingang", label: "Posteingang", quelle: "neu", filter: (a) => !a.rezension, sichtung: true, gruppen: "heft" },
  { id: "backlog", label: "Backlog", quelle: "alt", filter: (a) => !a.rezension, sichtung: true, gruppen: "heft" },
  { id: "rezensionen", label: "Rezensionen", quelle: "neu", filter: (a) => a.rezension, sichtung: true, gruppen: "buch" },
  { id: "gemerkt", label: "Gemerkt", quelle: "alle", filter: (a) => ["wichtig", "spaeter"].includes(st(a.id).s) },
  { id: "buecher", label: "Bücher bestellen", quelle: "alle", filter: (a) => st(a.id).b },
];

function pool(t) {
  if (t.quelle === "neu") return articles;
  if (t.quelle === "alt") return backlog || [];
  return backlog ? articles.concat(backlog) : articles;
}

const KATALOG = {
  vorhanden: ["ok", "in der UB vorhanden"],
  nicht_gefunden: ["warn", "nicht in der UB"],
  im_verbund: ["", "im Verbund K10plus, Tübingen bitte prüfen"],
  wahrscheinlich: ["", "wahrscheinlich vorhanden, bitte prüfen"],
  unsicher: ["", "Bestand unklar, bitte prüfen"],
  fehler: ["", "Katalog nicht erreichbar"],
};

function fmtDate(iso) {
  if (!iso) return "";
  const [y, m, d] = iso.split("-");
  return d ? `${d}.${m}.${y}` : m ? `${m}/${y}` : y;
}

function dayLabel(iso) {
  const today = new Date().toISOString().slice(0, 10);
  const yest = new Date(Date.now() - 864e5).toISOString().slice(0, 10);
  if (iso === today) return "Heute neu";
  if (iso === yest) return "Gestern neu";
  return `Neu seit ${fmtDate(iso)}`;
}

function visible() {
  const t = TABS.find((x) => x.id === tab) || TABS[0];
  const q = $("#suche").value.trim().toLowerCase();
  const nurNeue = $("#nur-neue").checked && t.sichtung;
  const kat = $("#kategorie").value;
  return pool(t).filter((a) => {
    if (!t.filter(a)) return false;
    if (kat && t.sichtung && a.kategorie !== kat) return false;
    const s = st(a.id).s;
    if (t.sichtung && s === "ignoriert") return false;
    if (nurNeue && s) return false;
    if (q) {
      const hay = `${a.title} ${a.authors.join(" ")} ${a.journal} ${a.abstract}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

function renderTabs() {
  const nav = $("#tabs");
  nav.innerHTML = "";
  for (const t of TABS) {
    const b = document.createElement("button");
    b.setAttribute("role", "tab");
    b.setAttribute("aria-selected", String(t.id === tab));
    b.textContent = t.label;
    const n = t.quelle === "alt" && !backlog ? (meta.backlog || 0) : pool(t).filter((a) => t.filter(a) && (!t.sichtung || !st(a.id).s)).length;
    if (n) {
      const z = document.createElement("span");
      z.className = "zahl";
      z.textContent = n;
      b.append(z);
    }
    b.onclick = () => { tab = t.id; localStorageSet("radar-tab", tab); show(); };
    nav.append(b);
  }
}

function card(a) {
  const node = $("#tpl-karte").content.firstElementChild.cloneNode(true);
  const s = st(a.id);
  node.classList.toggle("neu", !s.s);
  node.classList.toggle("markiert", !!a.hervorheben);
  node.classList.toggle("gesehen", !!s.s && s.s !== "wichtig");
  node.querySelector(".journal").textContent = a.journal;
  node.querySelector(".datum").textContent = fmtDate(a.published);
  if (a.rezension && a.buch) node.querySelector(".journal").textContent = `Rezension in ${a.journal}`;
  const link = node.querySelector(".titel a");
  link.textContent = a.title;
  link.href = a.url;
  node.querySelector(".autoren").textContent = a.authors.slice(0, 6).join(", ") + (a.authors.length > 6 ? " u. a." : "");

  const badges = node.querySelector(".badges");
  const badge = (text, cls = "") => { const b = document.createElement("span"); b.className = `badge ${cls}`; b.textContent = text; badges.append(b); return b; };
  if (a.hervorheben) badge("hervorgehoben", "hl");
  for (const t of a.themen || []) badge(t);
  if (a.oa_url) badge("Open Access", "ok");
  if (s.s === "wichtig") badge(s.z ? "in Zotero gespeichert" : "wichtig", "ok");
  if (s.s === "spaeter") badge("später lesen");
  if (s.b) badge(s.bz ? "Buch vorgemerkt (Zotero)" : "Buch vorgemerkt", "ok");
  if (a.rezension && a.katalog) {
    const [cls, text] = KATALOG[a.katalog.status] || ["", a.katalog.status];
    badge(text, cls);
  }

  const abs = node.querySelector(".abstract");
  if (a.abstract) abs.querySelector("p").textContent = a.abstract; else abs.remove();
  node.querySelector(".hinweis").textContent = a.hinweis || "";
  node.querySelector(".kommentar").textContent = [s.c, s.bc].filter(Boolean).join("\n\n");

  const open = node.querySelector(".oeffnen");
  open.href = a.url;
  open.onclick = () => { if (!st(a.id).s) { setSt(a.id, { s: "gesehen" }); } };

  const wichtig = node.querySelector(".wichtig");
  wichtig.onclick = () => openSave(a, "artikel");
  wichtig.textContent = s.s === "wichtig" ? "Kommentar ändern" : "Kommentieren & speichern";

  const later = node.querySelector(".spaeter");
  later.textContent = s.s === "spaeter" ? "Nicht mehr merken" : "Später";
  later.onclick = () => { setSt(a.id, { s: s.s === "spaeter" ? "gesehen" : "spaeter" }); render(); };

  const ign = node.querySelector(".ignorieren");
  ign.onclick = () => { setSt(a.id, { s: "ignoriert" }); render(); };

  if (a.rezension) {
    const knoepfe = node.querySelector(".knoepfe");
    if (a.katalog && a.katalog.link) {
      const k = document.createElement("a");
      k.className = "btn ghost"; k.target = "_blank"; k.rel = "noopener";
      k.href = a.katalog.link; k.textContent = "Im Katalog suchen";
      knoepfe.insertBefore(k, wichtig);
    }
    const buch = document.createElement("button");
    buch.textContent = s.b ? "Vormerkung ändern" : "Buch vormerken";
    buch.onclick = () => openSave(a, "buch");
    knoepfe.insertBefore(buch, wichtig);
    wichtig.classList.add("ghost");
  }
  return node;
}

function heftLabel(a) {
  const teile = [];
  if (a.band) teile.push(`Bd. ${a.band}`);
  if (a.heft) teile.push(`Heft ${a.heft}`);
  const jahr = (a.published || "").slice(0, 4);
  if (teile.length) return `${teile.join(", ")}${jahr ? ` (${jahr})` : ""}`;
  return a.published ? `ohne Heftangabe, ${fmtDate(a.published.slice(0, 7))}` : "ohne Heftangabe";
}

function seiteNum(a) { const n = parseInt(a.seite, 10); return Number.isFinite(n) ? n : 1e9; }

function journalRank(name) {
  const order = meta.reihenfolge || [];
  const i = order.indexOf(name);
  return i < 0 ? order.length : i;
}

function groupBy(items, keyFn) {
  const m = new Map();
  for (const a of items) {
    const k = keyFn(a);
    if (!m.has(k)) m.set(k, []);
    m.get(k).push(a);
  }
  return m;
}

function gruppe(id, titel, items, inhalt) {
  const d = document.createElement("details");
  d.className = "block";
  d.open = offen.has(id);
  d.ontoggle = () => {
    if (d.open) offen.add(id); else offen.delete(id);
    localStorageSet("radar-offen", JSON.stringify([...offen]));
    if (d.open && !d.dataset.gefuellt) { d.dataset.gefuellt = "1"; inhalt(d); }
  };
  const sum = document.createElement("summary");
  const h = document.createElement("span");
  h.className = "block-titel";
  h.textContent = titel;
  const n = items.filter((a) => !st(a.id).s).length;
  const z = document.createElement("span");
  z.className = "zahl";
  z.textContent = n ? `${n} neu` : `${items.length}`;
  sum.append(h, z);
  if (n) {
    const btn = document.createElement("button");
    btn.className = "ghost klein";
    btn.textContent = "alle gesichtet";
    btn.onclick = (e) => { e.preventDefault(); markSeen(items); };
    sum.append(btn);
  }
  d.append(sum);
  if (d.open) { d.dataset.gefuellt = "1"; inhalt(d); }
  return d;
}

function renderHefte(list, items) {
  const byJournal = groupBy(items, (a) => a.journal);
  const journals = [...byJournal.keys()].sort((x, y) => journalRank(x) - journalRank(y) || x.localeCompare(y));
  for (const j of journals) {
    const arts = byJournal.get(j);
    list.append(gruppe(`j:${tab}:${j}`, j, arts, (box) => {
      const hefte = groupBy(arts, heftLabel);
      const keys = [...hefte.keys()].sort((x, y) => {
        const px = hefte.get(x)[0].published || "", py = hefte.get(y)[0].published || "";
        return py.localeCompare(px);
      });
      for (const k of keys) {
        const h = document.createElement("h3");
        h.className = "heft";
        h.textContent = k;
        box.append(h);
        for (const a of hefte.get(k).sort((x, y) => seiteNum(x) - seiteNum(y) || x.title.localeCompare(y.title))) box.append(card(a));
      }
    }));
  }
}

function renderBuecher(list, items) {
  const kats = meta.kategorien || [];
  const byKat = groupBy(items, (a) => a.kategorie || "Weitere Geschichte");
  const order = [...byKat.keys()].sort((x, y) => (kats.indexOf(x) + 1 || 99) - (kats.indexOf(y) + 1 || 99));
  for (const k of order) {
    const h = document.createElement("h2");
    h.className = "gruppe";
    h.textContent = k;
    list.append(h);
    const byBook = groupBy(byKat.get(k), (a) => a.buch || a.id);
    const books = [...byBook.entries()].sort((x, y) => y[1].length - x[1].length || (y[1][0].published || "").localeCompare(x[1][0].published || ""));
    for (const [key, revs] of books) {
      const titel = revs[0].buchtitel || (revs[0].katalog && revs[0].katalog.buchtitel) || revs[0].title;
      const label = revs.length > 1 ? `${titel} · ${revs.length} Rezensionen` : titel;
      list.append(gruppe(`b:${key}`, label, revs, (box) => { for (const a of revs) box.append(card(a)); }));
    }
  }
}

function markSeen(items) {
  const now = new Date().toISOString();
  for (const a of items) if (!st(a.id).s) status[a.id] = { s: "gesehen", t: now };
  saveStatus();
  render();
}

async function show() {
  const t = TABS.find((x) => x.id === tab) || TABS[0];
  if (t.quelle !== "neu" && backlog === null && (t.quelle === "alt" || meta.backlog)) {
    $("#liste").innerHTML = '<p class="leer">Backlog wird geladen …</p>';
    try { backlog = await fetch("data/backlog.json", { cache: "no-cache" }).then((r) => r.json()); } catch { backlog = []; }
  }
  render();
}

function render() {
  renderTabs();
  const list = $("#liste");
  list.innerHTML = "";
  const items = visible();
  const t = TABS.find((x) => x.id === tab) || TABS[0];
  $("#anzahl").textContent = `${items.length} ${items.length === 1 ? "Eintrag" : "Einträge"}`;
  $("#alle-gesehen").hidden = !t.sichtung || !items.some((a) => !st(a.id).s);
  $("#nur-neue").parentElement.hidden = !t.sichtung;
  $("#kategorie").hidden = !t.sichtung;
  $("#alle-auf").hidden = !t.gruppen;
  $("#hilfe").textContent = {
    eingang: `Artikel der letzten ${meta.posteingang_monate || 6} Monate, nach Zeitschrift und Heft. Rezensionen und Editorials stehen nicht hier.`,
    backlog: `Ältere Artikel seit ${fmtDate(meta.abruf_ab || "2023-01-01")}, nach Zeitschrift und Heft.`,
    rezensionen: "Rezensionen nach Buch, mehrere Rezensionen zum selben Buch stehen zusammen.",
  }[tab] || "";

  if (tab === "buecher") list.append(bookToolbar(items));
  if (!items.length) {
    const p = document.createElement("p");
    p.className = "leer";
    p.textContent = t.sichtung && $("#nur-neue").checked ? "Alles gesichtet. Neue Artikel kommen jeden Morgen dazu." : "Hier ist noch nichts.";
    list.append(p);
    return;
  }
  if (t.gruppen === "heft") return renderHefte(list, items);
  if (t.gruppen === "buch") return renderBuecher(list, items);
  const sorted = [...items].sort((x, y) => (st(y.id).t || "").localeCompare(st(x.id).t || ""));
  for (const a of sorted) list.append(card(a));
}

function bookToolbar(items) {
  const div = document.createElement("div");
  div.className = "row";
  div.style.margin = "8px 0";
  const copy = document.createElement("button");
  copy.className = "ghost";
  copy.textContent = "Liste kopieren";
  copy.onclick = async () => {
    const text = items.map((a) => {
      const k = a.katalog || {};
      return `- ${k.buchtitel || a.title}${k.isbn ? ` (ISBN ${k.isbn})` : ""}\n  Rezension: ${a.journal}, ${a.url}${st(a.id).bc ? `\n  Notiz: ${st(a.id).bc}` : ""}`;
    }).join("\n");
    try { await navigator.clipboard.writeText(text); copy.textContent = "Kopiert"; } catch { prompt("Liste zum Kopieren:", text); }
  };
  div.append(copy);
  const url = meta.katalog && meta.katalog.anschaffungsvorschlag;
  if (url) {
    const a = document.createElement("a");
    a.className = "btn ghost"; a.href = url; a.target = "_blank"; a.rel = "noopener";
    a.textContent = "Anschaffungsvorschlag an die UB";
    div.append(a);
  }
  return div;
}

// ---------------------------------------------------------------- Speichern-Dialog

let current = null;

function openSave(a, mode) {
  current = { a, mode };
  const s = st(a.id);
  const dlg = $("#dlg-speichern");
  const z = meta.zotero || {};
  $("#sp-titel").textContent = mode === "buch" ? "Buch vormerken" : "Als wichtig speichern";
  $("#sp-artikel").textContent = mode === "buch" ? ((a.katalog && a.katalog.buchtitel) || a.title) : a.title;
  $("#sp-kommentar").value = (mode === "buch" ? s.bc : s.c) || "";
  $("#sp-ordner-wrap").hidden = mode === "buch";
  const sel = $("#sp-ordner");
  sel.innerHTML = "";
  for (const name of z.unterordner || []) {
    const o = document.createElement("option");
    o.value = name; o.textContent = `${z.eingang} / ${name}`;
    if (name === a.thema) o.selected = true;
    sel.append(o);
  }
  const hasZotero = !!Zotero.creds();
  $("#sp-ok").textContent = hasZotero ? (mode === "buch" ? "Vormerken und in Zotero speichern" : "In Zotero speichern") : "Zotero einrichten";
  $("#sp-status").textContent = (mode === "buch" ? s.bz : s.z) ? "Bereits in Zotero gespeichert; erneutes Speichern legt einen zweiten Eintrag an." : "";
  dlg.returnValue = "";
  dlg.showModal();
}

$("#dlg-speichern").addEventListener("close", async () => {
  const dlg = $("#dlg-speichern");
  if (!current || !["merken", "default"].includes(dlg.returnValue)) return;
  const { a, mode } = current;
  const comment = $("#sp-kommentar").value.trim();
  const ordner = $("#sp-ordner").value;
  const patch = mode === "buch" ? { b: true, bc: comment } : { s: "wichtig", c: comment };
  setSt(a.id, patch);
  render();
  if (dlg.returnValue !== "default") return;
  if (!Zotero.creds()) { $("#dlg-einstellungen").showModal(); return; }
  const z = meta.zotero || {};
  try {
    if (mode === "buch") {
      const col = await Zotero.ensureCollection(z.anschaffung);
      const key = await Zotero.saveBook(a, { comment, collectionKey: col, tag: z.tag || "radar" });
      setSt(a.id, { bz: key });
    } else {
      const col = await Zotero.ensureCollection(ordner, z.eingang);
      const key = await Zotero.saveArticle(a, { comment, collectionKey: col, tag: z.tag || "radar" });
      setSt(a.id, { z: key });
    }
    toast("In Zotero gespeichert.");
  } catch (e) {
    toast(`Nicht gespeichert: ${e.message}`, true);
  }
  render();
});

function toast(text, error = false) {
  const t = document.createElement("div");
  t.textContent = text;
  Object.assign(t.style, {
    position: "fixed", left: "50%", bottom: "24px", transform: "translateX(-50%)", zIndex: 20,
    background: error ? "var(--highlight)" : "var(--text)", color: "var(--surface)",
    padding: "10px 16px", borderRadius: "10px", maxWidth: "90vw",
  });
  document.body.append(t);
  setTimeout(() => t.remove(), error ? 7000 : 2500);
}

// ---------------------------------------------------------------- Einstellungen

$("#btn-einstellungen").onclick = () => {
  $("#z-uid").value = localStorageGet("z-uid") || "";
  $("#z-key").value = localStorageGet("z-key") || "";
  $("#z-status").textContent = "";
  const js = $("#journal-status");
  js.innerHTML = "";
  for (const [name, s] of Object.entries(meta.journals || {}).sort()) {
    const row = document.createElement("div");
    const n = document.createElement("span"); n.textContent = name;
    const v = document.createElement("span"); v.textContent = s;
    if (/Fehler|nicht/.test(s)) v.className = "fehler";
    row.append(n, v);
    js.append(row);
  }
  $("#dlg-einstellungen").showModal();
};
for (const id of ["z-uid", "z-key"]) {
  $(`#${id}`).addEventListener("change", (e) => {
    localStorageSet(id, e.target.value.trim());
    for (const k of Object.keys(localStorage)) if (k.startsWith("z-col:")) localStorage.removeItem(k);
  });
}
$("#z-test").onclick = async () => {
  localStorageSet("z-uid", $("#z-uid").value.trim());
  localStorageSet("z-key", $("#z-key").value.trim());
  $("#z-status").textContent = "prüfe …";
  try { $("#z-status").textContent = await Zotero.test(); } catch (e) { $("#z-status").textContent = e.message; }
};
$("#export").onclick = () => {
  const blob = new Blob([JSON.stringify(status, null, 1)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `radar-sichtung-${new Date().toISOString().slice(0, 10)}.json`;
  a.click();
};
$("#import").onchange = async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  try {
    const incoming = JSON.parse(await file.text());
    for (const [id, v] of Object.entries(incoming)) {
      if (!status[id] || (v.t || "") > (status[id].t || "")) status[id] = v;
    }
    saveStatus();
    render();
    toast("Sichtungsstand übernommen.");
  } catch { toast("Datei konnte nicht gelesen werden.", true); }
};

// ---------------------------------------------------------------- Start

$("#suche").addEventListener("input", render);
$("#nur-neue").addEventListener("change", render);
$("#kategorie").addEventListener("change", () => { localStorageSet("radar-kat", $("#kategorie").value); render(); });
$("#alle-gesehen").onclick = () => markSeen(visible());
$("#alle-auf").onclick = () => {
  const blocks = [...document.querySelectorAll("#liste details.block")];
  const auf = blocks.some((d) => !d.open);
  for (const d of blocks) d.open = auf;
};

async function load() {
  try {
    const [arts, m] = await Promise.all([
      fetch("data/articles.json", { cache: "no-cache" }).then((r) => r.json()),
      fetch("data/meta.json", { cache: "no-cache" }).then((r) => r.json()).catch(() => ({})),
    ]);
    articles = arts;
    meta = m;
    const sel = $("#kategorie");
    for (const k of meta.kategorien || []) { const o = document.createElement("option"); o.value = k; o.textContent = k; sel.append(o); }
    sel.value = localStorageGet("radar-kat") || "";
    if (meta.stand) $("#stand").textContent = `Stand ${new Date(meta.stand).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" })}`;
  } catch {
    $("#liste").innerHTML = '<p class="leer">Noch keine Daten. Der erste Abruf läuft täglich morgens.</p>';
    return;
  }
  show();
}
load();
