/* A video of the library in a site's frame, with its comments.
 *
 * The site signs the person in the same way as for the lives (apps/
 * integrations): the frame says it is ready, the site's script sends the
 * token in a message, and the frame trades it for a session it sends in a
 * header. Then the person comments and replies under the site's name and
 * picture, optionally at the moment of the video they are watching, and the
 * site's teachers can hide comments. A private video waits for the sign-in
 * before it plays.
 */
(function () {
  "use strict";

  var root = document.querySelector("[data-media-embed]");
  if (!root) return;

  var school = root.getAttribute("data-school") || "";
  var sessionUrl = root.getAttribute("data-session-url") || "";
  var commentsUrl = root.getAttribute("data-comments-url") || "";
  var hideUrl = root.getAttribute("data-hide-url") || "";
  var infoUrl = root.getAttribute("data-info-url") || "";
  var sprite = root.getAttribute("data-sprite") || "";
  var staffLabel = root.getAttribute("data-staff-label") || gettext("Streaming");
  var panel = root.querySelector("[data-comments]");
  var list = panel && panel.querySelector("[data-comment-list]");
  var form = panel && panel.querySelector("[data-comment-form]");
  var session = "";
  var replyTo = "";
  var signingIn = false;
  var pollTimer = 0;
  var POLL = 20000;

  function each(selector, fn) {
    Array.prototype.forEach.call(root.querySelectorAll(selector), fn);
  }

  function tellParent(message) {
    if (window.parent && window.parent !== window) window.parent.postMessage(message, "*");
  }

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

  function headers(json) {
    var out = { Accept: "application/json" };
    if (json) out["Content-Type"] = "application/json";
    if (session) out.Authorization = "Bearer " + session;
    return out;
  }

  function checkSession(response) {
    if (session && response.headers.get("X-QQplay-Session") === "expired") {
      session = "";
      showWho(null);
      tellParent({ type: "qqplay:token-needed" });
    }
  }

  function note(text, kind) {
    var box = document.querySelector(".toasts");
    if (!box) {
      box = document.createElement("ul");
      box.className = "toasts";
      box.setAttribute("role", "status");
      document.body.appendChild(box);
    }
    var item = document.createElement("li");
    item.className = "toast " + (kind || "info");
    var label = document.createElement("span");
    label.className = "toast-text";
    label.textContent = text;
    item.appendChild(label);
    box.appendChild(item);
    window.setTimeout(function () { item.remove(); }, kind === "error" ? 7000 : 3500);
  }

  function clock(seconds) {
    var s = Math.max(0, Math.floor(seconds));
    var h = Math.floor(s / 3600);
    var m = Math.floor((s % 3600) / 60);
    var r = ("0" + (s % 60)).slice(-2);
    return h ? h + ":" + ("0" + m).slice(-2) + ":" + r : m + ":" + r;
  }

  function video() {
    return root.querySelector("video");
  }

  function localTime(iso) {
    var date = new Date(iso);
    if (isNaN(date.getTime())) return "";
    return date.toLocaleString(document.documentElement.lang || undefined, { dateStyle: "short", timeStyle: "short" });
  }

  // ---------------------------------------------------------------- player

  function playPrivate(info) {
    var stage = root.querySelector("[data-embed-stage]");
    if (!stage || !info) return;
    stage.textContent = "";
    var box = document.createElement("div");
    box.className = "player";
    box.setAttribute("data-player", "");
    box.setAttribute("data-embed", "1");
    box.setAttribute("data-kind", info.kind || "video");
    if (info.hls) box.setAttribute("data-hls", info.hls);
    if (info.src) box.setAttribute("data-src", info.src);
    if (info.poster) box.setAttribute("data-poster", info.poster);
    var media = document.createElement("video");
    media.className = "player-media";
    media.setAttribute("controls", "");
    media.setAttribute("playsinline", "");
    media.setAttribute("preload", "metadata");
    if (info.poster) media.setAttribute("poster", info.poster);
    if (info.src) media.setAttribute("src", info.src);
    box.appendChild(media);
    stage.appendChild(box);
    if (window.AkonPlayer) window.AkonPlayer.init(stage);
  }

  // -------------------------------------------------------------- sign-in

  function showWho(user) {
    each("[data-chat-who]", function (node) {
      node.textContent = "";
      node.hidden = !user;
      if (!user) return;
      var face = document.createElement("span");
      face.innerHTML = user.avatar || ""; // built and escaped by the server
      var name = document.createElement("strong");
      name.textContent = user.name || "";
      node.appendChild(face);
      node.appendChild(name);
    });
  }

  function signInFailed(message, code) {
    each("[data-comments-signin]", function (node) { node.textContent = message; });
    if (root.hasAttribute("data-awaiting")) {
      each("[data-awaiting-title]", function (node) {
        node.textContent = code === "classes"
          ? gettext("This video is for other classes")
          : gettext("This video is only for the school's people");
      });
      each("[data-awaiting-text]", function (node) { node.textContent = message; });
    }
  }

  function signIn(token, origin) {
    if (!token || signingIn || !sessionUrl) return;
    signingIn = true;
    fetch(sessionUrl, {
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
        session = payload.session;
        showWho(payload.user);
        tellParent({ type: "qqplay:signed-in", name: (payload.user && payload.user.name) || "" });
        if (root.hasAttribute("data-awaiting") && infoUrl) {
          return fetch(infoUrl, { headers: headers() })
            .then(function (response) { return response.ok ? response.json() : null; })
            .then(function (info) {
              if (info) {
                root.removeAttribute("data-awaiting");
                playPrivate(info);
              } else {
                // Signed in, but the video is a recording for other classes.
                signInFailed(gettext("If you are in one of them, ask your school."), "classes");
              }
            });
        }
      })
      .then(function () { load(); })
      .catch(function (error) {
        signInFailed(error.message);
        tellParent({ type: "qqplay:error", message: error.message, code: error.code || "network" });
      })
      .then(function () { signingIn = false; });
  }

  if (school && sessionUrl) {
    window.addEventListener("message", function (event) {
      if (event.source !== window.parent || !event.data || event.data.type !== "qqplay:token") return;
      signIn(String(event.data.token || ""), event.origin);
    });
    var match = window.location.search.match(/[?&]token=([^&]+)/);
    if (match) signIn(decodeURIComponent(match[1]), parentOrigin());
    if (window.parent !== window) tellParent({ type: "qqplay:ready" });
  }

  // ------------------------------------------------------------- comments

  function icon(name) {
    return sprite ? '<svg class="icon icon-sm" aria-hidden="true" focusable="false"><use href="' + sprite + "#i-" + name + '"></use></svg>' : "";
  }

  function commentNode(c, isReply) {
    var item = document.createElement("li");
    item.className = "comment-item" + (isReply ? " is-reply" : "") + (c.mine ? " is-mine" : "");
    item.setAttribute("data-comment", c.id);
    var face = document.createElement("span");
    face.className = "chat-face";
    face.innerHTML = c.avatar || ""; // built and escaped by the server
    var main = document.createElement("div");
    main.className = "chat-main";
    var meta = document.createElement("div");
    meta.className = "chat-meta";
    var author = document.createElement("strong");
    author.className = "chat-author";
    author.textContent = c.author;
    meta.appendChild(author);
    if (c.staff) {
      var badge = document.createElement("span");
      badge.className = "chat-host";
      badge.textContent = staffLabel;
      meta.appendChild(badge);
    }
    var time = document.createElement("time");
    time.className = "chat-time";
    time.setAttribute("datetime", c.at);
    time.textContent = localTime(c.at);
    meta.appendChild(time);
    var body = document.createElement("p");
    body.className = "chat-body";
    if (typeof c.timestamp === "number") {
      var seek = document.createElement("button");
      seek.type = "button";
      seek.className = "comment-at";
      seek.setAttribute("data-seek", String(c.timestamp));
      seek.textContent = clock(c.timestamp);
      body.appendChild(seek);
      body.appendChild(document.createTextNode(" "));
    }
    body.appendChild(document.createTextNode(c.body));
    main.appendChild(meta);
    main.appendChild(body);
    var actions = document.createElement("div");
    actions.className = "comment-actions";
    if (!isReply && session) {
      var reply = document.createElement("button");
      reply.type = "button";
      reply.className = "btn btn-ghost btn-sm";
      reply.setAttribute("data-reply", c.id);
      reply.setAttribute("data-reply-name", c.author);
      reply.innerHTML = icon("reply");
      reply.appendChild(document.createTextNode(gettext("Reply")));
      actions.appendChild(reply);
    }
    if (c.can_hide) {
      var hide = document.createElement("button");
      hide.type = "button";
      hide.className = "btn btn-ghost btn-sm";
      hide.setAttribute("data-hide-comment", c.id);
      hide.innerHTML = icon("eye-off");
      hide.appendChild(document.createTextNode(gettext("Hide")));
      actions.appendChild(hide);
    }
    if (actions.children.length) main.appendChild(actions);
    item.appendChild(face);
    item.appendChild(main);
    if (!isReply && c.replies && c.replies.length) {
      var replies = document.createElement("ol");
      replies.className = "comment-replies";
      c.replies.forEach(function (r) { replies.appendChild(commentNode(r, true)); });
      main.appendChild(replies);
    }
    return item;
  }

  function render(data) {
    if (!list) return;
    list.textContent = "";
    (data.comments || []).forEach(function (c) { list.appendChild(commentNode(c, false)); });
    each("[data-comments-empty]", function (node) { node.hidden = !!(data.comments && data.comments.length); });
    each("[data-comments-count]", function (node) {
      node.textContent = String(data.count || 0);
      node.hidden = !data.count;
    });
    if (form) form.hidden = !data.can_post;
    each("[data-comments-signin]", function (node) { node.hidden = !!data.can_post; });
  }

  function load() {
    if (!panel || !commentsUrl) return;
    window.clearTimeout(pollTimer);
    fetch(commentsUrl, { headers: headers(), cache: "no-store" })
      .then(function (response) {
        checkSession(response);
        return response.ok ? response.json() : null;
      })
      .then(function (data) { if (data) render(data); })
      .catch(function () { /* the next round tries again */ })
      .then(function () { pollTimer = window.setTimeout(load, document.hidden ? POLL * 3 : POLL); });
  }

  function setReply(id, name) {
    replyTo = id || "";
    var box = form && form.querySelector("[data-replying]");
    if (!box) return;
    box.hidden = !replyTo;
    var label = box.querySelector("span");
    if (label) label.textContent = replyTo ? interpolate(gettext("Replying to %(name)s"), { name: name }, true) : "";
    each("[data-at-wrap]", function (node) { node.hidden = !!replyTo; });
    if (replyTo) form.querySelector("textarea").focus();
  }

  if (form) {
    var field = form.querySelector("textarea");
    var at = form.querySelector("[data-at]");
    var atLabel = form.querySelector("[data-at-label]");
    if (!video()) each("[data-at-wrap]", function (node) { node.hidden = true; });
    window.setInterval(function () {
      var media = video();
      if (atLabel && media && !media.paused) {
        atLabel.textContent = interpolate(gettext("at %(time)s"), { time: clock(media.currentTime) }, true);
      }
    }, 1000);
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var body = field.value.trim();
      if (!body) return;
      var media = video();
      var data = { body: body, parent: replyTo };
      if (!replyTo && at && at.checked && media && media.currentTime > 1) data.timestamp = media.currentTime;
      form.classList.add("is-busy");
      fetch(commentsUrl, { method: "POST", headers: headers(true), body: JSON.stringify(data) })
        .then(function (response) {
          checkSession(response);
          return response.json().catch(function () { return {}; }).then(function (payload) {
            if (!response.ok) throw new Error(payload.detail || gettext("The comment could not be sent."));
            return payload;
          });
        })
        .then(function () {
          field.value = "";
          setReply("", "");
          load();
        })
        .catch(function (error) { note(error.message, "error"); })
        .then(function () { form.classList.remove("is-busy"); });
    });
    field.addEventListener("keydown", function (event) {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        if (form.requestSubmit) form.requestSubmit();
      }
    });
  }

  root.addEventListener("click", function (event) {
    var button;
    if ((button = event.target.closest("[data-seek]"))) {
      var media = video();
      if (media) {
        media.currentTime = Number(button.getAttribute("data-seek")) || 0;
        media.play().catch(function () { /* the person presses play */ });
      }
      return;
    }
    if ((button = event.target.closest("[data-reply]"))) {
      setReply(button.getAttribute("data-reply"), button.getAttribute("data-reply-name"));
      return;
    }
    if ((button = event.target.closest("[data-cancel-reply]"))) {
      setReply("", "");
      return;
    }
    if ((button = event.target.closest("[data-hide-comment]"))) {
      var id = button.getAttribute("data-hide-comment");
      fetch(hideUrl.replace("00000000-0000-0000-0000-000000000000", id), { method: "POST", headers: headers() })
        .then(function (response) {
          if (!response.ok) throw new Error(gettext("The comment could not be hidden."));
          note(gettext("Comment hidden."), "success");
          load();
        })
        .catch(function (error) { note(error.message, "error"); });
    }
  });

  if (!root.hasAttribute("data-awaiting")) load();
})();
