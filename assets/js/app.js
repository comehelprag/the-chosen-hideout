/* ==========================================================================
   THE CHOSEN HIDEOUT — app.js
   No framework, no build step. Reads data/catalog.json and renders it.
   ========================================================================== */

const DATA_URL = "data/catalog.json";
const FAV_KEY = "tch:favs";
const ALL = "all";

const state = {
  q: "",
  cat: ALL,
  fav: false,
  sort: "recent",
};

let data = null;
const catsById = new Map();
const favs = new Set(readFavs());

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

/* ---------------------------------------------------------------- utils -- */

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[ch]));
}

function readFavs() {
  try {
    const raw = localStorage.getItem(FAV_KEY);
    const arr = raw ? JSON.parse(raw) : [];
    return Array.isArray(arr) ? arr : [];
  } catch {
    return [];
  }
}

function saveFavs() {
  try {
    localStorage.setItem(FAV_KEY, JSON.stringify([...favs]));
  } catch {
    /* private mode / storage full: ignore */
  }
}

function fmtSize(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let n = bytes;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i += 1;
  }
  const digits = n >= 100 || i === 0 ? 0 : 1;
  return `${n.toFixed(digits)} ${units[i]}`;
}

function parseDate(iso) {
  const d = new Date(`${iso}T00:00:00`);
  return Number.isNaN(d.getTime()) ? null : d;
}

function fmtDate(iso, opts = {}) {
  const d = parseDate(iso);
  if (!d) return "—";
  return d.toLocaleDateString("en-US", {
    day: "2-digit",
    month: "2-digit",
    year: opts.short ? undefined : "numeric",
  });
}

function catOf(id) {
  return catsById.get(id) || { name: id, accent: "#6f6bff", protonUrl: "" };
}

/* ---------------------------------------------------------------- links -- */

function applyProtonLinks() {
  const root = data?.site?.protonRoot || "";
  const usable = /^https?:\/\//.test(root);
  $$("[data-proton-link]").forEach((a) => {
    if (!usable) {
      a.hidden = true;
      return;
    }
    a.href = root;
  });
}

/* --------------------------------------------------------------- render -- */

function renderStats() {
  const items = data.items || [];
  const set = (key, value) => {
    const el = $(`[data-stat="${key}"]`);
    if (el) el.textContent = value;
  };
  set("categories", String((data.categories || []).length).padStart(2, "0"));
  set("items", String(items.length).padStart(2, "0"));
  set("updated", fmtDate(data.site?.updated || "", { short: true }));
}

function renderTicker() {
  const row = $("#tickerRow");
  if (!row) return;
  const words = [
    ...(data.categories || []).map((c) => c.name),
    "discord",
    "proton drive",
    "no dead links",
  ];
  const chunk = words.map((w) => `<span>${esc(w)}</span>`).join("");
  row.innerHTML = chunk + chunk; // duplicated so the loop stays seamless
}

function renderCategories() {
  const host = $("#cats");
  if (!host) return;
  const counts = new Map();
  for (const item of data.items || []) {
    counts.set(item.category, (counts.get(item.category) || 0) + 1);
  }

  host.innerHTML = (data.categories || []).map((cat, i) => {
    const n = counts.get(cat.id) || 0;
    const num = String(i + 1).padStart(2, "0");
    const letter = (cat.name || "?").trim().charAt(0).toUpperCase();
    return `
      <a class="cat" href="#catalogo" data-cat="${esc(cat.id)}"
         style="--accent:${esc(cat.accent || "#6f6bff")}" data-letter="${esc(letter)}">
        <div class="cat__top">
          <span class="cat__idx">${num} // folder</span>
          <span class="cat__count">${n} ${n === 1 ? "item" : "items"}</span>
        </div>
        <h3 class="cat__name">${esc(cat.name)}</h3>
        <p class="cat__desc">${esc(cat.desc)}</p>
        <span class="cat__go">
          View in catalog
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
               stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M5 12h14M13 6l6 6-6 6"/>
          </svg>
        </span>
      </a>`;
  }).join("");
}

function renderChips() {
  const host = $("#chips");
  if (!host) return;

  const chip = (id, label, extra = "") =>
    `<button class="chip ${extra}" type="button" data-filter="${esc(id)}"
       aria-pressed="${state.cat === id && !state.fav}">${esc(label)}</button>`;

  const total = (data.items || []).length;
  let html = chip(ALL, `All · ${total}`);
  html += (data.categories || []).map((c) => chip(c.id, c.name)).join("");
  html += `<button class="chip chip--fav" type="button" data-fav-filter
             aria-pressed="${state.fav}">★ Favorites · ${favs.size}</button>`;

  host.innerHTML = html;
}

function itemCard(item) {
  const cat = catOf(item.category);
  const isFav = favs.has(item.id);
  const url = item.protonUrl || cat.protonUrl || data.site?.protonRoot || "#";

  // O Proton não tem deep link: o link abre uma pasta, nunca um arquivo dentro
  // dela. O rótulo diz exatamente onde o clique vai cair, em vez de prometer
  // algo que o Drive não faz.
  const trail = (item.protonPath || "").split("/").filter(Boolean).join(" → ");
  const destination = item.protonUrl
    ? `Open ${item.name} in Proton Drive`
    : cat.protonUrl
      ? `Open the ${cat.name} folder in Proton Drive`
      : `Open Proton Drive, then ${trail || item.name}`;
  const date = item.badge
    ? `<span class="card__date" style="color:var(--orange)">${esc(item.badge)}</span>`
    : item.updated
      ? `<span class="card__date">${esc(fmtDate(item.updated, { short: true }))}</span>`
      : "";

  // sem versão/tamanho (comum antes do primeiro sync), mostra o caminho no Drive
  const metaParts = [];
  if (item.version && item.version !== "—") metaParts.push(item.version);
  if (item.sizeBytes > 0) metaParts.push(fmtSize(item.sizeBytes));
  const meta = metaParts.length ? metaParts.join(" · ") : (item.protonPath || "");

  const tags = (item.tags || [])
    .map((t) => `<span class="tag">#${esc(t)}</span>`)
    .join("");

  return `
    <article class="card" style="--accent:${esc(cat.accent)}" data-id="${esc(item.id)}">
      <div class="card__top">
        <span class="card__cat">${esc(cat.name)}</span>
        ${date}
      </div>

      <h3 class="card__name">${esc(item.name)}</h3>
      ${item.desc ? `<p class="card__desc">${esc(item.desc)}</p>` : ""}

      ${tags ? `<div class="card__tags">${tags}</div>` : ""}

      <div class="card__foot">
        <span class="card__meta" title="${esc(item.protonPath || "")}">
          ${esc(meta)}
        </span>
        <div class="card__actions">
          <button class="icon-btn ${isFav ? "is-fav" : ""}" type="button"
                  data-fav="${esc(item.id)}" aria-pressed="${isFav}"
                  aria-label="${isFav ? "Remove from favorites" : "Add to favorites"}">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
                 stroke-linejoin="round" aria-hidden="true">
              <path d="M12 20s-7-4.4-7-9.3A3.9 3.9 0 0 1 12 8a3.9 3.9 0 0 1 7 2.7C19 15.6 12 20 12 20Z"/>
            </svg>
          </button>
          <button class="icon-btn" type="button" data-copy="${esc(url)}"
                  aria-label="Copy Proton link">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
                 stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <rect x="9" y="9" width="11" height="11" rx="2"/>
              <path d="M5 15V6a2 2 0 0 1 2-2h8"/>
            </svg>
          </button>
          <a class="icon-btn" href="${esc(url)}" target="_blank" rel="noopener noreferrer"
             title="${esc(destination)}" aria-label="${esc(destination)}">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
                 stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M7 17 17 7M9 7h8v8"/>
            </svg>
          </a>
        </div>
      </div>
    </article>`;
}

function currentList() {
  const q = state.q.trim().toLowerCase();
  const list = (data.items || []).filter((item) => {
    if (state.fav && !favs.has(item.id)) return false;
    if (state.cat !== ALL && item.category !== state.cat) return false;
    if (!q) return true;

    const hay = [
      item.name,
      item.desc,
      item.version,
      catOf(item.category).name,
      ...(item.tags || []),
    ]
      .join(" ")
      .toLowerCase();

    return hay.includes(q);
  });

  const byName = (a, b) => a.name.localeCompare(b.name, "en", { sensitivity: "base" });

  switch (state.sort) {
    case "az":
      list.sort(byName);
      break;
    case "category":
      list.sort((a, b) => {
        const ca = catOf(a.category).name;
        const cb = catOf(b.category).name;
        return ca.localeCompare(cb, "en") || byName(a, b);
      });
      break;
    case "size":
      list.sort((a, b) => (b.sizeBytes || 0) - (a.sizeBytes || 0));
      break;
    default:
      list.sort((a, b) => String(b.updated || "").localeCompare(String(a.updated || "")) || byName(a, b));
  }

  return list;
}

function renderItems() {
  const host = $("#items");
  const empty = $("#empty");
  const count = $("#count");
  const clear = $("#clear");
  if (!host) return;

  const list = currentList();
  const total = (data.items || []).length;

  host.innerHTML = list.map(itemCard).join("");

  const filtered = state.q.trim() !== "" || state.cat !== ALL || state.fav;
  if (count) {
    count.textContent = filtered
      ? `${list.length} of ${total} packages`
      : `${total} packages · ${(data.categories || []).length} categories`;
  }

  if (empty) {
    empty.hidden = list.length !== 0;
    const title = $("#emptyTitle");
    const sub = $("#emptySub");
    const clearBtn = $("[data-clear]", empty);
    if (total === 0) {
      if (title) title.textContent = "Nothing indexed yet.";
      if (sub) sub.textContent = "The archive is empty until the Proton sync runs. Point tools/sync_proton.py at the share and it fills itself.";
      if (clearBtn) clearBtn.hidden = true;
    } else {
      if (title) title.textContent = "Nothing found.";
      if (sub) sub.textContent = "Try another term, or clear the filters.";
      if (clearBtn) clearBtn.hidden = false;
    }
  }
  if (clear) clear.hidden = !filtered;

  const favChip = $("[data-fav-filter]");
  if (favChip) {
    favChip.setAttribute("aria-pressed", String(state.fav));
    favChip.textContent = `★ Favorites · ${favs.size}`;
  }
}

function renderAll() {
  renderStats();
  renderTicker();
  renderCategories();
  renderChips();
  renderItems();
}

/* --------------------------------------------------------------- actions -- */

function setCategory(id) {
  state.cat = id;
  state.fav = false;
  $$("#chips [data-filter]").forEach((b) => {
    b.setAttribute("aria-pressed", String(b.dataset.filter === id && !state.fav));
  });
  syncUrl();
  renderItems();
}

function clearFilters() {
  state.q = "";
  state.cat = ALL;
  state.fav = false;
  const q = $("#q");
  if (q) q.value = "";
  const sort = $("#sort");
  if (sort) sort.value = state.sort;
  renderChips();
  syncUrl();
  renderItems();
}

function toggleFav(id) {
  if (favs.has(id)) favs.delete(id);
  else favs.add(id);
  saveFavs();
  renderItems();
}

function syncUrl() {
  const url = new URL(location.href);
  if (state.cat !== ALL) url.searchParams.set("cat", state.cat);
  else url.searchParams.delete("cat");
  history.replaceState(null, "", url);
}

async function copyText(text, btn) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
  }
  if (btn) {
    btn.classList.add("is-copied");
    setTimeout(() => btn.classList.remove("is-copied"), 1200);
  }
}

/* ----------------------------------------------------------------- boot -- */

function bindEvents() {
  const q = $("#q");
  q?.addEventListener("input", (e) => {
    state.q = e.target.value;
    renderItems();
  });

  $("#sort")?.addEventListener("change", (e) => {
    state.sort = e.target.value;
    renderItems();
  });

  $("#chips")?.addEventListener("click", (e) => {
    const favBtn = e.target.closest("[data-fav-filter]");
    if (favBtn) {
      state.fav = !state.fav;
      state.cat = ALL;
      renderChips();
      renderItems();
      return;
    }
    const btn = e.target.closest("[data-filter]");
    if (btn) setCategory(btn.dataset.filter);
  });

  $("#cats")?.addEventListener("click", (e) => {
    const card = e.target.closest("[data-cat]");
    if (!card) return;
    setCategory(card.dataset.cat);
    document.getElementById("catalogo")?.scrollIntoView({ behavior: "smooth", block: "start" });
  });

  $("#items")?.addEventListener("click", (e) => {
    const favBtn = e.target.closest("[data-fav]");
    if (favBtn) {
      toggleFav(favBtn.dataset.fav);
      return;
    }
    const copyBtn = e.target.closest("[data-copy]");
    if (copyBtn) copyText(copyBtn.dataset.copy, copyBtn);
  });

  $("#clear")?.addEventListener("click", clearFilters);
  $$("[data-clear]").forEach((b) => b.addEventListener("click", clearFilters));

  document.addEventListener("keydown", (e) => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || "");
    if (e.key === "/" && !typing) {
      e.preventDefault();
      $("#q")?.focus();
      $("#catalogo")?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
    if (e.key === "Escape" && document.activeElement === $("#q")) {
      $("#q").value = "";
      state.q = "";
      renderItems();
      $("#q").blur();
    }
  });
}

async function boot() {
  bindEvents();

  try {
    const res = await fetch(DATA_URL, { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    data = await res.json();
  } catch (err) {
    console.error("[TCH] failed to load catalog:", err);
    $("#items").hidden = true;
    $("#empty").hidden = true;
    $("#count").textContent = "catalog unavailable";
    $("#loaderr").hidden = false;
    applyProtonLinks();
    return;
  }

  data.items = Array.isArray(data.items) ? data.items : [];
  data.categories = Array.isArray(data.categories) ? data.categories : [];
  data.categories.forEach((c) => catsById.set(c.id, c));

  const catParam = new URLSearchParams(location.search).get("cat");
  if (catParam && catsById.has(catParam)) state.cat = catParam;

  applyProtonLinks();
  renderAll();
}

document.addEventListener("DOMContentLoaded", boot);
