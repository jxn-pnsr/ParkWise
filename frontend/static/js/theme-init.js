// Loaded in <head> (not a module) so the saved theme applies before the page paints.
(function () {
  try {
    var t = localStorage.getItem("pw-theme");
    if (t === "dark" || t === "light") document.documentElement.dataset.theme = t;
  } catch (e) { /* storage blocked */ }
})();
