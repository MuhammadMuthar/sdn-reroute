/* Theme toggle, scroll reveal, reduced-motion handling, and the results loader. */
(function () {
  'use strict';
  var root = document.documentElement;

  // ---- theme -----------------------------------------------------------
  var btn = document.getElementById('theme-toggle');
  function currentTheme() {
    var t = root.getAttribute('data-theme');
    if (t === 'dark' || t === 'light') return t;
    return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
  if (btn) {
    btn.addEventListener('click', function () {
      var next = currentTheme() === 'dark' ? 'light' : 'dark';
      root.setAttribute('data-theme', next);
      try { localStorage.setItem('theme', next); } catch (e) {}
    });
  }

  // ---- reduced motion: pause SMIL animations in the topology ------------
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)');
  function applyMotion() {
    var svg = document.querySelector('#topo svg');
    if (!svg || typeof svg.pauseAnimations !== 'function') return;
    if (reduce && reduce.matches) svg.pauseAnimations(); else svg.unpauseAnimations();
  }
  applyMotion();
  if (reduce && reduce.addEventListener) reduce.addEventListener('change', applyMotion);

  // ---- reveal on scroll (content is visible without JS or observers) ----
  var items = document.querySelectorAll('.reveal');
  if ('IntersectionObserver' in window && !(reduce && reduce.matches)) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) { en.target.classList.add('in'); io.unobserve(en.target); }
      });
    }, { rootMargin: '0px 0px -8% 0px', threshold: 0.05 });
    items.forEach(function (el) { io.observe(el); });
    // Safety net: anything still hidden after 2 s becomes visible.
    setTimeout(function () { items.forEach(function (el) { el.classList.add('in'); }); }, 2000);
  } else {
    items.forEach(function (el) { el.classList.add('in'); });
  }

  // ---- results ---------------------------------------------------------
  var PLACEHOLDER_SVGS = {
    line: '<svg viewBox="0 0 220 110" aria-hidden="true"><path class="pl-axis" d="M20,10 L20,95 L210,95"/><path class="pl-line" d="M20,40 L80,40 L95,80 L120,70 L150,52 L210,40"/></svg>',
    bars: '<svg viewBox="0 0 220 110" aria-hidden="true"><path class="pl-axis" d="M20,10 L20,95 L210,95"/><rect class="pl-bar" x="40" y="55" width="26" height="40"/><rect class="pl-bar" x="80" y="30" width="26" height="65"/><rect class="pl-bar" x="120" y="42" width="26" height="53"/><rect class="pl-bar" x="160" y="48" width="26" height="47"/></svg>',
    scatter: '<svg viewBox="0 0 220 110" aria-hidden="true"><path class="pl-axis" d="M20,10 L20,95 L210,95"/><circle class="pl-bar" cx="50" cy="70" r="4"/><circle class="pl-bar" cx="80" cy="60" r="4"/><circle class="pl-bar" cx="110" cy="52" r="4"/><circle class="pl-bar" cx="140" cy="38" r="4"/><circle class="pl-bar" cx="170" cy="30" r="4"/><circle class="pl-bar" cx="95" cy="75" r="4"/></svg>'
  };

  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }

  function renderResults(data) {
    if (!data) return;
    var note = document.getElementById('results-note');
    var upd = document.getElementById('results-updated');
    var pill = document.getElementById('status-text');
    if (data.status && data.status.headline && pill) pill.textContent = data.status.headline;
    if (data.status && data.status.note && note) note.textContent = data.status.note;
    if (upd) upd.textContent = data.updated ? 'Last updated ' + data.updated : 'Last updated: never';

    var numWrap = document.getElementById('numbers');
    if (numWrap && Array.isArray(data.numbers) && data.numbers.length) {
      numWrap.hidden = false;
      numWrap.innerHTML = data.numbers.map(function (n) {
        return '<div class="num-tile"><span class="v">' + esc(n.value) + (n.unit ? ' <span class="small muted">' + esc(n.unit) + '</span>' : '') + '</span>' +
          '<span class="l">' + esc(n.label) + '</span>' + (n.source ? '<span class="s">' + esc(n.source) + '</span>' : '') + '</div>';
      }).join('');
    }

    var figWrap = document.getElementById('figs');
    if (figWrap && Array.isArray(data.figures)) {
      figWrap.innerHTML = data.figures.map(function (f) {
        var body;
        if (f.src) {
          body = '<img src="' + esc(f.src) + '" alt="' + esc(f.alt || f.title) + '" loading="lazy">';
        } else {
          body = '<div class="ph">' + (PLACEHOLDER_SVGS[f.kind] || PLACEHOLDER_SVGS.line) +
            '<span>Not measured yet · produced by Chunk ' + esc(f.chunk) + '</span></div>';
        }
        return '<article class="fig"><div class="fig-head"><h3>' + esc(f.title) + '</h3><span class="tag' + (f.src ? ' done' : '') + '">' + (f.src ? 'measured' : 'Chunk ' + esc(f.chunk)) + '</span></div>' +
          body + '<div class="fig-foot"><span>' + esc(f.caption) + '</span>' + (f.script ? '<span class="src">' + esc(f.script) + '</span>' : '') + '</div></article>';
      }).join('');
    }
  }

  function loadResults() {
    if (!window.fetch) return;
    fetch('results/results.json', { cache: 'no-cache' })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(renderResults)
      .catch(function () {
        var inline = document.getElementById('results-inline');
        if (inline) { try { renderResults(JSON.parse(inline.textContent)); } catch (e) {} }
      });
  }
  loadResults();
})();
