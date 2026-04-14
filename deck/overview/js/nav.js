// === SECTION LOADER + NAV ===

var SECTIONS = [
  'sections/xkcd.html',
  'sections/problem.html',
  'sections/solution.html',
  'sections/layout.html',
  'sections/how-it-works.html',
  'sections/get-started.html',
];

var current = 0;
var substep = 0;
var locked = false;
var stops = [];
var svgCache = {};

var btnUp = document.getElementById('key-up');
var btnDown = document.getElementById('key-down');
var btnLeft = document.getElementById('key-left');
var btnRight = document.getElementById('key-right');
var dpad = document.getElementById('nav-dpad');

// --- Load sections ---
async function loadSections() {
  var main = document.getElementById('main');
  for (var i = 0; i < SECTIONS.length; i++) {
    var html = await fetch(SECTIONS[i]).then(function(r) { return r.text(); });
    main.insertAdjacentHTML('beforeend', html);
  }
  hljs.highlightAll();

  stops = [document.getElementById('hero')];
  var secs = document.querySelectorAll('main section');
  for (var j = 0; j < secs.length; j++) stops.push(secs[j]);

  // Inject animated SVGs inline
  var containers = document.querySelectorAll('.anim-svg[data-svg]');
  for (var k = 0; k < containers.length; k++) {
    var url = containers[k].getAttribute('data-svg');
    var txt = await fetch(url).then(function(r) { return r.text(); });
    svgCache[url] = txt;
    containers[k].innerHTML = txt;
  }

  updateKeys();
}

// --- Restart animated SVGs on current slide ---
function restartAnimatedSVGs() {
  if (!stops[current]) return;
  var containers = stops[current].querySelectorAll('.anim-svg');
  if (!containers.length) return;

  for (var j = 0; j < containers.length; j++) {
    var div = containers[j];
    if (typeof div.getAnimations === 'function') {
      var anims = div.getAnimations({ subtree: true });
      if (anims.length > 0) {
        for (var i = 0; i < anims.length; i++) {
          anims[i].cancel();
          anims[i].play();
        }
        continue;
      }
    }
    var url = div.getAttribute('data-svg');
    var cached = url && svgCache[url];
    if (!cached) continue;
    while (div.firstChild) div.removeChild(div.firstChild);
    (function(target, svg) {
      requestAnimationFrame(function() {
        requestAnimationFrame(function() {
          target.innerHTML = svg;
        });
      });
    })(div, cached);
  }
}

// --- Substep helpers ---
function getMaxSubsteps() {
  if (!stops[current]) return 0;
  var attr = stops[current].getAttribute('data-substeps');
  return attr ? parseInt(attr, 10) : 0;
}

function hasSubsteps() {
  return getMaxSubsteps() > 0;
}

function showSubstep() {
  if (!hasSubsteps()) return;
  var sec = stops[current];
  var subs = sec.querySelectorAll('.substep');
  for (var i = 0; i < subs.length; i++) {
    subs[i].style.display = (parseInt(subs[i].getAttribute('data-step'), 10) === substep) ? 'flex' : 'none';
  }
  // Update breadcrumb
  var pips = sec.querySelectorAll('.pip');
  for (var j = 0; j < pips.length; j++) {
    if (parseInt(pips[j].getAttribute('data-step'), 10) === substep) {
      pips[j].classList.add('active');
    } else {
      pips[j].classList.remove('active');
    }
  }
}

// --- Update d-pad ---
function updateKeys() {
  var last = stops.length - 1;
  btnUp.disabled = (current <= 0);
  btnDown.disabled = (current >= last);

  if (hasSubsteps()) {
    btnLeft.disabled = (substep <= 0);
    btnRight.disabled = (substep >= getMaxSubsteps() - 1);
  } else {
    btnLeft.disabled = true;
    btnRight.disabled = true;
  }

  dpad.classList.toggle('on-dark', current === 0);
  dpad.classList.toggle('on-light', current !== 0);
}

// --- Navigate sections ---
function goTo(idx) {
  var last = stops.length - 1;
  var next = Math.max(0, Math.min(idx, last));
  if (next === current || locked || stops.length === 0) return;
  locked = true;
  current = next;
  substep = 0;
  if (hasSubsteps()) showSubstep();
  updateKeys();
  stops[current].scrollIntoView({ behavior: 'smooth' });
  setTimeout(function() {
    locked = false;
    restartAnimatedSVGs();
  }, 750);
}

// --- Navigate substeps ---
function goSubstep(dir) {
  if (!hasSubsteps()) return;
  var next = substep + dir;
  if (next < 0 || next >= getMaxSubsteps()) return;
  substep = next;
  showSubstep();
  updateKeys();
}

// --- Flash ---
function flash(btn) {
  if (!btn) return;
  btn.classList.add('pressed');
  setTimeout(function() { btn.classList.remove('pressed'); }, 150);
}

// --- Scroll sync ---
var observer = new IntersectionObserver(function(entries) {
  if (locked) return;
  for (var i = 0; i < entries.length; i++) {
    if (entries[i].isIntersecting && entries[i].intersectionRatio >= 0.5) {
      var idx = stops.indexOf(entries[i].target);
      if (idx !== -1 && idx !== current) {
        current = idx;
        substep = 0;
        if (hasSubsteps()) showSubstep();
        updateKeys();
        restartAnimatedSVGs();
      }
    }
  }
}, { threshold: 0.5 });

// --- Keyboard ---
document.addEventListener('keydown', function(e) {
  var k = e.key;
  if (k === 'ArrowDown' || k.toLowerCase() === 's') {
    e.preventDefault(); goTo(current + 1); flash(btnDown);
  } else if (k === 'ArrowUp' || k.toLowerCase() === 'w') {
    e.preventDefault(); goTo(current - 1); flash(btnUp);
  } else if (k === 'ArrowRight' || k.toLowerCase() === 'd') {
    e.preventDefault(); goSubstep(1); flash(btnRight);
  } else if (k === 'ArrowLeft' || k.toLowerCase() === 'a') {
    e.preventDefault(); goSubstep(-1); flash(btnLeft);
  }
});

// --- Click ---
btnUp.addEventListener('click', function() { goTo(current - 1); flash(btnUp); });
btnDown.addEventListener('click', function() { goTo(current + 1); flash(btnDown); });
btnLeft.addEventListener('click', function() { goSubstep(-1); flash(btnLeft); });
btnRight.addEventListener('click', function() { goSubstep(1); flash(btnRight); });

// --- Init ---
loadSections().then(function() {
  for (var i = 0; i < stops.length; i++) observer.observe(stops[i]);
});