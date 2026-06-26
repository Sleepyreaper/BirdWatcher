// BirdWatcher weekly grid — fetches /api/week and renders the heat-quilt.

const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
let currentStart = null; // ISO date string of the week's Sunday

// A stable hue per species so each row reads like its own color in the quilt.
function hueFor(name) {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) % 360;
  return h;
}

// Count -> lightness bucket. More sightings = more saturated/brighter square.
function cellColor(name, count) {
  if (count === 0) return null;
  const hue = hueFor(name);
  const bucket = count >= 10 ? 4 : count >= 6 ? 3 : count >= 3 ? 2 : 1;
  const light = [0, 26, 36, 48, 62][bucket];
  const sat = [0, 55, 62, 70, 80][bucket];
  return `hsl(${hue} ${sat}% ${light}%)`;
}

async function loadWeek(start) {
  const url = start ? `/api/week?start=${start}` : "/api/week";
  const res = await fetch(url);
  const data = await res.json();
  currentStart = data.start;
  render(data);
}

function render(data) {
  const grid = document.getElementById("grid");
  const empty = document.getElementById("empty");
  grid.innerHTML = "";

  const todayIso = new Date().toISOString().slice(0, 10);

  // label
  const startD = new Date(data.start + "T00:00:00");
  const endD = new Date(data.days[6] + "T00:00:00");
  document.getElementById("weeklabel").textContent =
    `${fmt(startD)} – ${fmt(endD)}` + (data.is_current ? "  (this week)" : "");

  // header row
  const head = document.createElement("div");
  head.className = "row head";
  head.appendChild(div("species", "<span class='name'>Species</span>"));
  data.days.forEach((iso, i) => {
    const d = new Date(iso + "T00:00:00");
    head.appendChild(div("daycol", `${DAYS[i]}<span class='dnum'>${d.getDate()}</span>`));
  });
  grid.appendChild(head);

  // stats
  renderStats(data);

  empty.hidden = data.species.length > 0;

  // species rows
  for (const sp of data.species) {
    const row = document.createElement("div");
    row.className = "row";

    const thumb = sp.thumb
      ? `<img src="/captures/${sp.thumb}" alt="${sp.name}" loading="lazy"/>`
      : `<div class="noimg">🐦</div>`;
    row.appendChild(div("species",
      `${thumb}<div><div class="name">${sp.name}</div>` +
      `<div class="tot">${sp.total} this week</div></div>`));

    sp.counts.forEach((count, i) => {
      const cell = document.createElement("div");
      cell.className = "cell" + (count ? " has" : "") +
        (data.days[i] === todayIso ? " today" : "");
      const color = cellColor(sp.name, count);
      if (color) cell.style.background = color;
      if (count) {
        cell.textContent = count;
        const times = sp.times[i] || [];
        cell.title = `${sp.name} — ${data.days[i]}\n${count} visit(s)` +
          (times.length ? `\n${times.join(", ")}` : "");
        cell.onclick = () => openLightbox(sp, i, data.days[i]);
      }
      row.appendChild(cell);
    });

    grid.appendChild(row);
  }
}

function renderStats(data) {
  const total = data.species.reduce((a, s) => a + s.total, 0);
  const distinct = data.species.length;
  // busiest day across all species
  const perDay = [0, 0, 0, 0, 0, 0, 0];
  data.species.forEach((s) => s.counts.forEach((c, i) => (perDay[i] += c)));
  const busiestIdx = perDay.indexOf(Math.max(...perDay));
  const rarest = data.species.length
    ? data.species.reduce((a, b) => (b.total < a.total ? b : a))
    : null;

  const el = document.getElementById("stats");
  el.innerHTML = "";
  el.appendChild(stat(total, "Total visits"));
  el.appendChild(stat(distinct, "Species"));
  el.appendChild(stat(total ? DAYS[busiestIdx] : "—", "Busiest day"));
  el.appendChild(stat(rarest && total ? rarest.name : "—", "Rarest visitor"));
}

function openLightbox(sp, dayIdx, dayIso) {
  const lb = document.getElementById("lightbox");
  document.getElementById("lb-title").textContent = `${sp.name} — ${dayIso}`;
  const body = document.getElementById("lb-body");
  body.innerHTML = "";
  const times = sp.times[dayIdx] || [];
  // We only kept the best thumbnail per species in the payload; show it +
  // the list of times seen that day. (A future API can return all crops.)
  if (sp.thumb) {
    const fig = document.createElement("figure");
    fig.innerHTML =
      `<img src="/captures/${sp.thumb}" alt="${sp.name}"/>` +
      `<figcaption>best capture</figcaption>`;
    body.appendChild(fig);
  }
  const list = document.createElement("div");
  list.innerHTML = `<p>Seen at: ${times.join(", ") || "—"}</p>`;
  body.appendChild(list);
  lb.hidden = false;
}

// helpers
function div(cls, html) {
  const d = document.createElement("div");
  d.className = cls;
  d.innerHTML = html;
  return d;
}
function stat(num, lbl) {
  return div("stat", `<div class="num">${num}</div><div class="lbl">${lbl}</div>`);
}
function fmt(d) {
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

// nav
document.getElementById("prev").onclick = () => shift(-7);
document.getElementById("next").onclick = () => shift(7);
document.getElementById("today").onclick = () => loadWeek(null);
document.getElementById("lb-close").onclick = () =>
  (document.getElementById("lightbox").hidden = true);
document.getElementById("lightbox").onclick = (e) => {
  if (e.target.id === "lightbox") e.target.hidden = true;
};

function shift(days) {
  const d = new Date(currentStart + "T00:00:00");
  d.setDate(d.getDate() + days);
  loadWeek(d.toISOString().slice(0, 10));
}

loadWeek(null);
