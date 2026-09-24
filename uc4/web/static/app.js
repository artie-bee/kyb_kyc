/* Filtering, sorting, row links and the theme toggle.

   Every screen is complete in the HTML the server sends. This file narrows,
   reorders and navigates - nothing here fetches, and nothing here decides. With
   the script blocked the console still works; you just sort and filter less. */
(function () {
  "use strict";

  // ---- theme ------------------------------------------------------------
  var KEY = "wal-uc4-theme";
  try {
    var saved = localStorage.getItem(KEY);
    if (saved) document.documentElement.setAttribute("data-theme", saved);
  } catch (e) { /* private window, blocked storage: the default theme is fine */ }

  var themeBtn = document.getElementById("theme");
  if (themeBtn) themeBtn.addEventListener("click", function () {
    var root = document.documentElement;
    var now = root.getAttribute("data-theme");
    var dark = now === "dark" ||
      (now !== "light" && window.matchMedia("(prefers-color-scheme: dark)").matches);
    var next = dark ? "light" : "dark";
    root.setAttribute("data-theme", next);
    try { localStorage.setItem(KEY, next); } catch (e) { /* nothing to do */ }
  });

  // ---- case picker ------------------------------------------------------
  var picker = document.getElementById("casepick");
  if (picker) picker.addEventListener("change", function () {
    location.href = picker.getAttribute("data-base") + picker.value;
  });

  // ---- clickable rows ---------------------------------------------------
  // The row carries the same destination as the link in its first cell, so a
  // middle-click or a keyboard user still gets a real anchor.
  Array.prototype.forEach.call(document.querySelectorAll("tr.rowlink"), function (tr) {
    tr.addEventListener("click", function (ev) {
      if (ev.target.closest("a, button, input, select, label")) return;
      location.href = tr.getAttribute("data-href");
    });
  });

  // ---- filter and sort --------------------------------------------------
  var table = document.getElementById("cases");
  if (!table) return;
  var tbody = table.tBodies[0];
  var rows = Array.prototype.slice.call(tbody.rows);
  var shown = document.getElementById("shown");
  var empty = document.getElementById("empty");

  function checked(name) {
    var out = {};
    Array.prototype.forEach.call(
      document.querySelectorAll('input[name="' + name + '"]:checked'),
      function (b) { out[b.value] = true; });
    return out;
  }

  function apply() {
    var st = checked("status"), ow = checked("owner"), n = 0;
    rows.forEach(function (tr) {
      var ok = st[tr.dataset.status] && (ow[tr.dataset.owner] || !tr.dataset.owner);
      tr.hidden = !ok;
      if (ok) n++;
    });
    if (shown) shown.textContent = n;
    if (empty) empty.hidden = n !== 0;
  }

  Array.prototype.forEach.call(document.querySelectorAll(".filters input"),
    function (b) { b.addEventListener("change", apply); });

  var dir = {};
  Array.prototype.forEach.call(table.tHead.rows[0].cells, function (th, i) {
    if (!th.dataset.sort) return;
    th.tabIndex = 0;
    function sort() {
      var desc = dir[i] === "ascending";
      Array.prototype.forEach.call(table.tHead.rows[0].cells, function (o) {
        o.removeAttribute("aria-sort");
      });
      th.setAttribute("aria-sort", desc ? "descending" : "ascending");
      dir[i] = desc ? "descending" : "ascending";
      var num = th.dataset.sort === "num";
      rows.sort(function (a, b) {
        var x = a.cells[i].textContent.trim(), y = b.cells[i].textContent.trim();
        if (num) {
          // an em dash means "not scored", which sorts below every real number
          var nx = parseFloat(x), ny = parseFloat(y);
          if (isNaN(nx)) nx = -1;
          if (isNaN(ny)) ny = -1;
          return desc ? ny - nx : nx - ny;
        }
        return desc ? y.localeCompare(x) : x.localeCompare(y);
      });
      rows.forEach(function (tr) { tbody.appendChild(tr); });
    }
    th.addEventListener("click", sort);
    th.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); sort(); }
    });
  });

  apply();
})();
