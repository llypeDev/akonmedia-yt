/* AkonMedia player wiring.
 *
 * Prefers adaptive HLS when the browser supports it or hls.js is available,
 * and falls back to the progressive file otherwise. If HLS fails mid-stream the
 * player swaps to the progressive source rather than showing a dead frame.
 */
(function () {
  "use strict";

  function attach(video) {
    var hlsSource = video.getAttribute("data-hls");
    var fallback = video.getAttribute("data-fallback");
    if (!hlsSource) return;

    if (video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = hlsSource;
      return;
    }

    if (!window.Hls || !window.Hls.isSupported()) {
      if (fallback) video.src = fallback;
      return;
    }

    var hls = new window.Hls({ enableWorker: true, lowLatencyMode: false });
    hls.loadSource(hlsSource);
    hls.attachMedia(video);

    hls.on(window.Hls.Events.ERROR, function (_event, data) {
      if (!data || !data.fatal) return;
      hls.destroy();
      if (fallback) {
        video.src = fallback;
        video.load();
      }
    });
  }

  function init() {
    document.querySelectorAll("video[data-hls]").forEach(attach);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
