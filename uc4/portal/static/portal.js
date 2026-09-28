/* The portal's only script: the theme switch, and a sign of life while a file
   uploads. Like the console's app.js, it adds and never decides - every page is
   complete as the server sends it, and every upload is a plain form post, so
   with the script blocked nothing is lost but the two conveniences. */
(function () {
  "use strict";

  // ---- theme: the button stays hidden unless this script runs -------------
  var KEY = "wal-uc4-theme";
  try {
    var saved = localStorage.getItem(KEY);
    if (saved) document.documentElement.setAttribute("data-theme", saved);
  } catch (e) { /* private window, blocked storage: the default theme is fine */ }

  var themeBtn = document.getElementById("theme");
  if (themeBtn) {
    themeBtn.hidden = false;
    themeBtn.addEventListener("click", function () {
      var root = document.documentElement;
      var now = root.getAttribute("data-theme");
      var dark = now === "dark" ||
        (now !== "light" && window.matchMedia("(prefers-color-scheme: dark)").matches);
      var next = dark ? "light" : "dark";
      root.setAttribute("data-theme", next);
      try { localStorage.setItem(KEY, next); } catch (e) { /* nothing to do */ }
    });
  }

  // ---- drag and drop: an extra on a normal file input ---------------------
  // Without the script the input is an ordinary "choose a file" control and the
  // hint stays hidden, so the page never promises something it cannot do.
  Array.prototype.forEach.call(document.querySelectorAll(".dropzone"), function (zone) {
    var input = zone.querySelector("input[type=file]");
    var hint = zone.querySelector(".dropzone__hint");
    var text = zone.querySelector(".dropzone__text");
    if (!input) return;
    if (hint) hint.hidden = false;
    function show() {
      if (input.files && input.files.length && text) text.textContent = input.files[0].name;
    }
    input.addEventListener("change", show);
    ["dragenter", "dragover"].forEach(function (name) {
      zone.addEventListener(name, function (ev) {
        ev.preventDefault(); zone.classList.add("dropzone--over");
      });
    });
    ["dragleave", "drop"].forEach(function (name) {
      zone.addEventListener(name, function () { zone.classList.remove("dropzone--over"); });
    });
    zone.addEventListener("drop", function (ev) {
      ev.preventDefault();
      if (ev.dataTransfer && ev.dataTransfer.files.length) {
        input.files = ev.dataTransfer.files;
        show();
      }
    });
  });

  // ---- upload: say it is happening, and stop a second click resending it ---
  Array.prototype.forEach.call(document.querySelectorAll("form.upload"), function (form) {
    form.addEventListener("submit", function () {
      var btn = form.querySelector("button[type=submit]");
      if (!btn) return;
      btn.disabled = true;
      btn.textContent = "Uploading…";
    });
  });
})();
