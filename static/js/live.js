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

  var POLL = { live: 4000, interrupted: 3000, scheduled: 10000, soon: 4000, ended: 8000 };
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
    if (window.AkonApp && window.AkonApp.toast) window.AkonApp.toast(text, kind);
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
    this.reactionsRev = -1;
    this.pendingReactions = {};
    this.canReact = false;
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

    var self = this;
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden && !self.stopped) self.pollNow();
    });
  }

  LivePage.prototype.interval = function () {
    var s = this.state;
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
    if (document.hidden) wait *= 3;
    this.timer = window.setTimeout(function () { self.poll(); }, wait);
  };

  LivePage.prototype.pollNow = function () {
    window.clearTimeout(this.timer);
    this.poll();
  };

  LivePage.prototype.poll = function () {
    var self = this;
    var url = this.stateUrl + "?after=" + this.lastId + "&rrev=" + this.reactionsRev;
    // The host's control room is not an audience.
    if (this.mode !== "control") url += "&client=" + encodeURIComponent(this.client);
    fetch(url, { credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" } })
      .then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.json();
      })
      .then(function (data) { self.apply(data, false); })
      .catch(function () { /* a network blip: the next poll tries again */ })
      .then(function () { self.schedule(); });
  };

  LivePage.prototype.apply = function (data, first) {
    if (!data || !data.status) return;
    var previous = this.state.status;
    this.state = data;
    var root = this.root;
    root.setAttribute("data-status", data.status);

    each(root, "[data-live-status]", function (node) {
      node.textContent = data.label;
      node.className = node.className.replace(/\blive-status-\S+/g, "").trim() + " live-status-" + data.status;
    });

    var stage = data.on_air ? "on-air" : data.status === "scheduled" ? "scheduled" : "ended";
    each(root, "[data-stage]", function (node) {
      node.hidden = node.getAttribute("data-stage") !== stage;
    });
    each(root, "[data-when-status]", function (node) {
      node.hidden = node.getAttribute("data-when-status").split(" ").indexOf(data.status) < 0;
    });

    if (data.on_air && data.hls) {
      this.ensurePlayer(data.hls);
      if (previous === "interrupted" && data.status === "live" && this.player) this.player.reloadLive();
    } else {
      this.removePlayer();
    }

    this.renderRecording(data.recording);
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
    // In another site's page the same embed code goes on to play the recording.
    if (this.mode === "embed" && recording && recording.ready && recording.embed) {
      window.location.replace(recording.embed);
    }
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

  LivePage.prototype.bindChat = function () {
    var self = this;
    if (!this.chat) return;
    var form = this.chat.querySelector("[data-chat-form]");
    if (form) {
      var field = form.querySelector("textarea");
      var counter = form.querySelector("[data-chat-left]");
      var limit = field ? Number(field.getAttribute("maxlength")) || 500 : 500;
      var update = function () {
        if (!counter || !field) return;
        var left = limit - field.value.length;
        counter.textContent = String(left);
        counter.classList.toggle("is-low", left < 40);
      };
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
        self.send(form, field, update);
      });
      update();
    }
    this.chat.addEventListener("click", function (event) {
      var reaction = event.target.closest("[data-react-kind]");
      if (reaction) {
        event.preventDefault();
        var item = reaction.closest(".chat-msg");
        if (item) {
          item.classList.remove("is-picking");
          self.react(item, reaction.getAttribute("data-react-kind"));
        }
        return;
      }
      var opener = event.target.closest("[data-react-open]");
      if (opener) {
        event.preventDefault();
        var message = opener.closest(".chat-msg");
        var open = message && !message.classList.contains("is-picking");
        each(self.chat, ".chat-msg.is-picking", function (node) { node.classList.remove("is-picking"); });
        if (message && open) message.classList.add("is-picking");
        opener.setAttribute("aria-expanded", open ? "true" : "false");
        return;
      }
      var button = event.target.closest("[data-chat-hide]");
      if (!button) return;
      event.preventDefault();
      self.hide(button.getAttribute("data-chat-hide"));
    });
    // Tapping elsewhere closes an open reaction picker.
    document.addEventListener("click", function (event) {
      if (event.target.closest && event.target.closest(".chat-msg.is-picking")) return;
      each(self.chat, ".chat-msg.is-picking", function (node) { node.classList.remove("is-picking"); });
    });
  };

  LivePage.prototype.send = function (form, field, update) {
    var self = this;
    var body = field ? field.value.trim() : "";
    if (!body || this.sending) return;
    this.sending = true;
    form.classList.add("is-busy");
    var data = new URLSearchParams();
    data.set("body", body);
    fetch(this.chatUrl, {
      method: "POST",
      credentials: "same-origin",
      headers: { Accept: "application/json", "X-CSRFToken": csrf() },
      body: data,
    })
      .then(function (response) {
        return response.json().catch(function () { return {}; }).then(function (payload) {
          if (!response.ok) throw new Error(payload.detail || gettext("The message could not be sent."));
          return payload;
        });
      })
      .then(function (payload) {
        field.value = "";
        update();
        if (payload.message) self.addMessages([payload.message], true);
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

  LivePage.prototype.hide = function (id) {
    var self = this;
    var url = this.hideUrl ? this.hideUrl.replace("/0/hide/", "/" + encodeURIComponent(id) + "/hide/") : "";
    if (!url) return;
    fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { Accept: "application/json", "X-CSRFToken": csrf() },
    })
      .then(function (response) {
        if (!response.ok) throw new Error();
        self.removeMessages([Number(id)]);
        toast(gettext("Message hidden."), "success");
      })
      .catch(function () {
        toast(gettext("The message could not be hidden."), "error");
      });
  };

  LivePage.prototype.renderChat = function (chat, first) {
    if (!this.chat) return;
    var root = this.chat;
    root.classList.toggle("is-closed", !chat.open);
    each(root, "[data-chat-when]", function (node) {
      var when = node.getAttribute("data-chat-when");
      var show = when === "can-post" ? chat.can_post
        : when === "sign-in" ? chat.open && !chat.can_post
        : when === "closed" ? !chat.open && chat.enabled
        : when === "disabled" ? !chat.enabled
        : false;
      node.hidden = !show;
    });
    this.canReact = !!chat.can_react;
    root.classList.toggle("can-react", this.canReact);
    this.addMessages(chat.messages || [], first);
    this.removeMessages(chat.hidden || []);
    if (chat.reactions_from) this.applyReactionWindow(chat.reactions || {}, chat.reactions_from);
    if (typeof chat.reactions_rev === "number") this.reactionsRev = chat.reactions_rev;
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

  LivePage.prototype.applyReactionWindow = function (map, from) {
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
    });
  };

  LivePage.prototype.react = function (item, kind) {
    if (!this.canReact) {
      toast(gettext("Sign in to react."), "info");
      return;
    }
    var id = Number(item.getAttribute("data-id"));
    var url = this.reactUrl ? this.reactUrl.replace("/0/react/", "/" + id + "/react/") : "";
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
    var data = new URLSearchParams();
    data.set("kind", kind);
    data.set("on", on ? "1" : "0");
    fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { Accept: "application/json", "X-CSRFToken": csrf() },
      body: data,
    })
      .then(function (response) {
        return response.json().catch(function () { return {}; }).then(function (payload) {
          if (!response.ok) throw new Error(payload.detail || gettext("The reaction could not be saved."));
          return payload;
        });
      })
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

  LivePage.prototype.messageNode = function (m) {
    var item = document.createElement("li");
    item.className = "chat-msg" + (m.host ? " is-host" : "") + (m.mine ? " is-mine" : "");
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
    if (m.host) {
      var badge = document.createElement("span");
      badge.className = "chat-host";
      // Translators: badge next to the name of the person streaming, in the live chat.
      badge.textContent = gettext("Host");
      meta.appendChild(badge);
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
    main.appendChild(reactions);
    item.appendChild(face);
    item.appendChild(main);

    // Hovering (or focusing) a message shows its tools: the reactions, and
    // for moderators the button that hides it. On a touch screen a button
    // opens them.
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
      var hide = document.createElement("button");
      hide.type = "button";
      hide.className = "chat-hide";
      hide.setAttribute("data-chat-hide", String(m.id));
      // Translators: moderation button on a live chat message.
      hide.setAttribute("aria-label", gettext("Hide message"));
      hide.setAttribute("title", gettext("Hide message"));
      hide.innerHTML = this.sprite
        ? '<svg class="icon icon-sm" aria-hidden="true" focusable="false"><use href="' + this.sprite + '#i-eye-off"></use></svg>'
        : "×";
      tools.appendChild(hide);
    }
    item.appendChild(tools);
    var open = document.createElement("button");
    open.type = "button";
    open.className = "chat-react-open";
    open.setAttribute("data-react-open", "");
    open.setAttribute("aria-expanded", "false");
    // Translators: button on a touch screen that shows the reactions of a live chat message.
    open.setAttribute("aria-label", gettext("React to this message"));
    open.innerHTML = this.sprite
      ? '<svg class="icon icon-sm" aria-hidden="true" focusable="false"><use href="' + this.sprite + '#i-smile-plus"></use></svg>'
      : "+";
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
    var empty = !this.chatList || !this.chatList.children.length;
    each(this.chat, "[data-chat-empty]", function (node) {
      node.hidden = !empty;
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
