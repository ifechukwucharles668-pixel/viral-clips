/* app.js — view state, polling, and rendering for the Viral Clips MVP */

const STEP_ORDER = ["fetching_video", "extracting_transcript", "detecting_moments", "generating_clips"];
const STEP_LABELS = {
  fetching_video: "Fetching video",
  extracting_transcript: "Extracting transcript",
  detecting_moments: "Detecting viral moments",
  generating_clips: "Generating clips",
};

let currentJobId = null;
let currentJob = null;
let stopPolling = null;
let selectedClipId = null;

// ---------------------------------------------------------------------
// View switching
// ---------------------------------------------------------------------

function showView(name) {
  document.querySelectorAll(".view").forEach((el) => el.classList.remove("active"));
  document.getElementById(`view-${name}`).classList.add("active");
}

// ---------------------------------------------------------------------
// Tab switching (upload vs YouTube link)
// ---------------------------------------------------------------------

document.querySelectorAll(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    const target = btn.dataset.tab;
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.toggle("active", b === btn));
    document.querySelectorAll(".tab-panel").forEach((panel) => {
      panel.hidden = panel.dataset.panel !== target;
    });
  });
});

// ---------------------------------------------------------------------
// Upload flow
// ---------------------------------------------------------------------

let selectedFile = null;

document.getElementById("video-file-input").addEventListener("change", (e) => {
  selectedFile = e.target.files[0] || null;
  const label = document.getElementById("file-drop-label");
  const text = document.getElementById("file-drop-text");
  document.getElementById("upload-error").style.display = "none";
  if (selectedFile) {
    label.classList.add("has-file");
    text.textContent = selectedFile.name;
  } else {
    label.classList.remove("has-file");
    text.textContent = "Tap to choose a video file";
  }
});

document.getElementById("generate-upload-btn").addEventListener("click", async () => {
  const errorEl = document.getElementById("upload-error");
  const btn = document.getElementById("generate-upload-btn");
  errorEl.style.display = "none";

  if (!selectedFile) {
    errorEl.textContent = "Choose a video file first.";
    errorEl.style.display = "block";
    return;
  }

  btn.disabled = true;
  try {
    const jobId = await apiCreateUploadJob(selectedFile);
    currentJobId = jobId;
    resetProcessingUI();
    showView("processing");
    stopPolling = pollJob(jobId, { onUpdate: handleJobUpdate });
  } catch (err) {
    errorEl.textContent = err.message || "Could not start the job. Is the backend running?";
    errorEl.style.display = "block";
  } finally {
    btn.disabled = false;
  }
});

// ---------------------------------------------------------------------
// Landing -> submit (YouTube link)
// ---------------------------------------------------------------------

function isLikelyYoutubeUrl(value) {
  return /youtube\.com\/watch\?v=|youtu\.be\//.test(value);
}

document.getElementById("url-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = document.getElementById("youtube-url-input");
  const url = input.value.trim();
  const errorEl = document.getElementById("form-error");
  const btn = document.getElementById("generate-btn");

  errorEl.style.display = "none";

  if (!isLikelyYoutubeUrl(url)) {
    errorEl.textContent = "That doesn't look like a YouTube link. Try a full youtube.com or youtu.be URL.";
    errorEl.style.display = "block";
    return;
  }

  btn.disabled = true;
  try {
    const jobId = await apiCreateJob(url);
    currentJobId = jobId;
    resetProcessingUI();
    showView("processing");
    stopPolling = pollJob(jobId, { onUpdate: handleJobUpdate });
  } catch (err) {
    errorEl.textContent = err.message || "Could not start the job. Is the backend running?";
    errorEl.style.display = "block";
  } finally {
    btn.disabled = false;
  }
});

document.getElementById("try-again-btn").addEventListener("click", goHome);
document.getElementById("new-video-btn").addEventListener("click", goHome);

function goHome() {
  if (stopPolling) stopPolling();
  currentJobId = null;
  currentJob = null;
  selectedClipId = null;
  selectedFile = null;
  document.getElementById("youtube-url-input").value = "";
  document.getElementById("video-file-input").value = "";
  document.getElementById("file-drop-label").classList.remove("has-file");
  document.getElementById("file-drop-text").textContent = "Tap to choose a video file";
  showView("landing");
}
// ---------------------------------------------------------------------
// Processing view
// ---------------------------------------------------------------------

function resetProcessingUI() {
  document.getElementById("processing-error").style.display = "none";
  document.getElementById("scan-fill").style.width = "0%";
  document.getElementById("clip-progress-count").textContent = "";
  document.querySelectorAll("#steps-list .step-row").forEach((row) => {
    row.classList.remove("active", "done");
  });
}

function handleJobUpdate(job) {
  currentJob = job;

  if (job.status === "error") {
    showProcessingError(job.error);
    return;
  }

  const fill = document.getElementById("scan-fill");
  fill.style.width = `${job.progress || 0}%`;

  const stepIndex = STEP_ORDER.indexOf(job.step);
  document.querySelectorAll("#steps-list .step-row").forEach((row) => {
    const rowStep = row.dataset.step;
    const rowIndex = STEP_ORDER.indexOf(rowStep);
    row.classList.remove("active", "done");
    if (rowIndex < stepIndex || job.status === "done") {
      row.classList.add("done");
    } else if (rowIndex === stepIndex) {
      row.classList.add("active");
    }
  });

  if (job.step === "generating_clips" && job.clip_progress && job.clip_progress.total) {
    document.getElementById("clip-progress-count").textContent =
      `${job.clip_progress.current}/${job.clip_progress.total}`;
  }

  if (job.status === "done") {
    renderResults(job);
    showView("results");
  }
}

function showProcessingError(message) {
  const box = document.getElementById("processing-error");
  document.getElementById("processing-error-message").textContent =
    message || "An unexpected error occurred.";
  box.style.display = "block";
}

// ---------------------------------------------------------------------
// Results view
// ---------------------------------------------------------------------

function formatTime(seconds) {
  const s = Math.max(0, Math.round(seconds));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${String(r).padStart(2, "0")}`;
}

function renderResults(job) {
  document.getElementById("results-title").textContent =
    job.video_title ? `Clips from "${job.video_title}"` : "Your clips are ready";

  const durationStr = job.video_duration ? formatTime(job.video_duration) : "—";
  document.getElementById("results-source-info").textContent =
    `${job.clips.length} clips · source length ${durationStr} · transcript via ${job.transcript_source || "n/a"}`;

  const grid = document.getElementById("clip-grid");
  grid.innerHTML = "";

  job.clips.forEach((clip) => {
    const card = document.createElement("div");
    card.className = "clip-card";
    card.dataset.clipId = clip.id;
    card.innerHTML = `
      <div class="clip-thumb">
        <img src="${apiClipThumbnailUrl(job.id, clip.id)}" alt="${escapeHtml(clip.title)}" loading="lazy" />
        <span class="score-badge ${clip.label}">${clip.label}</span>
        <span class="time-badge">${formatTime(clip.start)} – ${formatTime(clip.end)}</span>
        <div class="play-overlay"><span class="pb">
          <svg viewBox="0 0 24 24" fill="none" stroke="#f4f2ee" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3l14 9-14 9V3z"/></svg>
        </span></div>
      </div>
      <div class="clip-body">
        <h4>${escapeHtml(clip.title)}</h4>
        <div class="clip-actions">
          <button class="icon-btn act-play">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3l14 9-14 9V3z"/></svg> Play
          </button>
          <a class="icon-btn act-dl-mp4" href="${apiClipVideoUrl(job.id, clip.id, { download: true })}" download>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v13m0 0l-4-4m4 4l4-4M5 21h14"/></svg> MP4
          </a>
          <a class="icon-btn act-dl-srt" href="${apiClipSrtUrl(job.id, clip.id)}" download>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6h16M4 12h10M4 18h7"/></svg> SRT
          </a>
        </div>
      </div>
    `;

    card.querySelector(".clip-thumb").addEventListener("click", () => {
      selectClip(clip);
      openPlayer(job, clip);
    });
    card.querySelector(".act-play").addEventListener("click", (e) => {
      e.stopPropagation();
      selectClip(clip);
      openPlayer(job, clip);
    });
    card.addEventListener("click", (e) => {
      if (e.target.closest("a")) return; // let download links behave natively
      if (e.target.closest(".act-play") || e.target.closest(".clip-thumb")) return;
      selectClip(clip);
    });

    grid.appendChild(card);
  });

  if (job.clips.length) {
    selectClip(job.clips[0]);
  }
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str || "";
  return div.innerHTML;
}

function selectClip(clip) {
  selectedClipId = clip.id;
  document.querySelectorAll(".clip-card").forEach((c) => {
    c.classList.toggle("selected", c.dataset.clipId === clip.id);
  });
  renderWhyPanel(clip);
}

function renderWhyPanel(clip) {
  const content = document.getElementById("why-panel-content");
  const reasonsHtml = clip.reasons
    .map((r) => `<div class="signal-item"><span class="si-dot"></span>${escapeHtml(r)}</div>`)
    .join("");

  content.innerHTML = `
    <h3>${escapeHtml(clip.title)}</h3>
    <div class="wp-meta">${formatTime(clip.start)}–${formatTime(clip.end)} · score ${clip.score} · ${clip.label}</div>
    <div class="signal-list">${reasonsHtml}</div>
    <div class="wp-transcript">${escapeHtml(clip.text)}</div>
  `;
}

// ---------------------------------------------------------------------
// Video player modal
// ---------------------------------------------------------------------

function srtToVtt(srtText) {
  return "WEBVTT\n\n" + srtText.replace(/(\d{2}:\d{2}:\d{2}),(\d{3})/g, "$1.$2");
}

let currentTrackUrl = null;

async function openPlayer(job, clip) {
  const video = document.getElementById("player-video");
  const backdrop = document.getElementById("player-backdrop");
  const captionsToggle = document.getElementById("captions-toggle");

  document.getElementById("player-title").textContent = clip.title;
  document.getElementById("player-download-btn").href = apiClipVideoUrl(job.id, clip.id, { download: true });

  // remove any previous track
  Array.from(video.querySelectorAll("track")).forEach((t) => t.remove());
  if (currentTrackUrl) {
    URL.revokeObjectURL(currentTrackUrl);
    currentTrackUrl = null;
  }

  video.src = apiClipVideoUrl(job.id, clip.id);
  video.playbackRate = parseFloat(document.getElementById("speed-select").value);

  try {
    const srtRes = await fetch(apiClipSrtUrl(job.id, clip.id));
    const srtText = await srtRes.text();
    const vttBlob = new Blob([srtToVtt(srtText)], { type: "text/vtt" });
    currentTrackUrl = URL.createObjectURL(vttBlob);

    const track = document.createElement("track");
    track.kind = "subtitles";
    track.label = "Captions";
    track.srclang = "en";
    track.src = currentTrackUrl;
    track.default = true;
    video.appendChild(track);

    video.addEventListener("loadedmetadata", () => {
      if (video.textTracks[0]) {
        video.textTracks[0].mode = captionsToggle.classList.contains("on") ? "showing" : "hidden";
      }
    }, { once: true });
  } catch (err) {
    // captions are best-effort; playback still works without them
    console.warn("Could not load captions for this clip", err);
  }

  backdrop.classList.add("active");
  video.play().catch(() => {});
}

function closePlayer() {
  const video = document.getElementById("player-video");
  video.pause();
  video.src = "";
  document.getElementById("player-backdrop").classList.remove("active");
}

document.getElementById("close-player-btn").addEventListener("click", closePlayer);
document.getElementById("player-backdrop").addEventListener("click", (e) => {
  if (e.target.id === "player-backdrop") closePlayer();
});

document.getElementById("captions-toggle").addEventListener("click", () => {
  const toggle = document.getElementById("captions-toggle");
  const video = document.getElementById("player-video");
  toggle.classList.toggle("on");
  if (video.textTracks[0]) {
    video.textTracks[0].mode = toggle.classList.contains("on") ? "showing" : "hidden";
  }
});

document.getElementById("speed-select").addEventListener("change", (e) => {
  document.getElementById("player-video").playbackRate = parseFloat(e.target.value);
});
