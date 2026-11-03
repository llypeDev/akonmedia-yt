/* QQplay interface behaviour.
 *
 * Small, dependency-free enhancements. Every page works without this file;
 * it adds keyboard shortcuts, menus, copy buttons, live processing status and
 * the row editors used by the subtitle and chapter screens.
 */
(function () {
  "use strict";

  // Translations come from Django's JavaScript catalog (/jsi18n/), loaded
  // before this file; the fallbacks keep everything working without it.
  var gettext = window.gettext || function (text) { return text; };
  var ngettext = window.ngettext || function (singular, plural, count) { return count === 1 ? singular : plural; };
  var interpolate = window.interpolate || function (fmt, obj, named) {
    if (named) return fmt.replace(/%\(\w+\)s/g, function (m) { return String(obj[m.slice(2, -2)]); });
    var i = 0;
    return fmt.replace(/%s/g, function () { return String(obj[i++]); });
  };

  var root = document.documentElement;
  var body = document.body;
  var WIDE = 1280;

  function store(key, value) {
    try {
      if (value === undefined) return localStorage.getItem(key);
      if (value === null) localStorage.removeItem(key);
      else localStorage.setItem(key, value);
    } catch (e) { /* private mode or blocked storage */ }
    return null;
  }

  function csrf() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    if (meta && meta.content) return meta.content;
    var match = document.cookie.match(/csrftoken=([^;]+)/);
    return match ? match[1] : "";
  }

  function player() {
    var el = document.querySelector(".player");
    if (window.AkonPlayer && el) {
      try { return window.AkonPlayer.get(el); } catch (e) { /* fall through */ }
    }
    return null;
  }

  function mediaElement() {
    var instance = player();
    if (instance && instance.video) return instance.video;
    return document.querySelector(".player video, video");
  }

  function clock(seconds) {
    seconds = Math.max(0, Math.floor(seconds || 0));
    var h = Math.floor(seconds / 3600);
    var m = Math.floor((seconds % 3600) / 60);
    var s = seconds % 60;
    var mm = h ? String(m).padStart(2, "0") : String(m);
    return (h ? h + ":" : "") + mm + ":" + String(s).padStart(2, "0");
  }

  /* ---------------------------------------------------------------- shell */

  function toggleSidebar() {
    if (window.innerWidth >= WIDE) {
      var collapsed = root.getAttribute("data-sidebar") === "collapsed";
      if (collapsed) root.removeAttribute("data-sidebar");
      else root.setAttribute("data-sidebar", "collapsed");
      store("akon-sidebar", collapsed ? null : "collapsed");
    } else {
      body.classList.toggle("drawer-open");
    }
  }

  function toggleTheme() {
    var next = root.getAttribute("data-theme") === "light" ? "dark" : "light";
    root.setAttribute("data-theme", next);
    store("akon-theme", next);
  }

  function openSearch() {
    body.classList.add("search-open");
    var input = document.querySelector("[data-search-input]");
    if (input) input.focus();
  }

  document.addEventListener("click", function (event) {
    var trigger = event.target.closest("[data-action]");
    if (!trigger) return;
    var action = trigger.getAttribute("data-action");
    if (action === "toggle-sidebar") { event.preventDefault(); toggleSidebar(); }
    else if (action === "toggle-theme") { event.preventDefault(); toggleTheme(); }
    else if (action === "open-search") { event.preventDefault(); openSearch(); }
    else if (action === "close-search") { event.preventDefault(); body.classList.remove("search-open"); }
    else if (action === "dismiss-toast") { event.preventDefault(); dismiss(trigger.closest("[data-toast]")); }
  });

  window.addEventListener("resize", function () {
    if (window.innerWidth >= WIDE) body.classList.remove("drawer-open");
  });

  function typing(target) {
    if (!target) return false;
    var tag = target.tagName;
    return target.isContentEditable || tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
  }

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      body.classList.remove("drawer-open", "search-open");
      closeDropdowns();
      return;
    }
    if (event.key === "/" && !event.ctrlKey && !event.metaKey && !event.altKey && !typing(event.target)) {
      var input = document.querySelector("[data-search-input]");
      if (input) {
        event.preventDefault();
        if (window.innerWidth < 900) openSearch();
        input.focus();
        input.select();
      }
    }
  });

  /* ------------------------------------------------------------ dropdowns */

  function closeDropdowns(except) {
    document.querySelectorAll("details[data-dropdown][open]").forEach(function (item) {
      if (item !== except) item.removeAttribute("open");
    });
  }

  document.addEventListener("toggle", function (event) {
    var target = event.target;
    if (target.matches && target.matches("details[data-dropdown]") && target.open) {
      closeDropdowns(target);
      var field = target.querySelector("input[type=text], input:not([type])");
      if (field && target.hasAttribute("data-autofocus")) field.focus();
    }
  }, true);

  document.addEventListener("click", function (event) {
    if (!event.target.closest("details[data-dropdown]")) closeDropdowns();
  });

  /* --------------------------------------------------------------- toasts */

  function dismiss(toast) {
    if (!toast || toast.classList.contains("leaving")) return;
    toast.classList.add("leaving");
    setTimeout(function () {
      var list = toast.parentNode;
      toast.remove();
      if (list && !list.children.length) list.remove();
    }, 240);
  }

  function toast(text, kind) {
    var list = document.querySelector(".toasts");
    if (!list) {
      list = document.createElement("ul");
      list.className = "toasts";
      list.setAttribute("role", "status");
      list.setAttribute("aria-live", "polite");
      body.appendChild(list);
    }
    var item = document.createElement("li");
    item.className = "toast " + (kind || "info");
    item.setAttribute("data-toast", "");
    var label = document.createElement("span");
    label.className = "toast-text";
    label.textContent = text;
    item.appendChild(label);
    list.appendChild(item);
    setTimeout(function () { dismiss(item); }, 3200);
  }
  window.AkonToast = toast;

  document.querySelectorAll("[data-toast]").forEach(function (item, index) {
    var lasting = item.classList.contains("error") ? 9000 : 5200;
    setTimeout(function () { dismiss(item); }, lasting + index * 400);
  });

  /* ----------------------------------------------------------------- copy */

  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(text);
    }
    return new Promise(function (resolve, reject) {
      var area = document.createElement("textarea");
      area.value = text;
      area.setAttribute("readonly", "");
      area.style.position = "fixed";
      area.style.opacity = "0";
      body.appendChild(area);
      area.select();
      try { document.execCommand("copy") ? resolve() : reject(new Error("copy failed")); }
      catch (e) { reject(e); }
      area.remove();
    });
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-copy], [data-copy-target], [data-copy-time]");
    if (!button) return;
    event.preventDefault();
    var text = button.getAttribute("data-copy");
    var targetSelector = button.getAttribute("data-copy-target");
    if (targetSelector) {
      var target = document.querySelector(targetSelector);
      text = target ? (target.value !== undefined ? target.value : target.textContent) : "";
    }
    if (button.hasAttribute("data-copy-time")) {
      var media = mediaElement();
      var at = media ? Math.floor(media.currentTime || 0) : 0;
      text = button.getAttribute("data-copy-time") + (at ? "?t=" + at : "");
    }
    copyText(text || "").then(function () {
      var label = button.getAttribute("data-copied") || gettext("Copied to clipboard");
      toast(label, "success");
      button.classList.add("is-on");
      setTimeout(function () { button.classList.remove("is-on"); }, 1200);
    }, function () {
      toast(gettext("Could not copy — select the text and copy it manually."), "error");
    });
  });

  /* ------------------------------------------------------------- confirms */

  // A form with data-confirm asks first, in a dialog drawn like the rest of
  // the portal rather than the browser's "this page says" window. The
  // question in the attribute is the title and anything after it the
  // explanation; the confirm button repeats the form's own button, in red
  // when that one is a danger button (or data-confirm-tone="danger").
  var SPRITE = body.getAttribute("data-sprite") || "";

  function iconSvg(name) {
    if (!SPRITE) return null;
    var ns = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(ns, "svg");
    svg.setAttribute("class", "icon");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    var use = document.createElementNS(ns, "use");
    use.setAttribute("href", SPRITE + "#i-" + name);
    svg.appendChild(use);
    return svg;
  }

  function splitQuestion(message) {
    var match = message.match(/^([\s\S]*?[?？])\s+([\s\S]+)$/);
    return match ? { title: match[1], text: match[2] } : { title: message, text: "" };
  }

  function confirmDialog(options) {
    if (typeof HTMLDialogElement !== "function") {
      return Promise.resolve(window.confirm(options.message));
    }
    return new Promise(function (resolve) {
      var parts = splitQuestion(options.message);
      var id = "confirm-" + Date.now();
      var opener = document.activeElement;

      var dialog = document.createElement("dialog");
      dialog.className = "confirm-dialog" + (options.danger ? " is-danger" : "");
      dialog.setAttribute("aria-labelledby", id + "-title");

      var box = document.createElement("form");
      box.method = "dialog";
      box.className = "confirm-box";

      var badge = document.createElement("span");
      badge.className = "confirm-icon";
      var glyph = iconSvg(options.danger ? "alert" : "info");
      if (glyph) badge.appendChild(glyph);
      box.appendChild(badge);

      var copy = document.createElement("div");
      copy.className = "confirm-copy";
      var title = document.createElement("h2");
      title.className = "confirm-title";
      title.id = id + "-title";
      title.textContent = parts.title;
      copy.appendChild(title);
      if (parts.text) {
        var text = document.createElement("p");
        text.className = "confirm-text";
        text.id = id + "-text";
        text.textContent = parts.text;
        copy.appendChild(text);
        dialog.setAttribute("aria-describedby", text.id);
      }
      box.appendChild(copy);

      var actions = document.createElement("div");
      actions.className = "confirm-actions";
      var cancel = document.createElement("button");
      cancel.className = "btn btn-ghost";
      cancel.value = "cancel";
      cancel.textContent = gettext("Cancel");
      var ok = document.createElement("button");
      ok.className = "btn " + (options.danger ? "btn-danger-solid" : "btn-primary");
      ok.value = "ok";
      ok.textContent = options.label || gettext("Confirm");
      // A destructive action starts on Cancel, so a stray Enter is harmless.
      (options.danger ? cancel : ok).autofocus = true;
      actions.appendChild(cancel);
      actions.appendChild(ok);
      box.appendChild(actions);
      dialog.appendChild(box);

      // A click on the dimmed backdrop lands on the dialog itself.
      dialog.addEventListener("click", function (event) {
        if (event.target === dialog) dialog.close("cancel");
      });
      dialog.addEventListener("close", function () {
        var confirmed = dialog.returnValue === "ok";
        dialog.remove();
        if (opener && typeof opener.focus === "function" && document.contains(opener)) opener.focus();
        resolve(confirmed);
      });

      body.appendChild(dialog);
      dialog.showModal();
    });
  }

  function confirmLabel(button) {
    if (!button) return "";
    var label = (button.textContent || "").replace(/\s+/g, " ").trim();
    return (label || button.getAttribute("aria-label") || button.title || "").slice(0, 40);
  }

  document.addEventListener("submit", function (event) {
    var form = event.target;
    var message = form.getAttribute && form.getAttribute("data-confirm");
    if (!message) return;
    if (form.__confirmed) {
      form.__confirmed = false;
      return;
    }
    // Hold the submission, and keep the form's own handlers from seeing it,
    // until the person answers.
    event.preventDefault();
    event.stopPropagation();
    var submitter = event.submitter || form.querySelector('[type="submit"], button:not([type])');
    var danger = form.getAttribute("data-confirm-tone") === "danger" || !!(submitter &&
      (submitter.classList.contains("btn-danger") || submitter.classList.contains("danger")));
    confirmDialog({ message: message, danger: danger, label: confirmLabel(submitter) }).then(function (yes) {
      if (!yes) return;
      form.__confirmed = true;
      if (typeof form.requestSubmit === "function") {
        form.requestSubmit(submitter && submitter.form === form ? submitter : undefined);
      } else {
        form.__confirmed = false;
        form.submit();
      }
    });
  }, true);

  /* ---------------------------------------------------- description clamp */

  document.querySelectorAll("[data-clamp]").forEach(function (box) {
    var prose = box.querySelector(".prose");
    var toggle = box.querySelector(".description-toggle");
    if (!prose || !toggle) return;
    var extra = box.querySelector(".description-extra");
    if (prose.scrollHeight <= prose.clientHeight + 4 && !extra) {
      box.classList.remove("clamp");
      toggle.hidden = true;
      return;
    }
    toggle.hidden = false;
    toggle.addEventListener("click", function () {
      var open = box.classList.toggle("clamp");
      toggle.textContent = open ? gettext("Show more") : gettext("Show less");
    });
  });

  /* ------------------------------------------------- start time from ?t= */

  (function () {
    var match = window.location.search.match(/[?&]t=(\d+)/) || window.location.hash.match(/#t=(\d+)/);
    if (!match) return;
    var seconds = parseInt(match[1], 10);
    var tries = 0;
    (function apply() {
      var instance = player();
      if (instance && instance.seek) { instance.seek(seconds); return; }
      var media = mediaElement();
      if (media && media.readyState >= 1) { media.currentTime = seconds; return; }
      if (media && tries === 0) {
        media.addEventListener("loadedmetadata", function () { media.currentTime = seconds; }, { once: true });
      }
      if (++tries < 20) setTimeout(apply, 150);
    })();
  })();

  /* ------------------------------------------- comment pinned to a moment */

  document.querySelectorAll("[data-capture-time]").forEach(function (form) {
    var box = form.querySelector("[data-time-toggle]");
    var hidden = form.querySelector("input[name=timestamp]");
    var label = form.querySelector("[data-time-label]");
    if (!box || !hidden) return;

    function refresh() {
      var media = mediaElement();
      var now = media ? media.currentTime || 0 : 0;
      if (label) label.textContent = clock(now);
      hidden.value = box.checked ? now.toFixed(2) : "";
    }
    box.addEventListener("change", refresh);
    form.addEventListener("submit", refresh);
    var media = mediaElement();
    if (media) media.addEventListener("timeupdate", function () { if (!box.checked && label) label.textContent = clock(media.currentTime); });
  });

  /* -------------------------------------------- live processing status */

  var STATE_LABELS = {
    pending: gettext("pending"),
    processing: gettext("processing"),
    ready: gettext("ready"),
    failed: gettext("failed"),
  };

  document.querySelectorAll("[data-poll-url]").forEach(function (node) {
    var url = node.getAttribute("data-poll-url");
    var current = node.getAttribute("data-poll-state");
    var label = node.querySelector("[data-poll-label]");
    var delay = 2500;
    var started = Date.now();

    function tick() {
      fetch(url, { headers: { Accept: "application/json" }, credentials: "same-origin" })
        .then(function (response) { return response.ok ? response.json() : null; })
        .then(function (data) {
          if (!data) return schedule();
          if (label) label.textContent = STATE_LABELS[data.state] || data.state;
          if (data.state !== current && (data.state === "ready" || data.state === "failed")) {
            toast(data.state === "ready" ? gettext("Processing finished — loading the player.") : gettext("Processing failed."), data.state === "ready" ? "success" : "error");
            setTimeout(function () { window.location.reload(); }, 900);
            return;
          }
          current = data.state;
          schedule();
        })
        .catch(schedule);
    }
    function schedule() {
      var elapsed = (Date.now() - started) / 1000;
      delay = elapsed > 120 ? 8000 : elapsed > 30 ? 4000 : 2500;
      setTimeout(tick, delay);
    }
    schedule();
  });

  /* ------------------------------------------------------- embed builder */

  document.querySelectorAll("[data-embed-builder]").forEach(function (builder) {
    var base = builder.getAttribute("data-embed-builder");
    var output = builder.querySelector("textarea");
    var preview = builder.querySelector("[data-embed-preview]");
    var inputs = builder.querySelectorAll("input");

    function build() {
      var params = [];
      inputs.forEach(function (input) {
        var name = input.getAttribute("data-param");
        if (!name || !input.checked) return;
        if (name === "start") {
          var media = mediaElement();
          var at = media ? Math.floor(media.currentTime || 0) : 0;
          if (at) params.push("start=" + at);
          var label = builder.querySelector("[data-start-label]");
          if (label) label.textContent = clock(at);
        } else {
          params.push(name + "=" + (input.getAttribute("data-value") || "1"));
        }
      });
      var src = base + (params.length ? "?" + params.join("&") : "");
      if (output) {
        output.value = '<iframe src="' + src + '" width="640" height="360" frameborder="0" ' +
          'allow="autoplay; fullscreen; picture-in-picture" allowfullscreen title="' +
          (builder.getAttribute("data-title") || "").replace(/"/g, "&quot;") + '"></iframe>';
      }
      if (preview) preview.setAttribute("data-src", src);
    }
    inputs.forEach(function (input) { input.addEventListener("change", build); });
    build();
  });

  /* -------------------------------------------------------- row editors */

  document.querySelectorAll("[data-rows]").forEach(function (editor) {
    var list = editor.querySelector("[data-rows-body]");
    var template = editor.querySelector("template");
    if (!list || !template) return;

    editor.addEventListener("click", function (event) {
      var add = event.target.closest("[data-row-add]");
      var remove = event.target.closest("[data-row-remove]");
      var seek = event.target.closest("[data-row-seek]");
      if (add) {
        event.preventDefault();
        var fragment = template.content.cloneNode(true);
        var media = mediaElement();
        var now = media ? media.currentTime || 0 : 0;
        fragment.querySelectorAll("[data-fill-now]").forEach(function (field) {
          field.value = field.getAttribute("data-fill-now") === "ms" ? stamp(now) : clock(now);
        });
        fragment.querySelectorAll("[data-fill-later]").forEach(function (field) {
          field.value = stamp(now + 2);
        });
        list.appendChild(fragment);
        renumber();
        var fields = list.lastElementChild && list.lastElementChild.querySelectorAll("input, textarea");
        if (fields && fields.length) fields[fields.length - 1].focus();
      } else if (remove) {
        event.preventDefault();
        var row = remove.closest("[data-row]");
        if (row) row.remove();
        renumber();
      } else if (seek) {
        var start = seek.closest("[data-row]").querySelector("input");
        var seconds = toSeconds(start ? start.value : "");
        var instance = player();
        if (seconds !== null && instance && instance.seek) instance.seek(seconds);
        else if (seconds !== null && mediaElement()) mediaElement().currentTime = seconds;
      }
    });

    function renumber() {
      list.querySelectorAll("[data-row-number]").forEach(function (cell, index) {
        cell.textContent = index + 1;
      });
    }
  });

  function stamp(seconds) {
    var ms = Math.max(0, Math.round((seconds || 0) * 1000));
    var h = Math.floor(ms / 3600000);
    var m = Math.floor((ms % 3600000) / 60000);
    var s = Math.floor((ms % 60000) / 1000);
    var r = ms % 1000;
    return String(h).padStart(2, "0") + ":" + String(m).padStart(2, "0") + ":" +
      String(s).padStart(2, "0") + "." + String(r).padStart(3, "0");
  }

  function toSeconds(text) {
    text = (text || "").trim().replace(",", ".");
    if (!text) return null;
    var parts = text.split(":").map(Number);
    if (parts.some(isNaN)) return null;
    return parts.reduce(function (total, part) { return total * 60 + part; }, 0);
  }

  /* Highlight the cue being spoken while the editor preview plays. */
  (function () {
    var table = document.querySelector("[data-cue-table]");
    var media = mediaElement();
    if (!table || !media) return;
    var last = null;
    media.addEventListener("timeupdate", function () {
      var now = media.currentTime;
      var active = null;
      table.querySelectorAll("[data-row]").forEach(function (row) {
        var fields = row.querySelectorAll("input");
        var start = toSeconds(fields[0] && fields[0].value);
        var end = toSeconds(fields[1] && fields[1].value);
        if (start !== null && end !== null && now >= start && now <= end) active = row;
      });
      if (active !== last) {
        if (last) last.classList.remove("cue-active");
        if (active) active.classList.add("cue-active");
        last = active;
      }
    });
  })();

  window.AkonApp = { toast: toast, csrf: csrf, clock: clock, confirm: confirmDialog };
})();
