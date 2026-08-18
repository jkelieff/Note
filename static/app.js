// Meeting Note Taker — dashboard front-end logic

const $ = (id) => document.getElementById(id);

// ── Tabs ─────────────────────────────────────────────────────────────────────
document.querySelectorAll(".tab").forEach((t) => {
  t.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
    t.classList.add("active");
    for (const name of ["upload", "record", "settings"]) {
      $("tab-" + name).hidden = name !== t.dataset.tab;
    }
  });
});

// ── Health check ─────────────────────────────────────────────────────────────
async function checkHealth() {
  try {
    const h = await (await fetch("/api/health")).json();
    const ff = h.ffmpeg
      ? '<span class="pill ok">ffmpeg ✓</span>'
      : '<span class="pill no">ffmpeg missing</span>';
    const ol = h.ollama
      ? '<span class="pill ok">local AI ✓</span>'
      : '<span class="pill no">Ollama not running</span>';
    $("health").innerHTML = ff + " " + ol;
  } catch { /* ignore */ }
}
checkHealth();

// ── Settings ─────────────────────────────────────────────────────────────────
async function loadSettings() {
  const cfg = await (await fetch("/api/config")).json();
  $("setModel").value = cfg.model || "base";
  $("setBackend").value = cfg.backend || "ollama";
  $("setSummaryModel").value = cfg.summary_model || "llama3.1";
  $("setOutput").value = cfg.output_dir || "";
  updateKeyHint();
}
function updateKeyHint() {
  const b = $("setBackend").value;
  $("keyNeeded").textContent =
    b === "ollama" ? "(not needed for local model)" :
    b === "gemini" ? "(free Gemini key)" : "(paid Claude key)";
}
$("setBackend").addEventListener("change", updateKeyHint);
$("saveSettings").addEventListener("click", async () => {
  const body = {
    model: $("setModel").value,
    backend: $("setBackend").value,
    summary_model: $("setSummaryModel").value.trim() || "llama3.1",
    output_dir: $("setOutput").value.trim(),
  };
  if ($("setKey").value.trim()) body.api_key = $("setKey").value.trim();
  await fetch("/api/config", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  $("settingsStatus").textContent = "Saved ✓";
  setTimeout(() => ($("settingsStatus").textContent = ""), 2000);
  checkHealth();
});
loadSettings();

// ── Upload flow ──────────────────────────────────────────────────────────────
let chosenFile = null;
const drop = $("drop"), fileInput = $("fileInput");

drop.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", () => setFile(fileInput.files[0]));
["dragover", "dragenter"].forEach((e) =>
  drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.add("hover"); }));
["dragleave", "drop"].forEach((e) =>
  drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.remove("hover"); }));
drop.addEventListener("drop", (ev) => {
  if (ev.dataTransfer.files.length) setFile(ev.dataTransfer.files[0]);
});
function setFile(f) {
  chosenFile = f;
  $("dropText").textContent = f ? "Selected: " + f.name : "Click to choose a file";
  $("uploadBtn").disabled = !f;
}
$("uploadBtn").addEventListener("click", () => {
  if (chosenFile) submitBlob(chosenFile, chosenFile.name);
});

// ── Record flow (browser screen + system audio capture) ──────────────────────
let mediaRecorder = null, chunks = [], recTimer = null, recSeconds = 0, recStream = null;

$("startRec").addEventListener("click", async () => {
  if (!navigator.mediaDevices?.getDisplayMedia) {
    alert("Your browser can't capture screen audio. Use Chrome or Edge.");
    return;
  }
  try {
    recStream = await navigator.mediaDevices.getDisplayMedia({
      video: true, audio: true,   // audio:true prompts "share system audio"
    });
  } catch {
    return; // user cancelled the picker
  }
  if (!recStream.getAudioTracks().length) {
    alert("No audio was shared. Please try again and tick 'Share system audio'.");
    recStream.getTracks().forEach((t) => t.stop());
    return;
  }
  chunks = [];
  mediaRecorder = new MediaRecorder(recStream, { mimeType: pickMime() });
  mediaRecorder.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
  mediaRecorder.onstop = () => {
    const blob = new Blob(chunks, { type: chunks[0]?.type || "video/webm" });
    submitBlob(blob, "live_meeting.webm");
  };
  // If the user stops sharing via the browser bar, end the recording too
  recStream.getVideoTracks()[0].addEventListener("ended", stopRecording);
  mediaRecorder.start();
  recSeconds = 0;
  $("recIdle").hidden = true; $("recLive").hidden = false;
  recTimer = setInterval(() => {
    recSeconds++;
    const m = Math.floor(recSeconds / 60), s = String(recSeconds % 60).padStart(2, "0");
    $("recTime").textContent = `${m}:${s}`;
  }, 1000);
});
$("stopRec").addEventListener("click", stopRecording);
function stopRecording() {
  if (mediaRecorder && mediaRecorder.state !== "inactive") mediaRecorder.stop();
  if (recStream) recStream.getTracks().forEach((t) => t.stop());
  clearInterval(recTimer);
  $("recIdle").hidden = false; $("recLive").hidden = true;
}
function pickMime() {
  for (const m of ["video/webm;codecs=vp8,opus", "video/webm", "audio/webm"]) {
    if (MediaRecorder.isTypeSupported(m)) return m;
  }
  return "";
}

// ── Submit + poll ────────────────────────────────────────────────────────────
async function submitBlob(blob, filename) {
  showProgress("Uploading…");
  $("resultCard").classList.remove("on");
  const fd = new FormData();
  fd.append("file", blob, filename);

  let res;
  try {
    res = await (await fetch("/api/process", { method: "POST", body: fd })).json();
  } catch (e) {
    return fail("Upload failed: " + e);
  }
  if (res.error) return fail(res.error);
  pollJob(res.job_id);
}

async function pollJob(jobId) {
  try {
    const job = await (await fetch("/api/job/" + jobId)).json();
    const last = job.steps?.[job.steps.length - 1];
    if (last) $("status").textContent = last;
    $("steps").textContent = (job.steps || []).join("\n");

    if (job.status === "done") return showResult(job.result);
    if (job.status === "error") return fail(job.error);
    setTimeout(() => pollJob(jobId), 900);
  } catch (e) {
    fail("Lost connection: " + e);
  }
}

function showProgress(msg) {
  $("progressCard").style.display = "block";
  $("bar").classList.add("on");
  $("status").textContent = msg;
  $("steps").textContent = "";
}
function fail(msg) {
  $("bar").classList.remove("on");
  $("status").textContent = "⚠ " + msg;
}
function showResult(r) {
  $("bar").classList.remove("on");
  $("status").textContent = "Done ✓";
  $("summary").innerHTML = renderMarkdown(r.summary);
  $("transcript").textContent = r.transcript || "(empty)";
  $("savedPaths").innerHTML =
    "Saved to:<br><code>" + r.summary_path + "</code><br><code>" + r.transcript_path + "</code>";
  $("resultCard").classList.add("on");
  $("resultCard").scrollIntoView({ behavior: "smooth" });
}

// ── Minimal markdown -> HTML (handles our summary format) ─────────────────────
function renderMarkdown(md) {
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const lines = (md || "").split("\n");
  let html = "", i = 0, inList = false;
  const closeList = () => { if (inList) { html += "</ul>"; inList = false; } };

  while (i < lines.length) {
    let line = lines[i];

    // table block
    if (line.includes("|") && lines[i + 1] && /^\s*\|?[\s:|-]+\|?\s*$/.test(lines[i + 1])) {
      closeList();
      const header = line.split("|").map((c) => c.trim()).filter(Boolean);
      html += "<table><thead><tr>" + header.map((h) => "<th>" + esc(h) + "</th>").join("") + "</tr></thead><tbody>";
      i += 2;
      while (i < lines.length && lines[i].includes("|")) {
        const cells = lines[i].split("|").map((c) => c.trim()).filter((c, idx, a) => !(idx === 0 && c === "") && !(idx === a.length - 1 && c === ""));
        html += "<tr>" + cells.map((c) => "<td>" + inline(esc(c)) + "</td>").join("") + "</tr>";
        i++;
      }
      html += "</tbody></table>";
      continue;
    }

    if (/^###\s+/.test(line)) { closeList(); html += "<h3>" + esc(line.replace(/^###\s+/, "")) + "</h3>"; }
    else if (/^##\s+/.test(line)) { closeList(); html += "<h3>" + esc(line.replace(/^##\s+/, "")) + "</h3>"; }
    else if (/^\s*[-*]\s+/.test(line)) {
      if (!inList) { html += "<ul>"; inList = true; }
      html += "<li>" + inline(esc(line.replace(/^\s*[-*]\s+/, ""))) + "</li>";
    }
    else if (line.trim() === "" || /^---+$/.test(line.trim())) { closeList(); }
    else { closeList(); html += "<p>" + inline(esc(line)) + "</p>"; }
    i++;
  }
  closeList();
  return html;

  function inline(s) {
    return s.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
            .replace(/`(.+?)`/g, "<code>$1</code>");
  }
}
