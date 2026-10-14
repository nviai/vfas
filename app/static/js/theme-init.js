// Loaded synchronously in <head> (no defer) so the light/dark theme is applied before the page paints – avoids a flash of the wrong colors.
// The `theme` cookie (light | dark | system) wins, then the data-theme set by the server on <html>.
(function () {
  try {
    var root = document.documentElement;
    var m = document.cookie.match(/(?:^|;\s*)theme=([^;]+)/);
    var theme = m ? decodeURIComponent(m[1]) : root.getAttribute("data-theme") || "system";
    var mq = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
    var apply = function () {
      var dark = theme === "dark" || (theme === "system" && !!mq && mq.matches);
      root.classList.toggle("dark", dark);
      root.setAttribute("data-theme", theme);
    };
    apply();
    if (mq && mq.addEventListener) {
      mq.addEventListener("change", function () { if (theme === "system") apply(); });
    }
    window.__theme = {
      get: function () { return theme; },
      set: function (value) { theme = value; apply(); },
    };
  } catch (e) { /* browser blocks cookies: keep the class set by the server */ }
  try {
    if (window.localStorage.getItem("ui.sidebar.collapsed") === "1") document.documentElement.setAttribute("data-sidebar", "collapsed");
  } catch (e) { /* localStorage is blocked */ }
})();
