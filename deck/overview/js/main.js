// === MAIN ORCHESTRATOR ===

const hero = document.getElementById("hero");
const canvas = document.getElementById("sky");
const ctx = canvas.getContext("2d");

// --- Stars ---
const PAD = 0.08;
const layers = [
  { stars: [], d: 0.015, n: 70 },
  { stars: [], d: 0.035, n: 40 },
  { stars: [], d: 0.06,  n: 16 },
];
const shoots = [];

function initStars() {
  for (const l of layers) {
    l.stars = [];
    for (let i = 0; i < l.n; i++) {
      l.stars.push({
        nx: -PAD + Math.random() * (1 + PAD * 2),
        ny: -PAD + Math.random() * (1 + PAD * 2),
        r: Math.random() * (l === layers[2] ? 1.5 : 1) + 0.2,
        ph: Math.random() * 6.28,
        sp: 0.2 + Math.random() * 0.6,
        blue: Math.random() > 0.88,
        hue: 210 + Math.floor(Math.random() * 20),
      });
    }
  }
  scheduleShoot();
}

function scheduleShoot() {
  setTimeout(() => {
    shoots.push({
      nx: Math.random() * 0.5 + 0.1,
      ny: Math.random() * 0.3,
      vx: 2.5 + Math.random() * 3,
      vy: 0.8 + Math.random() * 1.2,
      life: 1,
      len: 35 + Math.random() * 45,
    });
    scheduleShoot();
  }, 4000 + Math.random() * 8000);
}

function drawStars(w, h, t, smx, smy) {
  const ox = (smx - 0.5) * w;
  const oy = (smy - 0.5) * h;
  for (const l of layers) {
    const dx = ox * l.d, dy = oy * l.d;
    for (const s of l.stars) {
      const a = 0.1 + 0.9 * (0.5 + 0.5 * Math.sin(t * 0.001 * s.sp + s.ph));
      ctx.beginPath();
      ctx.arc(s.nx * w + dx, s.ny * h + dy, s.r, 0, 6.28);
      ctx.fillStyle = s.blue
        ? `hsla(${s.hue},55%,72%,${a})`
        : `rgba(255,255,255,${a})`;
      ctx.fill();
    }
  }
  for (let i = shoots.length - 1; i >= 0; i--) {
    const s = shoots[i];
    if (s.nx !== undefined) { s.x = s.nx * w; s.y = s.ny * h; delete s.nx; delete s.ny; }
    s.x += s.vx; s.y += s.vy; s.life -= 0.012;
    if (s.life <= 0) { shoots.splice(i, 1); continue; }
    const tail = s.len * 0.35;
    const g = ctx.createLinearGradient(s.x, s.y, s.x - s.vx * tail, s.y - s.vy * tail);
    g.addColorStop(0, `rgba(200,225,255,${s.life * 0.7})`);
    g.addColorStop(1, "rgba(200,225,255,0)");
    ctx.beginPath(); ctx.moveTo(s.x, s.y);
    ctx.lineTo(s.x - s.vx * tail, s.y - s.vy * tail);
    ctx.strokeStyle = g; ctx.lineWidth = 1; ctx.stroke();
    ctx.beginPath(); ctx.arc(s.x, s.y, 0.8, 0, 6.28);
    ctx.fillStyle = `rgba(255,255,255,${s.life * 0.4})`; ctx.fill();
  }
}

// --- Resize ---
let resizing = false;
function resize() {
  if (resizing) return;
  resizing = true;
  canvas.width = hero.offsetWidth * 2;
  canvas.height = hero.offsetHeight * 2;
  ctx.scale(2, 2);
  resizing = false;
}
resize();
let resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(resize, 100);
});

// --- Mouse ---
let mx = 0.5, my = 0.5, smx = 0.5, smy = 0.5;
hero.addEventListener("mousemove", (e) => {
  const r = hero.getBoundingClientRect();
  mx = (e.clientX - r.left) / r.width;
  my = (e.clientY - r.top) / r.height;
});
hero.addEventListener("mouseleave", () => { mx = 0.5; my = 0.5; });

// --- Init ---
initStars();

// --- Draw ---
function draw(t) {
  const w = hero.offsetWidth, h = hero.offsetHeight;
  ctx.clearRect(0, 0, w, h);
  smx += (mx - smx) * 0.05;
  smy += (my - smy) * 0.05;
  drawStars(w, h, t, smx, smy);
  requestAnimationFrame(draw);
}
requestAnimationFrame(draw);