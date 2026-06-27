// BirdWatcher weekly grid — reference photos on rows, deduped visit counts.

const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
let currentStart = null;

// stable hue per species so the grid reads like a quilt
function hueFor(name) {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) % 360;
  return h;
}
function cellColor(name, count) {
  if (!count) return null;
  const hue = hueFor(name);
  const b = count >= 10 ? 4 : count >= 6 ? 3 : count >= 3 ? 2 : 1;
  const light = [0, 26, 34, 44, 56][b];
  const sat = [0, 55, 64, 72, 82][b];
  return `hsl(${hue} ${sat}% ${light}%)`;
}

function el(tag, cls, html) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html != null) e.innerHTML = html;
  return e;
}
function fmt(iso) {
  return new Date(iso + "T00:00:00").toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

async function loadWeek(start) {
  const url = start ? `/api/week?start=${start}` : "/api/week";
  const d = await (await fetch(url)).json();
  currentStart = d.start;
  render(d);
}

function render(d) {
  document.getElementById("region").textContent =
    `${d.region.name || ""} · week of ${fmt(d.days[0])} – ${fmt(d.days[6])}`;
  document.getElementById("weeklabel").textContent =
    d.is_current ? "This week" : `${fmt(d.days[0])} – ${fmt(d.days[6])}`;

  const s = document.getElementById("stats");
  s.innerHTML = "";
  s.append(
    stat(d.stats.visits, "Visits this week"),
    stat(d.stats.species_seen, "Species seen"),
    stat(d.stats.on_list, "On your list"),
    stat(d.stats.busiest_day, "Busiest day"),
  );

  const g = document.getElementById("grid");
  g.innerHTML = "";
  const today = new Date().toISOString().slice(0, 10);

  const head = el("div", "row head");
  head.append(el("div", "species", `<span class="hlabel">Species</span>`));
  d.days.forEach((iso, i) => {
    const dt = new Date(iso + "T00:00:00");
    head.append(el("div", "daycol" + (iso === today ? " today" : ""),
      `${DAYS[i]}<span class="dnum">${dt.getDate()}</span>`));
  });
  g.append(head);

  document.getElementById("empty").hidden = d.seen.length > 0;
  d.seen.forEach((sp) => g.append(seenRow(sp, d, today)));

  const expected = d.catalog.filter((c) => !c.seen);
  if (expected.length) {
    g.append(el("div", "divider", `on your Cole's Special Feeder list · not seen this week`));
    expected.forEach((sp) => g.append(expectedRow(sp)));
  }
}

function avatar(ref) {
  return ref ? `<img class="av" src="${ref}" alt="" loading="lazy">` : `<div class="av ph">🐦</div>`;
}

function seenRow(sp, d, today) {
  const row = el("div", "row");
  const sub = `${sp.total} visit${sp.total === 1 ? "" : "s"}` + (sp.scientific ? ` · ${sp.scientific}` : "");
  row.append(el("div", "species",
    `${avatar(sp.reference)}<div class="meta"><div class="name">${sp.name}</div><div class="sub">${sub}</div></div>`));
  sp.counts.forEach((c, i) => {
    const cell = el("div", "cell" + (c ? " has" : "") + (d.days[i] === today ? " today" : ""));
    const col = cellColor(sp.name, c);
    if (col) cell.style.background = col;
    if (c) {
      cell.textContent = c;
      cell.title = `${sp.name} — ${d.days[i]}\n${c} visit(s)\n${(sp.times[i] || []).join(", ")}`;
      cell.onclick = () => openDetail(sp, i, d.days[i]);
    }
    row.append(cell);
  });
  return row;
}

function expectedRow(sp) {
  const row = el("div", "row dim");
  row.append(el("div", "species",
    `${avatar(sp.reference)}<div class="meta"><div class="name">${sp.name}</div><div class="sub">expected</div></div>`));
  for (let i = 0; i < 7; i++) row.append(el("div", "cell"));
  return row;
}

function openDetail(sp, dayIdx, dayIso) {
  const body = document.getElementById("lb-body");
  const ref = sp.reference ? `<figure><img src="${sp.reference}"><figcaption>field guide</figcaption></figure>` : "";
  const cap = sp.thumb ? `<figure><img src="/captures/${sp.thumb}"><figcaption>best frame caught</figcaption></figure>` : "";
  const times = sp.times[dayIdx] || [];
  body.innerHTML =
    `<h3>${sp.name}</h3>` +
    `<div class="sci">${[sp.scientific, sp.family].filter(Boolean).join(" · ")}</div>` +
    `<div class="shots">${ref}${cap}</div>` +
    (cap ? `<div class="note">best frame kept from the visit; blurrier ones discarded</div>` : "") +
    `<div class="kv"><span>${dayIso}</span><span>${times.length} visit(s)${times.length ? " · " + times.join(", ") : ""}</span></div>`;
  document.getElementById("lightbox").hidden = false;
}

function stat(n, l) {
  return el("div", "stat", `<div class="num">${n}</div><div class="lbl">${l}</div>`);
}

document.getElementById("prev").onclick = () => shift(-7);
document.getElementById("next").onclick = () => shift(7);
document.getElementById("today").onclick = () => loadWeek(null);
document.getElementById("lb-close").onclick = () => (document.getElementById("lightbox").hidden = true);
document.getElementById("lightbox").onclick = (e) => { if (e.target.id === "lightbox") e.target.hidden = true; };
function shift(days) {
  const dt = new Date(currentStart + "T00:00:00");
  dt.setDate(dt.getDate() + days);
  loadWeek(dt.toISOString().slice(0, 10));
}

loadWeek(null);

// auto-refresh so new captures appear without a manual reload (handy for a wall display)
setInterval(() => loadWeek(currentStart), 30000);
