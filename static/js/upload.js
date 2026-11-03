/* Chunked upload against /api/v1/uploads/.
 *
 * The server owns the chunk size, so the client asks for a session first and
 * then follows whatever it is told. Progress is per chunk, not per byte, which
 * is accurate enough and keeps the UI responsive on large files.
 */
(function () {
  "use strict";

  var form = document.getElementById("upload-form");
  if (!form) return;

  var fileInput = document.getElementById("file-input");
  var titleInput = document.getElementById("title-input");
  var visibilityInput = document.getElementById("visibility-input");
  var dropzone = document.getElementById("dropzone");
  var fileName = document.getElementById("file-name");
  var progress = document.getElementById("progress");
  var progressBar = document.getElementById("progress-bar");
  var status = document.getElementById("status");
  var submit = document.getElementById("submit-button");

  function csrf() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    if (meta) return meta.getAttribute("content");
    var match = document.cookie.match(/csrftoken=([^;]+)/);
    return match ? match[1] : "";
  }

  function say(text, isError) {
    status.textContent = text;
    status.classList.toggle("error", Boolean(isError));
  }

  function setProgress(fraction) {
    progress.hidden = false;
    progressBar.style.width = Math.max(0, Math.min(1, fraction)) * 100 + "%";
  }

  fileInput.addEventListener("change", function () {
    var file = fileInput.files[0];
    fileName.textContent = file ? file.name + " · " + (file.size / 1048576).toFixed(1) + " MB" : "";
  });

  ["dragenter", "dragover"].forEach(function (name) {
    dropzone.addEventListener(name, function (event) {
      event.preventDefault();
      dropzone.classList.add("over");
    });
  });

  ["dragleave", "drop"].forEach(function (name) {
    dropzone.addEventListener(name, function (event) {
      event.preventDefault();
      dropzone.classList.remove("over");
    });
  });

  dropzone.addEventListener("drop", function (event) {
    if (event.dataTransfer && event.dataTransfer.files.length) {
      fileInput.files = event.dataTransfer.files;
      fileInput.dispatchEvent(new Event("change"));
    }
  });

  form.addEventListener("submit", async function (event) {
    event.preventDefault();

    var file = fileInput.files[0];
    if (!file) {
      say("Pick a file first.", true);
      return;
    }

    submit.disabled = true;
    var token = csrf();

    try {
      var startResponse = await fetch("/api/v1/uploads/", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": token },
        body: JSON.stringify({
          filename: file.name,
          total_size: file.size,
          content_type: file.type || "application/octet-stream",
        }),
      });

      if (!startResponse.ok) throw new Error("could not start the upload");
      var session = await startResponse.json();
      var chunkSize = session.chunk_size;
      var total = Math.ceil(file.size / chunkSize);

      for (var index = 0; index < total; index++) {
        var slice = file.slice(index * chunkSize, Math.min(file.size, (index + 1) * chunkSize));
        var chunkResponse = await fetch(
          "/api/v1/uploads/" + session.id + "/chunk/?index=" + index,
          { method: "PUT", headers: { "X-CSRFToken": token }, body: slice }
        );
        if (!chunkResponse.ok) throw new Error("chunk " + index + " failed");
        setProgress((index + 1) / total);
        say("Uploading " + (index + 1) + " of " + total + " chunks");
      }

      say("Finishing…");
      var completeResponse = await fetch("/api/v1/uploads/" + session.id + "/complete/", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": token },
        body: JSON.stringify({
          title: titleInput.value || file.name.replace(/\.[^.]+$/, ""),
          visibility: visibilityInput.value,
        }),
      });

      if (!completeResponse.ok) throw new Error("the upload could not be completed");
      var result = await completeResponse.json();
      say("Done. Encoding starts now — opening the page.");
      window.location.href = "/view/" + result.media_uid + "/";
    } catch (error) {
      say(error.message || "upload failed", true);
      submit.disabled = false;
    }
  });
})();
