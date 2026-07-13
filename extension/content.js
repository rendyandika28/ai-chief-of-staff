// LinkedIn Job Assist — content script.
// Satu klik = auto-scan: scroll otomatis sampe feed habis, kumpulin post ber-EMAIL
// (cold approach — tanpa email di-skip), kirim sekali, server skor + auto-draft Gmail.
// Hasil ditandain badge per post di halaman — gak usah bolak-balik dashboard.
// CORS: fetch dilakukan background service worker, bukan di sini.

// LinkedIn ganti DOM sering — semua selector di satu tempat biar gampang patch.
// 2026: UI baru "SDUI" (class di-hash, rotasi) — pegangan cuma data-testid/componentkey/role.
// DOM lama (classic) dipertahankan sebagai fallback buat akun yang belum kena rollout.
const SEL = {
  sduiItem: 'div[role="listitem"]',
  sduiText: '[data-testid="expandable-text-box"], p[componentkey^="feed-commentary"]',
  post: 'div.feed-shared-update-v2, div[data-urn*="activity"]',
  postText: ".update-components-text, .feed-shared-inline-show-more-text",
  postTime: ".update-components-actor__sub-description, time",
  postActor: ".update-components-actor__title",
};

const EMAIL_RE = /[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/;
// Token umur di header post: "13h •", "30 mnt". "2nd"/"3rd+" (koneksi) gak kena.
const AGE_TOKEN_RE = /(\d+)\s*(mnt|menit|min|m|jam|hour|j|h|hari|day|d|mgg|minggu|w|bln|mo|thn|yr)\b/i;

let MAX_AGE_MIN = 1440; // default 24 jam, ubah di Options
chrome.storage.sync.get(["maxAgeMin"], (v) => { if (v.maxAgeMin > 0) MAX_AGE_MIN = v.maxAgeMin; });

const MAX_SCROLLS = 25; // batas scroll biar gak infinite di feed panjang
const IDLE_STOP = 3;    // n scroll tanpa post baru = feed habis

// "34m", "1h", "1 j", "30 mnt", "baru saja" → menit. Gak kebaca → null (skip, konservatif).
function parseAgeMinutes(text) {
  const t = (text || "").toLowerCase();
  if (/just now|baru saja|now/.test(t)) return 0;
  const m = t.match(/(\d+)\s*(m\b|mnt|menit|min)/);
  if (m) return parseInt(m[1], 10);
  const h = t.match(/(\d+)\s*(h\b|j\b|jam|hour)/);
  if (h) return parseInt(h[1], 10) * 60;
  if (/(\d+)\s*(d\b|hr\b|hari|day|w\b|mgg|minggu|week|bln|month|mo\b|thn|yr)/.test(t)) return 9999;
  return null;
}

function postUrl(el) {
  const urn = el.getAttribute("data-urn") || el.closest("[data-urn]")?.getAttribute("data-urn");
  if (urn) return `https://www.linkedin.com/feed/update/${urn}`;
  const a = el.querySelector('a[href*="/feed/update/"], a[href*="urn:li:activity"], a[href*="urn%3Ali%3Aactivity"]');
  return a ? a.href.split("?")[0] : "";
}

// ── Badge per post ──────────────────────────────────────────────────────────
const BADGE_STYLE = {
  ok:   { bg: "#123B2E", fg: "#6EE7C7", bd: "#2B7A5F" },  // kesimpen + draft
  warn: { bg: "#3B2E12", fg: "#F4B740", bd: "#7A652B" },  // kesimpen, draft gagal
  skip: { bg: "#1E242E", fg: "#7C8798", bd: "#333B48" },  // skor rendah / dup / no email
};

function badge(el, text, kind) {
  let b = el.querySelector(":scope > .lja-badge");
  if (!b) {
    b = document.createElement("div");
    b.className = "lja-badge";
    const s = BADGE_STYLE[kind] || BADGE_STYLE.skip;
    Object.assign(b.style, {
      position: "absolute", top: "8px", right: "8px", zIndex: 9999,
      padding: "3px 10px", borderRadius: "12px", fontSize: "11px", fontWeight: "600",
      background: s.bg, color: s.fg, border: `1px solid ${s.bd}`,
      fontFamily: "system-ui", pointerEvents: "none", maxWidth: "260px",
    });
    if (getComputedStyle(el).position === "static") el.style.position = "relative";
    el.appendChild(b);
  } else {
    const s = BADGE_STYLE[kind] || BADGE_STYLE.skip;
    Object.assign(b.style, { background: s.bg, color: s.fg, border: `1px solid ${s.bd}` });
  }
  b.textContent = text;
}

// ── Scan ────────────────────────────────────────────────────────────────────
function collectVisible() {
  // Ambil post yang belum diproses (dataset.ljaDone). Return [{el, item|null, skip}]
  let posts = [...document.querySelectorAll(SEL.sduiItem)].filter((el) => el.querySelector(SEL.sduiText));
  const sdui = posts.length > 0;
  if (!sdui) posts = [...document.querySelectorAll(SEL.post)];

  const found = [];
  posts.forEach((el) => {
    if (el.dataset.ljaDone) return;
    el.dataset.ljaDone = "1";

    let age, company, body;
    if (sdui) {
      const head = (el.innerText || "").slice(0, 300);
      age = parseAgeMinutes(head.match(AGE_TOKEN_RE)?.[0] || "");
      const lines = head.split("\n").map((s) => s.trim()).filter(Boolean);
      company = (lines.find((l) => !/^feed post|^postingan/i.test(l)) || "").slice(0, 120);
      body = [...el.querySelectorAll(SEL.sduiText)].map((t) => t.innerText).join("\n").trim();
    } else {
      age = parseAgeMinutes(el.querySelector(SEL.postTime)?.innerText);
      company = el.querySelector(SEL.postActor)?.innerText?.split("\n")[0]?.trim() || "";
      body = el.querySelector(SEL.postText)?.innerText?.trim() || "";
    }

    if (age === null || age > MAX_AGE_MIN) {
      badge(el, `⏭ > ${Math.round(MAX_AGE_MIN / 60)} jam`, "skip");
      found.push({ el, item: null });
      return;
    }
    if (body.length < 40) { found.push({ el, item: null }); return; }

    const email = body.match(EMAIL_RE)?.[0] || "";
    if (!email) {
      badge(el, "⏭ tanpa email", "skip");
      found.push({ el, item: null });
      return;
    }
    found.push({
      el,
      item: {
        type: "post",
        title: body.split("\n")[0].slice(0, 200),
        body: body.slice(0, 4000), email, company,
        location: "Remote", url: postUrl(el),
      },
    });
  });
  return found;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// SDUI: window gak pernah scroll (scrollY stuck 0) — scroller beneran = elemen <main>
// (overflow-y auto). Cari ancestor scrollable dari post, dorong scrollTop-nya langsung.
function findScrollable(el) {
  for (let n = el; n; n = n.parentElement) {
    if (n.scrollHeight > n.clientHeight + 300 && /(auto|scroll)/.test(getComputedStyle(n).overflowY)) return n;
  }
  return null;
}

function scrollFeed() {
  const posts = document.querySelectorAll(`${SEL.sduiItem}, ${SEL.post}`);
  const last = posts[posts.length - 1];
  const sc = (last && findScrollable(last)) || document.querySelector("main") || document.scrollingElement;
  if (sc) sc.scrollTop = sc.scrollHeight; // mentok bawah → trigger lazy-load batch berikut
}

async function autoScan(btn) {
  if (autoScan.running) return;
  autoScan.running = true;
  btn.disabled = true;

  const targets = []; // {el, item} yang bakal dikirim (ber-email)
  let seen = 0, idle = 0;

  for (let i = 0; i < MAX_SCROLLS && idle < IDLE_STOP; i++) {
    const found = collectVisible();
    seen += found.length;
    found.forEach((f) => f.item && targets.push(f));
    idle = found.length === 0 ? idle + 1 : 0;
    btn.textContent = `⇣ scroll ${i + 1} · ${seen} post · ${targets.length} ber-email`;
    scrollFeed();
    await sleep(1500 + Math.random() * 900); // jeda manusiawi, tunggu lazy-load
  }

  if (!targets.length) {
    finish(btn, seen ? `0 ber-email dari ${seen} post` : "0 post kebaca — selector DOM usang?");
    return;
  }

  btn.textContent = `⇡ kirim ${targets.length} + auto-draft…`;
  chrome.runtime.sendMessage(
    { kind: "ingest", items: targets.map((t) => t.item), auto_draft: true },
    (res) => {
      if (!res || res.error) {
        targets.forEach((t) => badge(t.el, "✗ gagal kirim", "warn"));
        finish(btn, `gagal: ${res?.error || "no response"}`);
        return;
      }
      // results urut sama persis dgn items yang dikirim
      let drafted = 0, stored = 0;
      res.results.forEach((r, i) => {
        const el = targets[i].el;
        if (!r.stored) {
          badge(el, r.job_id === null && r.score >= 75 ? "⏭ duplikat" : `⏭ ${r.score}% — skip`, "skip");
        } else if (r.drafted) {
          drafted++; stored++;
          badge(el, `✉ ${r.score}% — draft di Gmail`, "ok");
        } else {
          stored++;
          badge(el, `⚠ ${r.score}% kesimpen, draft gagal`, "warn");
        }
      });
      finish(btn, `✓ ${drafted} draft · ${stored} kesimpen · ${seen} post di-scan`);
    }
  );
}

function finish(btn, msg) {
  autoScan.running = false;
  btn.disabled = false;
  btn.textContent = msg;
  setTimeout(() => (btn.textContent = "⌕ Auto-scan"), 8000);
}

// Tombol floating — sekali per halaman.
if (!document.getElementById("ljassist-btn")) {
  const btn = document.createElement("button");
  btn.id = "ljassist-btn";
  btn.textContent = "⌕ Auto-scan";
  Object.assign(btn.style, {
    position: "fixed", bottom: "24px", right: "24px", zIndex: 99999,
    padding: "10px 16px", borderRadius: "24px", border: "none", cursor: "pointer",
    background: "#0B0E14", color: "#6EE7C7", fontSize: "13px", fontWeight: "600",
    boxShadow: "0 4px 14px rgba(0,0,0,.4)", maxWidth: "340px",
  });
  btn.onclick = () => autoScan(btn);
  document.body.appendChild(btn);
}
