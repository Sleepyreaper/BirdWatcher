// BirdWatcher dashboard — visual visits + acoustic (BirdNET-Go) detections.

const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const PAIRS = [["#CECBF6","#26215C"],["#F5C4B3","#4A1B0C"],["#9FE1CB","#04342C"],
  ["#F4C0D1","#4B1528"],["#B5D4F4","#042C53"],["#C0DD97","#173404"],
  ["#FAC775","#412402"],["#F7C1C1","#501313"]];
let currentStart = null;
const LAST = {};  // last-seen count per cell, to pulse when it rises

function el(tag, cls, html) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html != null) e.innerHTML = html;
  return e;
}
function fmt(iso) { return new Date(iso + "T00:00:00").toLocaleDateString(undefined, { month: "short", day: "numeric" }); }
function pairFor(name) { let h = 0; for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) % PAIRS.length; return PAIRS[h]; }
function initials(name) { const w = name.split(/\s+/); return (((w[0] || " ")[0]) + ((w[1] || w[0] || " ")[0])).toUpperCase(); }
function avatar(sp) {
  if (sp.reference) return `<img class="av" src="${sp.reference}" alt="" loading="lazy">`;
  const [bg, fg] = pairFor(sp.name);
  return `<div class="av-badge" style="background:${bg};color:${fg}">${initials(sp.name)}</div>`;
}
// blue heat ramp for "seen", teal ramp for "heard"
function heat(c) { if (!c) return null; return c >= 10 ? ["#185FA5","#fff"] : c >= 6 ? ["#378ADD","#fff"] : c >= 3 ? ["#85B7EB","#042C53"] : ["#B5D4F4","#0C447C"]; }
function heatT(c) { if (!c) return null; return c >= 10 ? ["#0F6E56","#fff"] : c >= 6 ? ["#1D9E75","#fff"] : c >= 3 ? ["#5DCAA5","#04342C"] : ["#9FE1CB","#04342C"]; }

async function loadWeek(start) {
  const url = start ? `/api/week?start=${start}` : "/api/week";
  const d = await (await fetch(url)).json();
  currentStart = d.start;
  render(d);
}

function render(d) {
  document.getElementById("region").textContent = `${d.region.name || ""} · ${fmt(d.days[0])} – ${fmt(d.days[6])}`;
  document.getElementById("weeklabel").textContent = d.is_current ? "this week" : `${fmt(d.days[0])}`;

  const s = document.getElementById("stats");
  s.innerHTML = "";
  s.append(stat(d.stats.visits, "visits this week"), stat(d.stats.species_seen, "species seen"));
  if (d.audio_on) s.append(stat(d.stats.species_heard, "species heard"));
  s.append(stat(d.stats.on_list, "on your list"), stat(d.stats.busiest_day, "busiest day"));

  document.getElementById("legend-note").innerHTML = d.audio_on ? `🔊 also heard that day — confirmed at the feeder` : "";

  const g = document.getElementById("grid");
  g.innerHTML = "";
  const today = new Date().toISOString().slice(0, 10);

  const head = el("div", "row head");
  head.append(el("div", "species", `<span class="hlabel">Species</span>`));
  d.days.forEach((iso, i) => {
    const dt = new Date(iso + "T00:00:00");
    head.append(el("div", "daycol" + (iso === today ? " today" : ""), `${DAYS[i]}<span class="dnum">${dt.getDate()}</span>`));
  });
  g.append(head);

  document.getElementById("empty").hidden = d.seen.length > 0 || d.heard_only.length > 0;
  d.seen.forEach((sp) => g.append(seenRow(sp, d, today)));

  if (d.heard_only.length) {
    g.append(el("div", "divider", `<span class="sound">🔊</span> heard nearby · not seen at the feeder`));
    d.heard_only.forEach((sp) => g.append(heardRow(sp, d, today)));
  }
  const expected = d.catalog.filter((c) => !c.seen && !c.heard);
  if (expected.length) {
    g.append(el("div", "divider", `on your Cole's Special Feeder list · not seen this week`));
    expected.forEach((sp) => g.append(expectedRow(sp)));
  }
  renderNow(d, today);
}

function cellFor(count, isToday, heard, ramp, key) {
  const cell = el("div", "cell" + (count ? " has" : "") + (isToday ? " today" : ""));
  const col = ramp(count);
  if (col) { cell.style.background = col[0]; cell.style.color = col[1]; cell.innerHTML = count + (heard ? `<span class="snd">🔊</span>` : ""); }
  if (key) { if (LAST[key] != null && count > LAST[key]) cell.classList.add("pulse"); LAST[key] = count; }
  return cell;
}

function seenRow(sp, d, today) {
  const row = el("div", "row srow");
  const sub = `${sp.total} visit${sp.total === 1 ? "" : "s"}` + (sp.scientific ? ` · ${sp.scientific}` : "");
  row.append(el("div", "species", `${avatar(sp)}<div style="min-width:0"><div class="nm">${sp.name}</div><div class="sub">${sub}</div></div>`));
  sp.counts.forEach((c, i) => {
    const heard = sp.heard && sp.heard[i] > 0;
    const cell = cellFor(c, d.days[i] === today, c && heard, heat, `${currentStart}|${sp.name}|${i}`);
    if (c) { cell.title = `${sp.name} — ${d.days[i]}\n${c} visit(s)` + (heard ? " · also heard 🔊" : ""); cell.onclick = () => openDetail(sp, i, d.days[i]); }
    row.append(cell);
  });
  return row;
}

function heardRow(sp, d, today) {
  const row = el("div", "row srow");
  row.append(el("div", "species", `${avatar(sp)}<div style="min-width:0"><div class="nm">${sp.name}</div><div class="sub" style="color:var(--accent)">heard${sp.scientific ? ` · ${sp.scientific}` : ""}</div></div>`));
  sp.heard.forEach((h, i) => {
    const cell = cellFor(h, d.days[i] === today, false, heatT, `${currentStart}|H|${sp.name}|${i}`);
    if (h) cell.title = `${sp.name} — ${d.days[i]}\nheard ${h}× 🔊 (not seen at the feeder)`;
    row.append(cell);
  });
  return row;
}

function expectedRow(sp) {
  const row = el("div", "row srow");
  row.style.opacity = ".5";
  row.append(el("div", "species", `${avatar(sp)}<div style="min-width:0"><div class="nm">${sp.name}</div><div class="sub">expected</div></div>`));
  for (let i = 0; i < 7; i++) row.append(el("div", "cell"));
  return row;
}

function renderNow(d, today) {
  const card = document.getElementById("nowcard");
  const idx = d.days.indexOf(today);
  if (idx < 0) { card.hidden = true; return; }
  const seenToday = d.seen.filter((s) => s.counts[idx] > 0).sort((a, b) => b.counts[idx] - a.counts[idx]).slice(0, 3);
  const heardToday = d.seen.concat(d.heard_only).filter((s) => s.heard && s.heard[idx] > 0).sort((a, b) => b.heard[idx] - a.heard[idx]).slice(0, 1);
  if (!seenToday.length && !heardToday.length) { card.hidden = true; return; }
  const items = document.getElementById("now-items");
  items.innerHTML = "";
  if (seenToday.length) {
    items.append(el("span", null, `at the feeder today: ` +
      seenToday.map((s) => `<b>${s.name}</b> (${s.counts[idx]})`).join(", ")));
  }
  if (heardToday.length && d.audio_on) {
    items.append(el("span", null, `🔊 most heard: <b>${heardToday[0].name}</b> (${heardToday[0].heard[idx]})`));
  }
  card.hidden = false;
}

function openDetail(sp, dayIdx, dayIso) {
  const body = document.getElementById("lb-body");
  const ref = sp.reference ? `<figure><img src="${sp.reference}"><figcaption>field guide</figcaption></figure>` : "";
  const cap = sp.thumb ? `<figure><img src="/captures/${sp.thumb}"><figcaption>best frame caught</figcaption></figure>` : "";
  const times = sp.times[dayIdx] || [];
  const heard = sp.heard && sp.heard[dayIdx] > 0;
  body.innerHTML =
    `<h3>${sp.name}${heard ? " 🔊" : ""}</h3>` +
    `<div class="sci">${[sp.scientific, sp.family].filter(Boolean).join(" · ")}</div>` +
    `<div class="shots">${ref}${cap}</div>` +
    (cap ? `<div class="note">best frame kept from the visit; blurrier ones discarded</div>` : "") +
    (heard ? `<div class="note heard">🔊 heard here too — confirmed at the feeder by sound</div>` : "") +
    `<div class="kv"><span>${dayIso}</span><span>${times.length} visit(s)${times.length ? " · " + times.join(", ") : ""}</span></div>`;
  document.getElementById("lightbox").hidden = false;
}

function stat(n, l) { return el("div", "stat", `<div class="num">${n}</div><div class="lbl">${l}</div>`); }

// theme
function applyTheme(t) { document.documentElement.setAttribute("data-theme", t); localStorage.setItem("bw-theme", t); }
applyTheme(localStorage.getItem("bw-theme") || "light");
document.getElementById("theme").onclick = () =>
  applyTheme(document.documentElement.getAttribute("data-theme") === "light" ? "dark" : "light");

document.getElementById("prev").onclick = () => shift(-7);
document.getElementById("next").onclick = () => shift(7);
document.getElementById("today").onclick = () => loadWeek(null);
document.getElementById("lb-close").onclick = () => (document.getElementById("lightbox").hidden = true);
document.getElementById("lightbox").onclick = (e) => { if (e.target.id === "lightbox") e.target.hidden = true; };
function shift(days) { const dt = new Date(currentStart + "T00:00:00"); dt.setDate(dt.getDate() + days); loadWeek(dt.toISOString().slice(0, 10)); }

loadWeek(null);
setInterval(() => loadWeek(currentStart), 30000);
