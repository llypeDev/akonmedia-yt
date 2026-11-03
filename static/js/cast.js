/* AkonMedia: streaming a live from the browser.
 *
 * In the control room, the "From this browser" tab records the camera, or the
 * screen with the microphone, using MediaRecorder, and posts one chunk a
 * second to the portal. A relay on the server turns the chunks into the same
 * RTMP stream an encoder such as OBS would send, so everything else about the
 * live (player, chat, recording) works unchanged.
 *
 * Also switches between the control room's two sources (tabs) and remembers
 * the last one used.
 */
(function () {
  "use strict";

  var gettext = window.gettext || function (text) { return text; };
  var interpolate = window.interpolate || function (fmt, obj) {
    return fmt.replace(/%\((\w+)\)s/g, function (_m, key) { return String(obj[key]); });
  };

  var TAB_KEY = "akon.live.source";
  var CHUNK_MS = 1000;
  var MAX_RETRIES = 4;
  var SLOW_QUEUE = 8;
  var MIME_TYPES = [
    "video/webm;codecs=h264,opus",
    "video/webm;codecs=vp8,opus",
    "video/webm;codecs=vp9,opus",
    "video/webm",
    "video/mp4;codecs=avc1,mp4a",
    "video/mp4",
  ];

  function csrf() {
    if (window.AkonApp && window.AkonApp.csrf) return window.AkonApp.csrf();
    var match = document.cookie.match(/csrftoken=([^;]+)/);
    return match ? match[1] : "";
  }

  function toast(text, kind) {
    if (window.AkonApp && window.AkonApp.toast) window.AkonApp.toast(text, kind);
  }

  function clock(seconds) {
    var s = Math.max(0, Math.floor(seconds));
    var h = Math.floor(s / 3600);
    var m = Math.floor((s % 3600) / 60);
    var r = s % 60;
    var mm = h ? ("0" + m).slice(-2) : String(m);
    return (h ? h + ":" : "") + mm + ":" + ("0" + r).slice(-2);
  }

  function megabits(bitsPerSecond) {
    var value = bitsPerSecond / 1e6;
    var decimal = (window.get_format && window.get_format("DECIMAL_SEPARATOR")) || ".";
    return value.toFixed(1).replace(".", decimal);
  }

  function wait(ms) {
    return new Promise(function (resolve) { window.setTimeout(resolve, ms); });
  }

  function storage(key, value) {
    try {
      if (value === undefined) return window.localStorage.getItem(key);
      window.localStorage.setItem(key, value);
    } catch (e) { /* storage blocked */ }
    return null;
  }

  // ------------------------------------------------------------- source tabs

  function bindTabs(root) {
    var tabs = Array.prototype.slice.call(root.querySelectorAll("[data-source-tab]"));
    if (!tabs.length) return;
    var select = function (name, focus) {
      tabs.forEach(function (tab) {
        var on = tab.getAttribute("data-source-tab") === name;
        tab.classList.toggle("active", on);
        tab.setAttribute("aria-selected", on ? "true" : "false");
        tab.setAttribute("tabindex", on ? "0" : "-1");
        if (on && focus) tab.focus();
      });
      Array.prototype.forEach.call(root.querySelectorAll("[data-source-panel]"), function (panel) {
        panel.hidden = panel.getAttribute("data-source-panel") !== name;
      });
      storage(TAB_KEY, name);
    };
    tabs.forEach(function (tab, index) {
      tab.addEventListener("click", function () { select(tab.getAttribute("data-source-tab"), false); });
      tab.addEventListener("keydown", function (event) {
        if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
        event.preventDefault();
        var next = tabs[(index + (event.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
        select(next.getAttribute("data-source-tab"), true);
      });
    });
    var remembered = storage(TAB_KEY);
    if (remembered && root.querySelector('[data-source-tab="' + remembered + '"]')) select(remembered, false);
  }

  // ------------------------------------------------------------------ caster

  function Caster(root) {
    this.root = root;
    this.startUrl = root.getAttribute("data-start-url");
    this.video = root.querySelector("[data-cast-video]");
    this.placeholder = root.querySelector("[data-cast-placeholder]");
    this.badge = root.querySelector("[data-cast-badge]");
    this.meter = root.querySelector("[data-cast-meter]");
    this.level = root.querySelector("[data-cast-level]");
    this.status = root.querySelector("[data-cast-status]");
    this.previewBtn = root.querySelector("[data-cast-preview]");
    this.startBtn = root.querySelector("[data-cast-start]");
    this.stopBtn = root.querySelector("[data-cast-stop]");
    this.stream = null;
    this.tracks = [];
    this.audioContext = null;
    this.recorder = null;
    this.queue = [];
    this.seq = 0;
    this.sending = false;
    this.streaming = false;
    this.session = null;
    this.sent = [];
    this.startedAt = 0;

    var supported = !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder);
    if (!window.isSecureContext) {
      root.querySelector("[data-cast-insecure]").hidden = false;
      this.disable();
      return;
    }
    if (!supported) {
      root.querySelector("[data-cast-unsupported]").hidden = false;
      this.disable();
      return;
    }
    if (!navigator.mediaDevices.getDisplayMedia) {
      var screen = root.querySelector("[data-cast-screen-option]");
      if (screen) screen.hidden = true;
    }

    var self = this;
    this.previewBtn.addEventListener("click", function () { self.preview(); });
    this.startBtn.addEventListener("click", function () { self.start(); });
    this.stopBtn.addEventListener("click", function () { self.stop(gettext("Streaming stopped.")); });
    Array.prototype.forEach.call(root.querySelectorAll('input[name="cast-source"]'), function (input) {
      input.addEventListener("change", function () {
        if (!self.streaming) self.release();
      });
    });
    window.addEventListener("beforeunload", function (event) {
      if (!self.streaming) return;
      event.preventDefault();
      event.returnValue = "";
    });
  }

  Caster.prototype.disable = function () {
    [this.previewBtn, this.startBtn].forEach(function (button) { if (button) button.disabled = true; });
  };

  Caster.prototype.source = function () {
    var checked = this.root.querySelector('input[name="cast-source"]:checked');
    return checked ? checked.value : "camera";
  };

  Caster.prototype.say = function (text, kind) {
    this.status.textContent = text || "";
    this.status.className = "cast-status" + (kind ? " is-" + kind : "");
  };

  /* Camera, or screen plus microphone (and the computer's sound when the
     browser shares it, mixed in with the microphone). */
  Caster.prototype.acquire = function () {
    var self = this;
    if (this.stream) return Promise.resolve(this.stream);
    var audio = { echoCancellation: true, noiseSuppression: true, autoGainControl: true };
    var source = this.source();
    var media;
    if (source === "screen") {
      media = navigator.mediaDevices.getDisplayMedia({
        video: { frameRate: { ideal: 30 }, width: { max: 1920 }, height: { max: 1080 } },
        audio: true,
      }).then(function (display) {
        return navigator.mediaDevices.getUserMedia({ audio: audio }).then(function (mic) {
          return [display, mic];
        }, function () {
          return [display, null];
        });
      }).then(function (pair) {
        var display = pair[0];
        var mic = pair[1];
        self.tracks = display.getTracks().concat(mic ? mic.getTracks() : []);
        var sounds = [display, mic].filter(function (stream) { return stream && stream.getAudioTracks().length; });
        var out = new MediaStream([display.getVideoTracks()[0]]);
        if (sounds.length === 1) {
          out.addTrack(sounds[0].getAudioTracks()[0]);
        } else if (sounds.length > 1) {
          out.addTrack(self.mix(sounds));
        }
        return out;
      });
    } else {
      media = navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 30 } },
        audio: audio,
      }).then(function (camera) {
        self.tracks = camera.getTracks();
        return camera;
      });
    }
    return media.then(function (stream) {
      self.stream = stream;
      var video = stream.getVideoTracks()[0];
      if (video) {
        // The browser's own "Stop sharing" button ends the stream cleanly.
        video.addEventListener("ended", function () {
          if (self.streaming) self.stop(gettext("Screen sharing ended, so streaming stopped."));
          else self.release();
        });
      }
      self.video.srcObject = stream;
      self.video.hidden = false;
      self.placeholder.hidden = true;
      var play = self.video.play();
      if (play && play.catch) play.catch(function () {});
      self.watchLevel(stream);
      return stream;
    });
  };

  /* An AudioContext made after the click may start suspended, and would then
     record silence. */
  Caster.prototype.audio = function () {
    var Context = window.AudioContext || window.webkitAudioContext;
    if (!Context) return null;
    this.audioContext = this.audioContext || new Context();
    if (this.audioContext.state === "suspended" && this.audioContext.resume) this.audioContext.resume();
    return this.audioContext;
  };

  Caster.prototype.mix = function (streams) {
    this.audio();
    var destination = this.audioContext.createMediaStreamDestination();
    var context = this.audioContext;
    streams.forEach(function (stream) {
      context.createMediaStreamSource(stream).connect(destination);
    });
    return destination.stream.getAudioTracks()[0];
  };

  Caster.prototype.watchLevel = function (stream) {
    var self = this;
    var track = stream.getAudioTracks()[0];
    if (!track || !this.meter || !this.audio()) return;
    var analyser = this.audioContext.createAnalyser();
    analyser.fftSize = 512;
    this.audioContext.createMediaStreamSource(new MediaStream([track])).connect(analyser);
    var data = new Uint8Array(analyser.fftSize);
    this.meter.hidden = false;
    var draw = function () {
      if (!self.stream) return;
      analyser.getByteTimeDomainData(data);
      var peak = 0;
      for (var i = 0; i < data.length; i++) peak = Math.max(peak, Math.abs(data[i] - 128));
      self.level.style.width = Math.min(100, (peak / 128) * 160).toFixed(0) + "%";
      window.requestAnimationFrame(draw);
    };
    window.requestAnimationFrame(draw);
  };

  Caster.prototype.release = function () {
    this.tracks.forEach(function (track) { track.stop(); });
    this.tracks = [];
    this.stream = null;
    if (this.audioContext && this.audioContext.close) this.audioContext.close();
    this.audioContext = null;
    this.video.srcObject = null;
    this.video.hidden = true;
    this.placeholder.hidden = false;
    if (this.meter) this.meter.hidden = true;
  };

  Caster.prototype.preview = function () {
    var self = this;
    this.say("");
    this.acquire().catch(function (error) { self.failToStart(error); });
  };

  Caster.prototype.failToStart = function (error) {
    var denied = error && (error.name === "NotAllowedError" || error.name === "SecurityError");
    this.say(denied
      ? gettext("The browser did not get permission to use the camera, the microphone or the screen.")
      : interpolate(gettext("Could not start: %(reason)s"), { reason: (error && error.message) || error }, true), "error");
    this.setStreaming(false);
  };

  Caster.prototype.setStreaming = function (on) {
    this.streaming = on;
    this.startBtn.hidden = on;
    this.previewBtn.hidden = on;
    this.stopBtn.hidden = !on;
    this.badge.hidden = !on;
    this.startBtn.disabled = false;
    Array.prototype.forEach.call(this.root.querySelectorAll('input[name="cast-source"]'), function (input) {
      input.disabled = on;
    });
    this.root.classList.toggle("is-streaming", on);
  };

  Caster.prototype.start = function () {
    var self = this;
    if (this.streaming) return;
    this.startBtn.disabled = true;
    this.say(gettext("Starting…"));
    this.acquire()
      .then(function () {
        return fetch(self.startUrl, {
          method: "POST",
          credentials: "same-origin",
          headers: { Accept: "application/json", "X-CSRFToken": csrf() },
        });
      })
      .then(function (response) {
        return response.json().catch(function () { return {}; }).then(function (payload) {
          if (!response.ok) throw new Error(payload.detail || gettext("The server did not accept the stream."));
          return payload;
        });
      })
      .then(function (session) {
        self.session = session;
        self.record();
      })
      .catch(function (error) { self.failToStart(error); });
  };

  Caster.prototype.mimeType = function () {
    for (var i = 0; i < MIME_TYPES.length; i++) {
      if (window.MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(MIME_TYPES[i])) return MIME_TYPES[i];
    }
    return "";
  };

  Caster.prototype.record = function () {
    var self = this;
    var options = { videoBitsPerSecond: 2500000, audioBitsPerSecond: 128000 };
    var type = this.mimeType();
    if (type) options.mimeType = type;
    try {
      this.recorder = new MediaRecorder(this.stream, options);
    } catch (error) {
      this.failToStart(error);
      return;
    }
    this.queue = [];
    this.seq = 0;
    this.sent = [];
    this.recorder.addEventListener("dataavailable", function (event) {
      if (event.data && event.data.size) {
        self.queue.push(event.data);
        self.pump();
      }
    });
    this.recorder.addEventListener("error", function (event) {
      self.stop(interpolate(gettext("Recording failed: %(reason)s"), {
        reason: (event.error && event.error.message) || "MediaRecorder",
      }, true), "error");
    });
    this.recorder.start(CHUNK_MS);
    this.startedAt = Date.now();
    this.setStreaming(true);
    this.say(gettext("Connecting to the server…"));
    this.ticker = window.setInterval(function () { self.report(); }, 1000);
  };

  Caster.prototype.chunkUrl = function (seq) {
    return this.session.chunk_url.replace(/\/0\/$/, "/" + seq + "/");
  };

  /* One chunk at a time, in order, each retried a few times. */
  Caster.prototype.pump = function () {
    var self = this;
    if (this.sending || !this.queue.length || !this.session) return;
    this.sending = true;
    var blob = this.queue.shift();
    var seq = this.seq;
    var attempt = 0;
    var send = function () {
      return fetch(self.chunkUrl(seq), {
        method: "POST",
        credentials: "same-origin",
        headers: { "X-CSRFToken": csrf(), "Content-Type": "application/octet-stream" },
        body: blob,
      }).then(function (response) {
        if (response.status === 409) {
          return response.json().catch(function () { return {}; }).then(function (payload) {
            var error = new Error(payload.detail || gettext("The stream stopped on the server."));
            error.final = true;
            throw error;
          });
        }
        if (!response.ok) throw new Error("HTTP " + response.status);
      }).catch(function (error) {
        if (error.final || ++attempt > MAX_RETRIES) throw error;
        return wait(500 * Math.pow(2, attempt)).then(send);
      });
    };
    send().then(function () {
      self.seq = seq + 1;
      self.sent.push({ at: Date.now(), bytes: blob.size });
      self.sending = false;
      if (self.queue.length) self.pump();
      else if (self.draining) self.draining();
    }, function (error) {
      self.sending = false;
      self.queue = [];
      if (self.streaming) self.stop(error.message, "error");
      else if (self.draining) self.draining();
    });
  };

  Caster.prototype.report = function () {
    if (!this.streaming) return;
    var now = Date.now();
    this.sent = this.sent.filter(function (item) { return now - item.at < 5000; });
    var bytes = this.sent.reduce(function (sum, item) { return sum + item.bytes; }, 0);
    var elapsed = (now - this.startedAt) / 1000;
    if (this.queue.length >= SLOW_QUEUE) {
      this.say(interpolate(gettext("Your connection is not keeping up: %(seconds)s s waiting to be sent."),
        { seconds: this.queue.length }, true), "warning");
      return;
    }
    if (!this.seq) return;
    this.say(interpolate(gettext("On air · %(time)s · sending %(rate)s Mbps"), {
      time: clock(elapsed),
      rate: megabits((bytes * 8) / 5),
    }, true), "live");
  };

  /* Stop recording, send what is left, tell the server, free the devices. */
  Caster.prototype.stop = function (message, kind) {
    var self = this;
    if (!this.streaming) return;
    this.setStreaming(false);
    window.clearInterval(this.ticker);
    this.stopBtn.disabled = true;
    this.say(gettext("Stopping…"));
    var session = this.session;
    var finish = function () {
      self.draining = null;
      var done = function () {
        self.session = null;
        self.release();
        self.stopBtn.disabled = false;
        self.say(message || gettext("Streaming stopped."), kind || "");
        if (kind === "error") toast(message, "error");
      };
      if (!session) return done();
      fetch(session.stop_url, {
        method: "POST",
        credentials: "same-origin",
        headers: { Accept: "application/json", "X-CSRFToken": csrf() },
      }).then(done, done);
    };
    var drained = function () {
      if (!self.sending && !self.queue.length) finish();
      else self.draining = function () { if (!self.sending && !self.queue.length) finish(); };
    };
    if (this.recorder && this.recorder.state !== "inactive") {
      this.recorder.addEventListener("stop", function () { window.setTimeout(drained, 0); }, { once: true });
      try {
        this.recorder.stop();
      } catch (e) {
        drained();
      }
    } else {
      drained();
    }
    // Never wait forever on a dead connection.
    window.setTimeout(function () { if (self.draining) finish(); }, 10000);
  };

  function boot() {
    var room = document.querySelector("[data-source-tabs]");
    if (room) bindTabs(room.closest("section") || document);
    Array.prototype.forEach.call(document.querySelectorAll("[data-cast]"), function (root) {
      if (!root.__caster) root.__caster = new Caster(root);
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
