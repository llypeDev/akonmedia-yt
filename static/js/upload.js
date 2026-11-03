/* Resumable chunked upload against /api/v1/uploads/.
 *
 *   POST   /api/v1/uploads/                 open a session (server owns chunk size)
 *   GET    /api/v1/uploads/<id>/            ask where a session got to (resume)
 *   PUT    /api/v1/uploads/<id>/chunk/?index=N
 *   POST   /api/v1/uploads/<id>/complete/   publish
 *   DELETE /api/v1/uploads/<id>/            cancel
 *
 * Every chunk is retried with exponential backoff, a 409 re-synchronises with
 * the index the server expects, and the session id is remembered per file so
 * closing the tab and choosing the same file again continues where it stopped.
 * Chunks go through XMLHttpRequest because fetch() cannot report upload
 * progress; the telemetry (speed, remaining time) is computed from it.
 */
(function () {
  "use strict";

  var form = document.getElementById("upload-form");
  if (!form) return;

  var $ = function (id) { return document.getElementById(id); };
  var fileInput = $("file-input");
  var dropzone = $("dropzone");
  var fileCard = $("file-card");
  var resumeNote = $("resume-note");
  var panel = $("progress-panel");
  var bar = $("progress-bar");
  var track = $("progress");
  var submit = $("submit-button");
  var cancelButton = $("cancel-button");

  var CHUNK_RETRIES = 6;
  var state = { file: null, session: null, xhr: null, cancelled: false, busy: false };

  /* ------------------------------------------------------------ helpers */

  function csrf() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    if (meta && meta.content) return meta.content;
    var match = document.cookie.match(/csrftoken=([^;]+)/);
    return match ? match[1] : "";
  }

  function bytes(n) {
    if (!isFinite(n) || n < 0) return "—";
    var units = ["B", "KB", "MB", "GB", "TB"];
    var i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return (i === 0 ? n : n.toFixed(n < 10 ? 2 : n < 100 ? 1 : 0)) + " " + units[i];
  }

  function duration(seconds) {
    if (!isFinite(seconds) || seconds < 0) return "—";
    seconds = Math.round(seconds);
    if (seconds < 60) return seconds + "s";
    var m = Math.floor(seconds / 60), s = seconds % 60;
    if (m < 60) return m + "m " + String(s).padStart(2, "0") + "s";
    return Math.floor(m / 60) + "h " + String(m % 60).padStart(2, "0") + "m";
  }

  function storageKey(file) {
    return "akon-upload:" + file.name + ":" + file.size + ":" + (file.lastModified || 0);
  }

  function remember(file, id) {
    try {
      if (id) localStorage.setItem(storageKey(file), id);
      else localStorage.removeItem(storageKey(file));
    } catch (e) { /* storage unavailable: no resume, upload still works */ }
  }

  function recall(file) {
    try { return localStorage.getItem(storageKey(file)); } catch (e) { return null; }
  }

  function kindOf(file) {
    var type = file.type || "";
    var name = file.name.toLowerCase();
    if (type.indexOf("image/") === 0) return ["image", "Image"];
    if (type.indexOf("audio/") === 0 || /\.(mp3|wav|flac|m4a|ogg|opus|aac)$/.test(name)) return ["music", "Audio"];
    if (type === "application/pdf" || /\.pdf$/.test(name)) return ["file-text", "Document"];
    return ["film", "Video"];
  }

  function iconMarkup(name) {
    var sprite = form.getAttribute("data-sprite") || "/static/icons/sprite.svg";
    return '<svg class="icon" aria-hidden="true"><use href="' + sprite + "#i-" + name + '"></use></svg>';
  }

  function say(text, tone) {
    var node = $("status");
    node.textContent = text;
    node.style.color = tone === "error" ? "var(--danger)" : tone === "ok" ? "var(--success)" : "";
  }

  function detail(text, tone) {
    var node = $("detail");
    node.textContent = text || "";
    node.className = "upload-status" + (tone ? " " + tone : "");
  }

  function phase(text) { $("progress-phase").textContent = text; }

  function setProgress(fraction) {
    fraction = Math.max(0, Math.min(1, fraction));
    bar.style.width = (fraction * 100).toFixed(2) + "%";
    $("progress-pct").textContent = Math.floor(fraction * 100) + "%";
  }

  function api(method, url, payload) {
    return fetch(url, {
      method: method,
      credentials: "same-origin",
      headers: payload ? { "Content-Type": "application/json", "X-CSRFToken": csrf() } : { "X-CSRFToken": csrf() },
      body: payload ? JSON.stringify(payload) : undefined,
    }).then(function (response) {
      return response.text().then(function (text) {
        var data = null;
        try { data = text ? JSON.parse(text) : null; } catch (e) { data = null; }
        if (!response.ok) {
          var error = new Error((data && data.detail) || ("HTTP " + response.status));
          error.status = response.status;
          error.data = data;
          throw error;
        }
        return data;
      });
    });
  }

  function wait(ms) { return new Promise(function (resolve) { setTimeout(resolve, ms); }); }

  /* -------------------------------------------------------- file choice */

  function choose(file) {
    if (!file) return;
    state.file = file;
    state.session = null;
    var kind = kindOf(file);
    $("file-icon").innerHTML = iconMarkup(kind[0]);
    $("file-name").textContent = file.name;
    $("file-meta").textContent = kind[1] + " · " + bytes(file.size) + (file.type ? " · " + file.type : "");
    fileCard.hidden = false;
    dropzone.hidden = true;
    var title = $("title-input");
    if (!title.value.trim()) title.value = file.name.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ").trim();

    resumeNote.hidden = true;
    var previous = recall(file);
    if (previous) {
      api("GET", "/api/v1/uploads/" + previous + "/").then(function (session) {
        if (session && !session.completed && session.total_size === file.size && session.filename === file.name) {
          state.session = session;
          $("resume-progress").textContent = Math.floor((session.progress || 0) * 100) + "%";
          resumeNote.hidden = false;
        } else {
          remember(file, null);
        }
      }).catch(function () { remember(file, null); });
    }
  }

  fileInput.addEventListener("change", function () { choose(fileInput.files[0]); });

  $("file-change").addEventListener("click", function () {
    if (state.busy) return;
    fileInput.value = "";
    fileCard.hidden = true;
    resumeNote.hidden = true;
    dropzone.hidden = false;
    state.file = null;
    fileInput.click();
  });

  $("resume-discard").addEventListener("click", function () {
    var session = state.session;
    state.session = null;
    resumeNote.hidden = true;
    if (state.file) remember(state.file, null);
    if (session) api("DELETE", "/api/v1/uploads/" + session.id + "/").catch(function () {});
  });

  ["dragenter", "dragover"].forEach(function (name) {
    dropzone.addEventListener(name, function (event) { event.preventDefault(); dropzone.classList.add("over"); });
  });
  ["dragleave", "drop"].forEach(function (name) {
    dropzone.addEventListener(name, function (event) { event.preventDefault(); dropzone.classList.remove("over"); });
  });
  dropzone.addEventListener("drop", function (event) {
    var files = event.dataTransfer && event.dataTransfer.files;
    if (files && files.length) {
      try { fileInput.files = files; } catch (e) { /* older browsers */ }
      choose(files[0]);
    }
  });

  /* ---------------------------------------------------------- transfer */

  function sendChunk(session, index, blob, onProgress) {
    return new Promise(function (resolve, reject) {
      var xhr = new XMLHttpRequest();
      state.xhr = xhr;
      xhr.open("PUT", "/api/v1/uploads/" + session.id + "/chunk/?index=" + index);
      xhr.setRequestHeader("X-CSRFToken", csrf());
      xhr.setRequestHeader("Content-Type", "application/octet-stream");
      xhr.upload.onprogress = function (event) { if (event.lengthComputable) onProgress(event.loaded); };
      xhr.onload = function () {
        var data = null;
        try { data = JSON.parse(xhr.responseText); } catch (e) { data = null; }
        if (xhr.status >= 200 && xhr.status < 300) return resolve(data);
        var error = new Error((data && data.detail) || ("HTTP " + xhr.status));
        error.status = xhr.status;
        error.data = data;
        reject(error);
      };
      xhr.onerror = function () { var e = new Error("network error"); e.status = 0; reject(e); };
      xhr.onabort = function () { var e = new Error("cancelled"); e.status = -1; reject(e); };
      xhr.send(blob);
    });
  }

  function run() {
    var file = state.file;
    var started = performance.now();
    var speed = 0;
    var lastSample = { time: started, bytes: 0 };

    function telemetry(sentBytes, index, total) {
      var now = performance.now();
      var dt = (now - lastSample.time) / 1000;
      if (dt >= 0.4) {
        var instant = (sentBytes - lastSample.bytes) / dt;
        speed = speed ? speed * 0.7 + instant * 0.3 : instant;
        lastSample = { time: now, bytes: sentBytes };
      }
      setProgress(sentBytes / file.size);
      $("t-sent").textContent = bytes(sentBytes) + " / " + bytes(file.size);
      $("t-speed").textContent = speed > 0 ? bytes(speed) + "/s" : "—";
      $("t-eta").textContent = speed > 0 ? duration((file.size - sentBytes) / speed) : "—";
      $("t-chunk").textContent = Math.min(index + 1, total) + " / " + total;
    }

    var opening = state.session
      ? Promise.resolve(state.session)
      : api("POST", "/api/v1/uploads/", {
          filename: file.name,
          total_size: file.size,
          content_type: file.type || "application/octet-stream",
        });

    return opening.then(function (session) {
      state.session = session;
      remember(file, session.id);
      var chunkSize = session.chunk_size;
      var total = Math.max(1, Math.ceil(file.size / chunkSize));
      var index = session.next_index || 0;
      lastSample.bytes = Math.min(file.size, index * chunkSize);
      telemetry(lastSample.bytes, index, total);
      say(index ? "Resuming at chunk " + (index + 1) + " of " + total : "Sending " + total + " chunk" + (total === 1 ? "" : "s"));
      cancelButton.hidden = false;

      function next(attempt) {
        if (state.cancelled) return Promise.reject(Object.assign(new Error("cancelled"), { status: -1 }));
        if (index >= total) return Promise.resolve();
        var start = index * chunkSize;
        var blob = file.slice(start, Math.min(file.size, start + chunkSize));
        return sendChunk(session, index, blob, function (loaded) {
          telemetry(start + loaded, index, total);
        }).then(function () {
          index += 1;
          telemetry(Math.min(file.size, index * chunkSize), index - 1, total);
          detail("");
          return next(0);
        }, function (error) {
          if (error.status === -1) throw error;
          if (error.status === 409 && error.data && typeof error.data.expected_index === "number") {
            index = error.data.expected_index;     // the server already has more than we thought
            return next(attempt);
          }
          if (error.status === 400 || error.status === 403 || error.status === 404 || error.status === 413) throw error;
          if (attempt >= CHUNK_RETRIES) throw error;
          var delay = Math.min(30000, 1000 * Math.pow(2, attempt));
          detail("Connection hiccup — retrying chunk " + (index + 1) + " in " + Math.round(delay / 1000) + "s…", "error");
          return wait(delay).then(function () {
            // Ask the server where it is: the failed request may have landed.
            return api("GET", "/api/v1/uploads/" + session.id + "/").then(function (fresh) {
              index = fresh.next_index;
            }, function () { /* keep our index */ });
          }).then(function () { return next(attempt + 1); });
        });
      }

      return next(0).then(function () {
        phase("Finalising");
        say("Upload complete — handing the file to the encoder…");
        detail("Large videos can take a while: posters, renditions and previews are generated now.");
        cancelButton.hidden = true;
        track.classList.add("done");
        var category = $("category-input");
        var checked = form.querySelector('input[name="visibility"]:checked');
        return api("POST", "/api/v1/uploads/" + session.id + "/complete/", {
          title: $("title-input").value.trim(),
          description: $("description-input").value,
          visibility: checked ? checked.value : "public",
          category: category && category.value ? category.value : null,
        });
      });
    });
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    if (state.busy) return;
    if (!state.file) {
      fileInput.click();
      return;
    }

    state.busy = true;
    state.cancelled = false;
    submit.disabled = true;
    resumeNote.hidden = true;
    panel.hidden = false;
    track.classList.remove("done", "error");
    phase("Uploading");
    detail("");

    run().then(function (result) {
      remember(state.file, null);
      setProgress(1);
      phase(result.state === "ready" ? "Published" : "Processing");
      say(result.state === "ready" ? "Done — opening your media page." : "Uploaded. Encoding has started — opening the page.", "ok");
      detail("");
      state.busy = false;
      setTimeout(function () { window.location.href = result.url || ("/view/" + result.media_uid + "/"); }, 700);
    }).catch(function (error) {
      state.busy = false;
      submit.disabled = false;
      cancelButton.hidden = true;
      if (error.status === -1) {
        phase("Cancelled");
        say("Upload cancelled.");
        detail("");
        setProgress(0);
        return;
      }
      track.classList.add("error");
      phase("Paused");
      say("The upload stopped: " + (error.message || "unknown error") + ".", "error");
      detail("Press “Upload and publish” again to continue from the last confirmed chunk.", "error");
    });
  });

  cancelButton.addEventListener("click", function () {
    if (!state.busy) return;
    state.cancelled = true;
    if (state.xhr) state.xhr.abort();
    var session = state.session;
    state.session = null;
    if (state.file) remember(state.file, null);
    if (session) api("DELETE", "/api/v1/uploads/" + session.id + "/").catch(function () {});
  });

  window.addEventListener("beforeunload", function (event) {
    if (!state.busy) return;
    event.preventDefault();
    event.returnValue = "";
  });
})();
