"use strict";

const FIELDS = [
  ["title", "Name / title", "text"],
  ["address", "Street address", "text"],
  ["city", "City", "text"],
  ["unit", "Unit", "text"],
  ["rent", "Rent ($/mo)", "number"],
  ["bedrooms", "Bedrooms", "number"],
  ["bathrooms", "Bathrooms", "number"],
  ["sqft", "Square feet", "number"],
  ["year_built", "Year built", "number"],
  ["in_unit_laundry", "In-unit laundry", "bool"],
  ["gym", "Gym in building", "bool"],
  ["covered_parking", "Covered parking", "bool"],
  ["parking_cost", "Parking ($/mo)", "number"],
  ["parking_type", "Parking type", "text"],
  ["utilities_estimate", "Utilities ($/mo est.)", "number"],
  ["monthly_fees", "Other monthly fees ($)", "number"],
  ["deposit", "Security deposit ($)", "number"],
  ["move_in_fees", "Non-refundable move-in fees ($)", "number"],
  ["pet_policy", "Pet policy", "text"],
  ["available_date", "Available", "text"],
  ["url", "Listing link", "url"],
];
const STATUS_LABELS = {
  interested: "Interested", contacted: "Contacted", tour_scheduled: "Tour scheduled",
  toured: "Toured", applied: "Applied", rejected: "Rejected", signed: "Signed",
};

let CONFIG = null;
let LISTINGS = [];
let current = null;
let currentTab = "details";

const $ = (sel, el = document) => el.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (n) => (n == null ? "—" : "$" + Math.round(n).toLocaleString());

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail ? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)) : res.statusText);
  return data;
}

function fieldInput(key, label, type, value) {
  const id = `f-${key}`;
  if (type === "bool") {
    const v = value === true ? "yes" : value === false ? "no" : "";
    return `<div><label for="${id}">${esc(label)}</label>
      <select id="${id}" data-field="${key}" data-type="bool">
        <option value="" ${v === "" ? "selected" : ""}>Unknown</option>
        <option value="yes" ${v === "yes" ? "selected" : ""}>Yes</option>
        <option value="no" ${v === "no" ? "selected" : ""}>No</option>
      </select></div>`;
  }
  return `<div><label for="${id}">${esc(label)}</label>
    <input id="${id}" data-field="${key}" data-type="${type}" type="${type === "number" ? "number" : type}" step="any" value="${esc(value ?? "")}"></div>`;
}

function readFields(root) {
  const out = {};
  root.querySelectorAll("[data-field]").forEach((el) => {
    const t = el.dataset.type;
    let v = el.value.trim();
    if (t === "bool") v = v === "yes" ? true : v === "no" ? false : null;
    else if (t === "number") v = v === "" ? null : Number(v);
    else if (v === "") v = null;
    out[el.dataset.field] = v;
  });
  return out;
}

function scoreClass(s) {
  if (s == null) return "";
  return s >= 70 ? "hi" : s >= 50 ? "mid" : "lo";
}

function commuteMinutes(l) {
  const c = l.commute;
  if (!c || c.error) return null;
  return Math.max(c.to_work.minutes, c.to_home.minutes);
}

function renderList() {
  const status = $("#filter-status").value;
  const onlyPass = $("#only-pass").checked;
  const sort = $("#sort").value;
  let items = LISTINGS.filter((l) => status === "all" || l.status !== "rejected");
  if (onlyPass) items = items.filter((l) => l.must_haves.every((m) => m.status === "pass"));
  const key = {
    construction: (l) => -(l.construction.score ?? -1),
    commute: (l) => commuteMinutes(l) ?? 1e9,
    monthly: (l) => (l.rent ? l.costs.monthly_total : 1e9),
    rent: (l) => l.rent ?? 1e9,
    updated: (l) => -Date.parse(l.updated_at),
  }[sort];
  items.sort((a, b) => key(a) - key(b));

  const list = $("#list");
  if (!items.length) {
    list.innerHTML = `<div class="empty">${LISTINGS.length ? "No listings match these filters." : "No listings yet. Paste one above to get started."}</div>`;
    return;
  }
  list.innerHTML = items.map((l) => {
    const c = l.commute && !l.commute.error ? l.commute : null;
    const cs = l.construction;
    const flagsWarn = cs.flags.filter((f) => f.level === "warn").length;
    return `<article class="item" data-id="${l.id}" tabindex="0">
      <div>
        <div class="name">${esc(l.title || l.address || "Untitled")}</div>
        <div class="meta">${esc([l.address, l.unit && "#" + l.unit, l.city].filter(Boolean).join(", "))} · ${esc(STATUS_LABELS[l.status] || l.status)}</div>
        <div class="stats">
          <div class="stat"><b>${money(l.rent)}</b><span>rent</span></div>
          <div class="stat"><b>${l.rent ? money(l.costs.monthly_total) : "—"}</b><span>true monthly${l.costs.monthly_unknown.length ? "*" : ""}</span></div>
          <div class="stat"><b>${c ? Math.round(c.to_work.minutes) + " / " + Math.round(c.to_home.minutes) + " min" : "—"}</b><span>to IonQ / home</span></div>
          <div class="stat"><b>${[l.bedrooms != null && l.bedrooms + " bd", l.bathrooms != null && l.bathrooms + " ba", l.sqft && Math.round(l.sqft) + " sq ft"].filter(Boolean).join(" · ") || "—"}</b><span>size</span></div>
        </div>
        <div class="chips">
          ${l.must_haves.map((m) => `<span class="chip ${m.status}">${m.status === "pass" ? "✓" : m.status === "fail" ? "✗" : "?"} ${esc(m.label)}</span>`).join("")}
          ${flagsWarn ? `<span class="chip warn">${flagsWarn} construction warning${flagsWarn > 1 ? "s" : ""}</span>` : ""}
        </div>
      </div>
      <div class="score ${scoreClass(cs.score)}" title="Construction quality">
        <b>${cs.score ?? "—"}</b><span>${cs.score == null ? "no data" : esc(cs.confidence) + " conf."}</span><br><span>construction</span>
      </div>
    </article>`;
  }).join("");
}

async function refresh() {
  LISTINGS = await api("/api/listings");
  renderList();
}

// ---- detail dialog -------------------------------------------------------

function openDetail(id) {
  current = LISTINGS.find((l) => l.id === id);
  if (!current) return;
  currentTab = "details";
  renderDetail();
  const dlg = $("#detail");
  if (!dlg.open) dlg.showModal();
}

function replaceCurrent(updated) {
  const i = LISTINGS.findIndex((l) => l.id === updated.id);
  if (i >= 0) LISTINGS[i] = updated; else LISTINGS.unshift(updated);
  current = updated;
  renderDetail();
  renderList();
}

function renderDetail() {
  const l = current;
  $("#d-title").textContent = l.title || l.address || "Listing";
  document.querySelectorAll("#d-tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === currentTab));
  const body = $("#d-body");
  body.innerHTML = { details: detailsTab, costs: costsTab, construction: constructionTab, photos: photosTab, tour: tourTab }[currentTab](l);
  bindTab(body, l);
}

function detailsTab(l) {
  const flags = l.extracted?.red_flags || [];
  return `
    <div class="row" style="margin-top:0">
      <div style="flex:1 1 200px"><label for="d-status">Status</label>
        <select id="d-status">${CONFIG.statuses.map((s) => `<option value="${s}" ${s === l.status ? "selected" : ""}>${STATUS_LABELS[s]}</option>`).join("")}</select>
      </div>
      ${l.url ? `<a href="${esc(l.url)}" target="_blank" rel="noopener noreferrer">Open listing ↗</a>` : ""}
    </div>
    ${flags.length ? `<h3>Warning signs in the listing</h3><ul class="flags">${flags.map((f) => `<li class="warn">${esc(f)}</li>`).join("")}</ul>` : ""}
    <h3>Details</h3>
    <div class="grid" id="d-fields">${FIELDS.map(([k, lab, t]) => fieldInput(k, lab, t, l[k])).join("")}</div>
    <div style="margin-top:10px"><label for="d-notes">Notes</label><textarea id="d-notes" rows="3">${esc(l.notes || "")}</textarea></div>
    <div class="row end"><span class="msg" id="d-msg"></span>
      <button class="danger" id="d-delete">Delete</button>
      <button id="d-save">Save</button></div>
    ${l.source_text ? `<details><summary>Original listing text</summary><pre class="src">${esc(l.source_text)}</pre></details>` : ""}`;
}

function costsTab(l) {
  const c = l.commute;
  const k = l.costs;
  const leg = (name, x) => `<tr><td>${name}</td><td class="num">${Math.round(x.minutes)} min</td><td class="num">${x.miles} mi</td>
    <td>${x.uses_toll ? `I-405 toll lanes, about $${x.toll_usd.toFixed(2)} (saves ${Math.round(x.minutes_saved_by_toll)} min)` : x.minutes_saved_by_toll > 0 ? `Free route (toll lanes save only ${Math.round(x.minutes_saved_by_toll)} min)` : "Free route"}${x.via ? ` · via ${esc(x.via)}` : ""}</td></tr>`;
  const commute = !c ? `<p class="note">No commute yet.</p>`
    : c.error ? `<p class="msg err">${esc(c.error)}</p>`
    : `<table><tr><th>Trip</th><th class="num">Time</th><th class="num">Distance</th><th>Route</th></tr>
        ${leg(`To IonQ, leaving ${c.depart_home}`, c.to_work)}${leg(`Home, leaving ${c.depart_work}`, c.to_home)}</table>
       <p class="note">Predicted traffic for ${esc(c.sample_day)}, a typical Tuesday/Wednesday. Toll lanes are used only when they save 5+ minutes.</p>`;
  const unknown = new Set(k.monthly_unknown);
  const line = (label, key) => `<tr><td>${label}${unknown.has(key) ? ' <span class="note">(not entered)</span>' : ""}</td><td class="num">${money(k.monthly[key])}</td></tr>`;
  return `
    <h3 style="margin-top:0">Commute to ${esc(CONFIG.work_address)}</h3>
    ${commute}
    <div class="row"><button class="ghost small" id="d-commute">Recalculate commute</button><span class="msg" id="d-msg"></span></div>
    <h3>True monthly cost</h3>
    <table>${line("Rent", "rent")}${line("Parking", "parking")}${line("Utilities", "utilities")}${line("Other monthly fees", "fees")}
      ${line("Driving (gas" + (c && !c.error && c.monthly_tolls ? " + tolls" : "") + `, ${CONFIG.workdays_per_month || 21} workdays)`, "commute")}
      <tr class="total"><td>Total per month</td><td class="num">${money(k.monthly_total)}</td></tr></table>
    <h3>Move-in cost</h3>
    <table><tr><td>First month's rent</td><td class="num">${money(k.move_in.first_month)}</td></tr>
      <tr><td>Security deposit</td><td class="num">${money(k.move_in.deposit)}</td></tr>
      <tr><td>Non-refundable fees</td><td class="num">${money(k.move_in.fees)}</td></tr>
      <tr class="total"><td>Due at move-in</td><td class="num">${money(k.move_in_total)}</td></tr></table>`;
}

function constructionTab(l) {
  const cs = l.construction;
  const b = l.building || {};
  const comps = cs.components.map((c) => `<tr><td>${esc(c.label)}<div class="note">${esc(c.detail || "")}</div></td><td class="num">${c.score}</td><td class="num">${c.weight}%</td></tr>`).join("");
  const opt = (vals, cur) => `<option value="">Unknown</option>` + vals.map((v) => `<option ${v === cur ? "selected" : ""}>${v}</option>`).join("");
  return `
    <div class="row" style="margin-top:0; align-items:flex-start">
      <div class="score ${scoreClass(cs.score)}"><b>${cs.score ?? "—"}</b><span>${cs.confidence ? esc(cs.confidence) + " confidence" : "no data"}</span></div>
      <p class="note" style="flex:1; margin:0">A screening estimate from county records, building-era patterns, your tour ratings and listing photos, not an inspection.
        ${cs.missing.length ? `<br>To improve confidence, add: ${cs.missing.map(esc).join("; ")}.` : ""}</p>
    </div>
    ${comps ? `<h3>How it was scored</h3><table><tr><th>Factor</th><th class="num">Score</th><th class="num">Weight</th></tr>${comps}</table>` : ""}
    ${cs.flags.length ? `<h3>What to know</h3><ul class="flags">${cs.flags.map((f) => `<li class="${f.level}">${esc(f.text)}</li>`).join("")}</ul>` : ""}
    <h3>Building record ${b.source ? `<span class="note">(${b.source === "manual" ? "entered by hand" : "King County Assessor" + (b.parcel ? ", parcel " + esc(b.parcel) : "")})</span>` : ""}</h3>
    <div id="cands">${CONFIG.assessor_records ? `<button class="ghost small" id="d-find">Find in county records</button>` : `<p class="note">No county records imported yet. See the README to import King County Assessor data, or enter what you know below (you can look up the building on King County's eReal Property site, or Snohomish County's property site for north Bothell and Mill Creek).</p>`}</div>
    <div class="grid" id="b-fields">
      <div><label for="b-year">Year built</label><input id="b-year" type="number" value="${esc(b.year_built ?? l.year_built ?? "")}"></div>
      <div><label for="b-eff">Effective / renovated year</label><input id="b-eff" type="number" value="${esc(b.eff_year ?? "")}"></div>
      <div><label for="b-stories">Stories</label><input id="b-stories" type="number" value="${esc(b.stories ?? "")}"></div>
      <div><label for="b-constr">Construction type</label><select id="b-constr">${opt(["Wood frame", "Reinforced concrete", "Structural steel", "Masonry", "Prefab steel"], b.constr_class)}</select></div>
      <div><label for="b-quality">Building quality grade</label><select id="b-quality">${opt(["Low", "Low/Average", "Average", "Average/Good", "Good", "Good/Excellent", "Excellent"], b.bldg_quality)}</select></div>
      <div><label for="b-cond">Condition</label><select id="b-cond">${opt(["Poor", "Fair", "Average", "Good", "Excellent"], b.condition)}</select></div>
    </div>
    <div class="row end"><span class="msg" id="d-msg"></span><button id="b-save">Save building details</button></div>`;
}

function photosTab(l) {
  const st = l.photos || { files: [], analysis: null, analyzed_files: [] };
  const a = st.analysis;
  const stale = a && JSON.stringify(st.files) !== JSON.stringify(st.analyzed_files || []);
  const breakdown = l.construction.photo_breakdown || (a ? Object.entries(a.categories).map(([key, c]) => ({ key, label: key, ...c })) : []);
  const stars = (r) => (r == null ? "—" : `${r}/5`);
  const thumbs = st.files.map((f, i) => `<figure class="thumb">
      <img src="/api/listings/${l.id}/photos/${encodeURIComponent(f)}" alt="Photo ${i + 1}" loading="lazy">
      <figcaption>${i + 1}</figcaption>
      <button class="ghost small" data-del="${esc(f)}" aria-label="Remove photo ${i + 1}">✕</button></figure>`).join("");
  return `
    <p class="note" style="margin-top:0">Save the listing photos (right-click → Save image, or screenshot them) and add them here.
      Photos are shrunk before upload. Up to 20 per listing; about 10-15 cents to analyze 10 photos.</p>
    <div class="row" style="margin-top:0">
      <label class="filebtn ghost">Add photos<input type="file" id="p-files" accept="image/*" multiple hidden></label>
      <button id="p-analyze" ${st.files.length && CONFIG.has_anthropic_key ? "" : "disabled"}>${a ? "Re-analyze photos" : "Analyze photos"}</button>
      <span class="msg" id="d-msg"></span>
    </div>
    ${!CONFIG.has_anthropic_key ? `<p class="note">Add ANTHROPIC_API_KEY to .env to analyze photos.</p>` : ""}
    ${st.files.length ? `<div class="thumbs">${thumbs}</div>` : `<p class="note">No photos yet.</p>`}
    ${a ? `
      ${stale ? `<p class="msg err">Photos changed since the last analysis. Re-analyze to update the score.</p>` : ""}
      <h3>What the photos show</h3>
      ${a.summary ? `<p style="margin-top:0">${esc(a.summary)}</p>` : ""}
      <table><tr><th>Item</th><th class="num">Rating</th><th>Evidence</th></tr>
        ${breakdown.map((c) => `<tr><td>${esc(c.label)}${c.overridden_by_tour ? '<div class="note">Your tour rating is used instead</div>' : ""}</td>
          <td class="num">${c.visible ? stars(c.rating) : '<span class="note">not shown</span>'}</td>
          <td>${esc(c.evidence || "")}${c.photos?.length ? ` <span class="note">(photo ${c.photos.join(", ")})</span>` : ""}</td></tr>`).join("")}
      </table>
      ${a.check_on_tour.length ? `<h3>Check on the tour</h3><ul class="flags">${a.check_on_tour.map((t) => `<li class="warn">${t.photo ? `Photo ${t.photo}: ` : ""}${esc(t.text)}</li>`).join("")}</ul>` : ""}
      ${a.photo_caveats.length ? `<h3>Limits of these photos</h3><ul class="flags">${a.photo_caveats.map((t) => `<li class="info">${esc(t)}</li>`).join("")}</ul>` : ""}
      <p class="note">Photos count for about 10% of the construction score. Items you rate on the tour replace the photo rating for the same item.</p>` : ""}`;
}

function shrinkImage(file, maxEdge = 1024) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      const scale = Math.min(1, maxEdge / Math.max(img.naturalWidth, img.naturalHeight));
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(img.naturalWidth * scale);
      canvas.height = Math.round(img.naturalHeight * scale);
      canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
      URL.revokeObjectURL(url);
      resolve(canvas.toDataURL("image/jpeg", 0.85));
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error(`${file.name} could not be read (HEIC photos: export as JPEG first)`)); };
    img.src = url;
  });
}

function tourTab(l) {
  const ratings = l.checklist?.ratings || {};
  return `
    <p class="note" style="margin-top:0">Rate each item 1 (bad) to 5 (great) during the tour. Tap again to clear. Ratings feed the construction score.</p>
    ${CONFIG.checklist.map((c) => `<div class="rate"><div><b>${esc(c.label)}</b></div><div class="hint">${esc(c.hint)}</div>
      <div class="seg" data-key="${c.key}">${[1, 2, 3, 4, 5].map((n) => `<button type="button" data-n="${n}" class="${ratings[c.key] === n ? "on" : ""}" aria-pressed="${ratings[c.key] === n}">${n}</button>`).join("")}</div></div>`).join("")}
    <div style="margin-top:10px"><label for="t-notes">Tour notes</label><textarea id="t-notes" rows="3">${esc(l.checklist?.notes || "")}</textarea></div>
    <div class="row end"><span class="msg" id="d-msg"></span><button id="t-save">Save checklist</button></div>`;
}

function setMsg(text, err = false) {
  const m = $("#d-msg");
  if (m) { m.textContent = text; m.classList.toggle("err", err); }
}

async function run(btn, fn) {
  btn.disabled = true;
  try { await fn(); } catch (e) { setMsg(e.message, true); } finally { btn.disabled = false; }
}

function bindTab(body, l) {
  const on = (sel, fn) => { const el = $(sel, body); if (el) el.addEventListener("click", () => run(el, fn)); };

  const status = $("#d-status", body);
  if (status) status.addEventListener("change", () => run(status, async () => replaceCurrent(await api(`/api/listings/${l.id}`, { method: "PATCH", body: { status: status.value } }))));

  on("#d-save", async () => {
    const fields = readFields($("#d-fields", body));
    fields.notes = $("#d-notes", body).value;
    replaceCurrent(await api(`/api/listings/${l.id}`, { method: "PATCH", body: fields }));
    setMsg("Saved.");
  });
  on("#d-delete", async () => {
    if (!confirm("Delete this listing?")) return;
    await api(`/api/listings/${l.id}`, { method: "DELETE" });
    LISTINGS = LISTINGS.filter((x) => x.id !== l.id);
    $("#detail").close();
    renderList();
  });
  on("#d-commute", async () => {
    setMsg("Calculating…");
    replaceCurrent(await api(`/api/listings/${l.id}/commute`, { method: "POST" }));
  });
  on("#d-find", async () => {
    const cands = await api(`/api/listings/${l.id}/assessor-matches`);
    const box = $("#cands", body);
    box.innerHTML = cands.length
      ? cands.map((c) => `<div class="cand"><div><b>${esc(c.name || c.address)}</b><div class="note">${esc(c.address)} · built ${esc(c.year_built ?? "?")} · ${esc(c.constr_class || "?")} · quality ${esc(c.bldg_quality || "?")} · match ${Math.round(c.match_score * 100)}%</div></div>
          <button class="small" data-aid="${c.id}">Use this</button></div>`).join("")
      : `<p class="note">No close match for "${esc(l.address || "")}". Complexes are often recorded under one main address; enter the details by hand below.</p>`;
    box.querySelectorAll("[data-aid]").forEach((btn) => btn.addEventListener("click", () => run(btn, async () => {
      replaceCurrent(await api(`/api/listings/${l.id}/building`, { method: "PUT", body: { assessor_id: Number(btn.dataset.aid) } }));
    })));
  });
  on("#b-save", async () => {
    const num = (id) => { const v = $(id, body).value; return v === "" ? null : Number(v); };
    const str = (id) => $(id, body).value || null;
    replaceCurrent(await api(`/api/listings/${l.id}/building`, { method: "PUT", body: {
      year_built: num("#b-year"), eff_year: num("#b-eff"), stories: num("#b-stories"),
      constr_class: str("#b-constr"), bldg_quality: str("#b-quality"), condition: str("#b-cond"),
    } }));
  });

  const files = $("#p-files", body);
  if (files) files.addEventListener("change", async () => {
    const picked = [...files.files];
    if (!picked.length) return;
    setMsg(`Preparing ${picked.length} photo${picked.length > 1 ? "s" : ""}…`);
    const images = [];
    const problems = [];
    for (const f of picked) {
      try { images.push(await shrinkImage(f)); } catch (e) { problems.push(e.message); }
    }
    try {
      if (images.length) {
        const res = await api(`/api/listings/${l.id}/photos`, { method: "POST", body: { images } });
        problems.push(...res.warnings);
        replaceCurrent(res);
      }
      setMsg(problems.length ? problems.join(" ") : "Photos added.", problems.length > 0);
    } catch (e) { setMsg(e.message, true); }
  });
  body.querySelectorAll("[data-del]").forEach((btn) => btn.addEventListener("click", () => run(btn, async () => {
    replaceCurrent(await api(`/api/listings/${l.id}/photos/${encodeURIComponent(btn.dataset.del)}`, { method: "DELETE" }));
  })));
  on("#p-analyze", async () => {
    setMsg("Analyzing photos… this can take up to a minute.");
    replaceCurrent(await api(`/api/listings/${l.id}/photos/analyze`, { method: "POST" }));
    setMsg("Analysis updated.");
  });

  body.querySelectorAll(".seg").forEach((seg) => seg.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    const wasOn = b.classList.contains("on");
    seg.querySelectorAll("button").forEach((x) => { x.classList.remove("on"); x.setAttribute("aria-pressed", "false"); });
    if (!wasOn) { b.classList.add("on"); b.setAttribute("aria-pressed", "true"); }
  }));
  on("#t-save", async () => {
    const ratings = {};
    body.querySelectorAll(".seg").forEach((seg) => {
      const sel = $("button.on", seg);
      if (sel) ratings[seg.dataset.key] = Number(sel.dataset.n);
    });
    replaceCurrent(await api(`/api/listings/${l.id}/checklist`, { method: "PUT", body: { ratings, notes: $("#t-notes", body).value } }));
    setMsg("Saved.");
  });
}

// ---- startup ---------------------------------------------------------------

async function init() {
  CONFIG = await api("/api/config");
  $("#sub").textContent = `Commute by car to ${CONFIG.work_address} · leave ${CONFIG.depart_home}, return ${CONFIG.depart_work}`;
  $("#keys").innerHTML = [
    [CONFIG.has_anthropic_key, "Claude extraction"],
    [CONFIG.has_google_key, "Drive times"],
    [CONFIG.assessor_records > 0, `County records${CONFIG.assessor_records ? ` (${CONFIG.assessor_records.toLocaleString()})` : ""}`],
  ].map(([ok, label]) => `<span class="chip ${ok ? "pass" : ""}" title="${ok ? "Ready" : "Not set up — see README"}">${ok ? "✓" : "○"} ${label}</span>`).join("");
  if (!CONFIG.has_anthropic_key) $("#add-extract").checked = false;
  $("#manual-fields").innerHTML = FIELDS.filter(([k]) => k !== "url").map(([k, l, t]) => fieldInput(k, l, t, null)).join("");

  $("#add-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = $("#add-btn");
    const msg = $("#add-msg");
    const text = $("#add-text").value.trim();
    const fields = Object.fromEntries(Object.entries(readFields($("#manual-fields"))).filter(([, v]) => v !== null));
    if (!text && !Object.keys(fields).length) { msg.textContent = "Paste listing text or enter some details."; msg.classList.add("err"); return; }
    btn.disabled = true;
    msg.classList.remove("err");
    msg.textContent = $("#add-extract").checked && text ? "Reading the listing and calculating the commute…" : "Saving…";
    try {
      const created = await api("/api/listings", { method: "POST", body: {
        text: text || null, url: $("#add-url").value.trim() || null, extract: $("#add-extract").checked, fields,
      } });
      msg.textContent = created.warnings.length ? created.warnings.join(" ") : "Added.";
      msg.classList.toggle("err", created.warnings.length > 0);
      $("#add-form").reset();
      if (!CONFIG.has_anthropic_key) $("#add-extract").checked = false;
      LISTINGS.unshift(created);
      renderList();
      openDetail(created.id);
    } catch (err) {
      msg.textContent = err.message;
      msg.classList.add("err");
    } finally {
      btn.disabled = false;
    }
  });

  ["#filter-status", "#sort", "#only-pass"].forEach((s) => $(s).addEventListener("change", renderList));
  $("#list").addEventListener("click", (e) => { const it = e.target.closest(".item"); if (it) openDetail(Number(it.dataset.id)); });
  $("#list").addEventListener("keydown", (e) => { const it = e.target.closest(".item"); if (it && e.key === "Enter") openDetail(Number(it.dataset.id)); });
  $("#d-close").addEventListener("click", () => $("#detail").close());
  $("#d-tabs").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) { currentTab = b.dataset.tab; renderDetail(); } });

  await refresh();
}

init().catch((e) => { $("#sub").textContent = "Could not load: " + e.message; });
