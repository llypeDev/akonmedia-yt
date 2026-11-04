/* An integration's page in the studio: show the secret, switch the code
 * examples, and keep the embed code and the preview link in step with the
 * live and options picked (without the script, the options form reloads). */
(function () {
  "use strict";

  document.querySelectorAll("[data-reveal]").forEach(function (button) {
    button.addEventListener("click", function () {
      var target = document.querySelector(button.getAttribute("data-reveal"));
      if (!target) return;
      var hidden = target.type === "password";
      target.type = hidden ? "text" : "password";
      button.setAttribute("aria-pressed", hidden ? "true" : "false");
      var label = button.querySelector("[data-reveal-label]");
      if (label) label.textContent = button.getAttribute(hidden ? "data-text-hide" : "data-text-show");
    });
  });

  document.querySelectorAll("[data-code-tabs]").forEach(function (list) {
    var tabs = Array.prototype.slice.call(list.querySelectorAll("[data-code-tab]"));
    var scope = list.parentElement;
    function select(name, focus) {
      tabs.forEach(function (tab) {
        var on = tab.getAttribute("data-code-tab") === name;
        tab.classList.toggle("active", on);
        tab.setAttribute("aria-selected", on ? "true" : "false");
        tab.tabIndex = on ? 0 : -1;
        if (on && focus) tab.focus();
      });
      scope.querySelectorAll("[data-code-panel]").forEach(function (panel) {
        panel.hidden = panel.getAttribute("data-code-panel") !== name;
      });
    }
    tabs.forEach(function (tab, index) {
      tab.addEventListener("click", function () { select(tab.getAttribute("data-code-tab"), false); });
      tab.addEventListener("keydown", function (event) {
        var step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
        if (!step) return;
        event.preventDefault();
        var next = tabs[(index + step + tabs.length) % tabs.length];
        select(next.getAttribute("data-code-tab"), true);
      });
    });
  });

  document.querySelectorAll("[data-color-pick]").forEach(function (picker) {
    var text = picker.parentElement.querySelector("[data-color-text]");
    if (!text) return;
    picker.addEventListener("input", function () { text.value = picker.value; });
    text.addEventListener("input", function () {
      if (/^#[0-9a-fA-F]{6}$/.test(text.value)) picker.value = text.value;
    });
  });

  var guide = document.querySelector("[data-integration-guide]");
  var form = guide && guide.querySelector("[data-snippet-form]");
  if (!form) return;
  var sdk = guide.getAttribute("data-sdk");
  var code = guide.querySelector("[data-snippet] code");
  var preview = guide.querySelector("[data-preview-link]");
  var live = form.querySelector("[data-snippet-live]");
  var layout = form.querySelector("[data-snippet-layout]");
  var theme = form.querySelector("[data-snippet-theme]");

  function update() {
    var attrs = 'data-qqplay-live="' + live.value + '" data-layout="' + layout.value + '"';
    if (theme.value) attrs += ' data-theme="' + theme.value + '"';
    if (code) {
      code.textContent =
        "<div " + attrs + "\n     data-token=\"<?= qqplay_token($student) ?>\"></div>\n" +
        '<script src="' + sdk + '" async></script>';
    }
    if (preview) {
      var url = new URL(preview.href, window.location.href);
      url.searchParams.set("live", live.value);
      url.searchParams.set("layout", layout.value);
      if (theme.value) url.searchParams.set("theme", theme.value);
      else url.searchParams.delete("theme");
      preview.href = url.toString();
    }
  }
  form.addEventListener("change", update);
  form.addEventListener("submit", function (event) { event.preventDefault(); update(); });
})();
