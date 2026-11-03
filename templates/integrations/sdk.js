{% autoescape off %}/* QQplay on your site: lives and their chat, with your own sign-in.
 *
 *   <div data-qqplay-live="LIVE_ID" data-layout="both" data-token="TOKEN"></div>
 *   <script src="{{ origin }}/embed/sdk.js" async></script>
 *
 * data-qqplay-live   the live's id (the end of its address on QQplay)
 * data-layout        both (player and chat, the default), player or chat
 * data-token         the token your server signs for the person signed in
 * data-token-url     or an address on your site that answers {"token": "..."}
 *                    for the person signed in; the script asks it again when
 *                    a session runs out, so long classes need nothing else
 * data-theme         dark or light (the person's system setting otherwise)
 * data-height        the chat's height in pixels, for data-layout="chat"
 *
 * Without a token the live shows and its chat can be read, not written to.
 * The token reaches the frame in a message to QQplay's own origin, never in
 * an address, so it is not left in histories or server logs.
 *
 * From your own script: QQplay.mount(element, {live, layout, token, tokenUrl,
 * theme, height}), QQplay.setToken(element, token) and QQplay.unmount(element).
 * The element receives "qqplay:signed-in", "qqplay:signed-out" and
 * "qqplay:error" events.
 */
(function () {
  "use strict";

  var ORIGIN = "{{ origin|escapejs }}";
  if (window.QQplay && window.QQplay.origin === ORIGIN) {
    window.QQplay.mountAll();
    return;
  }

  var CHAT_WIDTH = 340;
  var STACK_BELOW = 720;
  var STACKED_CHAT = 480;
  var widgets = [];

  function keyOf(token) {
    // The integration's key travels in the token's header (kid).
    try {
      var part = String(token || "").split(".")[0].replace(/-/g, "+").replace(/_/g, "/");
      while (part.length % 4) part += "=";
      var header = JSON.parse(atob(part));
      return typeof header.kid === "string" ? header.kid : "";
    } catch (e) {
      return "";
    }
  }

  function emit(widget, name, detail) {
    widget.el.dispatchEvent(new CustomEvent(name, { bubbles: true, detail: detail || {} }));
  }

  function Widget(el, options) {
    this.el = el;
    this.live = String(options.live || "");
    this.layout = ["both", "player", "chat"].indexOf(options.layout) >= 0 ? options.layout : "both";
    this.theme = options.theme === "dark" || options.theme === "light" ? options.theme : "";
    this.height = Number(options.height) || 600;
    this.token = options.token || "";
    this.tokenUrl = options.tokenUrl || "";
    this.frame = null;
    this.ready = false;
    var self = this;
    if (this.token || !this.tokenUrl) this.build();
    else this.fetchToken().then(function () { self.build(); });
  }

  Widget.prototype.fetchToken = function () {
    var self = this;
    if (!this.tokenUrl) return Promise.resolve("");
    return fetch(this.tokenUrl, { credentials: "include", headers: { Accept: "application/json" } })
      .then(function (response) { return response.ok ? response.json() : {}; })
      .then(function (data) {
        self.token = (data && data.token) || "";
        return self.token;
      })
      .catch(function () { return ""; });
  };

  Widget.prototype.build = function () {
    var params = ["layout=" + this.layout, "sdk=1"];
    var key = keyOf(this.token);
    if (key) params.push("school=" + encodeURIComponent(key));
    if (this.theme) params.push("theme=" + this.theme);
    var frame = document.createElement("iframe");
    frame.src = ORIGIN + "/embed/live/" + encodeURIComponent(this.live) + "/?" + params.join("&");
    frame.title = this.layout === "chat" ? "QQplay chat" : "QQplay live";
    frame.allow = "autoplay; fullscreen; picture-in-picture";
    frame.setAttribute("allowfullscreen", "");
    frame.setAttribute("loading", "lazy");
    frame.style.cssText = "display:block;width:100%;border:0;border-radius:12px;background:#05070c;";
    this.el.textContent = "";
    this.el.appendChild(frame);
    this.frame = frame;
    this.resize();
    var self = this;
    if (typeof ResizeObserver === "function") {
      this.observer = new ResizeObserver(function () { self.resize(); });
      this.observer.observe(this.el);
    } else {
      window.addEventListener("resize", function () { self.resize(); });
    }
  };

  Widget.prototype.resize = function () {
    if (!this.frame) return;
    var width = this.el.clientWidth || this.frame.clientWidth || 640;
    var height;
    if (this.layout === "player") height = width * 9 / 16;
    else if (this.layout === "chat") height = this.height;
    else if (width >= STACK_BELOW) height = Math.max(360, (width - CHAT_WIDTH) * 9 / 16);
    else height = width * 9 / 16 + STACKED_CHAT;
    this.frame.style.height = Math.round(height) + "px";
  };

  Widget.prototype.send = function () {
    if (!this.frame || !this.frame.contentWindow || !this.ready || !this.token) return;
    this.frame.contentWindow.postMessage({ type: "qqplay:token", token: this.token }, ORIGIN);
  };

  Widget.prototype.renew = function () {
    var self = this;
    if (!this.tokenUrl) {
      emit(this, "qqplay:signed-out");
      return;
    }
    this.fetchToken().then(function (token) {
      if (token) self.send();
      else emit(self, "qqplay:signed-out");
    });
  };

  Widget.prototype.destroy = function () {
    if (this.observer) this.observer.disconnect();
    if (this.frame) this.frame.remove();
    this.frame = null;
  };

  window.addEventListener("message", function (event) {
    if (event.origin !== ORIGIN || !event.data || typeof event.data.type !== "string") return;
    var widget = null;
    for (var i = 0; i < widgets.length; i++) {
      if (widgets[i].frame && widgets[i].frame.contentWindow === event.source) widget = widgets[i];
    }
    if (!widget) return;
    var data = event.data;
    if (data.type === "qqplay:ready") {
      widget.ready = true;
      widget.send();
    } else if (data.type === "qqplay:token-needed") {
      widget.renew();
    } else if (data.type === "qqplay:signed-in") {
      emit(widget, "qqplay:signed-in", { name: data.name || "" });
    } else if (data.type === "qqplay:error") {
      emit(widget, "qqplay:error", { message: data.message || "", code: data.code || "" });
    }
  });

  function fromAttributes(el) {
    return {
      live: el.getAttribute("data-qqplay-live"),
      layout: el.getAttribute("data-layout"),
      theme: el.getAttribute("data-theme"),
      height: el.getAttribute("data-height"),
      token: el.getAttribute("data-token"),
      tokenUrl: el.getAttribute("data-token-url"),
    };
  }

  function find(el) {
    for (var i = 0; i < widgets.length; i++) if (widgets[i].el === el) return widgets[i];
    return null;
  }

  var api = {
    origin: ORIGIN,
    version: 1,
    mount: function (el, options) {
      if (!el || find(el)) return find(el);
      var widget = new Widget(el, options || fromAttributes(el));
      widgets.push(widget);
      return widget;
    },
    mountAll: function () {
      var nodes = document.querySelectorAll("[data-qqplay-live]");
      for (var i = 0; i < nodes.length; i++) api.mount(nodes[i]);
    },
    setToken: function (el, token) {
      var widget = find(el);
      if (!widget) return;
      widget.token = token || "";
      widget.send();
    },
    unmount: function (el) {
      var widget = find(el);
      if (!widget) return;
      widget.destroy();
      widgets.splice(widgets.indexOf(widget), 1);
    },
  };
  window.QQplay = api;

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", api.mountAll);
  else api.mountAll();
})();
{% endautoescape %}
