"use strict";

const $ = (id) => document.getElementById(id);
const icons = (name) => {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "icon");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
};

const formats = {
  "dvd-video": { label: "Save ISO only", hint: "Burn DVD converts, writes, finalizes and verifies the disc. Save ISO only does not write a disc.", note: "Creates a standard DVD video disc that auto-plays selected titles in queue order, without a menu." },
  "data-disc": { label: "Save ISO only", hint: "Burn disc writes, finalizes and verifies your files. Save ISO only does not write a disc.", note: "Creates a data disc for computers and compatible players. Files are not converted." },
  "usb-compatible": { label: "Prepare video", hint: "Converts videos to H.264 MP4 files", note: "Converts video to H.264 MP4. Playback support depends on your TV or media player." },
  "usb-original": { label: "Copy files", hint: "Copies original files into a new project folder", note: "Copies originals without conversion into the destination folder or USB drive." },
  "mp3": { label: "Prepare audio", hint: "Creates MP3 audio in a new project folder", note: "Converts audio, or extracts it from video, into MP3 files." }
};

const pages = { media: "Media", output: "Output", activity: "Activity", devices: "Devices", tests: "Tests", help: "Help" };
const dvdMediaTypes = new Set([5, 6, 7, 8, 9, 10, 11, 13]);

let state = null;
let plan = null;
let online = false;
let busyCount = 0;
let uploading = false;
let planning = false;
let planTimer;
let pollTimer;
let planVersion = 0;
let mutationVersion = 0;
let fileSignature = "";
let deviceSignature = "";
let outputEdited = false;
let manualRecorder = null;
let manualUSB = null;
let manualRecorderName = "";
let manualUSBName = "";
let lastAnnouncedJob = "";
let burnRequest = null;
let burnReturnFocus = null;
let pollInFlight = false;
let inspectDetailsLoaded = false;
const excludedFiles = new Set();
let testResult = null;

function node(tag, className, content) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (content !== undefined && content !== null) element.textContent = content;
  return element;
}

function bytes(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "Unknown size";
  const size = Number(value);
  if (size === 0) return "0 bytes";
  const units = ["bytes", "KB", "MB", "GB", "TB"];
  const exponent = Math.min(Math.floor(Math.log(Math.max(size, 1)) / Math.log(1000)), units.length - 1);
  return `${(size / 1000 ** exponent).toLocaleString(undefined, { maximumFractionDigits: exponent > 1 ? 2 : 0 })} ${units[exponent]}`;
}

function duration(value) {
  if (!Number.isFinite(Number(value)) || Number(value) <= 0) return null;
  const total = Math.round(Number(value));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  return hours ? `${hours}h ${minutes}m` : `${minutes}:${String(seconds).padStart(2, "0")}`;
}

function preset() {
  return document.querySelector('input[name="preset"]:checked').value;
}

function isDisc() {
  return ["dvd-video", "data-disc"].includes(preset());
}

function activeJob() {
  return state && state.job && ["queued", "running"].includes(state.job.state);
}

function activeCompression() {
  return ["queued", "running"].includes(state?.compression?.state);
}

function selectedFiles() {
  return (state?.files || []).filter((file) => !excludedFiles.has(file.id));
}

function discWriteProtected(job) {
  if (typeof job?.noninterruptible === "boolean") return job.noninterruptible;
  return Boolean(job?.noninterruptible) || ["burning", "preparing", "validating", "initializing", "calibrating", "writing", "finalizing", "verifying", "finished"].includes(job?.phase);
}

function selectedRecorder() {
  return state?.devices?.drives?.find((drive) => String(drive.id) === $("recorder").value) || null;
}

function showPage(focus = true) {
  if (location.hash === "#main") {
    $("main").focus();
    return;
  }
  const requested = location.hash.slice(1);
  const page = Object.hasOwn(pages, requested) ? requested : "media";
  if (requested !== page) history.replaceState(null, "", `#${page}`);
  document.querySelectorAll(".workspace-page").forEach((section) => { section.hidden = section.id !== `page-${page}`; });
  document.querySelectorAll(".nav-item[data-page]").forEach((link) => {
    const current = link.dataset.page === page;
    link.classList.toggle("active", current);
    if (current) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  $("page-title").textContent = pages[page];
  document.title = `${pages[page]} · LumaBurn`;
  if (focus) {
    $("page-title").focus({ preventScroll: true });
    window.scrollTo({ top: 0, behavior: "instant" });
  }
}

function navigate(page) {
  if (location.hash === `#${page}`) showPage();
  else location.hash = page;
}

function orderedDevices(devices) {
  return [...devices].sort((a, b) => String(a.letter || a.id).localeCompare(String(b.letter || b.id), undefined, { numeric: true }));
}

function bestRecorder(drives, format) {
  const writers = orderedDevices(drives).filter((drive) => (drive.can_write === true || drive.writable === true) && (format !== "dvd-video" || drive.supports_dvd !== false));
  const compatible = (drive) => format !== "dvd-video" || dvdMediaTypes.has(Number(drive.physical_media_type));
  return writers.find((drive) => drive.blank && drive.writable && !drive.write_protected && !drive.error && compatible(drive))
    || writers.find((drive) => !drive.error && Number(drive.physical_media_type) > 0 && compatible(drive))
    || writers.find((drive) => !drive.error)
    || writers[0]
    || null;
}

function usbVolumes() {
  return orderedDevices(state?.devices?.volumes || []).filter((volume) =>
    (volume.is_usb === true || (volume.is_usb === undefined && Number(volume.drive_type) === 2))
    && [2, 3].includes(Number(volume.drive_type))
    && volume.is_system !== true && volume.read_only === false
    && /^[A-Za-z]:$/.test(volume.letter || "") && Boolean(volume.filesystem)
    && Number(volume.capacity_bytes) > 0 && volume.free_bytes !== null && volume.free_bytes !== undefined);
}

function volumeRoot(volume) {
  return `${volume.letter.toUpperCase()}\\`;
}

function volumeName(volume) {
  const label = String(volume.label || "").trim();
  const named = label && !/^(unknown|unnamed)( volume)?$|^no label$/i.test(label);
  return [volume.letter, named ? label : null].filter(Boolean).join(" · ") || "Drive";
}

function destinationUnavailable() {
  return !isDisc() && !outputEdited && manualUSB !== null && !usbVolumes().some((volume) => volume.letter === manualUSB);
}

function renderDestination() {
  const volumes = usbVolumes();
  const previous = $("output-dir").value;
  if (!outputEdited && !activeJob()) {
    const selected = volumes.find((volume) => volume.letter === manualUSB) || volumes[0];
    $("output-dir").value = !isDisc() && manualUSB ? `${manualUSB}\\`
      : !isDisc() && selected ? volumeRoot(selected) : state?.default_output || previous;
  }
  const currentPath = $("output-dir").value.trim().replaceAll("/", "\\").toUpperCase();
  const selected = !outputEdited && volumes.find((volume) => currentPath === volumeRoot(volume));
  const unavailable = destinationUnavailable();
  const signature = JSON.stringify([volumes.map((volume) => [volume.letter, volume.label, volume.free_bytes]), manualUSB, manualUSBName, unavailable]);
  if ($("usb-target").dataset.devices !== signature) {
    $("usb-target").dataset.devices = signature;
    $("usb-target").replaceChildren(new Option("Custom folder", ""));
    for (const volume of volumes) $("usb-target").append(new Option(`${volumeName(volume)} · ${bytes(volume.free_bytes)} free`, volume.letter));
    if (unavailable) $("usb-target").append(new Option(`${volumeName({letter: manualUSB, label: manualUSBName})} · Unavailable`, manualUSB));
  }
  $("usb-target").value = unavailable ? manualUSB : selected?.letter || "";
  $("usb-target-hint").textContent = unavailable ? "Selected USB drive is unavailable or read only. Reconnect it or choose another destination before preparing files."
    : selected ? `${selected.filesystem} · ${bytes(selected.free_bytes)} free. A project folder is created on this drive.`
    : volumes.length ? "Choose a detected USB drive or enter a folder below."
    : "No writable USB drive detected. Using a folder; connect a USB drive or choose another location.";
  return previous !== $("output-dir").value;
}

function requestBody() {
  return {
    preset: preset(),
    file_ids: selectedFiles().map((file) => file.id),
    recorder_id: isDisc() && selectedRecorder() ? $("recorder").value : null,
    output_dir: $("output-dir").value.trim(),
    standard: $("standard").value,
    quality: $("quality").value,
    label: $("disc-label").value.trim(),
    performance: $("performance").value || "gentle"
  };
}

function announce(message) {
  $("live-status").textContent = message;
}

function showError(error) {
  const message = error instanceof Error ? error.message : String(error);
  $("global-error-text").textContent = message;
  $("global-error").hidden = false;
}

function setConnection(connected) {
  online = connected;
  $("connection-notice").hidden = connected;
}

async function api(path, options = {}) {
  const headers = { ...options.headers };
  if (options.method && options.method !== "GET") {
    if (!state?.session_token) throw new Error("The local service is not connected yet. Please try again in a moment.");
    headers["X-Luma-Token"] = state.session_token;
  }
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    options = { ...options, body: JSON.stringify(options.body) };
  }
  let response;
  try {
    response = await fetch(path, { ...options, headers, cache: "no-store" });
  } catch {
    throw new Error("Could not reach LumaBurn on this computer. Keep the local application running, then try again.");
  }
  const result = await response.json().catch(() => null);
  if (!response.ok) throw new Error(result?.error || result?.message || `The local service returned an error (${response.status}).`);
  return result;
}

async function action(task) {
  busyCount += 1;
  updateControls();
  try { return await task(); }
  catch (error) { showError(error); return null; }
  finally { busyCount -= 1; updateControls(); }
}

function updateControls() {
  const locked = busyCount > 0 || uploading || Boolean(activeJob());
  const empty = !selectedFiles().length;
  const validLabel = !isDisc() || /^[A-Za-z0-9 _-]{1,32}$/.test($("disc-label").value.trim());
  const valid = online && !locked && !activeCompression() && !state?.testing && !planning && !empty && validLabel && !destinationUnavailable() && plan?.can_start === true;
  const drive = selectedRecorder();
  $("prepare-button").disabled = !valid;
  $("burn-button").hidden = !isDisc();
  $("burn-button").disabled = !valid || !drive?.blank || !drive?.writable || Boolean(drive?.write_protected);
  $("add-files").disabled = locked || !online;
  $("pick-folder").disabled = locked || !online;
  $("refresh-button").disabled = locked || !online || Boolean(state?.refreshing);
  $("recorder").disabled = locked;
  $("usb-target").disabled = locked;
  $("standard").disabled = locked;
  $("quality").disabled = locked;
  $("performance").disabled = locked;
  $("disc-label").disabled = locked;
  $("output-dir").disabled = locked;
  $("cancel-job").disabled = busyCount > 0 || discWriteProtected(state?.job);
  $("select-all").disabled = locked || !online;
  document.querySelectorAll('input[name="preset"],.file-remove,.file-select').forEach((element) => { element.disabled = locked || !online; });
  const inspecting = !online || locked || activeCompression() || Boolean(state?.testing);
  for (const id of ["pick-compression-file", "pick-compression-folder", "compression-path", "compression-engine", "run-tests", "pick-test-folder", "test-recorder", "test-path"]) $(id).disabled = inspecting;
  $("estimate-compression").disabled = inspecting || !$("compression-path").value.trim();
  for (const id of ["open-7z", "open-winrar"]) $(id).disabled = !online || busyCount > 0;
  $("cancel-compression").disabled = !online || busyCount > 0;
  $("media-heading").closest("section").setAttribute("aria-busy", String(uploading));
  $("prepare-button").setAttribute("aria-describedby", isDisc() ? "action-hint plan-feedback" : "action-hint plan-feedback usb-target-hint");
  $("burn-button").setAttribute("aria-describedby", "recorder-hint plan-feedback");
  if (activeJob()) $("action-hint").textContent = "A project is running. Your queue is locked until it finishes.";
  else if (activeCompression()) $("action-hint").textContent = "Wait for the compression estimate to finish before preparing media.";
  else if (state?.testing) $("action-hint").textContent = "Wait for the read-only checks to finish before preparing media.";
  else if (destinationUnavailable()) $("action-hint").textContent = "Reconnect the selected USB drive or choose another destination.";
  else if (uploading) $("action-hint").textContent = "Adding your files to the local workspace…";
  else if (planning) $("action-hint").textContent = "Checking your project…";
  else if (empty) $("action-hint").textContent = state?.files?.length ? "Select at least one file on Media to begin." : "Add files to begin · " + formats[preset()].hint;
  else $("action-hint").textContent = formats[preset()].hint;
}

function renderFiles() {
  const files = state?.files || [];
  const present = new Set(files.map((file) => file.id));
  for (const id of excludedFiles) if (!present.has(id)) excludedFiles.delete(id);
  const signature = JSON.stringify([files, [...excludedFiles]]);
  if (signature === fileSignature) return false;
  fileSignature = signature;
  $("file-list").replaceChildren();
  for (const file of files) {
    const row = node("li", "file-row");
    const selected = node("input", "file-select");
    selected.type = "checkbox";
    selected.checked = !excludedFiles.has(file.id);
    selected.setAttribute("aria-label", `Include ${file.name} in output`);
    row.classList.toggle("excluded", !selected.checked);
    selected.addEventListener("change", () => {
      if (selected.checked) excludedFiles.delete(file.id);
      else excludedFiles.add(file.id);
      row.classList.toggle("excluded", !selected.checked);
      fileSignature = JSON.stringify([state?.files || [], [...excludedFiles]]);
      renderSelection();
      schedulePlan();
    });
    const graphic = node("span", "file-icon");
    graphic.append(icons(file.video ? "film" : file.audio ? "music" : "folder"));
    const copy = node("div", "file-copy");
    copy.append(node("div", "file-name", file.name));
    const meta = node("div", "file-meta");
    meta.append(node("span", "", bytes(file.size)));
    const time = duration(file.duration);
    if (time) meta.append(node("span", "", time));
    if (file.width && file.height) meta.append(node("span", "", `${file.width} × ${file.height}`));
    copy.append(meta);
    if (file.error) copy.append(node("div", "file-error", file.error));
    const remove = node("button", "icon-button file-remove");
    remove.setAttribute("aria-label", `Remove ${file.name}`);
    remove.append(icons("close"));
    remove.addEventListener("click", () => action(async () => {
      mutationVersion += 1;
      await api(`/api/files/${encodeURIComponent(file.id)}`, { method: "DELETE" });
      await loadState();
      announce(`${file.name} removed from the project.`);
    }).then(() => {
      ($("file-list").querySelector(".file-remove") || $("add-files")).focus();
    }));
    row.append(selected, graphic, copy, remove);
    $("file-list").append(row);
  }
  $("file-list").hidden = files.length === 0;
  $("drop-zone").classList.toggle("compact", files.length > 0);
  $("drop-heading").textContent = files.length ? "Drop more files here" : "Drag files here";
  renderSelection();
  return true;
}

function renderSelection() {
  const files = selectedFiles();
  const count = state?.files?.length || 0;
  const size = files.reduce((total, file) => total + (Number(file.size) || 0), 0);
  const totalDuration = files.reduce((total, file) => total + (Number(file.duration) || 0), 0);
  const summary = files.length ? `${files.length} selected · ${bytes(size)}${totalDuration ? " · " + duration(totalDuration) : ""}` : "No files selected";
  $("file-count").textContent = `${count} ${count === 1 ? "file" : "files"}`;
  $("queue-total").textContent = summary;
  $("output-queue-summary").textContent = summary;
  $("selection-toolbar").hidden = !count;
  $("select-all").checked = count > 0 && files.length === count;
  $("select-all").indeterminate = files.length > 0 && files.length < count;
  $("edit-media").textContent = count ? "Edit media" : "Add files";
}

function renderDevices() {
  const drives = state?.devices?.drives || [];
  const previous = $("recorder").value;
  const signature = JSON.stringify([orderedDevices(drives), manualRecorder, manualRecorderName]);
  if (signature !== deviceSignature) {
    deviceSignature = signature;
    $("recorder").replaceChildren(new Option("Prepare image only", ""));
    for (const drive of orderedDevices(drives)) {
      const discStatus = drive.blank ? "Blank disc" : drive.media_type || "No blank disc";
      $("recorder").append(new Option(`${drive.letter ? drive.letter + " · " : ""}${drive.name} · ${discStatus}`, String(drive.id)));
    }
    if (manualRecorder && !drives.some((drive) => String(drive.id) === manualRecorder)) $("recorder").append(new Option(`${manualRecorderName || "Selected recorder"} · Disconnected`, manualRecorder));
  }
  $("recorder").value = activeJob() && drives.some((drive) => String(drive.id) === previous) ? previous
    : manualRecorder !== null ? manualRecorder : String(bestRecorder(drives, preset())?.id || "");
  const recorder = selectedRecorder();
  $("recorder-hint").textContent = recorder
    ? recorder.write_protected ? "This disc is write protected. Insert a blank writable disc to burn."
      : preset() === "dvd-video" && recorder.physical_media_type > 0 && !dvdMediaTypes.has(Number(recorder.physical_media_type)) ? "Insert a blank writable DVD for DVD video. Preparing an image is still available."
      : recorder.blank && recorder.writable ? `Blank writable disc${recorder.capacity_bytes ? " · " + bytes(recorder.capacity_bytes) : ""}.`
      : `${recorder.error ? recorder.error + " " : ""}Insert a blank writable ${preset() === "dvd-video" ? "DVD" : "disc"} to burn. Preparing an image is still available.`
    : manualRecorder ? "Selected recorder is disconnected. Reconnect it to burn; preparing a disc image is still available."
      : drives.length ? "Choose a drive to burn, or prepare a disc image first." : "No recorder detected. You can still prepare a DVD folder and disc image.";
  $("device-refresh-status").textContent = state?.refreshing ? "Checking devices…" : "";
  $("refresh-button").setAttribute("aria-busy", String(Boolean(state?.refreshing)));
  $("refresh-button").disabled = Boolean(state?.refreshing) || busyCount > 0 || Boolean(activeJob());
  const destinationChanged = renderDestination();
  renderDiagnostics();
  renderTestDevices();
  return previous !== $("recorder").value || destinationChanged;
}

function renderDependencies() {
  const tools = state?.tools || {};
  const needs = preset() === "dvd-video" ? ["ffmpeg", "ffprobe", "dvdauthor"] : ["usb-compatible", "mp3"].includes(preset()) ? ["ffmpeg", "ffprobe"] : [];
  const missing = needs.filter((name) => !tools[name]);
  $("dependency-notice").hidden = !state || missing.length === 0;
  $("dependency-message").textContent = `This format needs ${missing.join(", ")}. Install the missing tools to prepare your media.`;
}

function renderFormat() {
  const format = formats[preset()];
  document.querySelectorAll(".preset-card").forEach((card) => card.classList.toggle("selected", card.querySelector("input").checked));
  $("prepare-label").textContent = format.label;
  $("prepare-button").classList.toggle("primary-button", !isDisc());
  $("prepare-button").classList.toggle("secondary-button", isDisc());
  $("burn-label").textContent = preset() === "dvd-video" ? "Burn DVD" : "Burn disc";
  $("recorder-field").hidden = !isDisc();
  $("usb-target-field").hidden = isDisc();
  $("video-settings").hidden = !["dvd-video", "data-disc", "usb-compatible"].includes(preset());
  $("video-settings").classList.toggle("single-field", preset() !== "dvd-video");
  $("standard-field").hidden = preset() !== "dvd-video";
  $("quality-field").hidden = preset() !== "usb-compatible";
  $("disc-label-field").hidden = !isDisc();
  $("output-dir-label").textContent = isDisc() ? "Save prepared media to" : "Destination folder or USB drive";
  $("compatibility-note").querySelector("p span").textContent = format.note;
  renderDevices();
  renderDependencies();
  updateControls();
}

function renderPlan() {
  const estimated = Number(plan?.estimated_bytes);
  const capacity = Number(plan?.capacity_bytes);
  const hasEstimate = plan?.estimated_bytes !== null && plan?.estimated_bytes !== undefined && Number.isFinite(estimated) && selectedFiles().length;
  const percent = hasEstimate && capacity > 0 ? Math.min(100, Math.max(0, estimated / capacity * 100)) : 0;
  $("capacity-fill").style.width = `${percent}%`;
  $("capacity-track").setAttribute("aria-valuenow", String(Math.round(percent)));
  $("capacity-track").setAttribute("aria-valuetext", hasEstimate ? `${bytes(estimated)}${capacity > 0 ? " of " + bytes(capacity) : " estimated"}` : "Add files to estimate space");
  $("capacity-track").classList.toggle("over-capacity", hasEstimate && capacity > 0 && estimated > capacity);
  $("capacity-track").hidden = !isDisc() && !capacity;
  $("capacity-title").textContent = isDisc() ? "Estimated disc capacity" : "Estimated output size";
  $("capacity-text").textContent = hasEstimate
    ? `${bytes(estimated)} estimated${capacity > 0 ? " / " + bytes(capacity) + " available" : " output size"}${plan.video_bitrate ? " · " + (Number(plan.video_bitrate) / 1000000).toFixed(1) + " Mbps video" : ""}`
    : "Add your files to see the estimated space.";
  const errors = Array.isArray(plan?.errors) ? plan.errors : [];
  const notes = Array.isArray(plan?.notes) ? plan.notes : [];
  $("plan-feedback").replaceChildren();
  $("plan-feedback").hidden = !errors.length && !notes.length;
  $("plan-feedback").classList.toggle("has-errors", errors.length > 0);
  for (const [messages, className] of [[errors, "plan-errors"], [notes, "plan-notes"]]) {
    if (!messages.length) continue;
    const list = node("ul", className);
    messages.forEach((message) => list.append(node("li", "", message)));
    $("plan-feedback").append(list);
  }
  updateControls();
}

function schedulePlan() {
  clearTimeout(planTimer);
  planVersion += 1;
  plan = null;
  planning = online && Boolean(selectedFiles().length);
  renderPlan();
  planTimer = setTimeout(checkPlan, 220);
}

async function checkPlan() {
  const version = planVersion;
  if (!online || !selectedFiles().length) { planning = false; updateControls(); return; }
  planning = true;
  updateControls();
  try {
    const result = await api("/api/plan", { method: "POST", body: requestBody() });
    if (version !== planVersion) return;
    plan = result;
  } catch (error) {
    if (version !== planVersion) return;
    plan = { can_start: false, errors: [error.message] };
  } finally {
    if (version === planVersion) { planning = false; renderPlan(); }
  }
}

function renderJob() {
  const job = state?.job;
  $("job-panel").hidden = !job;
  $("activity-empty").hidden = Boolean(job);
  $("activity-count").hidden = !activeJob();
  $("activity-count").textContent = activeJob() ? state.job.state === "queued" ? "Queued" : "Running" : "";
  if (!job) return;
  $("job-title").textContent = job.action === "burn" ? "Disc burn"
    : ["dvd-video", "data-disc"].includes(job.preset) ? "ISO export" : "Current activity";
  $("job-state").textContent = job.state;
  $("job-phase").textContent = (job.phase || job.state).replaceAll("_", " ");
  const progress = job.progress !== null && job.progress !== undefined && Number.isFinite(Number(job.progress)) ? Math.min(100, Math.max(0, Number(job.progress))) : null;
  if (progress === null) $("job-progress").removeAttribute("value");
  else $("job-progress").value = progress;
  $("job-percent").textContent = progress === null ? "Progress unknown" : `${Math.round(progress)}%`;
  const phaseProgress = job.phase_progress !== null && job.phase_progress !== undefined && Number.isFinite(Number(job.phase_progress)) ? Math.min(100, Math.max(0, Number(job.phase_progress))) : null;
  if (phaseProgress === null) $("job-phase-progress").removeAttribute("value");
  else $("job-phase-progress").value = phaseProgress;
  $("job-phase-percent").textContent = phaseProgress === null ? "Progress unknown" : `${Math.round(phaseProgress)}% of phase`;
  $("job-message").textContent = job.message || "";
  $("job-error").hidden = !job.error;
  $("job-error").textContent = job.error || "";
  $("job-output").hidden = !job.output_dir;
  $("job-output-path").textContent = job.output_dir || "";
  $("job-logs").textContent = (job.logs || []).map((log) => typeof log === "string" ? log : JSON.stringify(log)).join("\n") || "No activity logged yet.";
  const protectedPhase = discWriteProtected(job);
  $("cancel-job").hidden = !activeJob();
  $("cancel-job").disabled = protectedPhase || busyCount > 0;
  $("cancel-job").textContent = protectedPhase ? "Disc write in progress" : "Cancel";
  $("cancel-job").title = protectedPhase ? "Disc writing cannot be interrupted safely." : "Cancel the current job";
  const announcement = `${job.id}:${job.state}:${job.phase}`;
  if (announcement !== lastAnnouncedJob) {
    lastAnnouncedJob = announcement;
    announce(`Project ${job.state}. ${job.message || job.phase || ""}`);
  }
}

function renderDiagnostics() {
  $("diagnostics-tools").replaceChildren();
  for (const name of ["ffmpeg", "ffprobe", "dvdauthor", "7z", "winrar"]) {
    const chip = node("div", "tool-chip");
    chip.append(node("strong", "", name === "7z" ? "7-Zip" : name === "winrar" ? "WinRAR" : name));
    chip.append(node("span", state?.tools?.[name] ? "available" : "", state?.tools?.[name] ? "Available" : "Missing"));
    $("diagnostics-tools").append(chip);
  }
  $("diagnostics-drives").replaceChildren();
  const drives = state?.devices?.drives || [];
  if (!drives.length) $("diagnostics-drives").append(node("p", "hardware-empty", "No optical drives detected."));
  for (const drive of drives) {
    const card = node("div", "hardware-card");
    card.append(node("strong", "", `${drive.letter ? drive.letter + " · " : ""}${drive.name}`));
    card.append(node("p", "", [drive.media_type || "No media reported", drive.blank ? "Blank" : "Not blank", drive.writable ? "Writable" : "Not writable", drive.capacity_bytes ? bytes(drive.capacity_bytes) + " capacity" : null].filter(Boolean).join(" · ")));
    if (drive.error) card.append(node("p", "hardware-error", drive.error));
    $("diagnostics-drives").append(card);
  }
  $("diagnostics-volumes").replaceChildren();
  const volumes = state?.devices?.volumes || [];
  if (!volumes.length) $("diagnostics-volumes").append(node("p", "hardware-empty", "No storage volumes reported."));
  for (const volume of volumes) {
    const card = node("div", "hardware-card");
    card.append(node("strong", "", volumeName(volume)));
    card.append(node("p", "", [volume.is_system ? "System drive" : Number(volume.drive_type) === 5 ? "Optical" : volume.is_usb === true || (volume.is_usb === undefined && Number(volume.drive_type) === 2) ? "USB" : "Internal storage", volume.read_only ? "Read only" : null, volume.filesystem || "Unknown filesystem", `${bytes(volume.free_bytes)} free / ${bytes(volume.capacity_bytes)}`].filter(Boolean).join(" · ")));
    $("diagnostics-volumes").append(card);
  }
  const test = state?.diagnostics;
  $("diagnostics-test").hidden = !test;
  $("drive-test-result").replaceChildren();
  if (test) {
    const passed = test.burned === true && test.verified === true;
    const card = node("div", "hardware-card");
    const title = node("strong", passed ? "test-passed" : "", passed ? "Write and readback passed" : "Drive test recorded");
    if (passed) title.prepend(icons("check"));
    card.append(title);
    const details = [test.drive];
    if (Array.isArray(test.sha256)) {
      details.push(`${test.sha256.length} ${test.sha256.length === 1 ? "file" : "files"} checked`);
      details.push(bytes(test.sha256.reduce((total, file) => total + (Number(file.bytes) || 0), 0)));
    }
    if (test.elapsed_seconds !== null && test.elapsed_seconds !== undefined && Number.isFinite(Number(test.elapsed_seconds))) details.push(`${Number(test.elapsed_seconds).toFixed(1)} seconds`);
    card.append(node("p", "", details.filter(Boolean).join(" · ")));
    if (Array.isArray(test.limitations)) card.append(node("p", "test-limitations", test.limitations.join(". ") + "."));
    $("drive-test-result").append(card);
    if (!inspectDetailsLoaded) $("diagnostics-details").textContent = JSON.stringify(test, null, 2);
  }
}

function renderCompression() {
  const tools = state?.tools || {};
  const engines = [tools["7z"] ? ["7z", "7-Zip · maximum"] : null, tools.winrar ? ["winrar", "WinRAR · best"] : null, ["lzma", "Built-in LZMA · maximum"]].filter(Boolean);
  const signature = JSON.stringify(engines);
  if ($("compression-engine").dataset.tools !== signature) {
    const previous = $("compression-engine").dataset.tools ? $("compression-engine").value : null;
    $("compression-engine").dataset.tools = signature;
    $("compression-engine").replaceChildren(...engines.map(([value, name]) => new Option(name, value)));
    $("compression-engine").value = engines.some(([value]) => value === previous) ? previous : engines[0][0];
  }
  $("open-7z").hidden = !tools["7z"];
  $("open-winrar").hidden = !tools.winrar;
  $("archiver-actions").hidden = !tools["7z"] && !tools.winrar;
  const estimate = state?.compression;
  $("compression-result").hidden = !estimate;
  $("cancel-compression").hidden = !activeCompression();
  if (!estimate) return;
  $("compression-state").textContent = estimate.state === "completed" ? "Estimate complete" : `Estimate ${estimate.state}`;
  const progress = estimate.progress !== null && estimate.progress !== undefined && Number.isFinite(Number(estimate.progress)) ? Math.min(100, Math.max(0, Number(estimate.progress))) : null;
  if (progress === null) $("compression-progress").removeAttribute("value");
  else $("compression-progress").value = progress;
  $("compression-percent").textContent = progress === null ? "Progress unknown" : `${Math.round(progress)}%`;
  $("compression-progress").hidden = !activeCompression();
  const completed = estimate.state === "completed";
  $("compression-totals").hidden = !completed;
  if (completed) {
    $("compression-original").textContent = bytes(estimate.total_bytes);
    $("compression-estimated").textContent = bytes(estimate.estimated_bytes);
    const savings = estimate.saved_percent !== null && estimate.saved_percent !== undefined && Number.isFinite(Number(estimate.saved_percent)) ? ` (${Number(estimate.saved_percent).toFixed(1)}%)` : "";
    $("compression-saved").textContent = `${bytes(estimate.saved_bytes)}${savings}`;
  }
  const method = estimate.engine === "7z" ? "7-Zip" : estimate.engine === "winrar" ? "WinRAR" : "LZMA";
  const detail = completed ? `${method} · ${estimate.file_count ?? 0} files · ${bytes(estimate.sampled_bytes)} sampled` : estimate.message || "";
  $("compression-detail").textContent = [estimate.path, detail, estimate.note].filter(Boolean).join(" · ");
  $("compression-error").hidden = !estimate.error;
  $("compression-error").textContent = estimate.error || "";
}

function renderTestDevices() {
  const drives = orderedDevices(state?.devices?.drives || []);
  const signature = JSON.stringify(drives.map((drive) => [drive.id, drive.letter, drive.name]));
  if ($("test-recorder").dataset.devices === signature) return;
  const previous = $("test-recorder").value;
  $("test-recorder").dataset.devices = signature;
  $("test-recorder").replaceChildren(new Option("Auto-detect mounted drive", ""));
  for (const drive of drives) $("test-recorder").append(new Option(`${drive.letter ? drive.letter + " · " : ""}${drive.name}`, String(drive.id)));
  if (previous && !drives.some((drive) => String(drive.id) === previous)) $("test-recorder").append(new Option("Selected drive · Disconnected", previous));
  $("test-recorder").value = previous;
}

function renderTests() {
  $("tests-result").hidden = !testResult;
  if (!testResult) return;
  $("tests-summary").textContent = testResult.summary || "Checks complete";
  $("tests-scope").textContent = testResult.scope || "";
  $("tests-checks").replaceChildren();
  for (const check of testResult.checks || []) {
    const card = node("div", "hardware-card");
    const status = check.passed === true ? "Passed" : check.passed === false ? "Failed" : "Not checked";
    const title = node("strong", check.passed === true ? "test-passed" : check.passed === false ? "test-failed" : "", `${status} · ${check.name}`);
    if (check.passed === true) title.prepend(icons("check"));
    card.append(title, node("p", "", check.message));
    $("tests-checks").append(card);
  }
  $("tests-limitations").textContent = testResult.note || "Physical player playback has not been tested.";
}

async function runTests() {
  await action(async () => {
    testResult = null;
    renderTests();
    $("run-tests").textContent = "Checking…";
    $("tests-heading").closest("section").setAttribute("aria-busy", "true");
    try {
      testResult = await api("/api/tests", { method: "POST", body: { recorder_id: $("test-recorder").value || null, path: $("test-path").value.trim() || null } });
      renderTests();
      announce(testResult.summary || "Read-only checks complete.");
    } finally {
      $("run-tests").textContent = "Run checks";
      $("tests-heading").closest("section").setAttribute("aria-busy", "false");
    }
  });
}

function applyState(result) {
  if (!result || !Array.isArray(result.files)) return;
  const hadJob = Boolean(activeJob());
  state = result;
  setConnection(true);
  const filesChanged = renderFiles();
  const oldDevices = deviceSignature;
  const destinationChanged = renderDevices();
  renderDependencies();
  renderJob();
  renderCompression();
  updateControls();
  if (filesChanged || oldDevices !== deviceSignature || destinationChanged || hadJob !== Boolean(activeJob())) schedulePlan();
}

async function loadState() {
  const version = mutationVersion;
  const result = await api("/api/state");
  if (version === mutationVersion) applyState(result);
  return result;
}

async function poll() {
  clearTimeout(pollTimer);
  if (pollInFlight || busyCount > 0 || uploading) { pollTimer = setTimeout(poll, 1500); return; }
  pollInFlight = true;
  try { await loadState(); }
  catch (error) {
    const wasOnline = online;
    setConnection(false);
    updateControls();
    if (wasOnline || !state) showError(error);
  } finally {
    pollInFlight = false;
    pollTimer = setTimeout(poll, activeJob() || activeCompression() ? 1500 : 5000);
  }
}

function pickFiles() {
  if (!online || busyCount || uploading || activeJob()) return;
  return action(async () => {
    mutationVersion += 1;
    const result = await api("/api/pick-files", { method: "POST" });
    if (Array.isArray(result?.files)) applyState(result);
    else await loadState();
    announce(`${state?.files?.length || 0} files in your project.`);
  });
}

function uploadFile(file, index, count) {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", "/api/upload");
    request.setRequestHeader("X-Luma-Token", state.session_token);
    request.setRequestHeader("X-File-Name", encodeURIComponent(file.name));
    request.setRequestHeader("Content-Type", "application/octet-stream");
    $("upload-text").textContent = `Adding ${file.name}${count > 1 ? ` (${index + 1} of ${count})` : ""}`;
    $("upload-percent").textContent = "";
    $("upload-progress").removeAttribute("value");
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        const percent = Math.min(100, Math.round(event.loaded / event.total * 100));
        $("upload-progress").value = percent;
        $("upload-percent").textContent = `${percent}%`;
        if (percent === 100) $("upload-text").textContent = `Inspecting ${file.name}…`;
      }
    };
    request.onload = () => {
      let result;
      try { result = JSON.parse(request.responseText); } catch { result = null; }
      if (request.status < 200 || request.status >= 300) reject(new Error(result?.error || `Could not add ${file.name} (${request.status}).`));
      else resolve(result);
    };
    request.onerror = () => reject(new Error(`Could not add ${file.name}. Check that the local application is still running.`));
    request.onabort = () => reject(new Error(`Adding ${file.name} was interrupted.`));
    request.send(file);
  });
}

async function uploadFiles(files) {
  if (!files.length) return;
  if (!online || busyCount || uploading || activeJob()) { showError("Wait for the current operation to finish before adding files."); return; }
  uploading = true;
  mutationVersion += 1;
  $("upload-status").hidden = false;
  updateControls();
  try {
    for (let index = 0; index < files.length; index += 1) {
      await uploadFile(files[index], index, files.length);
      await loadState();
    }
    announce(`${files.length} ${files.length === 1 ? "file" : "files"} added to your project.`);
  } catch (error) { showError(error); }
  finally {
    uploading = false;
    $("upload-status").hidden = true;
    updateControls();
    await loadState().catch(showError);
  }
}

async function startJob(actionName, body) {
  await action(async () => {
    mutationVersion += 1;
    await api("/api/jobs", { method: "POST", body: { ...(body || requestBody()), action: actionName } });
    await loadState();
    navigate("activity");
    clearTimeout(pollTimer);
    pollTimer = setTimeout(poll, 1000);
  });
}

$("add-files").addEventListener("click", pickFiles);
$("select-all").addEventListener("change", () => {
  excludedFiles.clear();
  if (!$("select-all").checked) for (const file of state?.files || []) excludedFiles.add(file.id);
  fileSignature = "";
  renderFiles();
  schedulePlan();
});
$("compression-path").addEventListener("input", updateControls);
for (const kind of ["file", "folder"]) $("pick-compression-" + kind).addEventListener("click", () => action(async () => {
  const result = await api("/api/pick-compression", { method: "POST", body: { kind } });
  if (result?.path) {
    $("compression-path").value = result.path;
    announce(`Compression source selected: ${result.path}`);
  }
}));
$("estimate-compression").addEventListener("click", () => action(async () => {
  mutationVersion += 1;
  await api("/api/compression/start", { method: "POST", body: { path: $("compression-path").value.trim(), engine: $("compression-engine").value } });
  await loadState();
  clearTimeout(pollTimer);
  pollTimer = setTimeout(poll, 1000);
  announce("Compression estimate started with maximum settings.");
}));
$("cancel-compression").addEventListener("click", () => action(async () => {
  mutationVersion += 1;
  await api("/api/compression/cancel", { method: "POST" });
  await loadState();
}));
for (const engine of ["7z", "winrar"]) $("open-" + engine).addEventListener("click", () => action(async () => {
  await api("/api/open-archiver", { method: "POST", body: { engine } });
}));
$("run-tests").addEventListener("click", runTests);
$("pick-test-folder").addEventListener("click", () => action(async () => {
  const result = await api("/api/pick-folder", { method: "POST" });
  if (result?.path) {
    $("test-path").value = result.path;
    testResult = null;
    renderTests();
    announce(`DVD folder selected: ${result.path}`);
  }
}));
for (const id of ["test-path", "test-recorder"]) $(id).addEventListener("change", () => { testResult = null; renderTests(); });
$("dismiss-error").addEventListener("click", () => { $("global-error").hidden = true; });
$("refresh-button").addEventListener("click", () => action(async () => {
  mutationVersion += 1;
  $("refresh-button").setAttribute("aria-busy", "true");
  try {
    const result = await api("/api/refresh", { method: "POST" });
    if (Array.isArray(result?.files)) applyState(result);
    else await loadState();
    schedulePlan();
    announce("Checking connected devices.");
  } finally { $("refresh-button").removeAttribute("aria-busy"); }
}));

document.querySelectorAll('input[name="preset"]').forEach((radio) => radio.addEventListener("change", () => { renderFormat(); schedulePlan(); }));
$("recorder").addEventListener("change", () => { manualRecorder = $("recorder").value; manualRecorderName = selectedRecorder()?.name || ""; renderDevices(); schedulePlan(); });
$("usb-target").addEventListener("change", () => {
  manualUSB = $("usb-target").value || null;
  manualUSBName = usbVolumes().find((volume) => volume.letter === manualUSB)?.label || "";
  outputEdited = manualUSB === null;
  renderDestination();
  schedulePlan();
});
for (const id of ["standard", "quality", "disc-label", "performance"]) $(id).addEventListener("change", schedulePlan);
$("disc-label").addEventListener("input", () => {
  const valid = /^[A-Za-z0-9 _-]{1,32}$/.test($("disc-label").value.trim());
  $("disc-label").setAttribute("aria-invalid", String(!valid));
  $("label-hint").textContent = valid ? "Up to 32 letters, numbers, spaces, or underscores." : "Use 1–32 letters, numbers, spaces, underscores, or hyphens.";
  schedulePlan();
});
$("output-dir").addEventListener("input", () => { outputEdited = true; manualUSB = null; renderDestination(); schedulePlan(); });
$("pick-folder").addEventListener("click", () => action(async () => {
  const result = await api("/api/pick-folder", { method: "POST" });
  if (result?.path) {
    $("output-dir").value = result.path;
    outputEdited = true;
    manualUSB = null;
    renderDestination();
    schedulePlan();
    announce(`Output folder selected: ${result.path}`);
  }
}));

$("prepare-button").addEventListener("click", () => startJob("prepare"));
$("burn-button").addEventListener("click", () => {
  const drive = selectedRecorder();
  if (!drive || !plan?.can_start || !drive.blank || !drive.writable) return;
  burnRequest = requestBody();
  burnReturnFocus = document.activeElement;
  const fileCount = burnRequest.file_ids.length;
  $("burn-description").textContent = `Write ${fileCount} ${fileCount === 1 ? "file" : "files"} as ${preset() === "dvd-video" ? "a DVD video disc" : "a data disc"} to ${drive.letter ? drive.letter + " · " : ""}${drive.name}. Disc writing cannot be cancelled once it begins.`;
  $("burn-dialog").showModal();
});
$("burn-cancel").addEventListener("click", () => $("burn-dialog").close());
$("burn-confirm").addEventListener("click", () => {
  const body = burnRequest;
  $("burn-dialog").close();
  if (body) startJob("burn", body);
});
$("burn-dialog").addEventListener("close", () => { burnRequest = null; burnReturnFocus?.focus(); });

$("cancel-job").addEventListener("click", () => action(async () => {
  if (!activeJob() || discWriteProtected(state.job)) return;
  mutationVersion += 1;
  await api(`/api/jobs/${encodeURIComponent(state.job.id)}/cancel`, { method: "POST" });
  await loadState();
}));
$("open-output").addEventListener("click", () => action(async () => {
  await api("/api/open-output", { method: "POST", body: { path: state?.job?.output_dir || $("output-dir").value.trim() } });
}));

window.addEventListener("hashchange", () => showPage());
$("inspect-system").addEventListener("click", () => action(async () => {
  $("inspect-system").disabled = true;
  $("diagnostics-details").closest("details").open = true;
  $("diagnostics-details").textContent = "Inspecting your computer…";
  try {
    const result = await api("/api/diagnostics");
    inspectDetailsLoaded = true;
    $("diagnostics-details").textContent = JSON.stringify(result, null, 2);
    $("diagnostics-details").closest("details").open = true;
    announce("System inspection complete.");
  } catch (error) {
    $("diagnostics-details").textContent = `Inspection failed: ${error.message}`;
    announce(`System inspection failed. ${error.message}`);
  } finally { $("inspect-system").disabled = false; }
}));

let dragDepth = 0;
$("drop-zone").addEventListener("dragenter", (event) => {
  if (event.dataTransfer?.types.includes("Files")) { event.preventDefault(); dragDepth += 1; $("drop-zone").classList.add("dragging"); }
});
$("drop-zone").addEventListener("dragover", (event) => { if (event.dataTransfer?.types.includes("Files")) { event.preventDefault(); event.dataTransfer.dropEffect = activeJob() || uploading ? "none" : "copy"; } });
$("drop-zone").addEventListener("dragleave", () => { dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) $("drop-zone").classList.remove("dragging"); });
$("drop-zone").addEventListener("drop", (event) => {
  event.preventDefault();
  dragDepth = 0;
  $("drop-zone").classList.remove("dragging");
  uploadFiles(Array.from(event.dataTransfer?.files || []));
});
window.addEventListener("dragover", (event) => { if (event.dataTransfer?.types.includes("Files")) event.preventDefault(); });
window.addEventListener("drop", (event) => { if (event.dataTransfer?.types.includes("Files")) event.preventDefault(); });
document.addEventListener("visibilitychange", () => { if (!document.hidden) { clearTimeout(pollTimer); poll(); } });

showPage(false);
renderFormat();
renderPlan();
poll();
