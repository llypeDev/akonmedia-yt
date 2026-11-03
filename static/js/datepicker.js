/* QQplay date and time picker.
 *
 * Replaces the browser's own control for <input type="datetime-local"> (and
 * type="date") with a calendar drawn like the rest of the portal. The native
 * input stays in the form, hidden, and keeps the value in its usual format
 * ("2026-11-14T19:00"), so the form and any script reading it (live.js turns
 * it into an ISO timestamp) work exactly as before. Without this file the
 * native control is used, styled by app.css.
 *
 * Optional attribute on the input: data-picker-min="today" fades and blocks
 * the days before today (a date already chosen stays selectable).
 */
(function () {
  "use strict";

  var gettext = window.gettext || function (text) { return text; };
  var LANG = document.documentElement.lang || navigator.language || "pt-BR";
  var SPRITE = document.body.getAttribute("data-sprite") || "";
  var MINUTES = [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55];
  var counter = 0;

  function fmt(options) {
    try { return new Intl.DateTimeFormat(LANG, options); } catch (e) { return new Intl.DateTimeFormat(undefined, options); }
  }
  var SHORT_MONTH = fmt({ month: "short" });
  var FULL_DAY = fmt({ weekday: "long", day: "numeric", month: "long", year: "numeric" });
  var MONTH = fmt({ month: "long", year: "numeric" });
  var WEEKDAY = fmt({ weekday: "short" });

  function capital(text) { return text ? text.charAt(0).toLocaleUpperCase(LANG) + text.slice(1) : text; }
  function pad(n) { return (n < 10 ? "0" : "") + n; }

  function icon(name) {
    return SPRITE ? '<svg class="icon icon-sm" aria-hidden="true" focusable="false"><use href="' + SPRITE + "#i-" + name + '"></use></svg>' : "";
  }

  function sameDay(a, b) {
    return !!a && !!b && a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  }

  function startOfDay(date) { return new Date(date.getFullYear(), date.getMonth(), date.getDate()); }

  function addDays(date, days) { return new Date(date.getFullYear(), date.getMonth(), date.getDate() + days); }

  function addMonths(date, months) {
    var target = new Date(date.getFullYear(), date.getMonth() + months, 1);
    var last = new Date(target.getFullYear(), target.getMonth() + 1, 0).getDate();
    return new Date(target.getFullYear(), target.getMonth(), Math.min(date.getDate(), last));
  }

  /* "2026-11-14T19:00" (or "2026-11-14") in local time, as a Date. */
  function parseValue(value) {
    var m = /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}))?/.exec(value || "");
    if (!m) return null;
    return new Date(+m[1], +m[2] - 1, +m[3], m[4] ? +m[4] : 0, m[5] ? +m[5] : 0);
  }

  function weekdayNames() {
    var names = [];
    var sunday = new Date(2026, 10, 1); // 1 November 2026 is a Sunday
    for (var i = 0; i < 7; i++) {
      names.push(capital(WEEKDAY.format(addDays(sunday, i)).replace(/\.$/, "")));
    }
    return names;
  }

  function Picker(input) {
    this.input = input;
    this.withTime = input.type === "datetime-local";
    this.minToday = input.getAttribute("data-picker-min") === "today";
    this.id = "dtp-" + (++counter);
    this.open = false;
    this.build();
    this.sync();
  }

  Picker.prototype.value = function () { return parseValue(this.input.value); };

  Picker.prototype.build = function () {
    var self = this;
    var input = this.input;
    input.hidden = true;

    var wrap = document.createElement("div");
    wrap.className = "dtp";
    wrap.innerHTML =
      '<button type="button" class="dtp-trigger" id="' + this.id + '-trigger" aria-haspopup="dialog" aria-expanded="false"' +
      ' aria-controls="' + this.id + '-panel" aria-describedby="' + this.id + '-value">' +
      '<span class="dtp-trigger-icon">' + icon("calendar") + "</span>" +
      '<span class="dtp-value" id="' + this.id + '-value"></span>' +
      '<span class="dtp-chevron">' + icon("chevron-down") + "</span></button>" +
      '<button type="button" class="dtp-clear" hidden aria-label="' + gettext("Clear date") + '" title="' + gettext("Clear date") + '">' + icon("x") + "</button>";
    input.insertAdjacentElement("afterend", wrap);
    this.wrap = wrap;
    this.trigger = wrap.querySelector(".dtp-trigger");
    this.label = wrap.querySelector(".dtp-value");
    this.clearButton = wrap.querySelector(".dtp-clear");

    // The field's label now names (and opens) the picker.
    var label = input.closest("label");
    if (label) label.htmlFor = this.trigger.id;
    else if (input.id) {
      var outer = document.querySelector('label[for="' + input.id + '"]');
      if (outer) outer.htmlFor = this.trigger.id;
    }

    var panel = document.createElement("div");
    panel.className = "dtp-panel";
    panel.id = this.id + "-panel";
    panel.hidden = true;
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", this.withTime ? gettext("Choose date and time") : gettext("Choose a date"));
    var weekdays = weekdayNames().map(function (name) { return '<span aria-hidden="true">' + name + "</span>"; }).join("");
    var hours = "";
    for (var h = 0; h < 24; h++) hours += '<option value="' + h + '">' + pad(h) + "</option>";
    panel.innerHTML =
      '<div class="dtp-head">' +
      '<button type="button" class="dtp-nav" data-step="-1" aria-label="' + gettext("Previous month") + '">' + icon("chevron-left") + "</button>" +
      '<div class="dtp-month" aria-live="polite"></div>' +
      '<button type="button" class="dtp-nav" data-step="1" aria-label="' + gettext("Next month") + '">' + icon("chevron-right") + "</button>" +
      "</div>" +
      '<div class="dtp-weekdays">' + weekdays + "</div>" +
      '<div class="dtp-grid" role="grid"></div>' +
      (this.withTime
        ? '<div class="dtp-time"><span class="dtp-time-label">' + icon("clock") + gettext("Time") + "</span>" +
          '<div class="dtp-time-inputs"><select class="dtp-hour" aria-label="' + gettext("Hour") + '">' + hours + "</select>" +
          '<span class="dtp-colon" aria-hidden="true">:</span>' +
          '<select class="dtp-minute" aria-label="' + gettext("Minutes") + '"></select></div></div>'
        : "") +
      '<div class="dtp-foot">' +
      '<button type="button" class="btn btn-ghost btn-sm" data-action="today">' + gettext("Today") + "</button>" +
      '<span class="dtp-spacer"></span>' +
      '<button type="button" class="btn btn-ghost btn-sm" data-action="clear">' + gettext("Clear") + "</button>" +
      '<button type="button" class="btn btn-primary btn-sm" data-action="done">' + gettext("Done") + "</button>" +
      "</div>";
    document.body.appendChild(panel);
    this.panel = panel;
    this.grid = panel.querySelector(".dtp-grid");
    this.month = panel.querySelector(".dtp-month");
    this.hour = panel.querySelector(".dtp-hour");
    this.minute = panel.querySelector(".dtp-minute");

    var scrim = document.createElement("div");
    scrim.className = "dtp-scrim";
    scrim.hidden = true;
    document.body.appendChild(scrim);
    this.scrim = scrim;
    scrim.addEventListener("click", function () { self.close(true); });

    this.trigger.addEventListener("click", function () { self.open ? self.close(true) : self.show(); });
    this.trigger.addEventListener("keydown", function (event) {
      if (event.key === "ArrowDown" && !self.open) { event.preventDefault(); self.show(); }
    });
    this.clearButton.addEventListener("click", function () { self.set(null); self.trigger.focus(); });

    panel.addEventListener("click", function (event) {
      var nav = event.target.closest(".dtp-nav");
      if (nav) { self.moveMonth(+nav.getAttribute("data-step")); return; }
      var day = event.target.closest(".dtp-day");
      if (day && !day.disabled) { self.pick(new Date(+day.getAttribute("data-time"))); return; }
      var action = event.target.closest("[data-action]");
      if (!action) return;
      var what = action.getAttribute("data-action");
      if (what === "today") self.pick(startOfDay(new Date()));
      else if (what === "clear") { self.set(null); self.render(true); }
      else if (what === "done") self.close(true);
    });
    this.grid.addEventListener("keydown", function (event) { self.onGridKey(event); });
    panel.addEventListener("keydown", function (event) {
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); self.close(true); }
    });
    if (this.hour) {
      this.hour.addEventListener("change", function () { self.changeTime(); });
      this.minute.addEventListener("change", function () { self.changeTime(); });
    }

    // Leaving the picker with Tab or a click elsewhere closes it.
    panel.addEventListener("focusout", function (event) {
      var next = event.relatedTarget;
      if (next && !panel.contains(next) && !wrap.contains(next)) self.close(false);
    });
    document.addEventListener("pointerdown", function (event) {
      if (!self.open) return;
      if (panel.contains(event.target) || wrap.contains(event.target) || scrim.contains(event.target)) return;
      self.close(false);
    }, true);
    var reposition = function () { if (self.open) self.place(); };
    window.addEventListener("resize", reposition);
    window.addEventListener("scroll", reposition, true);

    // The form may set the value itself (live.js does, from the stored time).
    input.addEventListener("change", function () { if (!self.writing) self.sync(); });
  };

  /* The trigger's text and the clear button, from the input's value. */
  Picker.prototype.sync = function () {
    var date = this.value();
    this.wrap.classList.toggle("has-value", !!date);
    this.clearButton.hidden = !date;
    if (!date) {
      this.label.textContent = this.withTime ? gettext("Choose date and time") : gettext("Choose a date");
      this.trigger.removeAttribute("title");
      return;
    }
    // Compact enough for a narrow column ("8 nov. 2026 · 19:30"); the full
    // date is in the tooltip and in the calendar itself.
    var time = this.withTime ? " · " + pad(date.getHours()) + ":" + pad(date.getMinutes()) : "";
    this.label.textContent = date.getDate() + " " + SHORT_MONTH.format(date) + " " + date.getFullYear() + time;
    this.trigger.title = capital(FULL_DAY.format(date)) + time;
  };

  Picker.prototype.set = function (date) {
    var value = "";
    if (date) {
      value = date.getFullYear() + "-" + pad(date.getMonth() + 1) + "-" + pad(date.getDate());
      if (this.withTime) value += "T" + pad(date.getHours()) + ":" + pad(date.getMinutes());
    }
    if (value === this.input.value) { this.sync(); return; }
    this.writing = true;
    this.input.value = value;
    this.input.dispatchEvent(new Event("input", { bubbles: true }));
    this.input.dispatchEvent(new Event("change", { bubbles: true }));
    this.writing = false;
    this.sync();
  };

  /* A day was chosen: keep the time already set, or the next full hour. */
  Picker.prototype.pick = function (day) {
    var current = this.value();
    var hours, minutes;
    if (current) { hours = current.getHours(); minutes = current.getMinutes(); }
    else if (this.hour && this.pendingTime) { hours = this.pendingTime[0]; minutes = this.pendingTime[1]; }
    else { hours = Math.min(new Date().getHours() + 1, 23); minutes = 0; }
    this.set(new Date(day.getFullYear(), day.getMonth(), day.getDate(), hours, minutes));
    this.view = startOfDay(day);
    this.render(true);
    if (!this.withTime) this.close(true);
  };

  Picker.prototype.changeTime = function () {
    var hours = +this.hour.value;
    var minutes = +this.minute.value;
    var current = this.value();
    if (!current) { this.pendingTime = [hours, minutes]; return; }
    this.set(new Date(current.getFullYear(), current.getMonth(), current.getDate(), hours, minutes));
  };

  Picker.prototype.moveMonth = function (step) {
    this.view = addMonths(this.view, step);
    this.render(false);
  };

  Picker.prototype.render = function (focusDay) {
    var selected = this.value();
    var today = startOfDay(new Date());
    var view = this.view;
    this.month.textContent = capital(MONTH.format(view));

    var first = new Date(view.getFullYear(), view.getMonth(), 1);
    var start = addDays(first, -first.getDay());
    var html = "";
    for (var week = 0; week < 6; week++) {
      html += '<div class="dtp-week" role="row">';
      for (var d = 0; d < 7; d++) {
        var date = addDays(start, week * 7 + d);
        var classes = ["dtp-day"];
        var outside = date.getMonth() !== view.getMonth();
        var isSelected = sameDay(date, selected);
        var past = this.minToday && date < today && !isSelected;
        if (outside) classes.push("is-outside");
        if (sameDay(date, today)) classes.push("is-today");
        if (isSelected) classes.push("is-selected");
        html += '<button type="button" role="gridcell" class="' + classes.join(" ") + '"' +
          ' data-time="' + date.getTime() + '" tabindex="' + (sameDay(date, view) ? "0" : "-1") + '"' +
          ' aria-label="' + capital(FULL_DAY.format(date)) + '"' +
          (isSelected ? ' aria-selected="true"' : "") +
          (sameDay(date, today) ? ' aria-current="date"' : "") +
          (past ? " disabled" : "") + ">" + date.getDate() + "</button>";
      }
      html += "</div>";
    }
    this.grid.innerHTML = html;

    if (this.hour) {
      var base = selected || (this.pendingTime ? new Date(2000, 0, 1, this.pendingTime[0], this.pendingTime[1]) : null);
      var hours = base ? base.getHours() : Math.min(new Date().getHours() + 1, 23);
      var minutes = base ? base.getMinutes() : 0;
      var options = MINUTES.slice();
      if (options.indexOf(minutes) === -1) { options.push(minutes); options.sort(function (a, b) { return a - b; }); }
      this.minute.innerHTML = options.map(function (m) { return '<option value="' + m + '">' + pad(m) + "</option>"; }).join("");
      this.hour.value = String(hours);
      this.minute.value = String(minutes);
    }
    if (focusDay) {
      var target = this.grid.querySelector('.dtp-day[tabindex="0"]');
      if (target) target.focus();
    }
  };

  Picker.prototype.onGridKey = function (event) {
    var day = event.target.closest(".dtp-day");
    if (!day) return;
    var current = new Date(+day.getAttribute("data-time"));
    var next = null;
    switch (event.key) {
      case "ArrowLeft": next = addDays(current, -1); break;
      case "ArrowRight": next = addDays(current, 1); break;
      case "ArrowUp": next = addDays(current, -7); break;
      case "ArrowDown": next = addDays(current, 7); break;
      case "Home": next = addDays(current, -current.getDay()); break;
      case "End": next = addDays(current, 6 - current.getDay()); break;
      case "PageUp": next = addMonths(current, event.shiftKey ? -12 : -1); break;
      case "PageDown": next = addMonths(current, event.shiftKey ? 12 : 1); break;
      default: return;
    }
    event.preventDefault();
    this.view = next;
    this.render(true);
  };

  Picker.prototype.place = function (scrollFirst) {
    var panel = this.panel;
    var sheet = window.matchMedia("(max-width: 560px)").matches;
    panel.classList.toggle("is-sheet", sheet);
    this.scrim.hidden = !sheet;
    if (sheet) {
      panel.style.top = panel.style.left = "";
      return;
    }
    var rect = this.trigger.getBoundingClientRect();
    var width = panel.offsetWidth;
    var height = panel.offsetHeight;
    var gap = 8;
    var edge = 12;
    if (scrollFirst) {
      // Not enough room below: scroll the page up a little first, as long as
      // the field stays in view, so the calendar opens under the field.
      var missing = rect.bottom + gap + height + edge - window.innerHeight;
      if (missing > 0 && rect.top - missing > 72) {
        window.scrollBy(0, missing);
        rect = this.trigger.getBoundingClientRect();
      }
    }
    var left = Math.min(Math.max(edge, rect.left), window.innerWidth - width - edge);
    var below = window.innerHeight - rect.bottom - gap - edge;
    var above = rect.top - gap - edge;
    var top;
    if (below >= height) top = rect.bottom + gap;
    else if (above >= height) top = rect.top - height - gap;
    else {
      // Neither side has room: take the roomier one and stay on screen,
      // even if the calendar then covers part of the field.
      top = below >= above ? rect.bottom + gap : rect.top - height - gap;
      top = Math.min(Math.max(edge, top), window.innerHeight - height - edge);
    }
    panel.style.left = Math.round(left) + "px";
    panel.style.top = Math.round(Math.max(edge, top)) + "px";
  };

  Picker.prototype.show = function () {
    var selected = this.value();
    this.view = startOfDay(selected || new Date());
    this.panel.hidden = false;
    this.open = true;
    this.trigger.setAttribute("aria-expanded", "true");
    this.wrap.classList.add("is-open");
    this.render(false);
    this.place(true);
    var target = this.grid.querySelector('.dtp-day[tabindex="0"]');
    if (target) target.focus();
  };

  Picker.prototype.close = function (refocus) {
    if (!this.open) return;
    this.open = false;
    this.panel.hidden = true;
    this.scrim.hidden = true;
    this.trigger.setAttribute("aria-expanded", "false");
    this.wrap.classList.remove("is-open");
    if (refocus) this.trigger.focus();
  };

  function boot() {
    document.querySelectorAll('input[type="datetime-local"], input[type="date"]').forEach(function (input) {
      if (input.__picker || input.disabled || input.readOnly) return;
      input.__picker = new Picker(input);
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
  // live.js fills the field from the stored time on the same event; read it
  // again once every DOMContentLoaded handler has run.
  window.addEventListener("load", function () {
    document.querySelectorAll('input[type="datetime-local"], input[type="date"]').forEach(function (input) {
      if (input.__picker) input.__picker.sync();
    });
  });
  window.AkonDatePicker = { init: boot };
})();
