/* api.js — talks to the Flask backend. Edit API_BASE_URL for your deployment. */

const API_BASE_URL = window.VIRAL_CLIPS_API_BASE || "http://localhost:5000";

async function apiCreateJob(youtubeUrl) {
  const res = await fetch(`${API_BASE_URL}/api/jobs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ youtube_url: youtubeUrl }),
  });
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.error || "Could not start the job.");
  }
  return data.job_id;
}

async function apiGetJob(jobId) {
  const res = await fetch(`${API_BASE_URL}/api/jobs/${jobId}`);
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.error || "Could not fetch job status.");
  }
  return data;
}

function apiClipVideoUrl(jobId, clipId, { download = false } = {}) {
  const suffix = download ? "?download=1" : "";
  return `${API_BASE_URL}/api/jobs/${jobId}/clips/${clipId}/video${suffix}`;
}

function apiClipSrtUrl(jobId, clipId) {
  return `${API_BASE_URL}/api/jobs/${jobId}/clips/${clipId}/srt`;
}

function apiClipThumbnailUrl(jobId, clipId) {
  return `${API_BASE_URL}/api/jobs/${jobId}/clips/${clipId}/thumbnail`;
}

/** Poll a job until it's done or errored. onUpdate is called on every poll. */
function pollJob(jobId, { onUpdate, intervalMs = 1500 }) {
  let stopped = false;

  const tick = async () => {
    if (stopped) return;
    try {
      const job = await apiGetJob(jobId);
      onUpdate(job);
      if (job.status === "done" || job.status === "error") {
        return;
      }
    } catch (err) {
      onUpdate({ status: "error", error: err.message });
      return;
    }
    setTimeout(tick, intervalMs);
  };

  tick();
  return () => { stopped = true; };
}
