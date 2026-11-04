/* QQplay live pages: the viewer page, the studio's control room and the
 * embed.
 *
 * The server renders every state of the page (waiting, on air, ended, each
 * state of the recording) with its own translated text; this script only
 * switches between them as the live's state changes. It polls one JSON
 * endpoint, which also brings the new chat messages, a little faster while the
 * stream is on air and slower in a background tab.
 *
 * Markup contract: [data-live] on the root, with data-state-url, data-mode
 * ("watch" | "control" | "embed") and the initial state in a
 * <script type="application/json" data-live-state>.
 */
(function () {
  "use strict";

  var gettext = window.gettext || function (text) { return text; };
  var ngettext = window.ngettext || function (singular, plural, count) {
    return count === 1 ? singular : plural;
  };
  var interpolate = window.interpolate || function (fmt, obj) {
    return fmt.replace(/%\((\w+)\)s/g, function (_match, key) { return String(obj[key]); });
  };

  var POLL = { live: 4000, interrupted: 3000, scheduled: 10000, soon: 4000, ended: 8000, pushed: 20000 };
  /* The chat's reactions, in the order they are offered. The pictures are
     emoji, drawn by the viewer's own system. */
  var REACTIONS = [
    { kind: "like", emoji: "\uD83D\uDC4D" },
    { kind: "love", emoji: "\u2764\uFE0F" },
    { kind: "haha", emoji: "\uD83D\uDE02" },
    { kind: "wow", emoji: "\uD83D\uDE2E" },
    { kind: "clap", emoji: "\uD83D\uDC4F" },
    { kind: "fire", emoji: "\uD83D\uDD25" },
  ];
  var REACTION_ORDER = {};
  REACTIONS.forEach(function (r, index) { REACTION_ORDER[r.kind] = index; });

  function reactionLabel(kind) {
    // Translators: names of the reactions in the live chat, read out by screen readers.
    if (kind === "like") return gettext("Like");
    if (kind === "love") return gettext("Love");
    if (kind === "haha") return gettext("Funny");
    if (kind === "wow") return gettext("Wow");
    if (kind === "clap") return gettext("Applause");
    if (kind === "fire") return gettext("Fire");
    return kind;
  }

  function reactionEmoji(kind) {
    for (var i = 0; i < REACTIONS.length; i++) if (REACTIONS[i].kind === kind) return REACTIONS[i].emoji;
    return "";
  }
  var CLIENT_KEY = "akon.live.client";

  function each(scope, selector, fn) {
    Array.prototype.forEach.call(scope.querySelectorAll(selector), fn);
  }

  function clientId() {
    var id = "";
    try {
      id = window.sessionStorage.getItem(CLIENT_KEY) || "";
    } catch (e) { /* storage blocked */ }
    if (!/^[A-Za-z0-9_-]{8,40}$/.test(id)) {
      var bytes = new Uint8Array(12);
      (window.crypto || window.msCrypto).getRandomValues(bytes);
      id = Array.prototype.map.call(bytes, function (b) { return ("0" + b.toString(16)).slice(-2); }).join("");
      try {
        window.sessionStorage.setItem(CLIENT_KEY, id);
      } catch (e) { /* storage blocked: one id per page load */ }
    }
    return id;
  }

  function csrf() {
    if (window.AkonApp && window.AkonApp.csrf) return window.AkonApp.csrf();
    var match = document.cookie.match(/csrftoken=([^;]+)/);
    return match ? match[1] : "";
  }

  function toast(text, kind) {
    if (window.AkonApp && window.AkonApp.toast) {
      window.AkonApp.toast(text, kind);
      return;
    }
    // In an embed frame, where app.js does not load: a short note at the bottom.
    if (!text || !document.body) return;
    var list = document.querySelector(".toasts");
    if (!list) {
      list = document.createElement("ul");
      list.className = "toasts";
      list.setAttribute("role", "status");
      list.setAttribute("aria-live", "polite");
      document.body.appendChild(list);
    }
    var item = document.createElement("li");
    item.className = "toast " + (kind || "info");
    var label = document.createElement("span");
    label.className = "toast-text";
    label.textContent = text;
    item.appendChild(label);
    list.appendChild(item);
    window.setTimeout(function () { item.remove(); }, kind === "error" ? 7000 : 3500);
  }

  /* The page's site, for a frame opened there (see apps/integrations). */
  function parentOrigin() {
    try {
      if (location.ancestorOrigins && location.ancestorOrigins.length) return location.ancestorOrigins[0];
    } catch (e) { /* not available */ }
    try {
      return document.referrer ? new URL(document.referrer).origin : "";
    } catch (e) {
      return "";
    }
  }

  function tellParent(message) {
    // Never anything secret: any page could be the parent.
    if (window.parent && window.parent !== window) window.parent.postMessage(message, "*");
  }

  function lang() {
    return document.documentElement.getAttribute("lang") || undefined;
  }

  function localTime(iso, style) {
    var date = new Date(iso);
    if (isNaN(date.getTime())) return "";
    var options = style === "time"
      ? { hour: "2-digit", minute: "2-digit" }
      : { dateStyle: "medium", timeStyle: "short" };
    try {
      return new Intl.DateTimeFormat(lang(), options).format(date);
    } catch (e) {
      return date.toLocaleString();
    }
  }

  /* "2 h 05 min", "4 min 12 s", "12 s" */
  function countdown(seconds) {
    var s = Math.max(0, Math.round(seconds));
    var days = Math.floor(s / 86400);
    var hours = Math.floor((s % 86400) / 3600);
    var minutes = Math.floor((s % 3600) / 60);
    var rest = s % 60;
    // Translators: countdown pieces; d = days, h = hours, min = minutes, s = seconds.
    if (days) return interpolate(gettext("%(days)s d %(hours)s h"), { days: days, hours: hours }, true);
    if (hours) return interpolate(gettext("%(hours)s h %(minutes)s min"), { hours: hours, minutes: ("0" + minutes).slice(-2) }, true);
    if (minutes) return interpolate(gettext("%(minutes)s min %(seconds)s s"), { minutes: minutes, seconds: ("0" + rest).slice(-2) }, true);
    return interpolate(gettext("%(seconds)s s"), { seconds: rest }, true);
  }

  // ----------------------------------------------------------- the page

  function LivePage(root) {
    this.root = root;
    this.mode = root.getAttribute("data-mode") || "watch";
    this.stateUrl = root.getAttribute("data-state-url");
    this.chatUrl = root.getAttribute("data-chat-url");
    this.hideUrl = root.getAttribute("data-hide-url");
    this.reactUrl = root.getAttribute("data-react-url");
    // A frame on a site with an integration: the site signs the person in.
    this.school = root.getAttribute("data-school") || "";
    this.sessionUrl = root.getAttribute("data-session-url") || "";
    this.awaiting = root.hasAttribute("data-awaiting");
    this.session = "";
    this.signingIn = false;
    this.chatVersion = -1;
    this.pendingReactions = {};
    this.canReact = false;
    this.moderator = false;
    this.slowMode = 0;
    this.mutedUntil = 0;
    this.tab = "chat";
    this.sprite = root.getAttribute("data-sprite") || "";
    this.client = clientId();
    this.lastId = 0;
    this.player = null;
    this.timer = 0;
    this.stopped = false;
    this.state = {};
    this.slot = root.querySelector("[data-live-player]");
    this.chat = root.querySelector("[data-chat]");
    this.chatList = root.querySelector("[data-chat-list]");

    var initial = root.querySelector("script[data-live-state]");
    var state = {};
    try {
      state = JSON.parse(initial ? initial.textContent : "{}") || {};
    } catch (e) { /* keep the server-rendered page as it is */ }

    this.bindChat();
    this.apply(state, true);
    this.startClock();
    this.schedule();
    if (this.school && this.sessionUrl) this.bootSchool();

    var self = this;
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden && !self.stopped) self.pollNow();
    });
  }

  LivePage.prototype.interval = function () {
    var s = this.state;
    // With the server pushing changes, polling is only a safety net.
    if (this.pushed && (s.status === "live" || s.status === "interrupted")) return POLL.pushed;
    if (s.status === "live") return POLL.live;
    if (s.status === "interrupted") return POLL.interrupted;
    if (s.status === "scheduled") {
      var at = s.scheduled_for ? new Date(s.scheduled_for).getTime() : NaN;
      return isFinite(at) && at - Date.now() < 120000 ? POLL.soon : POLL.scheduled;
    }
    var recording = s.recording;
    if (recording && (recording.state === "pending" || recording.state === "processing")) return POLL.ended;
    // Ended for good: nothing more will change.
    return 0;
  };

  LivePage.prototype.schedule = function () {
    var self = this;
    window.clearTimeout(this.timer);
    var wait = this.interval();
    if (!wait || !this.stateUrl) {
      this.stopped = true;
      return;
    }
    this.stopped = false;
    if (document.hidden) wait *= 3;
    this.timer = window.setTimeout(function () { self.poll(); }, wait);
  };

  /* The server's push channel (apps/live/stream.py). Where it is not served,
     the first answer closes it for good and the page just polls. */
  LivePage.prototype.listen = function (url) {
    if (this.events || this.eventsFailed || typeof EventSource !== "function") return;
    var self = this;
    var source = new EventSource(url);
    this.events = source;
    source.addEventListener("open", function () {
      self.pushed = true;
    });
    source.addEventListener("change", function () {
      // Spread a class's polls over half a second instead of all at once.
      window.clearTimeout(self.pushTimer);
      self.pushTimer = window.setTimeout(function () { self.pollNow(); }, 100 + Math.random() * 400);
    });
    source.addEventListener("error", function () {
      self.pushed = false;
      if (source.readyState === 2) {
        self.events = null;
        self.eventsFailed = true;
        self.schedule();
      }
    });
  };

  LivePage.prototype.pollNow = function () {
    window.clearTimeout(this.timer);
    this.poll();
  };

  LivePage.prototype.poll = function () {
    var self = this;
    var url = this.stateUrl + "?after=" + this.lastId + "&cv=" + this.chatVersion;
    // The host's control room is not an audience.
    if (this.mode !== "control") url += "&client=" + encodeURIComponent(this.client);
    fetch(url, { credentials: "same-origin", cache: "no-store", headers: this.headers() })
      .then(function (response) {
        self.checkSession(response);
        if (response.status === 404 && self.awaiting && self.session) {
          // Signed in, but the live is for other classes of the school.
          self.signInFailed(gettext("If you are in one of them, ask your school."), "classes");
        }
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.json();
      })
      .then(function (data) { self.apply(data, false); })
      .catch(function () { /* a network blip: the next poll tries again */ })
      .then(function () { self.schedule(); });
  };

  LivePage.prototype.apply = function (data, first) {
    if (!data || !data.status) return;
    if (this.awaiting) {
      // A private live, shown now that the site has signed the person in.
      this.awaiting = false;
      each(this.root, "[data-awaiting-screen]", function (node) { node.remove(); });
      each(this.root, "[data-stage-wrap]", function (node) { node.hidden = false; });
    }
    var previous = this.state.status;
    this.state = data;
    var root = this.root;
    root.setAttribute("data-status", data.status);

    each(root, "[data-live-status]", function (node) {
      node.textContent = data.label;
      node.className = node.className.replace(/\blive-status-\S+/g, "").trim() + " live-status-" + data.status;
    });

    var stage = data.on_air || this.playingRecording ? "on-air" : data.status === "scheduled" ? "scheduled" : "ended";
    each(root, "[data-stage]", function (node) {
      node.hidden = node.getAttribute("data-stage") !== stage;
    });
    each(root, "[data-when-status]", function (node) {
      node.hidden = node.getAttribute("data-when-status").split(" ").indexOf(data.status) < 0;
    });

    if (data.on_air && data.hls) {
      this.ensurePlayer(data.hls);
      if (previous === "interrupted" && data.status === "live" && this.player) this.player.reloadLive();
    } else if (!this.playingRecording) {
      this.removePlayer();
    }

    this.renderRecording(data.recording);
    if (data.events && data.on_air) this.listen(data.events);
    else if (this.events && !data.on_air) {
      this.events.close();
      this.events = null;
      this.pushed = false;
    }
    this.renderViewers(data);
    this.renderControl(data.control);
    if (data.chat) this.renderChat(data.chat, first);
    if (!first && previous && previous !== data.status) {
      root.dispatchEvent(new CustomEvent("live:status", { bubbles: true, detail: { from: previous, to: data.status } }));
    }
  };

  // ------------------------------------------------------------ player

  LivePage.prototype.ensurePlayer = function (hls) {
    if (!this.slot || (this.player && !this.player.destroyed)) return;
    var root = this.root;
    this.slot.textContent = "";
    var box = document.createElement("div");
    box.className = "player";
    box.setAttribute("data-player", "");
    box.setAttribute("data-live", "1");
    box.setAttribute("data-kind", "video");
    box.setAttribute("data-hls", hls);
    box.setAttribute("data-autoplay", "1");
    var title = root.getAttribute("data-title");
    if (title) box.setAttribute("data-title", title);
    var poster = root.getAttribute("data-poster");
    if (poster) box.setAttribute("data-poster", poster);
    if (this.mode === "embed") box.setAttribute("data-embed", "1");
    var video = document.createElement("video");
    video.className = "player-media";
    video.setAttribute("controls", "");
    video.setAttribute("playsinline", "");
    video.setAttribute("preload", "auto");
    // The studio's preview never makes noise on its own.
    if (this.mode === "control") {
      video.muted = true;
      video.setAttribute("muted", "");
    }
    box.appendChild(video);
    this.slot.appendChild(box);

    if (window.AkonPlayer) {
      window.AkonPlayer.init(this.slot);
      this.player = window.AkonPlayer.get(box);
    } else {
      video.src = hls;
    }
  };

  LivePage.prototype.removePlayer = function () {
    if (this.player) {
      this.player.destroy();
      this.player = null;
    }
    if (this.slot && this.slot.firstChild) this.slot.textContent = "";
  };

  // --------------------------------------------------------- recording

  LivePage.prototype.renderRecording = function (recording) {
    var state = recording ? recording.state : "";
    if (state === "ready" && !recording.ready) state = "none";
    each(this.root, "[data-when-recording]", function (node) {
      node.hidden = node.getAttribute("data-when-recording").split(" ").indexOf(state) < 0;
    });
    each(this.root, "[data-recording-link]", function (node) {
      if (recording && recording.url) node.setAttribute("href", recording.url);
    });
    // In another site's page the same embed code goes on to play the recording:
    // where the live was when the frame came with the play details (a school's
    // frame, chat included), or by moving to the recording's own embed.
    if (this.mode === "embed" && recording && recording.ready) {
      var play = recording.play;
      if (play && (play.hls || play.src)) this.playRecording(play);
      else if (recording.embed && !this.root.classList.contains("embed-layout")) window.location.replace(recording.embed);
    }
  };

  LivePage.prototype.playRecording = function (play) {
    if (this.playingRecording || !this.slot) return;
    this.removePlayer();
    this.playingRecording = true;
    var box = document.createElement("div");
    box.className = "player";
    box.setAttribute("data-player", "");
    box.setAttribute("data-kind", play.kind || "video");
    box.setAttribute("data-embed", "1");
    if (play.hls) box.setAttribute("data-hls", play.hls);
    if (play.src) box.setAttribute("data-src", play.src);
    if (play.poster) box.setAttribute("data-poster", play.poster);
    var title = this.root.getAttribute("data-title");
    if (title) box.setAttribute("data-title", title);
    var video = document.createElement("video");
    video.className = "player-media";
    video.setAttribute("controls", "");
    video.setAttribute("playsinline", "");
    video.setAttribute("preload", "metadata");
    if (play.poster) video.setAttribute("poster", play.poster);
    if (play.src) video.setAttribute("src", play.src);
    box.appendChild(video);
    this.slot.textContent = "";
    this.slot.appendChild(box);
    if (window.AkonPlayer) {
      window.AkonPlayer.init(this.slot);
      this.player = window.AkonPlayer.get(box);
    }
    each(this.root, "[data-stage]", function (node) {
      node.hidden = node.getAttribute("data-stage") !== "on-air";
    });
  };

  // ----------------------------------------------------------- viewers

  LivePage.prototype.renderViewers = function (data) {
    var count = data.viewers || 0;
    // Whoever has this page open is watching, even before their first poll
    // has been counted.
    if (this.mode !== "control" && data.on_air) count = Math.max(count, 1);
    each(this.root, "[data-live-viewers]", function (node) {
      // Translators: number of people watching a live stream right now.
      node.textContent = interpolate(ngettext("%(count)s watching", "%(count)s watching", count), { count: count }, true);
      node.hidden = !data.on_air;
    });
  };

  LivePage.prototype.renderControl = function (control) {
    if (!control) return;
    each(this.root, "[data-control-publishing]", function (node) {
      node.textContent = node.getAttribute(control.publishing ? "data-text-on" : "data-text-off") || "";
      node.classList.toggle("is-on", !!control.publishing);
    });
    each(this.root, "[data-control-peak]", function (node) {
      node.textContent = String(control.peak || 0);
    });
    each(this.root, "[data-control-sessions]", function (node) {
      node.textContent = String(control.sessions || 0);
    });
    each(this.root, "[data-control-error]", function (node) {
      node.textContent = control.recording_error || "";
      node.hidden = !control.recording_error;
    });
  };

  // -------------------------------------------------------------- clock

  LivePage.prototype.startClock = function () {
    var self = this;
    var tick = function () {
      var s = self.state;
      each(self.root, "[data-live-countdown]", function (node) {
        var at = s.scheduled_for ? new Date(s.scheduled_for).getTime() : NaN;
        if (s.status !== "scheduled" || !isFinite(at)) return;
        var left = (at - Date.now()) / 1000;
        node.textContent = left > 0
          // Translators: %(time)s is a countdown such as "2 h 05 min".
          ? interpolate(gettext("Starts in %(time)s"), { time: countdown(left) }, true)
          : gettext("Starting any moment now");
      });
      each(self.root, "[data-live-elapsed]", function (node) {
        var since = s.started_at ? new Date(s.started_at).getTime() : NaN;
        if (!s.on_air || !isFinite(since)) {
          node.hidden = true;
          return;
        }
        node.hidden = false;
        node.textContent = countdown((Date.now() - since) / 1000);
      });
    };
    tick();
    window.setInterval(tick, 1000);
  };

  // --------------------------------------------------------------- chat

  LivePage.prototype.icon = function (name, css) {
    return this.sprite
      ? '<svg class="icon ' + (css || "icon-sm") + '" aria-hidden="true" focusable="false"><use href="' + this.sprite + "#i-" + name + '"></use></svg>'
      : "";
  };

  LivePage.prototype.endpoint = function (template, id) {
    return template ? template.replace("/0/", "/" + encodeURIComponent(id) + "/") : "";
  };

  /* POST to the chat and resolve with the answer, or reject with its reason. */
  LivePage.prototype.post = function (url, fields, fallback) {
    var self = this;
    var data = new URLSearchParams();
    Object.keys(fields || {}).forEach(function (key) { data.set(key, fields[key]); });
    return fetch(url, { method: "POST", credentials: "same-origin", headers: this.headers(), body: data })
      .then(function (response) {
        self.checkSession(response);
        return response.json().catch(function () { return {}; }).then(function (payload) {
          if (!response.ok) throw new Error(payload.detail || fallback || gettext("Something went wrong."));
          return payload;
        });
      });
  };

  LivePage.prototype.bindChat = function () {
    var self = this;
    if (!this.chat) return;
    this.questionsList = this.chat.querySelector("[data-chat-questions]");
    var form = this.chat.querySelector("[data-chat-form]");
    if (form) {
      var field = form.querySelector("textarea");
      var counter = form.querySelector("[data-chat-left]");
      var ask = form.querySelector("[data-chat-ask]");
      var limit = field ? Number(field.getAttribute("maxlength")) || 500 : 500;
      var placeholder = field ? field.getAttribute("placeholder") : "";
      var update = function () {
        if (!counter || !field) return;
        var left = limit - field.value.length;
        counter.textContent = String(left);
        counter.classList.toggle("is-low", left < 40);
      };
      if (ask) {
        ask.addEventListener("click", function () {
          var on = ask.getAttribute("aria-pressed") !== "true";
          ask.setAttribute("aria-pressed", on ? "true" : "false");
          form.classList.toggle("is-question", on);
          if (field) field.setAttribute("placeholder", on ? field.getAttribute("data-placeholder-question") : placeholder);
          if (field) field.focus();
        });
      }
      if (field) {
        field.addEventListener("input", update);
        field.addEventListener("keydown", function (event) {
          if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
            event.preventDefault();
            if (form.requestSubmit) form.requestSubmit();
            else form.dispatchEvent(new Event("submit", { cancelable: true }));
          }
        });
      }
      form.addEventListener("submit", function (event) {
        event.preventDefault();
        self.send(form, field, update, ask);
      });
      update();
    }

    each(this.chat, "[data-chat-tab]", function (tab) {
      tab.addEventListener("click", function () { self.showTab(tab.getAttribute("data-chat-tab")); });
    });

    var slow = this.chat.querySelector("[data-slow-mode]");
    if (slow) {
      slow.addEventListener("change", function () {
        self.post(self.root.getAttribute("data-settings-url"), { slow_mode: slow.value })
          .then(function () {
            toast(Number(slow.value) ? gettext("Slow mode on.") : gettext("Slow mode off."), "success");
            self.pollNow();
          })
          .catch(function (error) { toast(error.message, "error"); });
      });
    }

    this.chat.addEventListener("click", function (event) {
      var target = event.target;
      var button;
      if ((button = target.closest("[data-react-kind]"))) {
        event.preventDefault();
        var item = button.closest(".chat-msg");
        if (item) {
          item.classList.remove("is-picking");
          self.react(item, button.getAttribute("data-react-kind"));
        }
        return;
      }
      if ((button = target.closest("[data-chat-vote]"))) {
        event.preventDefault();
        self.vote(Number(button.getAttribute("data-chat-vote")));
        return;
      }
      if ((button = target.closest("[data-react-open]"))) {
        event.preventDefault();
        var message = button.closest(".chat-msg");
        var open = message && !message.classList.contains("is-picking");
        each(self.chat, ".chat-msg.is-picking", function (node) { node.classList.remove("is-picking"); });
        if (message && open) message.classList.add("is-picking");
        button.setAttribute("aria-expanded", open ? "true" : "false");
        return;
      }
      if ((button = target.closest("[data-chat-hide]"))) {
        event.preventDefault();
        self.hide(button.getAttribute("data-chat-hide"));
        return;
      }
      if ((button = target.closest("[data-chat-pin]"))) {
        event.preventDefault();
        var pinning = button.getAttribute("data-pin-on") === "1";
        self.post(self.endpoint(self.root.getAttribute("data-pin-url"), button.getAttribute("data-chat-pin")), { on: pinning ? "1" : "0" })
          .then(function () {
            toast(pinning ? gettext("Message pinned.") : gettext("Message unpinned."), "success");
            self.pollNow();
          })
          .catch(function (error) { toast(error.message, "error"); });
        return;
      }
      if ((button = target.closest("[data-chat-answer]"))) {
        event.preventDefault();
        var done = button.getAttribute("data-answer-on") === "1";
        self.post(self.endpoint(self.root.getAttribute("data-answer-url"), button.getAttribute("data-chat-answer")), { on: done ? "1" : "0" })
          .then(function () {
            toast(done ? gettext("Question marked as answered.") : gettext("Question opened again."), "success");
            self.pollNow();
          })
          .catch(function (error) { toast(error.message, "error"); });
        return;
      }
      if ((button = target.closest("[data-chat-mute]"))) {
        event.preventDefault();
        self.post(self.endpoint(self.root.getAttribute("data-mute-url"), button.getAttribute("data-chat-mute")), { minutes: "5" })
          .then(function (payload) {
            toast(interpolate(gettext("%(name)s is muted for 5 minutes."), { name: payload.muted }, true), "success");
            self.pollNow();
          })
          .catch(function (error) { toast(error.message, "error"); });
        return;
      }
      if ((button = target.closest("[data-unmute]"))) {
        event.preventDefault();
        self.post(self.endpoint(self.root.getAttribute("data-unmute-url"), button.getAttribute("data-unmute")), {})
          .then(function () { self.pollNow(); })
          .catch(function (error) { toast(error.message, "error"); });
      }
    });
    // Tapping elsewhere closes an open reaction picker.
    document.addEventListener("click", function (event) {
      if (event.target.closest && event.target.closest(".chat-msg.is-picking")) return;
      each(self.chat, ".chat-msg.is-picking", function (node) { node.classList.remove("is-picking"); });
    });
  };

  LivePage.prototype.showTab = function (name) {
    if (!this.chat) return;
    each(this.chat, "[data-chat-tab]", function (tab) {
      var on = tab.getAttribute("data-chat-tab") === name;
      tab.classList.toggle("active", on);
      tab.setAttribute("aria-selected", on ? "true" : "false");
      tab.tabIndex = on ? 0 : -1;
    });
    this.tab = name;
    each(this.chat, "[data-chat-view]", function (view) {
      view.hidden = view.getAttribute("data-chat-view") !== name;
    });
    this.syncEmpty();
  };

  LivePage.prototype.send = function (form, field, update, ask) {
    var self = this;
    var body = field ? field.value.trim() : "";
    if (!body || this.sending) return;
    var question = ask && ask.getAttribute("aria-pressed") === "true";
    this.sending = true;
    form.classList.add("is-busy");
    this.post(this.chatUrl, { body: body, question: question ? "1" : "0" }, gettext("The message could not be sent."))
      .then(function (payload) {
        field.value = "";
        update();
        if (ask && question) ask.click();
        if (payload.message) self.addMessages([payload.message], true);
        if (question) self.pollNow();
        self.startSlowWait();
      })
      .catch(function (error) {
        toast(error.message, "error");
      })
      .then(function () {
        self.sending = false;
        form.classList.remove("is-busy");
        field.focus();
      });
  };

  /* In slow mode the send button counts down to the next message allowed. */
  LivePage.prototype.startSlowWait = function () {
    var seconds = this.moderator ? 0 : this.slowMode || 0;
    var form = this.chat && this.chat.querySelector("[data-chat-form]");
    var button = form && form.querySelector('button[type="submit"]');
    if (!seconds || !button) return;
    var label = button.innerHTML;
    var until = Date.now() + seconds * 1000;
    button.disabled = true;
    window.clearInterval(this.slowTimer);
    var tick = function () {
      var left = Math.ceil((until - Date.now()) / 1000);
      if (left <= 0) {
        window.clearInterval(this.slowTimer);
        button.disabled = false;
        button.innerHTML = label;
        return;
      }
      button.textContent = interpolate(gettext("Wait %(seconds)s s"), { seconds: left }, true);
    }.bind(this);
    tick();
    this.slowTimer = window.setInterval(tick, 500);
  };

  LivePage.prototype.hide = function (id) {
    var self = this;
    var url = this.endpoint(this.hideUrl, id);
    if (!url) return;
    this.post(url, {}, gettext("The message could not be hidden."))
      .then(function () {
        self.removeMessages([Number(id)]);
        toast(gettext("Message hidden."), "success");
        self.pollNow();
      })
      .catch(function (error) { toast(error.message, "error"); });
  };

  LivePage.prototype.renderNotes = function (chat) {
    var root = this.chat;
    var muted = this.mutedUntil && this.mutedUntil > Date.now();
    var canPost = chat.can_post && !muted;
    root.classList.toggle("is-closed", !chat.open);
    each(root, "[data-chat-when]", function (node) {
      var when = node.getAttribute("data-chat-when");
      var show = when === "can-post" ? canPost
        : when === "sign-in" ? chat.open && !chat.can_post && !muted
        : when === "closed" ? !chat.open && chat.enabled
        : when === "disabled" ? !chat.enabled
        : false;
      node.hidden = !show;
    });
    var mutedNote = root.querySelector("[data-chat-muted]");
    if (mutedNote) {
      mutedNote.hidden = !(muted && chat.open);
      if (muted) {
        mutedNote.textContent = interpolate(gettext("A moderator muted you until %(time)s."), { time: localTime(new Date(this.mutedUntil).toISOString(), "time") }, true);
      }
    }
    var slowNote = root.querySelector("[data-chat-slow]");
    if (slowNote) {
      var slow = this.slowMode || 0;
      slowNote.hidden = !(slow && canPost);
      if (slow) slowNote.textContent = interpolate(gettext("Slow mode: one message every %(seconds)s s."), { seconds: slow }, true);
    }
  };

  LivePage.prototype.renderChat = function (chat, first) {
    if (!this.chat) return;
    var self = this;
    this.canReact = !!chat.can_react;
    this.moderator = !!chat.can_moderate;
    this.slowMode = chat.slow_mode || 0;
    this.chat.classList.toggle("can-react", this.canReact);
    this.chat.classList.toggle("can-moderate", this.moderator);
    if (!chat.unchanged) {
      this.mutedUntil = chat.muted_until ? new Date(chat.muted_until).getTime() : 0;
      window.clearTimeout(this.muteTimer);
      if (this.mutedUntil > Date.now()) {
        // Let the person write again as soon as the time is up.
        this.muteTimer = window.setTimeout(function () { self.renderNotes(self.lastChat || chat); }, this.mutedUntil - Date.now() + 500);
      }
    }
    this.lastChat = chat;
    this.renderNotes(chat);
    if (typeof chat.version === "number") this.chatVersion = chat.version;
    if (chat.unchanged) return;

    this.addMessages(chat.messages || [], first);
    this.removeMessages(chat.hidden || []);
    if (chat.reactions_from) this.applyReactionWindow(chat.reactions || {}, chat.votes || {}, chat.reactions_from);
    this.renderPinned(chat.pinned);
    this.renderQuestions(chat.questions || []);
    this.renderModeration(chat);
  };

  LivePage.prototype.renderPinned = function (m) {
    var box = this.chat.querySelector("[data-chat-pinned]");
    if (!box) return;
    box.textContent = "";
    box.hidden = !m;
    if (!m) return;
    var icon = document.createElement("span");
    icon.className = "chat-pinned-icon";
    icon.innerHTML = this.icon("pin");
    var text = document.createElement("div");
    text.className = "chat-pinned-text";
    var who = document.createElement("strong");
    who.textContent = m.author;
    var body = document.createElement("span");
    body.textContent = m.body;
    text.appendChild(who);
    text.appendChild(body);
    box.appendChild(icon);
    box.appendChild(text);
    if (this.moderator) {
      var unpin = document.createElement("button");
      unpin.type = "button";
      unpin.className = "chat-pinned-off";
      unpin.setAttribute("data-chat-pin", String(m.id));
      unpin.setAttribute("data-pin-on", "0");
      unpin.setAttribute("aria-label", gettext("Unpin"));
      unpin.title = gettext("Unpin");
      unpin.innerHTML = this.icon("x");
      box.appendChild(unpin);
    }
  };

  LivePage.prototype.voteButton = function (m) {
    var vote = document.createElement("button");
    vote.type = "button";
    vote.className = "chat-vote" + (m.voted ? " is-mine" : "");
    vote.setAttribute("data-chat-vote", String(m.id));
    vote.setAttribute("aria-pressed", m.voted ? "true" : "false");
    // Translators: votes on a question in the live chat, e.g. "Votes: 4".
    var label = interpolate(gettext("Votes: %(count)s"), { count: m.votes || 0 }, true);
    vote.setAttribute("aria-label", label);
    vote.title = this.canReact ? gettext("Vote for this question") : label;
    if (!this.canReact) vote.setAttribute("aria-disabled", "true");
    vote.innerHTML = this.icon("vote") + '<span class="chat-vote-count"></span>';
    vote.querySelector(".chat-vote-count").textContent = String(m.votes || 0);
    return vote;
  };

  LivePage.prototype.renderQuestions = function (questions) {
    var list = this.questionsList;
    if (!list) return;
    var self = this;
    this.questions = questions;
    list.textContent = "";
    questions.forEach(function (q) {
      var item = document.createElement("li");
      item.className = "chat-question" + (q.mine ? " is-mine" : "");
      item.setAttribute("data-question", String(q.id));
      var head = document.createElement("div");
      head.className = "chat-meta";
      var author = document.createElement("strong");
      author.className = "chat-author";
      author.textContent = q.author;
      head.appendChild(author);
      if (q.staff) head.appendChild(self.staffBadge());
      var body = document.createElement("p");
      body.className = "chat-body";
      body.textContent = q.body;
      var main = document.createElement("div");
      main.className = "chat-main";
      main.appendChild(head);
      main.appendChild(body);
      item.appendChild(self.voteButton(q));
      item.appendChild(main);
      if (self.moderator) {
        var done = document.createElement("button");
        done.type = "button";
        done.className = "btn btn-sm chat-answer-btn";
        done.setAttribute("data-chat-answer", String(q.id));
        done.setAttribute("data-answer-on", "1");
        done.innerHTML = self.icon("check");
        done.appendChild(document.createTextNode(gettext("Answered")));
        item.appendChild(done);
      }
      list.appendChild(item);
    });
    each(this.chat, "[data-questions-count]", function (node) {
      node.textContent = String(questions.length);
      node.hidden = !questions.length;
    });
    this.syncEmpty();
  };

  LivePage.prototype.renderModeration = function (chat) {
    var box = this.chat.querySelector("[data-chat-mod]");
    if (!box) return;
    box.hidden = !this.moderator;
    if (!this.moderator) return;
    var slow = box.querySelector("[data-slow-mode]");
    if (slow && document.activeElement !== slow) slow.value = String(chat.slow_mode || 0);
    var list = box.querySelector("[data-muted-list]");
    if (!list) return;
    list.textContent = "";
    var muted = chat.muted || [];
    if (!muted.length) {
      var none = document.createElement("li");
      none.className = "muted";
      none.textContent = gettext("Nobody right now.");
      list.appendChild(none);
    }
    var self = this;
    muted.forEach(function (m) {
      var item = document.createElement("li");
      var name = document.createElement("span");
      name.textContent = interpolate(gettext("%(name)s, until %(time)s"), { name: m.name, time: localTime(m.until, "time") }, true);
      var free = document.createElement("button");
      free.type = "button";
      free.className = "btn btn-ghost btn-sm";
      free.setAttribute("data-unmute", String(m.id));
      free.textContent = gettext("Unmute");
      item.appendChild(name);
      item.appendChild(free);
      list.appendChild(item);
    });
  };

  LivePage.prototype.staffBadge = function () {
    var badge = document.createElement("span");
    badge.className = "chat-host";
    // Translators: badge next to the names of the host and the moderators in the live chat.
    badge.textContent = this.root.getAttribute("data-staff-label") || gettext("Streaming");
    return badge;
  };

  // ------------------------------------------------------ site sign-in

  LivePage.prototype.headers = function () {
    var headers = { Accept: "application/json", "X-CSRFToken": csrf() };
    if (this.session) headers.Authorization = "Bearer " + this.session;
    return headers;
  };

  LivePage.prototype.checkSession = function (response) {
    if (!this.session || response.headers.get("X-QQplay-Session") !== "expired") return;
    this.session = "";
    this.showWho(null);
    tellParent({ type: "qqplay:token-needed" });
  };

  LivePage.prototype.bootSchool = function () {
    var self = this;
    if (this.awaiting) each(this.root, "[data-chat-when]", function (node) { node.hidden = true; });
    window.addEventListener("message", function (event) {
      if (event.source !== window.parent || !event.data || event.data.type !== "qqplay:token") return;
      self.signIn(String(event.data.token || ""), event.origin);
    });
    // A plain frame may carry the token in its address instead.
    var match = window.location.search.match(/[?&]token=([^&]+)/);
    if (match) this.signIn(decodeURIComponent(match[1]), parentOrigin());
    if (window.parent !== window) tellParent({ type: "qqplay:ready" });
    else if (!match) this.signInFailed(gettext("Open this live from your school's site."), "frame");
  };

  LivePage.prototype.signIn = function (token, origin) {
    if (!token || this.signingIn) return;
    var self = this;
    this.signingIn = true;
    fetch(this.sessionUrl, {
      method: "POST",
      credentials: "omit",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ token: token, origin: origin || "" }),
    })
      .then(function (response) {
        return response.json().catch(function () { return {}; }).then(function (payload) {
          if (!response.ok) {
            var error = new Error(payload.detail || gettext("You could not be signed in."));
            error.code = payload.code || "";
            throw error;
          }
          return payload;
        });
      })
      .then(function (payload) {
        self.session = payload.session;
        self.showWho(payload.user);
        self.resetChat();
        tellParent({ type: "qqplay:signed-in", name: (payload.user && payload.user.name) || "" });
        self.pollNow();
      })
      .catch(function (error) {
        self.signInFailed(error.message, error.code || "network");
        tellParent({ type: "qqplay:error", message: error.message, code: error.code || "network" });
      })
      .then(function () { self.signingIn = false; });
  };

  LivePage.prototype.signInFailed = function (message, code) {
    each(this.root, "[data-school-note]", function (node) { node.textContent = message; });
    if (this.awaiting) {
      each(this.root, "[data-awaiting-title]", function (node) {
        node.textContent = code === "classes"
          ? gettext("This live is for other classes")
          : gettext("This live is only for the school's people");
      });
      each(this.root, "[data-awaiting-text]", function (node) { node.textContent = message; });
    }
  };

  LivePage.prototype.showWho = function (user) {
    each(this.root, "[data-chat-who]", function (node) {
      node.textContent = "";
      node.hidden = !user;
      if (!user) return;
      var face = document.createElement("span");
      face.innerHTML = user.avatar || ""; // built and escaped by the server
      var name = document.createElement("strong");
      name.textContent = user.name || "";
      node.appendChild(face);
      node.appendChild(name);
      node.title = user.moderator
        ? interpolate(gettext("Signed in as %(name)s, moderator"), { name: user.name }, true)
        : interpolate(gettext("Signed in as %(name)s"), { name: user.name }, true);
    });
  };

  /* Start the chat over as the person now signed in: their own messages
     and reactions are marked from the server's answer. */
  LivePage.prototype.resetChat = function () {
    if (this.chatList) this.chatList.textContent = "";
    this.lastId = 0;
    this.chatVersion = -1;
    this.pendingReactions = {};
  };

  // ----------------------------------------------------------- reactions

  LivePage.prototype.renderReactions = function (item, list, pop) {
    var box = item.querySelector("[data-reactions]");
    if (!box) return;
    var before = {};
    (item.__reactions || []).forEach(function (r) { before[r.kind] = r.count; });
    item.__reactions = list.map(function (r) { return { kind: r.kind, count: r.count, mine: !!r.mine }; });
    box.textContent = "";
    box.hidden = !list.length;
    var canReact = this.canReact;
    item.__reactions.forEach(function (r) {
      var chip = document.createElement("button");
      chip.type = "button";
      chip.className = "chat-chip" + (r.mine ? " is-mine" : "");
      chip.setAttribute("data-react-kind", r.kind);
      chip.setAttribute("aria-pressed", r.mine ? "true" : "false");
      if (!canReact) chip.setAttribute("aria-disabled", "true");
      var label = reactionLabel(r.kind);
      // Translators: a reaction under a live chat message, e.g. "Like: 3".
      var text = interpolate(gettext("%(reaction)s: %(count)s"), { reaction: label, count: r.count }, true);
      chip.setAttribute("aria-label", text);
      chip.title = text;
      var emoji = document.createElement("span");
      emoji.className = "chat-chip-emoji";
      emoji.setAttribute("aria-hidden", "true");
      emoji.textContent = reactionEmoji(r.kind);
      var count = document.createElement("span");
      count.className = "chat-chip-count";
      count.setAttribute("aria-hidden", "true");
      count.textContent = String(r.count);
      chip.appendChild(emoji);
      chip.appendChild(count);
      if (r.kind === pop || (before[r.kind] !== undefined && r.count > before[r.kind])) chip.classList.add("is-pop");
      box.appendChild(chip);
    });
  };

  LivePage.prototype.applyReactionWindow = function (map, votes, from) {
    if (!this.chatList) return;
    var self = this;
    Array.prototype.forEach.call(this.chatList.children, function (item) {
      var id = Number(item.getAttribute("data-id"));
      if (!id || id < from || self.pendingReactions[id]) return;
      var next = map[String(id)] || [];
      var now = item.__reactions || [];
      var same = next.length === now.length && next.every(function (r, i) {
        return r.kind === now[i].kind && r.count === now[i].count && !!r.mine === now[i].mine;
      });
      if (!same) self.renderReactions(item, next);
      var vote = votes[String(id)];
      if (vote) {
        self.setVotes(id, vote.votes, vote.voted);
        self.setAnswered(item, !!vote.answered);
      }
    });
  };

  /* A question on screen answered (or opened again) by a moderator since. */
  LivePage.prototype.setAnswered = function (item, answered) {
    if (item.classList.contains("is-answered") === answered) return;
    item.classList.toggle("is-answered", answered);
    var tag = item.querySelector(".chat-tag");
    // Translators: tag on a live chat message sent as a question for the host.
    if (tag) tag.textContent = answered ? gettext("Answered") : gettext("Question");
    each(item, "[data-chat-answer]", function (button) { button.hidden = answered; });
  };

  LivePage.prototype.react = function (item, kind) {
    if (!this.canReact) {
      toast(gettext("Sign in to react."), "info");
      return;
    }
    var id = Number(item.getAttribute("data-id"));
    var url = this.endpoint(this.reactUrl, id);
    if (!id || !url) return;
    var self = this;
    var previous = (item.__reactions || []).map(function (r) { return { kind: r.kind, count: r.count, mine: r.mine }; });
    var list = previous.map(function (r) { return { kind: r.kind, count: r.count, mine: r.mine }; });
    var entry = null;
    list.forEach(function (r) { if (r.kind === kind) entry = r; });
    var on = !(entry && entry.mine);
    // Shown at once; the answer then sets the real counts.
    if (on) {
      if (entry) { entry.count += 1; entry.mine = true; }
      else list.push({ kind: kind, count: 1, mine: true });
    } else if (entry) {
      entry.count -= 1;
      entry.mine = false;
      if (entry.count <= 0) list = list.filter(function (r) { return r !== entry; });
    }
    list.sort(function (a, b) { return REACTION_ORDER[a.kind] - REACTION_ORDER[b.kind]; });
    this.renderReactions(item, list, on ? kind : "");

    this.pendingReactions[id] = (this.pendingReactions[id] || 0) + 1;
    this.post(url, { kind: kind, on: on ? "1" : "0" }, gettext("The reaction could not be saved."))
      .then(function (payload) {
        self.pendingReactions[id] -= 1;
        if (!self.pendingReactions[id] && document.contains(item)) self.renderReactions(item, payload.reactions || []);
      })
      .catch(function (error) {
        self.pendingReactions[id] -= 1;
        if (document.contains(item)) self.renderReactions(item, previous);
        toast(error.message, "error");
      });
  };

  /* Every vote button of a question, in the chat and in the questions list. */
  LivePage.prototype.setVotes = function (id, votes, voted) {
    var self = this;
    each(this.chat, '[data-chat-vote="' + id + '"]', function (button) {
      button.classList.toggle("is-mine", !!voted);
      button.setAttribute("aria-pressed", voted ? "true" : "false");
      var label = interpolate(gettext("Votes: %(count)s"), { count: votes }, true);
      button.setAttribute("aria-label", label);
      if (!self.canReact) button.title = label;
      var count = button.querySelector(".chat-vote-count");
      if (count) count.textContent = String(votes);
    });
  };

  LivePage.prototype.vote = function (id) {
    if (!this.canReact) {
      toast(gettext("Sign in to vote."), "info");
      return;
    }
    var button = this.chat.querySelector('[data-chat-vote="' + id + '"]');
    if (!button) return;
    var self = this;
    var voted = button.getAttribute("aria-pressed") === "true";
    var count = Number((button.querySelector(".chat-vote-count") || {}).textContent) || 0;
    this.setVotes(id, Math.max(0, count + (voted ? -1 : 1)), !voted);
    this.post(this.endpoint(this.reactUrl, id), { kind: "up", on: voted ? "0" : "1" }, gettext("The vote could not be saved."))
      .then(function (payload) { self.setVotes(id, payload.votes || 0, !!payload.voted); })
      .catch(function (error) {
        self.setVotes(id, count, voted);
        toast(error.message, "error");
      });
  };

  LivePage.prototype.toolButton = function (attrs, icon, label, css) {
    var button = document.createElement("button");
    button.type = "button";
    button.className = css || "chat-hide";
    Object.keys(attrs).forEach(function (key) { button.setAttribute(key, attrs[key]); });
    button.setAttribute("aria-label", label);
    button.title = label;
    button.innerHTML = this.icon(icon) || "•";
    return button;
  };

  LivePage.prototype.messageNode = function (m) {
    var item = document.createElement("li");
    item.className = "chat-msg" + (m.host ? " is-host" : "") + (m.staff ? " is-staff" : "") +
      (m.mine ? " is-mine" : "") + (m.question ? " is-question" : "") + (m.answered ? " is-answered" : "");
    item.setAttribute("data-id", String(m.id));
    var face = document.createElement("span");
    face.className = "chat-face";
    face.innerHTML = m.avatar || ""; // built and escaped by the server
    var main = document.createElement("div");
    main.className = "chat-main";
    var meta = document.createElement("div");
    meta.className = "chat-meta";
    var author = document.createElement("strong");
    author.className = "chat-author";
    author.textContent = m.author;
    meta.appendChild(author);
    if (m.staff) meta.appendChild(this.staffBadge());
    if (m.question) {
      var tag = document.createElement("span");
      tag.className = "chat-tag";
      // Translators: tag on a live chat message sent as a question for the host.
      tag.textContent = m.answered ? gettext("Answered") : gettext("Question");
      meta.appendChild(tag);
    }
    var time = document.createElement("time");
    time.className = "chat-time";
    time.setAttribute("datetime", m.at);
    time.textContent = localTime(m.at, "time");
    meta.appendChild(time);
    var body = document.createElement("p");
    body.className = "chat-body";
    body.textContent = m.body;
    var reactions = document.createElement("div");
    reactions.className = "chat-reactions";
    reactions.setAttribute("data-reactions", "");
    reactions.hidden = true;
    main.appendChild(meta);
    main.appendChild(body);
    if (m.question) {
      var line = document.createElement("div");
      line.className = "chat-question-line";
      line.appendChild(this.voteButton(m));
      main.appendChild(line);
    }
    main.appendChild(reactions);
    item.appendChild(face);
    item.appendChild(main);

    // Hovering (or focusing) a message shows its tools: the reactions, and
    // for moderators pinning, answering, muting and hiding. On a touch
    // screen a button opens them.
    var tools = document.createElement("div");
    tools.className = "chat-tools";
    var picker = document.createElement("span");
    picker.className = "chat-picker";
    REACTIONS.forEach(function (r) {
      var pick = document.createElement("button");
      pick.type = "button";
      pick.className = "chat-pick";
      pick.setAttribute("data-react-kind", r.kind);
      var label = reactionLabel(r.kind);
      // Translators: button that reacts to a live chat message, e.g. "React: Like".
      var text = interpolate(gettext("React: %(reaction)s"), { reaction: label }, true);
      pick.setAttribute("aria-label", text);
      pick.title = label;
      pick.textContent = r.emoji;
      picker.appendChild(pick);
    });
    tools.appendChild(picker);
    if (m.can_hide) {
      tools.appendChild(this.toolButton({ "data-chat-pin": String(m.id), "data-pin-on": "1" }, "pin", gettext("Pin over the chat")));
      if (m.question && !m.answered) {
        tools.appendChild(this.toolButton({ "data-chat-answer": String(m.id), "data-answer-on": "1" }, "check", gettext("Mark as answered")));
      }
      if (!m.staff) {
        tools.appendChild(this.toolButton({ "data-chat-mute": String(m.id) }, "mute", gettext("Mute the author for 5 minutes")));
      }
      // Translators: moderation button on a live chat message.
      tools.appendChild(this.toolButton({ "data-chat-hide": String(m.id) }, "eye-off", gettext("Hide message")));
    }
    item.appendChild(tools);
    var open = document.createElement("button");
    open.type = "button";
    open.className = "chat-react-open";
    open.setAttribute("data-react-open", "");
    open.setAttribute("aria-expanded", "false");
    // Translators: button on a touch screen that shows the reactions of a live chat message.
    open.setAttribute("aria-label", gettext("React to this message"));
    open.innerHTML = this.icon("smile-plus") || "+";
    item.appendChild(open);
    if (m.reactions && m.reactions.length) this.renderReactions(item, m.reactions);
    return item;
  };

  LivePage.prototype.addMessages = function (messages, forceScroll) {
    if (!this.chatList || !messages.length) {
      this.syncEmpty();
      return;
    }
    var list = this.chatList;
    var nearBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 80;
    var self = this;
    messages.forEach(function (m) {
      if (m.id > self.lastId) self.lastId = m.id;
      var existing = list.querySelector('[data-id="' + m.id + '"]');
      if (existing) {
        if (m.reactions && !self.pendingReactions[m.id]) self.renderReactions(existing, m.reactions);
        return;
      }
      list.appendChild(self.messageNode(m));
    });
    // Keep the list bounded on very busy chats.
    while (list.children.length > 300) list.removeChild(list.firstChild);
    if (forceScroll || nearBottom) list.scrollTop = list.scrollHeight;
    this.syncEmpty();
  };

  LivePage.prototype.removeMessages = function (ids) {
    if (!this.chatList || !ids.length) return;
    var list = this.chatList;
    ids.forEach(function (id) {
      var node = list.querySelector('[data-id="' + id + '"]');
      if (node) node.remove();
    });
    this.syncEmpty();
  };

  LivePage.prototype.syncEmpty = function () {
    if (!this.chat) return;
    var questions = this.tab === "questions";
    var empty = !this.chatList || !this.chatList.children.length;
    each(this.chat, "[data-chat-empty]", function (node) {
      node.hidden = questions || !empty;
    });
    var noQuestions = !this.questionsList || !this.questionsList.children.length;
    each(this.chat, "[data-questions-empty]", function (node) {
      node.hidden = !questions || !noQuestions;
    });
  };

  // ------------------------------------------------------- page helpers

  function formatLocalTimes(scope) {
    each(scope, "time[data-local]", function (node) {
      var text = localTime(node.getAttribute("datetime"), node.getAttribute("data-local"));
      if (text) node.textContent = text;
    });
  }

  /* datetime-local fields are read in the browser's time zone and sent as an
     ISO timestamp next to the field, so 19:00 means 19:00 where the person is. */
  function bindScheduleFields(scope) {
    each(scope, "input[type=datetime-local][data-utc]", function (input) {
      var form = input.form;
      var hidden = form ? form.querySelector('input[name="' + input.getAttribute("data-iso-field") + '"]') : null;
      var utc = input.getAttribute("data-utc");
      if (utc) {
        var date = new Date(utc);
        if (!isNaN(date.getTime())) {
          var local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
          input.value = local.toISOString().slice(0, 16);
        }
      }
      if (!form || !hidden) return;
      form.addEventListener("submit", function () {
        var value = input.value ? new Date(input.value) : null;
        hidden.value = value && !isNaN(value.getTime()) ? value.toISOString() : "";
      });
    });
  }

  function bindReveal(scope) {
    each(scope, "[data-reveal]", function (button) {
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
  }

  function boot() {
    formatLocalTimes(document);
    bindScheduleFields(document);
    bindReveal(document);
    each(document, "[data-live]", function (root) {
      if (root.__livePage) return;
      root.__livePage = new LivePage(root);
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
