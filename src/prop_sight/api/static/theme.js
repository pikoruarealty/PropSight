/* Theme toggle and the user menu.
 *
 * The initial class is set by an inline script in <head> before first paint;
 * this file only handles the toggle afterwards. Anything that has baked a colour
 * into a canvas (Chart.js) listens for `propsight:themechange` and repaints —
 * CSS variables cannot reach inside a canvas.
 */
(function () {
  var STORAGE_KEY = 'propsight-theme';

  function setTheme(dark) {
    document.documentElement.classList.toggle('dark', dark);
    try { localStorage.setItem(STORAGE_KEY, dark ? 'dark' : 'light'); } catch (e) {}
    document.dispatchEvent(new CustomEvent('propsight:themechange', { detail: { dark: dark } }));
  }

  var toggle = document.getElementById('theme-toggle');
  if (toggle) {
    toggle.addEventListener('click', function () {
      setTheme(!document.documentElement.classList.contains('dark'));
    });
  }

  // Follow the OS only while the user has expressed no preference of their own.
  var media = window.matchMedia('(prefers-color-scheme: dark)');
  media.addEventListener('change', function (e) {
    var stored = null;
    try { stored = localStorage.getItem(STORAGE_KEY); } catch (err) {}
    if (!stored) setTheme(e.matches);
  });

  // The print stylesheet forces a white/black page for the PDF — deliberately,
  // ink-saving is the point. Charts don't care about that stylesheet (their
  // colours are baked into the canvas at draw time from whatever the theme
  // was), but re-asserting the theme and forcing a repaint here is what
  // guarantees the live page actually matches it again once the print dialog
  // closes, rather than trusting that nothing drifted while it was open.
  window.addEventListener('afterprint', function () {
    var stored = null;
    try { stored = localStorage.getItem(STORAGE_KEY); } catch (err) {}
    var dark = stored ? stored === 'dark' : media.matches;
    setTheme(dark);
  });

  var btn = document.getElementById('user-menu-btn');
  var panel = document.getElementById('user-menu-panel');
  if (btn && panel) {
    btn.addEventListener('click', function (e) {
      e.stopPropagation();
      var open = panel.classList.toggle('hidden');
      btn.setAttribute('aria-expanded', String(!open));
    });
    document.addEventListener('click', function () {
      panel.classList.add('hidden');
      btn.setAttribute('aria-expanded', 'false');
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') panel.classList.add('hidden');
    });
  }
})();
