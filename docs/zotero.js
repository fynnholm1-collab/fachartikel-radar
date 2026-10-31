// Minimaler Client für die Zotero Web API v3 (https://www.zotero.org/support/dev/web_api/v3/start).
// Läuft direkt im Browser; Nutzer-ID und Schlüssel liegen nur im localStorage dieses Geräts.

const Zotero = (() => {
  const API = "https://api.zotero.org";

  function creds() {
    const uid = (localStorage.getItem("z-uid") || "").trim();
    const key = (localStorage.getItem("z-key") || "").trim();
    return uid && key ? { uid, key } : null;
  }

  async function call(path, { method = "GET", body } = {}) {
    const c = creds();
    if (!c) throw new Error("Zotero ist noch nicht eingerichtet (Einstellungen).");
    const res = await fetch(`${API}/users/${c.uid}${path}`, {
      method,
      headers: {
        "Zotero-API-Key": c.key,
        "Zotero-API-Version": "3",
        ...(body ? { "Content-Type": "application/json" } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (res.status === 403) throw new Error("Zugriff verweigert: Schlüssel oder Schreibrecht prüfen.");
    if (!res.ok) throw new Error(`Zotero antwortet mit ${res.status}: ${await res.text()}`);
    return res.json();
  }

  async function create(path, objects) {
    const r = await call(path, { method: "POST", body: objects });
    const failed = r.failed && Object.values(r.failed)[0];
    if (failed) throw new Error(`Zotero: ${failed.message}`);
    return Object.values(r.success || {});
  }

  async function test() {
    const c = creds();
    if (!c) throw new Error("Bitte Nutzer-ID und Schlüssel eintragen.");
    const res = await fetch(`${API}/keys/current`, { headers: { "Zotero-API-Key": c.key, "Zotero-API-Version": "3" } });
    if (!res.ok) throw new Error("Schlüssel ungültig.");
    const info = await res.json();
    if (String(info.userID) !== c.uid) throw new Error(`Der Schlüssel gehört zur Nutzer-ID ${info.userID}.`);
    if (!(info.access && info.access.user && info.access.user.write)) throw new Error("Dem Schlüssel fehlt das Schreibrecht.");
    return `Verbunden als ${info.username}.`;
  }

  // Sammlungen: findet oder legt an; Ergebnis wird pro Gerät zwischengespeichert.
  async function allCollections() {
    let out = [], start = 0;
    for (;;) {
      const page = await call(`/collections?limit=100&start=${start}`);
      out = out.concat(page);
      if (page.length < 100) return out;
      start += 100;
    }
  }

  async function ensureCollection(name, parentName) {
    const cacheKey = `z-col:${parentName || ""}/${name}`;
    const cached = localStorage.getItem(cacheKey);
    if (cached) return cached;
    const cols = await allCollections();
    let parentKey = false;
    if (parentName) {
      parentKey = await ensureCollection(parentName);
    }
    const found = cols.find((c) => c.data.name === name && (c.data.parentCollection || false) === parentKey);
    const key = found ? found.key : (await create("/collections", [{ name, parentCollection: parentKey }]))[0];
    localStorage.setItem(cacheKey, key);
    return key;
  }

  function creators(names) {
    return (names || []).filter(Boolean).map((n) => {
      const parts = n.trim().split(/\s+/);
      if (parts.length === 1) return { creatorType: "author", name: n };
      return { creatorType: "author", lastName: parts.pop(), firstName: parts.join(" ") };
    });
  }

  function noteHtml(text, extra) {
    const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c]);
    const lines = text ? esc(text).split(/\n/).map((l) => `<p>${l}</p>`).join("") : "";
    return `<h2>Kommentar (Fachartikel-Radar)</h2>${lines}<p><em>${esc(extra)}</em></p>`;
  }

  async function saveArticle(a, { comment, collectionKey, tag }) {
    const item = {
      itemType: "journalArticle",
      title: a.title,
      creators: creators(a.authors),
      publicationTitle: a.journal,
      date: a.published,
      DOI: a.doi || "",
      url: a.url || "",
      abstractNote: a.abstract || "",
      tags: [{ tag }, ...(comment ? [{ tag: "kommentiert" }] : [])],
      collections: collectionKey ? [collectionKey] : [],
    };
    const [key] = await create("/items", [item]);
    const today = new Date().toISOString().slice(0, 10);
    await create("/items", [{ itemType: "note", parentItem: key, note: noteHtml(comment, `gespeichert am ${today}`), tags: [{ tag }] }]);
    return key;
  }

  async function saveBook(a, { comment, collectionKey, tag }) {
    const k = a.katalog || {};
    const item = {
      itemType: "book",
      title: k.buchtitel || a.title,
      ISBN: k.isbn || "",
      tags: [{ tag }, { tag: "anschaffung" }],
      collections: collectionKey ? [collectionKey] : [],
      extra: `Rezension: ${a.title} (${a.journal}${a.doi ? ", DOI " + a.doi : ""})`,
    };
    const [key] = await create("/items", [item]);
    const today = new Date().toISOString().slice(0, 10);
    await create("/items", [{ itemType: "note", parentItem: key, note: noteHtml(comment, `vorgemerkt am ${today}; Katalog: ${k.status || "unbekannt"}`), tags: [{ tag }] }]);
    return key;
  }

  return { creds, test, ensureCollection, saveArticle, saveBook };
})();
