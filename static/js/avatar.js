/* Account page: the profile picture.
 *
 * Picking a character, the photo or the initials saves at once. A photo is
 * framed first: drag to place it in the circle, zoom with the slider, the
 * wheel or two fingers, and only the framed square (512px JPEG) is sent; the
 * server crops, scales and re-encodes it again whatever arrives. After a
 * change, every picture of the account on the page (the header's included)
 * is swapped for the new one.
 *
 * Without this script the form still works: pick, choose a file, save.
 */
(function () {
  "use strict";

  var form = document.querySelector("[data-avatar-picker]");
  if (!form) return;

  var app = window.AkonApp || {};
  var toast = app.toast || function () {};
  var csrf = app.csrf || function () { return ""; };
  var sprite = document.body.getAttribute("data-sprite") || "";
  var userId = form.getAttribute("data-user") || "";
  var limit = (Number(form.getAttribute("data-photo-limit")) || 8) * 1024 * 1024;
  var fileInput = form.querySelector("[data-avatar-file]");
  var photoOption = form.querySelector("[data-avatar-photo-option]");
  var photoFace = form.querySelector("[data-avatar-photo]");
  var removeButton = document.querySelector("[data-avatar-remove]");
  var removeForm = document.getElementById("avatar-remove");
  var saveButton = form.querySelector("[data-avatar-save]");
  var EXPORT_SIZE = 512;
  var MAX_ZOOM = 4;

  // Each pick saves itself.
  if (saveButton) saveButton.hidden = true;

  function checked() {
    var radio = form.querySelector('input[name="choice"]:checked');
    return radio ? radio.value : "initials";
  }
  var current = checked();

  function select(value) {
    var radio = form.querySelector('input[name="choice"][value="' + value + '"]');
    if (radio) radio.checked = true;
    current = value;
  }

  function setBusy(on) {
    form.classList.toggle("is-busy", on);
    form.setAttribute("aria-busy", on ? "true" : "false");
  }

  function post(data) {
    return fetch(form.action, {
      method: "POST",
      credentials: "same-origin",
      headers: { Accept: "application/json", "X-CSRFToken": csrf() },
      body: data,
    }).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (payload) {
        if (!response.ok) throw new Error(payload.detail || gettext("The picture could not be saved."));
        return payload;
      });
    });
  }

  /* Swap every avatar of this account on the page for the new one. */
  function apply(payload) {
    var html = payload.html || {};
    var holder = document.createElement("div");
    document.querySelectorAll('[data-avatar-of="' + userId + '"]').forEach(function (node) {
      var size = (node.className.match(/\bavatar-(xs|sm|md|lg|xl)\b/) || [])[1];
      if (!size || !html[size]) return;
      holder.innerHTML = html[size]; // built and escaped by the server
      var fresh = holder.firstElementChild;
      if (fresh) node.replaceWith(fresh);
    });

    var hasPhoto = !!payload.photo;
    if (photoOption) {
      photoOption.hidden = !hasPhoto;
      var radio = photoOption.querySelector("input");
      if (radio) radio.disabled = !hasPhoto;
    }
    if (photoFace) {
      photoFace.textContent = "";
      if (hasPhoto) {
        var img = document.createElement("img");
        img.src = payload.photo;
        img.alt = "";
        photoFace.appendChild(img);
      }
    }
    if (removeButton) removeButton.hidden = !hasPhoto;
    select(payload.choice || "initials");
  }

  function send(data, done) {
    setBusy(true);
    return post(data)
      .then(function (payload) {
        apply(payload);
        toast(payload.detail || done, "success");
      })
      .catch(function (error) {
        select(current);
        toast(error.message, "error");
      })
      .then(function () { setBusy(false); });
  }

  form.addEventListener("change", function (event) {
    var target = event.target;
    if (target.name === "choice") {
      var previous = current;
      var data = new FormData();
      data.append("choice", target.value);
      setBusy(true);
      post(data)
        .then(function (payload) {
          apply(payload);
          toast(payload.detail, "success");
        })
        .catch(function (error) {
          select(previous);
          toast(error.message, "error");
        })
        .then(function () { setBusy(false); });
    } else if (target === fileInput && target.files && target.files[0]) {
      var file = target.files[0];
      target.value = ""; // the same file can be picked again after a cancel
      pick(file);
    }
  });

  // The confirmation in app.js runs first; once answered, remove in place.
  if (removeForm) {
    removeForm.addEventListener("submit", function (event) {
      event.preventDefault();
      var data = new FormData();
      data.append("remove_photo", "1");
      send(data);
    });
  }

  function upload(blob, name) {
    var data = new FormData();
    data.append("photo", blob, name);
    return send(data);
  }

  function pick(file) {
    if (file.type && !/^image\//.test(file.type)) {
      toast(gettext("This file is not a picture we can read. Use a JPEG, PNG, WebP or GIF."), "error");
      return;
    }
    if (file.size > limit) {
      toast(interpolate(gettext("The picture is too large: the limit is %(limit)s MB."), { limit: Math.round(limit / 1048576) }, true), "error");
      return;
    }
    if (typeof HTMLDialogElement !== "function" || !window.URL || !URL.createObjectURL) {
      upload(file, file.name);
      return;
    }
    var url = URL.createObjectURL(file);
    var img = new Image();
    img.onload = function () {
      frame(img).then(function (blob) {
        URL.revokeObjectURL(url);
        if (blob) upload(blob, "photo.jpg");
      });
    };
    img.onerror = function () {
      URL.revokeObjectURL(url);
      // Perhaps a format this browser cannot show (HEIC, say): let the
      // server try, it says why if it cannot either.
      upload(file, file.name);
    };
    img.src = url;
  }

  function icon(name) {
    if (!sprite) return null;
    var ns = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(ns, "svg");
    svg.setAttribute("class", "icon icon-sm");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    var use = document.createElementNS(ns, "use");
    use.setAttribute("href", sprite + "#i-" + name);
    svg.appendChild(use);
    return svg;
  }

  /* The framing dialog. Resolves with the framed square, or null. */
  function frame(img) {
    return new Promise(function (resolve) {
      var opener = document.activeElement;
      var id = "crop-" + Date.now();
      var dialog = document.createElement("dialog");
      dialog.className = "confirm-dialog crop-dialog";
      dialog.setAttribute("aria-labelledby", id + "-title");
      dialog.setAttribute("aria-describedby", id + "-text");

      var box = document.createElement("form");
      box.method = "dialog";
      box.className = "crop-box";

      var title = document.createElement("h2");
      title.className = "confirm-title";
      title.id = id + "-title";
      title.textContent = gettext("Frame your photo");
      var text = document.createElement("p");
      text.className = "confirm-text";
      text.id = id + "-text";
      text.textContent = gettext("Drag to place it in the circle, and zoom in or out with the slider.");

      var stage = document.createElement("div");
      stage.className = "crop-stage";
      stage.tabIndex = 0;
      stage.setAttribute("role", "img");
      stage.setAttribute("aria-label", gettext("Photo being framed. Arrow keys move it, plus and minus zoom."));
      var picture = img;
      picture.className = "crop-picture";
      picture.alt = "";
      picture.draggable = false;
      var ring = document.createElement("span");
      ring.className = "crop-ring";
      stage.appendChild(picture);
      stage.appendChild(ring);

      var zoomRow = document.createElement("label");
      zoomRow.className = "crop-zoom";
      var small = icon("image");
      var large = icon("image");
      if (small) small.setAttribute("class", "icon icon-xs");
      if (large) large.setAttribute("class", "icon crop-zoom-large");
      var range = document.createElement("input");
      range.type = "range";
      range.min = "1";
      range.max = String(MAX_ZOOM);
      range.step = "0.01";
      range.value = "1";
      range.setAttribute("aria-label", gettext("Zoom"));
      if (small) zoomRow.appendChild(small);
      zoomRow.appendChild(range);
      if (large) zoomRow.appendChild(large);

      var actions = document.createElement("div");
      actions.className = "confirm-actions";
      var cancel = document.createElement("button");
      cancel.className = "btn btn-ghost";
      cancel.value = "cancel";
      cancel.textContent = gettext("Cancel");
      var ok = document.createElement("button");
      ok.className = "btn btn-primary";
      ok.value = "ok";
      ok.autofocus = true;
      var okIcon = icon("check");
      if (okIcon) ok.appendChild(okIcon);
      ok.appendChild(document.createTextNode(gettext("Use this photo")));
      actions.appendChild(cancel);
      actions.appendChild(ok);

      box.appendChild(title);
      box.appendChild(text);
      box.appendChild(stage);
      box.appendChild(zoomRow);
      box.appendChild(actions);
      dialog.appendChild(box);
      document.body.appendChild(dialog);
      dialog.showModal();

      var W = img.naturalWidth || img.width;
      var H = img.naturalHeight || img.height;
      var S = stage.clientWidth || 280;
      var base = S / Math.min(W, H);
      var zoom = 1;
      var x = 0;
      var y = 0;

      function scale() { return base * zoom; }
      function clamp() {
        x = Math.min(0, Math.max(S - W * scale(), x));
        y = Math.min(0, Math.max(S - H * scale(), y));
      }
      function draw() {
        picture.style.width = W * scale() + "px";
        picture.style.height = H * scale() + "px";
        picture.style.transform = "translate(" + x + "px," + y + "px)";
      }
      function zoomTo(value, cx, cy) {
        if (cx == null) { cx = S / 2; cy = S / 2; }
        var ix = (cx - x) / scale();
        var iy = (cy - y) / scale();
        zoom = Math.max(1, Math.min(MAX_ZOOM, value));
        x = cx - ix * scale();
        y = cy - iy * scale();
        clamp();
        draw();
        range.value = String(zoom);
      }
      x = (S - W * scale()) / 2;
      y = (S - H * scale()) / 2;
      draw();

      // Drag with one pointer, pinch with two.
      var pointers = {};
      var drag = null;
      var pinch = null;
      function local(event) {
        var rect = stage.getBoundingClientRect();
        return { x: event.clientX - rect.left, y: event.clientY - rect.top };
      }
      function points() {
        return Object.keys(pointers).map(function (key) { return pointers[key]; });
      }
      stage.addEventListener("pointerdown", function (event) {
        stage.setPointerCapture(event.pointerId);
        pointers[event.pointerId] = local(event);
        var list = points();
        if (list.length === 1) {
          drag = { px: list[0].x, py: list[0].y, x: x, y: y };
          pinch = null;
        } else if (list.length === 2) {
          drag = null;
          pinch = { d: Math.hypot(list[0].x - list[1].x, list[0].y - list[1].y) || 1, zoom: zoom };
        }
      });
      stage.addEventListener("pointermove", function (event) {
        if (!pointers[event.pointerId]) return;
        pointers[event.pointerId] = local(event);
        var list = points();
        if (pinch && list.length === 2) {
          var d = Math.hypot(list[0].x - list[1].x, list[0].y - list[1].y);
          zoomTo(pinch.zoom * d / pinch.d, (list[0].x + list[1].x) / 2, (list[0].y + list[1].y) / 2);
        } else if (drag && list.length === 1) {
          x = drag.x + (list[0].x - drag.px);
          y = drag.y + (list[0].y - drag.py);
          clamp();
          draw();
        }
      });
      function release(event) {
        delete pointers[event.pointerId];
        var list = points();
        pinch = null;
        drag = list.length === 1 ? { px: list[0].x, py: list[0].y, x: x, y: y } : null;
      }
      stage.addEventListener("pointerup", release);
      stage.addEventListener("pointercancel", release);
      stage.addEventListener("wheel", function (event) {
        event.preventDefault();
        var at = local(event);
        zoomTo(zoom * Math.exp(-event.deltaY * 0.0015), at.x, at.y);
      }, { passive: false });
      stage.addEventListener("keydown", function (event) {
        var step = event.shiftKey ? 40 : 10;
        var handled = true;
        if (event.key === "ArrowLeft") x += step;
        else if (event.key === "ArrowRight") x -= step;
        else if (event.key === "ArrowUp") y += step;
        else if (event.key === "ArrowDown") y -= step;
        else if (event.key === "+" || event.key === "=") { zoomTo(zoom * 1.1); return event.preventDefault(); }
        else if (event.key === "-" || event.key === "_") { zoomTo(zoom / 1.1); return event.preventDefault(); }
        else handled = false;
        if (!handled) return;
        event.preventDefault();
        clamp();
        draw();
      });
      range.addEventListener("input", function () { zoomTo(Number(range.value)); });

      dialog.addEventListener("click", function (event) {
        if (event.target === dialog) dialog.close("cancel");
      });
      dialog.addEventListener("close", function () {
        var accepted = dialog.returnValue === "ok";
        var done = function (blob) {
          dialog.remove();
          if (opener && typeof opener.focus === "function" && document.contains(opener)) opener.focus();
          resolve(blob);
        };
        if (!accepted) return done(null);
        var canvas = document.createElement("canvas");
        canvas.width = EXPORT_SIZE;
        canvas.height = EXPORT_SIZE;
        var ctx = canvas.getContext("2d");
        ctx.fillStyle = "#ffffff"; // under a transparent PNG
        ctx.fillRect(0, 0, EXPORT_SIZE, EXPORT_SIZE);
        ctx.imageSmoothingQuality = "high";
        ctx.drawImage(img, -x / scale(), -y / scale(), S / scale(), S / scale(), 0, 0, EXPORT_SIZE, EXPORT_SIZE);
        if (canvas.toBlob) canvas.toBlob(done, "image/jpeg", 0.92);
        else done(null);
      });
    });
  }
})();
