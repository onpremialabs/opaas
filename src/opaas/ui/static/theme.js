// The theme the person chose, set before the page paints (D88). Loaded in
// <head>, not deferred, so a dark page never flashes light. Until a choice is
// made the system's preference holds (opaas.css). Kept in this browser only.
"use strict";
(function () {
  let chosen = null;
  try { chosen = localStorage.getItem("opaas.theme"); } catch (e) { chosen = null; }
  if (chosen === "light" || chosen === "dark") document.documentElement.dataset.theme = chosen;
})();
