/* AkonMedia player.
 *
 * Progressive enhancement over a native <video controls> element. Without
 * JavaScript the browser's own controls stay in place. With it, the element
 * gets an adaptive streaming engine and a custom control surface:
 *
 *   - Apple devices play HLS natively; everything else uses the vendored
 *     hls.js; the progressive file is the last resort, and also what a stream
 *     falls back to when HLS fails mid-flight.
 *   - Controls: seek bar with buffer, chapters and storyboard previews,
 *     volume, captions rendered by the player, speed and quality menus,
 *     picture-in-picture, theater mode, fullscreen and a stats overlay.
 *   - Keyboard shortcuts follow the conventions people already know.
 *
 * Markup contract (emitted by the templates):
 *
 *   <div class="player" data-player data-src="…mp4" data-hls="…m3u8" …>
 *     <video class="player-media" controls playsinline preload="metadata" …>
 *       <track kind="subtitles" …>
 *     </video>
 *   </div>
 *
 * Public API: window.AkonPlayer = { players, get(el), init(scope) }.
 */
(function () {
  "use strict";

  if (window.AkonPlayer && window.AkonPlayer.__loaded) return;

  var STORE = "akon.player.";
  var SPEEDS = [0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2];
  var IDLE_MS = 2500;
  var UPNEXT_SECONDS = 5;
  var RESUME_LIMIT = 50;
  var players = [];
  var uid = 0;

  var reducedMotion = !!(window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches);

  function noop() {}

  // ------------------------------------------------------------------
  // icons: 24×24, stroke style, currentColor
  // ------------------------------------------------------------------

  var SVG_OPEN = '<svg class="pl-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" ' +
    'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" ' +
    'stroke-linejoin="round" aria-hidden="true" focusable="false">';
  var SPEAKER = '<path d="M4 9.5h3.2L11.5 6v12l-4.3-3.5H4z" fill="currentColor" stroke-width="1.6"/>';

  var ICONS = {
    play: '<path d="M8 5.6v12.8a1 1 0 0 0 1.53.85l10.04-6.4a1 1 0 0 0 0-1.7L9.53 4.75A1 1 0 0 0 8 5.6z" fill="currentColor" stroke="none"/>',
    pause: '<rect x="6.5" y="5" width="3.8" height="14" rx="1.1" fill="currentColor" stroke="none"/>' +
      '<rect x="13.7" y="5" width="3.8" height="14" rx="1.1" fill="currentColor" stroke="none"/>',
    replay: '<path d="M4 12a8 8 0 1 0 2.4-5.7"/><path d="M4 4.5V9h4.5"/>',
    prev: '<path d="M6 5.5v13"/><path d="M18 6.4v11.2a.9.9 0 0 1-1.4.74l-8-5.6a.9.9 0 0 1 0-1.48l8-5.6A.9.9 0 0 1 18 6.4z" fill="currentColor" stroke="none"/>',
    next: '<path d="M18 5.5v13"/><path d="M6 6.4v11.2a.9.9 0 0 0 1.4.74l8-5.6a.9.9 0 0 0 0-1.48l-8-5.6A.9.9 0 0 0 6 6.4z" fill="currentColor" stroke="none"/>',
    volHigh: SPEAKER + '<path d="M15 9.2a4 4 0 0 1 0 5.6"/><path d="M17.8 6.6a7.6 7.6 0 0 1 0 10.8"/>',
    volLow: SPEAKER + '<path d="M15 9.2a4 4 0 0 1 0 5.6"/>',
    volMute: SPEAKER + '<path d="M15.5 9.5l5 5"/><path d="M20.5 9.5l-5 5"/>',
    cc: '<rect x="2.5" y="5" width="19" height="14" rx="3"/>' +
      '<path d="M10.4 10.1a2.4 2.4 0 1 0 0 3.8"/><path d="M17 10.1a2.4 2.4 0 1 0 0 3.8"/>',
    gear: '<path d="M10.16 4.63L10.40 1.93L13.60 1.93L13.84 4.63A7.6 7.6 0 0 1 15.91 5.49L18.00 3.75L20.25 6.00' +
      'L18.51 8.09A7.6 7.6 0 0 1 19.37 10.16L22.07 10.40L22.07 13.60L19.37 13.84A7.6 7.6 0 0 1 18.51 15.91' +
      'L20.25 18.00L18.00 20.25L15.91 18.51A7.6 7.6 0 0 1 13.84 19.37L13.60 22.07L10.40 22.07L10.16 19.37' +
      'A7.6 7.6 0 0 1 8.09 18.51L6.00 20.25L3.75 18.00L5.49 15.91A7.6 7.6 0 0 1 4.63 13.84L1.93 13.60' +
      'L1.93 10.40L4.63 10.16A7.6 7.6 0 0 1 5.49 8.09L3.75 6.00L6.00 3.75L8.09 5.49A7.6 7.6 0 0 1 10.16 4.63Z" ' +
      'stroke-width="1.7"/><circle cx="12" cy="12" r="3"/>',
    pip: '<rect x="2.5" y="4.5" width="19" height="15" rx="2.5"/>' +
      '<rect x="12" y="11.5" width="7" height="5.5" rx="1.2" fill="currentColor" stroke="none"/>',
    theater: '<rect x="2" y="6.5" width="20" height="11" rx="2.2"/>',
    theaterOff: '<rect x="4.5" y="5" width="15" height="14" rx="2.2"/><path d="M8 9h8"/>',
    fullscreen: '<path d="M4 9V6a2 2 0 0 1 2-2h3"/><path d="M15 4h3a2 2 0 0 1 2 2v3"/>' +
      '<path d="M20 15v3a2 2 0 0 1-2 2h-3"/><path d="M9 20H6a2 2 0 0 1-2-2v-3"/>',
    fullscreenOff: '<path d="M9 4v3a2 2 0 0 1-2 2H4"/><path d="M20 9h-3a2 2 0 0 1-2-2V4"/>' +
      '<path d="M15 20v-3a2 2 0 0 1 2-2h3"/><path d="M4 15h3a2 2 0 0 1 2 2v3"/>',
    check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    chevRight: '<path d="M9.5 6l6 6-6 6"/>',
    chevLeft: '<path d="M14.5 6l-6 6 6 6"/>',
    close: '<path d="M6.5 6.5l11 11"/><path d="M17.5 6.5l-11 11"/>',
    speed: '<path d="M4.6 17.5a8.5 8.5 0 1 1 14.8 0"/><path d="M12 14l3.8-4.6"/><circle cx="12" cy="14" r="1.2" fill="currentColor"/>',
    quality: '<path d="M12 3.5l8.5 4.6L12 12.7 3.5 8.1z"/><path d="M3.5 12.2L12 16.8l8.5-4.6"/><path d="M3.5 16.1L12 20.7l8.5-4.6"/>',
    stats: '<path d="M3 12h3.5l2.5-6.5 4.5 13 2.6-6.5H21"/>',
    back: '<path d="M11 6.5L5.5 12l5.5 5.5"/><path d="M18.5 6.5L13 12l5.5 5.5"/>',
    forward: '<path d="M13 6.5l5.5 5.5-5.5 5.5"/><path d="M5.5 6.5L11 12l-5.5 5.5"/>',
    external: '<path d="M14 4h6v6"/><path d="M20 4l-8.5 8.5"/><path d="M18 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4"/>',
    alert: '<path d="M12 3.8l9.2 15.9H2.8z"/><path d="M12 10v4.2"/><circle cx="12" cy="17.2" r=".6" fill="currentColor"/>',
  };

  function icon(name) {
    return SVG_OPEN + (ICONS[name] || "") + "</svg>";
  }

  // ------------------------------------------------------------------
  // small helpers
  // ------------------------------------------------------------------

  function h(tag, className, attrs, children) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (attrs) {
      Object.keys(attrs).forEach(function (key) {
        var value = attrs[key];
        if (value === false || value === null || value === undefined) return;
        if (key === "text") node.textContent = value;
        else if (key === "html") node.innerHTML = value;
        else node.setAttribute(key, value === true ? "" : String(value));
      });
    }
    (children || []).forEach(function (child) {
      if (child) node.appendChild(child);
    });
    return node;
  }

  function on(target, type, handler, options) {
    target.addEventListener(type, handler, options || false);
  }

  function once(target, type, handler) {
    target.addEventListener(type, handler, { once: true });
  }

  // Metadata can arrive before the script runs (cached or very fast media);
  // waiting for an event that already fired would wait forever.
  function whenMetadata(video, handler) {
    if (video.readyState >= 1) handler();
    else once(video, "loadedmetadata", handler);
  }

  function clamp(value, low, high) {
    return Math.min(high, Math.max(low, value));
  }

  function clock(seconds) {
    if (!isFinite(seconds) || seconds < 0) seconds = 0;
    seconds = Math.floor(seconds);
    var hours = Math.floor(seconds / 3600);
    var minutes = Math.floor((seconds % 3600) / 60);
    var rest = seconds % 60;
    var ss = rest < 10 ? "0" + rest : String(rest);
    if (hours) return hours + ":" + (minutes < 10 ? "0" : "") + minutes + ":" + ss;
    return minutes + ":" + ss;
  }

  function rateLabel(rate) {
    return rate === 1 ? "Normal" : rate + "×";
  }

  function bitrate(bps) {
    if (!bps || !isFinite(bps)) return "–";
    if (bps >= 1e6) return (bps / 1e6).toFixed(2) + " Mbps";
    return Math.round(bps / 1e3) + " kbps";
  }

  function flag(value) {
    if (value === undefined || value === null) return false;
    if (value === "") return true; // a bare attribute means "on"
    return !/^(0|false|no|off)$/i.test(String(value).trim());
  }

  function number(value, fallback) {
    var parsed = parseFloat(value);
    return isFinite(parsed) ? parsed : fallback;
  }

  function sameOrigin(url) {
    try {
      return new URL(url, window.location.href).origin === window.location.origin;
    } catch (e) {
      return false;
    }
  }

  function load(key, fallback) {
    try {
      var raw = window.localStorage.getItem(STORE + key);
      return raw === null ? fallback : JSON.parse(raw);
    } catch (e) {
      return fallback;
    }
  }

  function save(key, value) {
    try {
      if (value === null || value === undefined) window.localStorage.removeItem(STORE + key);
      else window.localStorage.setItem(STORE + key, JSON.stringify(value));
    } catch (e) {
      /* storage can be full, disabled or blocked: preferences are optional */
    }
  }

  function dispatch(target, name, detail) {
    var event;
    try {
      event = new CustomEvent(name, { bubbles: true, detail: detail });
    } catch (e) {
      event = document.createEvent("CustomEvent");
      event.initCustomEvent(name, true, false, detail);
    }
    target.dispatchEvent(event);
  }

  function fullscreenElement() {
    return document.fullscreenElement || document.webkitFullscreenElement || null;
  }

  function matches(node, selector) {
    try {
      return !!(node && node.matches && node.matches(selector));
    } catch (e) {
      return false;
    }
  }

  function focusQuietly(node) {
    if (!node) return;
    try {
      node.focus({ preventScroll: true });
    } catch (e) {
      node.focus();
    }
  }

  function parseJSON(raw) {
    if (!raw) return null;
    try {
      return JSON.parse(raw);
    } catch (e) {
      return null;
    }
  }

  function readChapters(raw) {
    var list = parseJSON(raw);
    if (!Array.isArray(list)) return [];
    return list
      .map(function (item) {
        return {
          start: number(item && item.start, NaN),
          title: String((item && item.title) || ""),
        };
      })
      .filter(function (item) {
        return isFinite(item.start) && item.start >= 0;
      })
      .sort(function (a, b) {
        return a.start - b.start;
      });
  }

  function readStoryboard(raw) {
    var data = parseJSON(raw);
    if (!data || typeof data !== "object" || !data.src) return null;
    var board = {
      src: String(data.src),
      interval: number(data.interval, 0),
      cols: Math.floor(number(data.cols, 0)),
      rows: Math.floor(number(data.rows, 0)),
      width: number(data.width, 0),
      height: number(data.height, 0),
      count: Math.floor(number(data.count, 0)),
    };
    if (!(board.interval > 0 && board.cols > 0 && board.rows > 0 &&
          board.width > 0 && board.height > 0)) {
      return null;
    }
    if (!(board.count > 0)) board.count = board.cols * board.rows;
    return board;
  }

  function readOptions(root, video) {
    var d = root.dataset;
    return {
      src: d.src || video.getAttribute("src") || video.currentSrc || "",
      hls: d.hls || "",
      kind: d.kind === "audio" ? "audio" : "video",
      title: d.title || "",
      poster: d.poster || video.getAttribute("poster") || "",
      chapters: readChapters(d.chapters),
      storyboard: readStoryboard(d.storyboard),
      start: Math.max(0, number(d.start, 0)),
      autoplay: flag(d.autoplay),
      controls: d.controls === undefined ? true : flag(d.controls),
      nextUrl: d.nextUrl || "",
      nextTitle: d.nextTitle || "",
      prevUrl: d.prevUrl || "",
      embed: flag(d.embed),
      brandUrl: d.brandUrl || "",
      brandLabel: d.brandLabel || "",
      visualizer: flag(d.visualizer),
      resumeKey: d.resumeKey || "",
    };
  }

  // ------------------------------------------------------------------
  // the player
  // ------------------------------------------------------------------

  function Player(root) {
    this.root = root;
    this.video = root.querySelector("video");
    this.id = ++uid;
    this.hls = null;
    this.engine = "progressive";
    this.started = false;
    this.menuOpen = false;
    this.statsOn = false;
    this.scrubbing = false;
    this.scrubTime = 0;
    this.overControls = false;
    this.trackList = [];
    this.activeTrack = null;
    this.lastTrack = null;
    this.playingLevel = -1;
    this.lastText = "";
    this.raf = 0;
    this.timers = {};
    root.__akonPlayer = this;
    this.opts = readOptions(root, this.video);
    this.mount();
  }

  Player.prototype.mount = function () {
    var root = this.root;
    var video = this.video;
    var o = this.opts;

    root.classList.add("pl-paused");
    if (o.kind === "audio") root.classList.add("pl-audio");
    if (o.embed) root.classList.add("pl-embed");
    if (!o.controls) root.classList.add("pl-bare");
    if (o.nextUrl) root.classList.add("pl-has-next");
    if (!root.hasAttribute("tabindex")) root.setAttribute("tabindex", "-1");
    if (!root.hasAttribute("role")) root.setAttribute("role", "region");
    root.setAttribute(
      "aria-label",
      (o.kind === "audio" ? "Audio player" : "Video player") + (o.title ? ": " + o.title : "")
    );

    video.classList.add("player-media");
    video.setAttribute("playsinline", "");
    video.playsInline = true;
    if (o.poster && !video.getAttribute("poster")) video.setAttribute("poster", o.poster);

    this.buildLayers();
    if (o.controls) this.buildControls();

    this.restorePreferences();
    this.bindMedia();
    this.bindSurface();
    this.setupTracks();
    this.observeSize();
    this.attachSource();
    this.initResume();

    // Only now that a working surface exists do the native controls go away.
    video.removeAttribute("controls");
    video.controls = false;
    root.classList.add("pl-ready");

    this.renderTime();
    this.renderVolume();
    this.updatePlayState();

    // The source may already have failed while the scripts were loading.
    if (this.engine !== "hls.js" && video.error) this.reportMediaError();

    if (o.autoplay) this.autoplay();
    dispatch(root, "player:ready", { player: this });
  };

  // ---------------------------------------------------------------- DOM

  Player.prototype.button = function (className, label, iconName, tip) {
    var node = h("button", "pl-btn " + className, {
      type: "button",
      "aria-label": label,
      "data-tip": tip || label,
      html: icon(iconName),
    });
    return node;
  };

  Player.prototype.setButton = function (node, label, iconName, tip) {
    if (!node) return;
    node.setAttribute("aria-label", label);
    node.setAttribute("data-tip", tip || label);
    if (node.__icon !== iconName) {
      node.__icon = iconName;
      var badge = node.querySelector(".pl-badge");
      node.innerHTML = icon(iconName);
      if (badge) node.appendChild(badge);
    }
  };

  Player.prototype.buildLayers = function () {
    var o = this.opts;
    var root = this.root;
    var self = this;

    if (o.kind === "audio" && o.visualizer && !reducedMotion && sameOrigin(o.src || "")) {
      this.canvas = h("canvas", "pl-viz", { "aria-hidden": "true" });
      root.appendChild(this.canvas);
    }

    this.shade = h("div", "pl-shade", { "aria-hidden": "true" });
    root.appendChild(this.shade);

    if (o.embed && o.title) {
      this.titleBar = h("div", "pl-titlebar", null, [
        h("span", "pl-titlebar-text", { text: o.title }),
      ]);
      root.appendChild(this.titleBar);
    }

    this.captionBox = h("div", "pl-captions", { "aria-hidden": "true" });
    root.appendChild(this.captionBox);

    this.spinner = h("div", "pl-spinner", { "aria-hidden": "true" });
    root.appendChild(this.spinner);

    this.bigPlay = h("button", "pl-bigplay", {
      type: "button",
      "aria-label": "Play",
      html: icon("play"),
    });
    on(this.bigPlay, "click", function (event) {
      event.stopPropagation();
      self.userToggle();
      self.focusRoot(event);
    });
    root.appendChild(this.bigPlay);

    this.osd = h("div", "pl-osd", { "aria-hidden": "true" });
    root.appendChild(this.osd);

    if (o.embed && o.brandUrl) {
      this.brand = h("a", "pl-brand", {
        href: o.brandUrl,
        target: "_blank",
        rel: "noopener",
        "aria-label": "Open on " + (o.brandLabel || "the site") + " (new tab)",
      }, [
        h("span", "pl-brand-label", { text: o.brandLabel || "Watch" }),
        h("span", "pl-brand-icon", { html: icon("external") }),
      ]);
      root.appendChild(this.brand);
    }

    this.live = h("div", "pl-sr", { "aria-live": "polite", "aria-atomic": "true" });
    root.appendChild(this.live);
  };

  Player.prototype.buildControls = function () {
    var o = this.opts;
    var self = this;
    var root = this.root;

    var controls = (this.controls = h("div", "pl-controls"));

    // seek bar
    this.progress = h("div", "pl-progress", {
      role: "slider",
      tabindex: "0",
      "aria-label": "Seek",
      "aria-valuemin": "0",
      "aria-valuemax": "0",
      "aria-valuenow": "0",
      "aria-valuetext": "0:00",
    });
    this.rail = h("div", "pl-rail");
    this.bufferedBox = h("div", "pl-buffered");
    this.hoverFill = h("div", "pl-hoverfill");
    this.played = h("div", "pl-played");
    this.ticks = h("div", "pl-ticks");
    this.rail.appendChild(this.bufferedBox);
    this.rail.appendChild(this.hoverFill);
    this.rail.appendChild(this.played);
    this.rail.appendChild(this.ticks);
    this.thumb = h("div", "pl-thumb");
    this.preview = o.storyboard ? h("div", "pl-preview", { "aria-hidden": "true" }) : null;
    this.tipChapter = h("span", "pl-tip-chapter");
    this.tipTime = h("span", "pl-tip-time");
    this.tip = h("div", "pl-tip", { "aria-hidden": "true" }, [
      this.preview,
      this.tipChapter,
      this.tipTime,
    ]);
    this.progress.appendChild(this.rail);
    this.progress.appendChild(this.thumb);
    this.progress.appendChild(this.tip);

    // button row
    var left = h("div", "pl-group pl-left");
    var right = h("div", "pl-group pl-right");

    if (o.prevUrl) {
      this.prevBtn = this.button("pl-prev", "Previous", "prev", "Previous");
      on(this.prevBtn, "click", function () {
        window.location.href = o.prevUrl;
      });
      left.appendChild(this.prevBtn);
    }

    this.playBtn = this.button("pl-play", "Play", "play", "Play (k)");
    on(this.playBtn, "click", function (event) {
      self.userToggle();
      self.focusRoot(event);
    });
    left.appendChild(this.playBtn);

    if (o.nextUrl) {
      this.nextBtn = this.button("pl-next", "Next", "next", "Next");
      on(this.nextBtn, "click", function () {
        self.goNext();
      });
      left.appendChild(this.nextBtn);
    }

    this.muteBtn = this.button("pl-mute", "Mute", "volHigh", "Mute (m)");
    on(this.muteBtn, "click", function (event) {
      self.toggleMute();
      self.focusRoot(event);
    });
    this.volume = h("input", "pl-volume", {
      type: "range",
      min: "0",
      max: "1",
      step: "0.01",
      value: "1",
      "aria-label": "Volume",
    });
    on(this.volume, "input", function () {
      var value = parseFloat(self.volume.value) || 0;
      self.video.volume = value;
      self.video.muted = value === 0;
    });
    left.appendChild(h("div", "pl-vol", null, [this.muteBtn, this.volume]));

    this.curEl = h("span", "pl-cur", { text: "0:00" });
    this.durEl = h("span", "pl-dur", { text: "--:--" });
    this.timeEl = h("div", "pl-time", null, [
      this.curEl,
      h("span", "pl-sep", { text: " / " }),
      this.durEl,
    ]);
    left.appendChild(this.timeEl);

    this.chapterEl = h("span", "pl-chapter", { hidden: true });
    left.appendChild(this.chapterEl);

    this.ccBtn = this.button("pl-cc", "Subtitles", "cc", "Subtitles (c)");
    this.ccBtn.setAttribute("aria-pressed", "false");
    this.ccBtn.hidden = true;
    on(this.ccBtn, "click", function (event) {
      self.toggleCaptions();
      self.focusRoot(event);
    });
    right.appendChild(this.ccBtn);

    this.gearBtn = this.button("pl-gear", "Settings", "gear", "Settings");
    this.gearBtn.setAttribute("aria-haspopup", "menu");
    this.gearBtn.setAttribute("aria-expanded", "false");
    this.badge = h("span", "pl-badge", { hidden: true });
    this.gearBtn.appendChild(this.badge);
    on(this.gearBtn, "click", function (event) {
      self.menuByKeyboard = event.detail === 0;
      self.toggleMenu(self.menuByKeyboard);
    });
    right.appendChild(this.gearBtn);

    if (this.canPip()) {
      this.pipBtn = this.button("pl-pip", "Picture-in-picture", "pip", "Picture-in-picture");
      on(this.pipBtn, "click", function (event) {
        self.togglePip();
        self.focusRoot(event);
      });
      right.appendChild(this.pipBtn);
    }

    if (!o.embed) {
      this.theaterBtn = this.button("pl-theater", "Theater mode", "theater", "Theater mode (t)");
      this.theaterBtn.setAttribute("aria-pressed", "false");
      on(this.theaterBtn, "click", function (event) {
        self.setTheater(!document.body.classList.contains("is-theater"), true);
        self.focusRoot(event);
      });
      right.appendChild(this.theaterBtn);
    }

    if (this.canFullscreen()) {
      this.fsBtn = this.button("pl-fs-btn", "Full screen", "fullscreen", "Full screen (f)");
      on(this.fsBtn, "click", function (event) {
        self.toggleFullscreen();
        self.focusRoot(event);
      });
      right.appendChild(this.fsBtn);
    }

    var bar = h("div", "pl-bar", null, [left, right]);
    controls.appendChild(this.progress);
    controls.appendChild(bar);
    root.appendChild(controls);

    this.menu = h("div", "pl-menu", { role: "menu", "aria-label": "Settings", hidden: true });
    root.appendChild(this.menu);

    this.bindProgress();
    this.bindMenu();
    this.renderTicks();

    on(controls, "pointerenter", function () {
      self.overControls = true;
    });
    on(controls, "pointerleave", function () {
      self.overControls = false;
    });
  };

  // ------------------------------------------------------- media engine

  Player.prototype.attachSource = function () {
    var o = this.opts;
    var video = this.video;
    var self = this;

    if (o.src && !video.getAttribute("src") && !video.querySelector("source")) {
      video.setAttribute("src", o.src);
    }

    if (o.hls) {
      var canNative = typeof video.canPlayType === "function" &&
        video.canPlayType("application/vnd.apple.mpegurl") !== "";
      var apple = /Apple/i.test(navigator.vendor || "");
      var Hls = window.Hls;
      var hlsOk = !!(Hls && typeof Hls.isSupported === "function" && Hls.isSupported());

      if (canNative && (apple || !hlsOk)) {
        this.engine = "native";
        video.src = o.hls;
      } else if (hlsOk) {
        this.engine = "hls.js";
        this.startHls();
      }
    }

    if (this.engine === "progressive" && o.start > 0) {
      whenMetadata(video, function () {
        var d = self.duration();
        if (!d || o.start < d) video.currentTime = o.start;
      });
    } else if (this.engine === "native" && o.start > 0) {
      once(video, "loadedmetadata", function () {
        var d = self.duration();
        if (!d || o.start < d) video.currentTime = o.start;
      });
    }
  };

  Player.prototype.startHls = function () {
    var Hls = window.Hls;
    var self = this;
    var video = this.video;
    var hls = (this.hls = new Hls({
      enableWorker: true,
      lowLatencyMode: false,
      capLevelToPlayerSize: true,
      backBufferLength: 60,
      startPosition: this.opts.start > 0 ? this.opts.start : -1,
    }));
    var recovered = false;
    var retried = false;

    hls.on(Hls.Events.MANIFEST_PARSED, function () {
      self.renderBadge();
      if (self.menuOpen) self.renderMenu(self.menu.getAttribute("data-page") || "main");
    });
    hls.on(Hls.Events.LEVEL_SWITCHED, function (_event, data) {
      self.playingLevel = data ? data.level : -1;
      self.renderBadge();
      if (self.menuOpen && self.menu.getAttribute("data-page") !== "quality") {
        self.renderMenu(self.menu.getAttribute("data-page") || "main");
      }
    });
    hls.on(Hls.Events.BUFFER_CODECS, function (_event, data) {
      var parts = [];
      if (data && data.video && data.video.codec) parts.push(data.video.codec);
      if (data && data.audio && data.audio.codec) parts.push(data.audio.codec);
      if (parts.length) self.bufferCodecs = parts.join(" / ");
    });
    hls.on(Hls.Events.ERROR, function (_event, data) {
      if (!data || !data.fatal) return;
      if (data.type === Hls.ErrorTypes.MEDIA_ERROR && !recovered) {
        recovered = true;
        hls.recoverMediaError();
        return;
      }
      if (data.type === Hls.ErrorTypes.NETWORK_ERROR && !retried && data.details !== "manifestLoadError") {
        retried = true;
        hls.startLoad();
        return;
      }
      self.fallbackToProgressive();
    });

    hls.loadSource(this.opts.hls);
    hls.attachMedia(video);
  };

  Player.prototype.fallbackToProgressive = function () {
    var video = this.video;
    var position = video.currentTime || this.opts.start || 0;
    var wasPlaying = !video.paused;

    if (this.hls) {
      try {
        this.hls.destroy();
      } catch (e) {
        /* already torn down */
      }
      this.hls = null;
    }
    this.engine = "progressive";
    this.playingLevel = -1;
    this.renderBadge();
    if (this.menuOpen) this.renderMenu("main");

    if (!this.opts.src) {
      this.showError("This stream could not be loaded.");
      return;
    }
    once(video, "loadedmetadata", function () {
      if (position) video.currentTime = position;
      if (wasPlaying) {
        var promise = video.play();
        if (promise && promise.catch) promise.catch(noop);
      }
    });
    video.src = this.opts.src;
    video.load();
  };

  Player.prototype.levels = function () {
    return this.hls && this.hls.levels ? this.hls.levels : [];
  };

  Player.prototype.setLevel = function (index) {
    if (!this.hls) return;
    this.hls.currentLevel = index;
    var level = this.levels()[index];
    this.flash(index < 0 ? "Auto" : level ? level.height + "p" : "Auto", "quality");
    this.renderBadge();
  };

  Player.prototype.qualityLabel = function () {
    if (!this.hls) return "";
    var levels = this.levels();
    if (this.hls.autoLevelEnabled) {
      var playing = levels[this.playingLevel];
      return "Auto" + (playing && playing.height ? " (" + playing.height + "p)" : "");
    }
    var chosen = levels[this.hls.currentLevel];
    return chosen && chosen.height ? chosen.height + "p" : "Auto";
  };

  Player.prototype.renderBadge = function () {
    if (!this.badge) return;
    var height = 0;
    if (this.hls) {
      var level = this.levels()[this.playingLevel];
      height = level ? level.height : 0;
    } else if (this.opts.kind === "video") {
      height = this.video.videoHeight || 0;
    }
    var text = height >= 2160 ? "4K" : height >= 720 ? "HD" : "";
    this.badge.textContent = text;
    this.badge.hidden = !text;
  };

  // ------------------------------------------------------------ events

  Player.prototype.bindMedia = function () {
    var self = this;
    var video = this.video;
    var root = this.root;

    on(video, "play", function () {
      self.started = true;
      root.classList.add("pl-started");
      root.classList.remove("pl-ended", "pl-error-state");
      self.hideUpNext();
      self.hideResume();
      self.updatePlayState();
      self.resumeAudio();
      self.poke();
      self.loop();
    });
    on(video, "pause", function () {
      self.updatePlayState();
      self.setWaiting(false);
      self.showControls();
      self.saveResume();
    });
    on(video, "playing", function () {
      self.setWaiting(false);
      self.updatePlayState();
    });
    on(video, "waiting", function () {
      self.setWaiting(true);
    });
    on(video, "seeking", function () {
      self.setWaiting(true);
    });
    ["seeked", "canplay", "canplaythrough", "loadeddata"].forEach(function (type) {
      on(video, type, function () {
        self.setWaiting(false);
      });
    });
    on(video, "timeupdate", function () {
      self.renderTime();
      self.maybeSaveResume();
    });
    on(video, "progress", function () {
      self.renderBuffered();
    });
    ["loadedmetadata", "durationchange"].forEach(function (type) {
      on(video, type, function () {
        self.renderTime();
        self.renderTicks();
        self.renderBuffered();
        self.renderBadge();
        self.applyRate();
      });
    });
    on(video, "resize", function () {
      self.renderBadge();
    });
    on(video, "volumechange", function () {
      self.renderVolume();
      save("volume", Math.round(video.volume * 100) / 100);
      save("muted", video.muted && !self.autoMuted);
    });
    on(video, "ratechange", function () {
      save("rate", video.playbackRate);
    });
    on(video, "ended", function () {
      root.classList.add("pl-ended");
      self.updatePlayState();
      self.showControls();
      self.clearResume();
      self.showUpNext();
      dispatch(root, "player:ended", { player: self });
    });
    on(video, "error", function () {
      if (self.engine === "hls.js") return; // hls.js reports and falls back by itself
      self.reportMediaError();
    });
    on(video, "enterpictureinpicture", function () {
      self.setButton(self.pipBtn, "Exit picture-in-picture", "pip");
      if (self.pipBtn) self.pipBtn.setAttribute("aria-pressed", "true");
    });
    on(video, "leavepictureinpicture", function () {
      self.setButton(self.pipBtn, "Picture-in-picture", "pip");
      if (self.pipBtn) self.pipBtn.setAttribute("aria-pressed", "false");
    });
    on(window, "pagehide", function () {
      self.saveResume();
    });
  };

  Player.prototype.bindSurface = function () {
    var self = this;
    var root = this.root;

    on(root, "pointermove", function (event) {
      if (event.pointerType === "mouse") self.poke();
    });
    on(root, "pointerleave", function (event) {
      if (event.pointerType !== "mouse") return;
      self.overControls = false;
      clearTimeout(self.timers.idle);
      self.timers.idle = setTimeout(function () {
        self.maybeIdle();
      }, 700);
    });
    on(root, "pointerdown", function (event) {
      self.lastPointer = event.pointerType || "mouse";
    });
    on(root, "click", function (event) {
      self.onSurfaceClick(event);
    });
    on(root, "dblclick", function (event) {
      if (self.lastPointer === "touch" || !self.isSurface(event.target)) return;
      self.toggleFullscreen();
    });
    on(root, "focusin", function () {
      self.poke();
    });
  };

  Player.prototype.isSurface = function (target) {
    if (!target || !target.closest) return true;
    return !target.closest(
      ".pl-controls, .pl-menu, .pl-stats, .pl-upnext, .pl-toast, .pl-unmute, .pl-brand, .pl-error, button, a, input"
    );
  };

  Player.prototype.onSurfaceClick = function (event) {
    if (!this.isSurface(event.target)) return;
    if (this.menuOpen) {
      this.closeMenu();
      return;
    }
    this.focusRoot(event);
    if (this.lastPointer === "touch") {
      this.onTap(event);
      return;
    }
    this.userToggle();
  };

  Player.prototype.onTap = function (event) {
    var self = this;
    var rect = this.root.getBoundingClientRect();
    var x = rect.width ? (event.clientX - rect.left) / rect.width : 0.5;
    var now = Date.now();

    if (this.timers.tap && now - this.lastTapAt < 300) {
      clearTimeout(this.timers.tap);
      this.timers.tap = 0;
      if (x < 0.35) this.skip(-10);
      else if (x > 0.65) this.skip(10);
      else this.toggleFullscreen();
      return;
    }

    this.lastTapAt = now;
    var hidden = this.root.classList.contains("pl-idle");
    this.timers.tap = setTimeout(function () {
      self.timers.tap = 0;
      if (hidden && self.started && !self.video.paused) self.poke();
      else self.userToggle();
    }, 260);
  };

  Player.prototype.focusRoot = function (event) {
    // After a pointer click, keyboard shortcuts should reach the player rather
    // than re-activating whichever button kept focus. Keyboard activations
    // (detail 0) keep focus where the visitor put it.
    if (event && event.detail === 0) return;
    focusQuietly(this.root);
  };

  // ------------------------------------------------- controls visibility

  Player.prototype.poke = function () {
    var self = this;
    this.root.classList.remove("pl-idle");
    clearTimeout(this.timers.idle);
    this.timers.idle = setTimeout(function () {
      self.maybeIdle();
    }, IDLE_MS);
  };

  Player.prototype.maybeIdle = function () {
    var video = this.video;
    if (video.paused || video.ended || this.menuOpen || this.scrubbing) return;
    if (this.overControls) {
      this.poke();
      return;
    }
    var active = document.activeElement;
    if (active && this.controls && this.controls.contains(active) && matches(active, ":focus-visible")) {
      this.poke();
      return;
    }
    this.root.classList.add("pl-idle");
  };

  Player.prototype.showControls = function () {
    clearTimeout(this.timers.idle);
    this.root.classList.remove("pl-idle");
  };

  Player.prototype.setWaiting = function (waiting) {
    var root = this.root;
    clearTimeout(this.timers.wait);
    if (waiting) {
      this.timers.wait = setTimeout(function () {
        root.classList.add("pl-waiting");
      }, 180);
    } else {
      root.classList.remove("pl-waiting");
    }
  };

  // ------------------------------------------------------------ playback

  Player.prototype.play = function () {
    this.ensureAudioGraph();
    var promise = this.video.play();
    if (promise && promise.catch) promise.catch(noop);
    return promise;
  };

  Player.prototype.pause = function () {
    this.video.pause();
  };

  Player.prototype.toggle = function () {
    if (this.video.paused || this.video.ended) this.play();
    else this.pause();
  };

  Player.prototype.userToggle = function () {
    if (this.autoMuted && !this.video.paused) {
      // The first deliberate interaction after a muted autoplay unmutes.
      this.unmute();
      return;
    }
    this.toggle();
  };

  Player.prototype.autoplay = function () {
    var self = this;
    var video = this.video;
    var promise = video.play();
    if (!promise || !promise.catch) return;
    promise.catch(function () {
      video.muted = true;
      self.autoMuted = true;
      var retry = video.play();
      if (retry && retry.then) {
        retry.then(function () {
          self.showUnmute();
        }).catch(function () {
          video.muted = false;
          self.autoMuted = false;
        });
      }
    });
  };

  Player.prototype.showUnmute = function () {
    var self = this;
    if (this.unmuteBtn) return;
    this.unmuteBtn = h("button", "pl-unmute", { type: "button", "aria-label": "Unmute" }, [
      h("span", "pl-unmute-icon", { html: icon("volMute") }),
      h("span", null, { text: "Tap to unmute" }),
    ]);
    on(this.unmuteBtn, "click", function (event) {
      event.stopPropagation();
      self.unmute();
    });
    this.root.appendChild(this.unmuteBtn);
  };

  Player.prototype.unmute = function () {
    this.autoMuted = false;
    this.video.muted = false;
    if (!this.video.volume) this.video.volume = 0.6;
    if (this.unmuteBtn) {
      this.unmuteBtn.remove();
      this.unmuteBtn = null;
    }
  };

  Player.prototype.duration = function () {
    var d = this.video.duration;
    return isFinite(d) && d > 0 ? d : 0;
  };

  Player.prototype.seek = function (seconds) {
    var d = this.duration();
    var target = Math.max(0, number(seconds, 0));
    if (d) target = Math.min(target, d);
    try {
      this.video.currentTime = target;
    } catch (e) {
      return;
    }
    if (this.video.ended && target < d) this.root.classList.remove("pl-ended");
    this.renderTime(target);
  };

  Player.prototype.skip = function (delta) {
    this.seek(this.video.currentTime + delta);
    this.flash((delta > 0 ? "+" : "−") + Math.abs(delta) + "s", delta > 0 ? "forward" : "back");
  };

  Player.prototype.frameStep = function (direction) {
    this.seek(this.video.currentTime + direction / 30);
    this.flash(direction > 0 ? "Frame ›" : "‹ Frame");
  };

  Player.prototype.updatePlayState = function () {
    var root = this.root;
    var video = this.video;
    var paused = video.paused || video.ended;
    root.classList.toggle("pl-paused", paused);
    root.classList.toggle("pl-playing", !paused);
    var label = video.ended ? "Replay" : paused ? "Play" : "Pause";
    var iconName = video.ended ? "replay" : paused ? "play" : "pause";
    this.setButton(this.playBtn, label, iconName, label + " (k)");
    if (this.bigPlay && this.bigPlay.__icon !== iconName) {
      this.bigPlay.__icon = iconName;
      this.bigPlay.innerHTML = icon(video.ended ? "replay" : "play");
      this.bigPlay.setAttribute("aria-label", video.ended ? "Replay" : "Play");
    }
  };

  // ----------------------------------------------------------- volume

  Player.prototype.restorePreferences = function () {
    var video = this.video;
    var volume = load("volume", null);
    if (typeof volume === "number" && volume >= 0 && volume <= 1) video.volume = volume;
    if (load("muted", false) === true) video.muted = true;
    var rate = load("rate", 1);
    this.rate = SPEEDS.indexOf(rate) >= 0 ? rate : 1;
    this.applyRate();

    if (this.theaterBtn && load("theater", false) === true) this.setTheater(true, false);
  };

  Player.prototype.applyRate = function () {
    if (!this.rate) return;
    this.video.defaultPlaybackRate = this.rate;
    if (this.video.playbackRate !== this.rate) this.video.playbackRate = this.rate;
  };

  Player.prototype.setRate = function (rate) {
    this.rate = rate;
    this.applyRate();
    this.flash(rate + "×", "speed");
  };

  Player.prototype.stepRate = function (direction) {
    var index = SPEEDS.indexOf(this.video.playbackRate);
    if (index < 0) index = SPEEDS.indexOf(1);
    this.setRate(SPEEDS[clamp(index + direction, 0, SPEEDS.length - 1)]);
  };

  Player.prototype.toggleMute = function () {
    var video = this.video;
    if (this.autoMuted) {
      this.unmute();
    } else if (video.muted || video.volume === 0) {
      video.muted = false;
      if (video.volume === 0) video.volume = 0.5;
    } else {
      video.muted = true;
    }
    this.flash(video.muted ? "Muted" : Math.round(video.volume * 100) + "%",
      video.muted ? "volMute" : "volHigh");
  };

  Player.prototype.nudgeVolume = function (delta) {
    var video = this.video;
    if (this.autoMuted) this.unmute();
    var next = clamp(Math.round((video.volume + delta) * 100) / 100, 0, 1);
    video.volume = next;
    video.muted = next === 0;
    this.flash(Math.round(next * 100) + "%", next === 0 ? "volMute" : next < 0.5 ? "volLow" : "volHigh");
  };

  Player.prototype.renderVolume = function () {
    var video = this.video;
    var level = video.muted ? 0 : video.volume;
    var iconName = level === 0 ? "volMute" : level < 0.5 ? "volLow" : "volHigh";
    this.setButton(this.muteBtn, level === 0 ? "Unmute" : "Mute", iconName,
      (level === 0 ? "Unmute" : "Mute") + " (m)");
    if (this.volume) {
      this.volume.value = String(level);
      this.volume.style.setProperty("--pl-v", Math.round(level * 100) + "%");
      this.volume.setAttribute("aria-valuetext", Math.round(level * 100) + "%");
    }
  };

  // ------------------------------------------------------------ timeline

  Player.prototype.loop = function () {
    var self = this;
    if (this.raf) return;
    var tick = function () {
      self.raf = 0;
      var video = self.video;
      if (video.paused || video.ended) {
        self.drawViz(true);
        return;
      }
      self.renderTime();
      self.drawViz(false);
      self.raf = window.requestAnimationFrame(tick);
    };
    this.raf = window.requestAnimationFrame(tick);
  };

  Player.prototype.renderTime = function (forced) {
    var video = this.video;
    var d = this.duration();
    var t = typeof forced === "number" ? forced : this.scrubbing ? this.scrubTime : video.currentTime;
    if (!isFinite(t)) t = 0;

    if (this.progress) {
      var fraction = d ? clamp(t / d, 0, 1) : 0;
      this.progress.style.setProperty("--pl-p", (fraction * 100).toFixed(3) + "%");
      var text = clock(t) + "|" + (d ? clock(d) : "");
      if (text !== this.lastText) {
        this.lastText = text;
        this.curEl.textContent = clock(t);
        this.durEl.textContent = d ? clock(d) : "--:--";
        this.progress.setAttribute("aria-valuemax", String(Math.round(d)));
        this.progress.setAttribute("aria-valuenow", String(Math.round(t)));
        this.progress.setAttribute("aria-valuetext", clock(t) + (d ? " of " + clock(d) : ""));
        this.renderChapter(t);
      }
    }
  };

  Player.prototype.renderBuffered = function () {
    if (!this.bufferedBox) return;
    var d = this.duration();
    var ranges = this.video.buffered;
    var box = this.bufferedBox;
    var count = d && ranges ? ranges.length : 0;
    while (box.children.length > count) box.removeChild(box.lastChild);
    while (box.children.length < count) box.appendChild(document.createElement("i"));
    for (var i = 0; i < count; i++) {
      var start = ranges.start(i) / d;
      var end = ranges.end(i) / d;
      box.children[i].style.left = (start * 100).toFixed(3) + "%";
      box.children[i].style.width = (Math.max(0, end - start) * 100).toFixed(3) + "%";
    }
  };

  Player.prototype.bufferAhead = function () {
    var video = this.video;
    var ranges = video.buffered;
    var t = video.currentTime;
    for (var i = 0; ranges && i < ranges.length; i++) {
      if (ranges.start(i) <= t + 0.25 && ranges.end(i) >= t) return Math.max(0, ranges.end(i) - t);
    }
    return 0;
  };

  Player.prototype.chapterAt = function (t) {
    var list = this.opts.chapters;
    var found = null;
    for (var i = 0; i < list.length; i++) {
      if (list[i].start <= t + 0.001) found = list[i];
      else break;
    }
    return found;
  };

  Player.prototype.renderChapter = function (t) {
    if (!this.chapterEl) return;
    var chapter = this.chapterAt(t);
    var title = chapter ? chapter.title : "";
    if (this.chapterEl.textContent !== title) this.chapterEl.textContent = title;
    this.chapterEl.hidden = !title;
  };

  Player.prototype.renderTicks = function () {
    if (!this.ticks) return;
    var d = this.duration();
    this.ticks.textContent = "";
    if (!d) return;
    var self = this;
    this.opts.chapters.forEach(function (chapter) {
      if (chapter.start <= 0 || chapter.start >= d) return;
      var tick = document.createElement("i");
      tick.style.left = ((chapter.start / d) * 100).toFixed(3) + "%";
      self.ticks.appendChild(tick);
    });
  };

  Player.prototype.bindProgress = function () {
    var self = this;
    var progress = this.progress;

    on(progress, "pointerenter", function () {
      self.loadStoryboard();
    });
    on(progress, "pointerdown", function (event) {
      if (event.button !== undefined && event.button !== 0) return;
      if (!self.duration()) return;
      event.preventDefault();
      self.loadStoryboard();
      try {
        progress.setPointerCapture(event.pointerId);
      } catch (e) {
        /* not every pointer can be captured */
      }
      self.scrubbing = true;
      self.lastScrubSeek = 0;
      self.root.classList.add("pl-scrubbing");
      self.scrubTo(event, false);
    });
    on(progress, "pointermove", function (event) {
      if (self.scrubbing) self.scrubTo(event, false);
      else if (event.pointerType === "mouse") self.hoverAt(event);
    });
    var finish = function (event) {
      if (!self.scrubbing) return;
      self.scrubTo(event, true);
      self.scrubbing = false;
      self.root.classList.remove("pl-scrubbing");
      try {
        progress.releasePointerCapture(event.pointerId);
      } catch (e) {
        /* already released */
      }
      if (event.pointerType !== "mouse") self.hideTip();
      self.poke();
    };
    on(progress, "pointerup", finish);
    on(progress, "pointercancel", finish);
    on(progress, "pointerleave", function () {
      if (!self.scrubbing) self.hideTip();
    });
    on(progress, "keydown", function (event) {
      var d = self.duration();
      if (!d) return;
      var t = self.video.currentTime;
      var handled = true;
      switch (event.key) {
        case "ArrowLeft":
        case "ArrowDown":
          self.seek(t - 5);
          break;
        case "ArrowRight":
        case "ArrowUp":
          self.seek(t + 5);
          break;
        case "PageDown":
          self.seek(t - d / 10);
          break;
        case "PageUp":
          self.seek(t + d / 10);
          break;
        case "Home":
          self.seek(0);
          break;
        case "End":
          self.seek(d);
          break;
        default:
          handled = false;
      }
      if (handled) {
        event.preventDefault();
        event.stopPropagation();
        self.poke();
      }
    });
  };

  Player.prototype.fractionAt = function (event) {
    var rect = this.rail.getBoundingClientRect();
    if (!rect.width) return 0;
    return clamp((event.clientX - rect.left) / rect.width, 0, 1);
  };

  Player.prototype.scrubTo = function (event, final) {
    var d = this.duration();
    if (!d) return;
    var fraction = this.fractionAt(event);
    var t = fraction * d;
    this.scrubTime = t;
    this.renderTime(t);
    this.showTipAt(fraction, t);
    var now = Date.now();
    if (final || now - (this.lastScrubSeek || 0) > 120) {
      this.lastScrubSeek = now;
      this.seek(t);
    }
  };

  Player.prototype.hoverAt = function (event) {
    var d = this.duration();
    if (!d) return;
    var fraction = this.fractionAt(event);
    this.progress.style.setProperty("--pl-h", (fraction * 100).toFixed(3) + "%");
    this.showTipAt(fraction, fraction * d);
  };

  Player.prototype.showTipAt = function (fraction, t) {
    var chapter = this.chapterAt(t);
    this.tipTime.textContent = clock(t);
    this.tipChapter.textContent = chapter && this.opts.chapters.length > 1 ? chapter.title : "";
    this.renderPreview(t);
    this.tip.classList.add("pl-on");

    var railWidth = this.rail.offsetWidth;
    var tipWidth = this.tip.offsetWidth;
    var x = fraction * railWidth;
    var half = tipWidth / 2;
    var railLeft = this.rail.offsetLeft;
    var min = half - railLeft + 4;
    var max = railWidth - half + (this.progress.offsetWidth - railWidth - railLeft) - 4;
    this.tip.style.left = railLeft + clamp(x, min, Math.max(min, max)) + "px";
  };

  Player.prototype.hideTip = function () {
    if (this.tip) this.tip.classList.remove("pl-on");
  };

  // -------------------------------------------------- storyboard preview

  Player.prototype.loadStoryboard = function () {
    var board = this.opts.storyboard;
    if (!board || this.storyboardRequested || !this.preview) return;
    this.storyboardRequested = true;
    var self = this;
    var image = new Image();
    image.onload = function () {
      self.preview.classList.add("pl-preview-ready");
    };
    image.onerror = function () {
      self.preview.remove();
      self.preview = null;
    };
    image.src = board.src;
    this.preview.style.backgroundImage = 'url("' + board.src.replace(/"/g, "%22") + '")';
  };

  Player.prototype.renderPreview = function (t) {
    var board = this.opts.storyboard;
    var preview = this.preview;
    if (!board || !preview) return;
    var root = this.root;
    var cap = root.classList.contains("pl-w-xs") ? 120 : root.classList.contains("pl-w-sm") ? 160 : 240;
    var displayWidth = Math.min(board.width, cap);
    var scale = displayWidth / board.width;
    var index = Math.min(board.count - 1, Math.max(0, Math.floor(t / board.interval)));
    var column = index % board.cols;
    var row = Math.floor(index / board.cols);
    preview.style.width = Math.round(board.width * scale) + "px";
    preview.style.height = Math.round(board.height * scale) + "px";
    preview.style.backgroundSize =
      Math.round(board.cols * board.width * scale) + "px " + Math.round(board.rows * board.height * scale) + "px";
    preview.style.backgroundPosition =
      -Math.round(column * board.width * scale) + "px " + -Math.round(row * board.height * scale) + "px";
  };

  // ------------------------------------------------------------ captions

  Player.prototype.setupTracks = function () {
    var self = this;
    var tracks = this.video.textTracks;
    if (!tracks) return;

    var collect = function () {
      self.trackList = [];
      for (var i = 0; i < tracks.length; i++) {
        var track = tracks[i];
        if (track.kind === "subtitles" || track.kind === "captions") self.trackList.push(track);
      }
      if (self.ccBtn) self.ccBtn.hidden = self.trackList.length === 0;
      if (self.activeTrack && self.trackList.indexOf(self.activeTrack) < 0) self.activeTrack = null;
    };
    collect();

    // Initial choice: the visitor's last decision, else an explicit `default`.
    var preference = load("cc", null);
    var chosen = null;
    if (preference && preference.on) {
      chosen = this.trackList.filter(function (track) {
        return track.language === preference.lang;
      })[0] || this.defaultTrack() || this.trackList[0] || null;
    } else if (!preference) {
      chosen = this.defaultTrack();
    }
    this.selectTrack(chosen, false);

    if (tracks.addEventListener) {
      on(tracks, "addtrack", function () {
        collect();
        self.enforceTrackModes();
      });
      on(tracks, "removetrack", collect);
      // The browser may switch a `default` track to "showing" on its own; the
      // player draws cues itself, so keep everything at hidden/disabled.
      on(tracks, "change", function () {
        self.enforceTrackModes();
      });
    }
    on(this.video, "loadedmetadata", function () {
      self.enforceTrackModes();
    });
  };

  Player.prototype.defaultTrack = function () {
    var element = this.video.querySelector("track[default]");
    return element && element.track ? element.track : null;
  };

  Player.prototype.enforceTrackModes = function () {
    var active = this.activeTrack;
    this.trackList.forEach(function (track) {
      var wanted = track === active ? "hidden" : "disabled";
      if (track.mode !== wanted) track.mode = wanted;
    });
  };

  Player.prototype.selectTrack = function (track, persist) {
    var self = this;
    if (this.activeTrack && this.activeTrack !== track) {
      this.activeTrack.oncuechange = null;
    }
    this.activeTrack = track || null;
    if (track) {
      this.lastTrack = track;
      track.oncuechange = function () {
        self.renderCues();
      };
    }
    this.enforceTrackModes();
    this.renderCues();
    this.root.classList.toggle("pl-cc-on", !!track);
    if (this.ccBtn) this.ccBtn.setAttribute("aria-pressed", track ? "true" : "false");
    if (persist) save("cc", track ? { on: true, lang: track.language } : { on: false });
  };

  Player.prototype.toggleCaptions = function () {
    if (!this.trackList.length) return;
    if (this.activeTrack) {
      this.selectTrack(null, true);
      this.flash("Subtitles off", "cc");
    } else {
      var track = this.lastTrack && this.trackList.indexOf(this.lastTrack) >= 0
        ? this.lastTrack
        : this.trackList[0];
      this.selectTrack(track, true);
      this.flash(track.label || track.language || "Subtitles on", "cc");
    }
  };

  Player.prototype.renderCues = function () {
    var box = this.captionBox;
    if (!box) return;
    box.textContent = "";
    var track = this.activeTrack;
    var cues = track && track.activeCues;
    if (!cues) return;
    for (var i = 0; i < cues.length; i++) {
      var cue = cues[i];
      var line = h("span", "pl-cue");
      if (typeof cue.getCueAsHTML === "function") line.appendChild(cue.getCueAsHTML());
      else line.textContent = cue.text || "";
      box.appendChild(line);
    }
  };

  Player.prototype.trackLabel = function (track) {
    if (!track) return "Off";
    return track.label || (track.language ? track.language.toUpperCase() : "Subtitles");
  };

  // ----------------------------------------------------------------- menu

  Player.prototype.bindMenu = function () {
    var self = this;
    var menu = this.menu;

    this.onOutside = function (event) {
      if (menu.contains(event.target) || self.gearBtn.contains(event.target)) return;
      self.closeMenu();
    };

    // Tabbing out of an open menu closes it, as menus do everywhere else.
    on(menu, "focusout", function (event) {
      var next = event.relatedTarget;
      if (!self.menuOpen || !next) return;
      if (menu.contains(next) || self.gearBtn.contains(next)) return;
      self.closeMenuQuietly();
    });

    on(menu, "keydown", function (event) {
      var items = Array.prototype.slice.call(menu.querySelectorAll(".pl-mi"));
      var index = items.indexOf(document.activeElement);
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        event.stopPropagation();
        if (!items.length) return;
        var step = event.key === "ArrowDown" ? 1 : -1;
        focusQuietly(items[(index + step + items.length) % items.length]);
      } else if (event.key === "ArrowLeft" && menu.getAttribute("data-page") !== "main") {
        event.preventDefault();
        event.stopPropagation();
        self.renderMenu("main", true);
      } else if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        self.menuByKeyboard = true;
        self.closeMenu();
      }
    });
  };

  Player.prototype.toggleMenu = function (viaKeyboard) {
    if (this.menuOpen) this.closeMenu();
    else this.openMenu("main", viaKeyboard);
  };

  Player.prototype.openMenu = function (page, focusFirst) {
    if (!this.menu) return;
    this.menuOpen = true;
    this.menu.hidden = false;
    this.root.classList.add("pl-menu-open");
    this.gearBtn.setAttribute("aria-expanded", "true");
    this.showControls();
    this.renderMenu(page || "main", focusFirst);
    document.addEventListener("pointerdown", this.onOutside, true);
  };

  Player.prototype.closeMenu = function () {
    if (!this.menuOpen) return;
    var hadFocus = this.menu.contains(document.activeElement);
    this.menuOpen = false;
    this.menu.hidden = true;
    this.root.classList.remove("pl-menu-open");
    this.gearBtn.setAttribute("aria-expanded", "false");
    document.removeEventListener("pointerdown", this.onOutside, true);
    if (hadFocus) {
      // Keyboard users go back to the button that opened the menu; pointer
      // users get the player itself, so Space keeps meaning play/pause.
      if (this.menuByKeyboard) focusQuietly(this.gearBtn);
      else this.focusRoot();
    }
    this.poke();
  };

  Player.prototype.closeMenuQuietly = function () {
    // Close without moving focus: the visitor already put it somewhere else.
    this.menuOpen = false;
    this.menu.hidden = true;
    this.root.classList.remove("pl-menu-open");
    this.gearBtn.setAttribute("aria-expanded", "false");
    document.removeEventListener("pointerdown", this.onOutside, true);
    this.poke();
  };

  Player.prototype.menuItem = function (spec) {
    var self = this;
    var role = spec.checked === undefined ? "menuitem" : spec.toggle ? "menuitemcheckbox" : "menuitemradio";
    var item = h("button", "pl-mi" + (spec.className ? " " + spec.className : ""), {
      type: "button",
      role: role,
      "aria-checked": spec.checked === undefined ? null : spec.checked ? "true" : "false",
    });
    if (spec.back) {
      item.appendChild(h("span", "pl-mi-icon", { html: icon("chevLeft") }));
      item.appendChild(h("span", "pl-mi-label", { text: spec.label }));
    } else if (spec.checked !== undefined && !spec.toggle) {
      item.appendChild(h("span", "pl-mi-check", { html: icon("check") }));
      item.appendChild(h("span", "pl-mi-label", { text: spec.label }));
      if (spec.tag) item.appendChild(h("span", "pl-mi-tag", { text: spec.tag }));
    } else {
      item.appendChild(h("span", "pl-mi-icon", { html: icon(spec.icon) }));
      item.appendChild(h("span", "pl-mi-label", { text: spec.label }));
      if (spec.toggle) {
        item.appendChild(h("span", "pl-switch", { "aria-hidden": "true" }));
      } else {
        item.appendChild(h("span", "pl-mi-value", { text: spec.value || "" }));
        item.appendChild(h("span", "pl-mi-chev", { html: icon("chevRight") }));
      }
    }
    on(item, "click", function (event) {
      event.stopPropagation();
      self.menuByKeyboard = event.detail === 0;
      spec.action.call(self, event);
    });
    return item;
  };

  Player.prototype.renderMenu = function (page, focusFirst) {
    var self = this;
    var menu = this.menu;
    var video = this.video;
    var hadFocus = menu.contains(document.activeElement) || focusFirst;
    menu.textContent = "";
    menu.setAttribute("data-page", page);

    var back = function (label) {
      menu.appendChild(self.menuItem({
        back: true,
        label: label,
        className: "pl-mi-back",
        action: function () {
          self.renderMenu("main", true);
        },
      }));
    };

    if (page === "speed") {
      back("Playback speed");
      SPEEDS.forEach(function (rate) {
        menu.appendChild(self.menuItem({
          label: rateLabel(rate),
          checked: video.playbackRate === rate,
          action: function () {
            self.setRate(rate);
            self.closeMenu();
          },
        }));
      });
    } else if (page === "quality") {
      back("Quality");
      menu.appendChild(self.menuItem({
        label: "Auto" + (this.hls && this.hls.autoLevelEnabled && this.levels()[this.playingLevel]
          ? " (" + this.levels()[this.playingLevel].height + "p)" : ""),
        checked: !!(this.hls && this.hls.autoLevelEnabled),
        action: function () {
          self.setLevel(-1);
          self.closeMenu();
        },
      }));
      var levels = this.levels()
        .map(function (level, index) {
          return { level: level, index: index };
        })
        .sort(function (a, b) {
          return (b.level.height || 0) - (a.level.height || 0) || (b.level.bitrate || 0) - (a.level.bitrate || 0);
        });
      var heights = {};
      levels.forEach(function (entry) {
        heights[entry.level.height] = (heights[entry.level.height] || 0) + 1;
      });
      levels.forEach(function (entry) {
        var level = entry.level;
        var label = level.height ? level.height + "p" : bitrate(level.bitrate);
        if (level.height && heights[level.height] > 1) label += " · " + bitrate(level.bitrate);
        menu.appendChild(self.menuItem({
          label: label,
          tag: level.height >= 2160 ? "4K" : level.height >= 720 ? "HD" : "",
          checked: !!(self.hls && !self.hls.autoLevelEnabled && self.hls.currentLevel === entry.index),
          action: function () {
            self.setLevel(entry.index);
            self.closeMenu();
          },
        }));
      });
    } else if (page === "cc") {
      back("Subtitles");
      menu.appendChild(self.menuItem({
        label: "Off",
        checked: !this.activeTrack,
        action: function () {
          self.selectTrack(null, true);
          self.closeMenu();
        },
      }));
      this.trackList.forEach(function (track) {
        menu.appendChild(self.menuItem({
          label: self.trackLabel(track),
          checked: self.activeTrack === track,
          action: function () {
            self.selectTrack(track, true);
            self.closeMenu();
          },
        }));
      });
    } else {
      menu.appendChild(self.menuItem({
        icon: "speed",
        label: "Playback speed",
        value: rateLabel(video.playbackRate),
        action: function () {
          self.renderMenu("speed", true);
        },
      }));
      if (this.levels().length > 1) {
        menu.appendChild(self.menuItem({
          icon: "quality",
          label: "Quality",
          value: this.qualityLabel(),
          action: function () {
            self.renderMenu("quality", true);
          },
        }));
      }
      if (this.trackList.length) {
        menu.appendChild(self.menuItem({
          icon: "cc",
          label: "Subtitles",
          value: this.trackLabel(this.activeTrack),
          action: function () {
            self.renderMenu("cc", true);
          },
        }));
      }
      menu.appendChild(self.menuItem({
        icon: "stats",
        label: "Stats for nerds",
        toggle: true,
        checked: this.statsOn,
        className: "pl-mi-stats",
        action: function () {
          self.toggleStats();
          self.closeMenu();
        },
      }));
    }

    if (hadFocus) {
      var target = menu.querySelector('.pl-mi[aria-checked="true"]:not(.pl-mi-stats)') ||
        menu.querySelector(".pl-mi:not(.pl-mi-back)") || menu.querySelector(".pl-mi");
      if (target) focusQuietly(target);
    }
  };

  // ---------------------------------------------------------------- stats

  Player.prototype.toggleStats = function (force) {
    var self = this;
    var turnOn = typeof force === "boolean" ? force : !this.statsOn;
    this.statsOn = turnOn;
    this.root.classList.toggle("pl-stats-on", turnOn);
    clearInterval(this.timers.stats);

    if (!turnOn) {
      if (this.statsBox) this.statsBox.hidden = true;
      return;
    }
    if (!this.statsBox) this.buildStats();
    this.statsBox.hidden = false;
    this.renderStats();
    this.timers.stats = setInterval(function () {
      self.renderStats();
    }, 500);
  };

  Player.prototype.buildStats = function () {
    var self = this;
    var rows = ["Engine", "Viewport", "Resolution", "Level", "Codecs", "Bandwidth", "Buffer", "Frames", "Speed", "Volume", "Position"];
    var list = h("dl", "pl-stats-list");
    this.statCells = {};
    rows.forEach(function (name) {
      var value = h("dd", null, { text: "–" });
      self.statCells[name] = value;
      list.appendChild(h("dt", null, { text: name }));
      list.appendChild(value);
    });
    this.bufferMeter = h("i");
    var close = h("button", "pl-stats-close", { type: "button", "aria-label": "Close stats", html: icon("close") });
    on(close, "click", function (event) {
      event.stopPropagation();
      self.toggleStats(false);
    });
    this.statsBox = h("div", "pl-stats", { role: "status", hidden: true }, [
      h("div", "pl-stats-head", { text: "Stats for nerds" }),
      close,
      list,
      h("div", "pl-meter", { "aria-hidden": "true" }, [this.bufferMeter]),
    ]);
    this.root.appendChild(this.statsBox);
  };

  Player.prototype.renderStats = function () {
    var video = this.video;
    var cells = this.statCells;
    var set = function (name, value) {
      if (cells[name].textContent !== value) cells[name].textContent = value;
    };
    var dpr = Math.round((window.devicePixelRatio || 1) * 100) / 100;

    var engine = this.engine === "hls.js" ? "HLS · hls.js" : this.engine === "native" ? "HLS · native" : "Progressive";
    set("Engine", engine);
    set("Viewport", this.root.clientWidth + "×" + this.root.clientHeight + " @" + dpr + "x");
    set("Resolution", video.videoWidth ? video.videoWidth + "×" + video.videoHeight
      : this.opts.kind === "audio" ? "audio only" : "–");

    var level = this.hls ? this.levels()[this.playingLevel] : null;
    set("Level", level
      ? (level.height ? level.height + "p · " : "") + bitrate(level.bitrate) + (this.hls.autoLevelEnabled ? " (auto)" : " (manual)")
      : "–");
    var codecs = level ? [level.videoCodec, level.audioCodec].filter(Boolean).join(" / ") : "";
    set("Codecs", codecs || this.bufferCodecs || "–");
    set("Bandwidth", this.hls ? bitrate(this.hls.bandwidthEstimate) : "–");

    var ahead = this.bufferAhead();
    set("Buffer", ahead.toFixed(1) + " s ahead");
    this.bufferMeter.style.width = clamp((ahead / 30) * 100, 0, 100).toFixed(1) + "%";

    var quality = typeof video.getVideoPlaybackQuality === "function" ? video.getVideoPlaybackQuality() : null;
    set("Frames", quality ? quality.droppedVideoFrames + " dropped / " + quality.totalVideoFrames : "–");
    set("Speed", video.playbackRate + "×");
    set("Volume", video.muted ? "muted" : Math.round(video.volume * 100) + "%");
    set("Position", clock(video.currentTime) + " / " + clock(this.duration()));
  };

  // ----------------------------------------------------------------- OSD

  Player.prototype.flash = function (text, iconName) {
    var osd = this.osd;
    if (!osd) return;
    osd.textContent = "";
    if (iconName) osd.appendChild(h("span", "pl-osd-icon", { html: icon(iconName) }));
    if (text) osd.appendChild(h("span", "pl-osd-text", { text: text }));
    osd.classList.remove("pl-osd-show");
    void osd.offsetWidth; // restart the animation
    osd.classList.add("pl-osd-show");
    if (text) this.live.textContent = text;
  };

  // ------------------------------------------------- theater, fullscreen, PiP

  Player.prototype.setTheater = function (turnOn, persist) {
    document.body.classList.toggle("is-theater", turnOn);
    if (this.theaterBtn) {
      this.theaterBtn.setAttribute("aria-pressed", turnOn ? "true" : "false");
      this.setButton(this.theaterBtn, turnOn ? "Default view" : "Theater mode",
        turnOn ? "theaterOff" : "theater", (turnOn ? "Default view" : "Theater mode") + " (t)");
    }
    if (persist) save("theater", turnOn);
    dispatch(this.root, "player:theater", { on: turnOn, player: this });
  };

  Player.prototype.canFullscreen = function () {
    var root = this.root;
    return !!(root.requestFullscreen || root.webkitRequestFullscreen || this.video.webkitEnterFullscreen);
  };

  Player.prototype.toggleFullscreen = function () {
    var root = this.root;
    if (fullscreenElement()) {
      var exit = document.exitFullscreen || document.webkitExitFullscreen;
      if (exit) {
        var exiting = exit.call(document);
        if (exiting && exiting.catch) exiting.catch(noop);
      }
      return;
    }
    var request = root.requestFullscreen || root.webkitRequestFullscreen;
    if (request) {
      try {
        var entering = request.call(root);
        if (entering && entering.catch) entering.catch(noop);
      } catch (e) {
        /* refused (no user gesture, iframe without allowfullscreen…) */
      }
    } else if (this.video.webkitEnterFullscreen) {
      try {
        this.video.webkitEnterFullscreen();
      } catch (e) {
        /* iOS refuses before metadata is loaded */
      }
    }
  };

  Player.prototype.onFullscreenChange = function () {
    var active = fullscreenElement() === this.root;
    this.root.classList.toggle("pl-fs", active);
    this.setButton(this.fsBtn, active ? "Exit full screen" : "Full screen",
      active ? "fullscreenOff" : "fullscreen", (active ? "Exit full screen" : "Full screen") + " (f)");
    if (active && this.opts.kind === "video" && screen.orientation && screen.orientation.lock &&
        this.video.videoWidth > this.video.videoHeight && window.matchMedia &&
        window.matchMedia("(pointer: coarse)").matches) {
      screen.orientation.lock("landscape").catch(noop);
    }
    this.poke();
  };

  Player.prototype.canPip = function () {
    return !!(document.pictureInPictureEnabled && this.opts.kind !== "audio" &&
      typeof this.video.requestPictureInPicture === "function" && !this.video.disablePictureInPicture);
  };

  Player.prototype.togglePip = function () {
    if (document.pictureInPictureElement === this.video) {
      document.exitPictureInPicture().catch(noop);
    } else if (typeof this.video.requestPictureInPicture === "function") {
      this.video.requestPictureInPicture().catch(noop);
    }
  };

  // -------------------------------------------------------------- up next

  Player.prototype.showUpNext = function () {
    var self = this;
    var o = this.opts;
    if (!o.nextUrl || this.upnext) return;

    var gradientId = "pl-ring-" + this.id;
    var circumference = 2 * Math.PI * 26;
    var ring = h("div", "pl-ring", {
      html: '<svg viewBox="0 0 60 60" aria-hidden="true"><defs><linearGradient id="' + gradientId +
        '" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#2ee6a6"/><stop offset=".5" stop-color="#22d3ee"/>' +
        '<stop offset="1" stop-color="#7c6cff"/></linearGradient></defs>' +
        '<circle class="pl-ring-bg" cx="30" cy="30" r="26"/>' +
        '<circle class="pl-ring-fg" cx="30" cy="30" r="26" stroke="url(#' + gradientId + ')" ' +
        'stroke-dasharray="' + circumference.toFixed(2) + '" stroke-dashoffset="0"/></svg>',
    });
    var count = h("span", "pl-ring-count", { text: String(UPNEXT_SECONDS) });
    ring.appendChild(count);

    var cancel = h("button", "pl-pill", { type: "button", text: "Cancel" });
    var playNow = h("button", "pl-pill pl-pill-primary", { type: "button" }, [
      h("span", "pl-pill-icon", { html: icon("next") }),
      h("span", null, { text: "Play now" }),
    ]);
    on(cancel, "click", function (event) {
      event.stopPropagation();
      self.hideUpNext();
    });
    on(playNow, "click", function (event) {
      event.stopPropagation();
      self.goNext();
    });

    this.upnext = h("div", "pl-upnext", { role: "dialog", "aria-label": "Up next" }, [
      h("div", "pl-upnext-card", null, [
        ring,
        h("div", "pl-upnext-label", { text: "Up next" }),
        h("div", "pl-upnext-title", { text: o.nextTitle || "Next item" }),
        h("div", "pl-upnext-actions", null, [cancel, playNow]),
      ]),
    ]);
    this.root.appendChild(this.upnext);

    var foreground = ring.querySelector(".pl-ring-fg");
    var started = Date.now();
    var total = UPNEXT_SECONDS * 1000;
    this.timers.upnext = setInterval(function () {
      var elapsed = Date.now() - started;
      var left = Math.max(0, total - elapsed);
      count.textContent = String(Math.ceil(left / 1000));
      foreground.setAttribute("stroke-dashoffset", ((elapsed / total) * circumference).toFixed(2));
      if (left <= 0) {
        clearInterval(self.timers.upnext);
        self.goNext();
      }
    }, 100);
    focusQuietly(playNow);
  };

  Player.prototype.hideUpNext = function () {
    clearInterval(this.timers.upnext);
    if (this.upnext) {
      this.upnext.remove();
      this.upnext = null;
    }
  };

  Player.prototype.goNext = function () {
    this.hideUpNext();
    if (this.opts.nextUrl) window.location.href = this.opts.nextUrl;
  };

  // --------------------------------------------------------------- resume

  Player.prototype.initResume = function () {
    var self = this;
    var o = this.opts;
    if (!o.resumeKey || o.start > 0) return;
    whenMetadata(this.video, function () {
      var record = load("pos." + o.resumeKey, null);
      var d = self.duration();
      if (!record || !d || self.started) return;
      var t = number(record.t, 0);
      if (t > 10 && t < d * 0.95) self.showResume(t);
    });
  };

  Player.prototype.showResume = function (t) {
    var self = this;
    var resume = h("button", "pl-pill pl-pill-primary pl-pill-sm", { type: "button", text: "Resume " + clock(t) });
    var dismiss = h("button", "pl-toast-close", { type: "button", "aria-label": "Dismiss", html: icon("close") });
    on(resume, "click", function (event) {
      event.stopPropagation();
      self.hideResume();
      self.seek(t);
      self.play();
    });
    on(dismiss, "click", function (event) {
      event.stopPropagation();
      self.hideResume();
    });
    this.resumeToast = h("div", "pl-toast", { role: "status" }, [
      h("span", "pl-toast-text", { text: "Continue where you left off?" }),
      resume,
      dismiss,
    ]);
    this.root.appendChild(this.resumeToast);
    this.timers.resume = setTimeout(function () {
      self.hideResume();
    }, 9000);
  };

  Player.prototype.hideResume = function () {
    clearTimeout(this.timers.resume);
    if (this.resumeToast) {
      this.resumeToast.remove();
      this.resumeToast = null;
    }
  };

  Player.prototype.maybeSaveResume = function () {
    var now = Date.now();
    if (now - (this.lastResumeSave || 0) < 4000) return;
    this.lastResumeSave = now;
    this.saveResume();
  };

  Player.prototype.saveResume = function () {
    var key = this.opts.resumeKey;
    if (!key || !this.started) return;
    var t = this.video.currentTime;
    var d = this.duration();
    if (!d) return;
    if (t < 10 || t > d * 0.95) {
      this.clearResume();
      return;
    }
    save("pos." + key, { t: Math.floor(t), d: Math.floor(d), at: Date.now() });
    var index = load("pos.index", []);
    if (!Array.isArray(index)) index = [];
    index = index.filter(function (entry) {
      return entry !== key;
    });
    index.push(key);
    while (index.length > RESUME_LIMIT) save("pos." + index.shift(), null);
    save("pos.index", index);
  };

  Player.prototype.clearResume = function () {
    if (this.opts.resumeKey) save("pos." + this.opts.resumeKey, null);
  };

  // --------------------------------------------------------------- errors

  Player.prototype.reportMediaError = function () {
    var error = this.video.error;
    var messages = {
      1: "Playback was aborted.",
      2: "A network error interrupted the download.",
      3: "The file could not be decoded.",
      4: "The file is missing or its format is not supported by this browser.",
    };
    this.showError((error && messages[error.code]) || "This media could not be played.");
  };

  Player.prototype.showError = function (message) {
    this.setWaiting(false);
    this.root.classList.add("pl-error-state");
    if (this.errorBox) this.errorBox.remove();
    this.errorBox = h("div", "pl-error", { role: "alert" }, [
      h("span", "pl-error-icon", { html: icon("alert") }),
      h("strong", null, { text: "Playback failed" }),
      h("span", "pl-error-text", { text: message }),
    ]);
    this.root.appendChild(this.errorBox);
  };

  // ----------------------------------------------------- audio visualizer

  Player.prototype.ensureAudioGraph = function () {
    if (!this.canvas || this.audioContext === false) return;
    if (!this.audioContext) {
      var Context = window.AudioContext || window.webkitAudioContext;
      if (!Context) {
        this.audioContext = false;
        return;
      }
      try {
        var context = new Context();
        var source = context.createMediaElementSource(this.video);
        var analyser = context.createAnalyser();
        analyser.fftSize = 512;
        analyser.smoothingTimeConstant = 0.78;
        source.connect(analyser);
        analyser.connect(context.destination);
        this.audioContext = context;
        this.analyser = analyser;
        this.frequencies = new Uint8Array(analyser.frequencyBinCount);
        this.levelsViz = [];
      } catch (e) {
        this.audioContext = false;
        return;
      }
    }
    this.resumeAudio();
  };

  Player.prototype.resumeAudio = function () {
    if (this.audioContext && this.audioContext.state === "suspended") {
      var resumed = this.audioContext.resume();
      if (resumed && resumed.catch) resumed.catch(noop);
    }
  };

  Player.prototype.drawViz = function (stopping) {
    var canvas = this.canvas;
    if (!canvas || !this.analyser) return;
    var ratio = Math.min(2, window.devicePixelRatio || 1);
    var width = Math.max(1, Math.round(canvas.clientWidth * ratio));
    var height = Math.max(1, Math.round(canvas.clientHeight * ratio));
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
      this.vizGradient = null;
    }
    var context = canvas.getContext("2d");
    context.clearRect(0, 0, width, height);
    if (stopping) {
      this.levelsViz = [];
      return;
    }

    this.analyser.getByteFrequencyData(this.frequencies);
    var bins = this.frequencies;
    var usable = Math.floor(bins.length * 0.7);
    var pad = Math.round(width * 0.06);
    var span = width - pad * 2;
    var bars = clamp(Math.floor(canvas.clientWidth / 15), 24, 88);
    var gap = Math.max(2, Math.round(3 * ratio));
    var barWidth = Math.max(2, (span - gap * (bars - 1)) / bars);
    var middle = height / 2;
    var maxHeight = height * 0.3;

    if (!this.vizGradient) {
      var gradient = context.createLinearGradient(0, 0, width, 0);
      gradient.addColorStop(0, "#2ee6a6");
      gradient.addColorStop(0.5, "#22d3ee");
      gradient.addColorStop(1, "#7c6cff");
      this.vizGradient = gradient;
    }
    context.fillStyle = this.vizGradient;

    for (var i = 0; i < bars; i++) {
      // Logarithmic grouping: low frequencies get more bars, as hearing does.
      var low = Math.floor(Math.pow(usable, i / bars));
      var high = Math.max(low + 1, Math.floor(Math.pow(usable, (i + 1) / bars)));
      var sum = 0;
      for (var j = low; j < high; j++) sum += bins[j];
      // A gentle power curve keeps loud lows from pinning every bar at the top.
      var value = Math.pow(sum / (high - low) / 255, 1.35);
      var previous = this.levelsViz[i] || 0;
      var level = Math.max(value, previous * 0.9);
      this.levelsViz[i] = level;
      if (level < 0.015) continue; // silence draws nothing, not a dotted line
      var barHeight = Math.max(1.5 * ratio, level * maxHeight);
      var x = pad + i * (barWidth + gap);
      var radius = Math.min(barWidth / 2, 4 * ratio);
      roundRect(context, x, middle - barHeight, barWidth, barHeight * 2, radius);
    }
  };

  function roundRect(context, x, y, width, height, radius) {
    context.beginPath();
    if (typeof context.roundRect === "function") {
      context.roundRect(x, y, width, height, radius);
    } else {
      context.rect(x, y, width, height);
    }
    context.fill();
  }

  // ---------------------------------------------------------------- sizing

  Player.prototype.observeSize = function () {
    var root = this.root;
    var apply = function () {
      var width = root.clientWidth;
      root.classList.toggle("pl-w-sm", width < 640);
      root.classList.toggle("pl-w-xs", width < 440);
      root.style.setProperty("--pl-cap", Math.round(clamp(width * 0.03, 14, 34)) + "px");
    };
    apply();
    if (window.ResizeObserver) {
      this.resizeObserver = new ResizeObserver(apply);
      this.resizeObserver.observe(root);
    } else {
      on(window, "resize", apply);
    }
  };

  Player.prototype.inView = function (threshold) {
    var rect = this.root.getBoundingClientRect();
    var viewport = window.innerHeight || document.documentElement.clientHeight;
    if (!rect.height) return false;
    var visible = Math.min(rect.bottom, viewport) - Math.max(rect.top, 0);
    return visible / rect.height >= (threshold || 0.4);
  };

  // -------------------------------------------------------------- keyboard

  Player.prototype.handleKey = function (event) {
    var key = event.key;
    var video = this.video;

    if (key === "Escape") {
      if (this.menuOpen) {
        this.closeMenu();
        return true;
      }
      if (this.upnext) {
        this.hideUpNext();
        return true;
      }
      if (this.statsOn) {
        this.toggleStats(false);
        return true;
      }
      return false;
    }
    if (this.menuOpen && this.menu.contains(event.target)) return false;

    switch (key) {
      case " ":
      case "k":
      case "K":
        this.userToggle();
        break;
      case "j":
      case "J":
        this.skip(-10);
        break;
      case "l":
      case "L":
        this.skip(10);
        break;
      case "ArrowLeft":
        this.skip(-5);
        break;
      case "ArrowRight":
        this.skip(5);
        break;
      case "ArrowUp":
        this.nudgeVolume(0.05);
        break;
      case "ArrowDown":
        this.nudgeVolume(-0.05);
        break;
      case "m":
      case "M":
        this.toggleMute();
        break;
      case "f":
      case "F":
        this.toggleFullscreen();
        break;
      case "c":
      case "C":
        if (!this.trackList.length) return false;
        this.toggleCaptions();
        break;
      case "t":
      case "T":
        if (!this.theaterBtn) return false;
        this.setTheater(!document.body.classList.contains("is-theater"), true);
        break;
      case "i":
      case "I":
        this.toggleStats();
        break;
      case "Home":
        this.seek(0);
        break;
      case "End":
        this.seek(this.duration());
        break;
      case "<":
        this.stepRate(-1);
        break;
      case ">":
        this.stepRate(1);
        break;
      case ",":
        if (!video.paused) return false;
        this.frameStep(-1);
        break;
      case ".":
        if (!video.paused) return false;
        this.frameStep(1);
        break;
      default:
        if (/^[0-9]$/.test(key)) {
          var d = this.duration();
          if (!d) return false;
          this.seek((d * Number(key)) / 10);
          break;
        }
        return false;
    }
    this.poke();
    return true;
  };

  function isTyping(target, key) {
    if (!target || !target.tagName) return false;
    if (target.isContentEditable) return true;
    var tag = target.tagName;
    if (tag === "TEXTAREA" || tag === "SELECT") return true;
    if (tag === "INPUT") {
      var type = (target.type || "text").toLowerCase();
      if (type === "range") return /^(Arrow|Home|End|Page)/.test(key);
      if (["button", "checkbox", "radio", "submit", "reset", "color", "file"].indexOf(type) >= 0) {
        return key === " " || key === "Enter";
      }
      return true;
    }
    // Space and Enter belong to whatever button or link has focus.
    if ((tag === "BUTTON" || tag === "A" || tag === "SUMMARY") && (key === " " || key === "Enter")) return true;
    var role = target.getAttribute && target.getAttribute("role");
    if (role && /^(button|menuitem|menuitemradio|menuitemcheckbox|link|tab|option|checkbox|switch)$/.test(role) &&
        (key === " " || key === "Enter")) {
      return true;
    }
    return false;
  }

  function playerFor(target) {
    var root = target && target.closest ? target.closest("[data-player]") : null;
    if (root && root.__akonPlayer) return root.__akonPlayer;
    var bodyFocused = !target || target === document.body || target === document.documentElement ||
      target === document;
    if (!bodyFocused) return null;
    for (var i = 0; i < players.length; i++) {
      if (document.body.contains(players[i].root) && players[i].inView()) return players[i];
    }
    return null;
  }

  function onKeydown(event) {
    if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey) return;
    var player = playerFor(event.target);
    if (!player || isTyping(event.target, event.key)) return;
    if (player.handleKey(event)) event.preventDefault();
  }

  function onDocumentClick(event) {
    var target = event.target;
    var link = target && target.closest ? target.closest("[data-seek]") : null;
    if (!link || link.closest("[data-player]")) return;
    var seconds = number(link.getAttribute("data-seek"), NaN);
    if (!isFinite(seconds)) return;
    var player = players.filter(function (candidate) {
      return document.body.contains(candidate.root);
    })[0];
    if (!player) return;
    event.preventDefault();
    player.seek(seconds);
    player.play();
    player.poke();
    if (!player.inView(0.6)) {
      player.root.scrollIntoView({ behavior: reducedMotion ? "auto" : "smooth", block: "center" });
    }
  }

  function onFullscreenChange() {
    players.forEach(function (player) {
      player.onFullscreenChange();
    });
  }

  // ------------------------------------------------------------------ boot

  function init(scope) {
    var context = scope && scope.querySelectorAll ? scope : document;
    var roots = context.querySelectorAll("[data-player]");
    for (var i = 0; i < roots.length; i++) {
      var root = roots[i];
      if (root.__akonPlayer || !root.querySelector("video")) continue;
      try {
        players.push(new Player(root));
      } catch (error) {
        // A broken enhancement must never take the native player down with it.
        var video = root.querySelector("video");
        if (video) video.setAttribute("controls", "");
        if (window.console && console.error) console.error("AkonPlayer:", error);
      }
    }
    return players;
  }

  window.AkonPlayer = {
    __loaded: true,
    players: players,
    init: init,
    get: function (element) {
      var root = element && element.closest ? element.closest("[data-player]") : null;
      return (root && root.__akonPlayer) || null;
    },
  };

  document.addEventListener("keydown", onKeydown);
  document.addEventListener("click", onDocumentClick);
  document.addEventListener("fullscreenchange", onFullscreenChange);
  document.addEventListener("webkitfullscreenchange", onFullscreenChange);

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      init();
    });
  } else {
    init();
  }
})();
