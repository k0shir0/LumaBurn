"""Local, offline-first media authoring application. Run with Python 3.11+."""
from __future__ import annotations

import argparse
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import threading
import time
from urllib.parse import unquote, urlsplit
import uuid
import webbrowser

from . import media
from . import compression
from . import dvdtests

ROOT = Path(__file__).resolve().parent.parent
LOCAL = ROOT / ".local"
TOKEN = secrets.token_urlsafe(32)
LOCK = threading.RLock()
PICKER_LOCK = threading.Lock()
DEVICE_READY = threading.Condition(LOCK)
FILES: dict[str, dict] = {}
DEVICES = {"drives": [], "volumes": [], "events": []}
CURRENT_JOB = None
COMPRESSION_TASK = None
TESTING = False
REFRESHING = False
DEVICE_REFRESH_SECONDS = 10
DEFAULT_OUTPUT = str(LOCAL / "exports")
PORT = 9847


def native(action: str, request: dict | None = None, job=None, timeout=60) -> dict:
    LOCAL.mkdir(exist_ok=True)
    config = LOCAL / ("request-" + uuid.uuid4().hex + ".json")
    config.write_text(json.dumps(request or {}), encoding="utf-8")
    script = ROOT / "native" / "optical.ps1"
    command = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "-Action", action, "-RequestPath", str(config)]
    try:
        if job is None:
            response = subprocess.run(command, capture_output=True, text=True, encoding="utf-8-sig", errors="replace", timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            lines = response.stdout.strip().splitlines()
            if not lines:
                raise RuntimeError(response.stderr.strip() or "The native drive service returned no result.")
            result = json.loads(lines[-1])
            if response.returncode or result.get("ok") is False:
                raise RuntimeError(result.get("error") or response.stderr.strip() or "Native operation failed.")
            return result
        result = None
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8-sig", errors="replace", creationflags=media.process_flags(job.request.get("performance", "gentle") == "gentle")) as process:
            # Disc writes are intentionally not exposed as killable media processes.
            if action == "image":
                job.process = process
            for line in process.stdout:
                if action == "image" and job.cancel.is_set():
                    process.terminate()
                    process.wait(timeout=10)
                    job.process = None
                    raise media.Cancelled("ISO export cancelled.")
                try:
                    event = json.loads(line.strip())
                    if "ok" in event:
                        result = event
                    elif "phase" in event:
                        changes = {k: event[k] for k in ["phase", "progress", "message"] if k in event}
                        raw = changes.get("progress")
                        changes["phase_progress"] = raw
                        ranges = {"imaging": (80, 18), "hashing": (98, 1)} if action == "image" else {"writing": (80, 15), "verifying": (95, 4)}
                        if raw is not None and event["phase"] in ranges:
                            start, span = ranges[event["phase"]]
                            changes["progress"] = round(start + max(0, min(100, float(raw))) / 100 * span, 1)
                        else:
                            changes["progress"] = None
                        job.update(**changes)
                    else:
                        job.log(line.strip())
                except ValueError:
                    job.log(line.strip())
            code = process.wait()
            if action == "image":
                job.process = None
                if job.cancel.is_set():
                    raise media.Cancelled("ISO export cancelled.")
        if code or not result or result.get("ok") is False:
            if result:
                job.update(native_result=result, burned=bool(result.get("burned")), media_may_be_changed=bool(result.get("media_may_be_changed") or result.get("burned")), verification=result.get("verification"))
                job.log(json.dumps(result, ensure_ascii=True))
            message = (result or {}).get("error") or "Native disc operation failed. Check the activity log."
            if result and result.get("media_may_be_changed") and not result.get("burned"):
                message += " The disc may be partially written. Check or replace it before trying again."
            raise RuntimeError(message)
        return result
    finally:
        config.unlink(missing_ok=True)


def refresh_devices(allow_active_job=False):
    global DEVICES, REFRESHING
    with LOCK:
        if REFRESHING:
            return False
        if not allow_active_job and CURRENT_JOB and CURRENT_JOB.data["state"] in {"queued", "running"}:
            return False
        if TESTING or (COMPRESSION_TASK and COMPRESSION_TASK.data["state"] in {"queued", "running"}):
            return False
        REFRESHING = True
    try:
        detected = native("inspect", timeout=30)
        with LOCK:
            DEVICES = {key: detected.get(key, []) for key in ["drives", "volumes", "events"]}
    except Exception as exc:
        with LOCK:
            DEVICES = {"drives": [], "volumes": [], "events": [{"message": str(exc), "level": "error"}]}
    finally:
        with LOCK:
            REFRESHING = False
            DEVICE_READY.notify_all()
    return True


def monitor_devices(stop):
    """Detect attachment/media changes while idle; never inspect during a running job."""
    while not stop.is_set():
        refresh_devices()
        if stop.wait(DEVICE_REFRESH_SECONDS):
            break


def add_file(path: Path, uploaded=False):
    path = path.resolve(strict=True)
    if not path.is_file():
        raise ValueError("Only regular files can be added.")
    record = media.probe(path)
    record.update(id=uuid.uuid4().hex, path=str(path), uploaded=uploaded)
    with LOCK:
        FILES[record["id"]] = record
    return public_file(record)


def public_file(file):
    return {k: v for k, v in file.items() if k not in {"path", "uploaded"}}


def snapshot():
    with LOCK:
        result = {"files": [public_file(f) for f in FILES.values()], "devices": copy.deepcopy(DEVICES), "tools": media.tools_status(), "job": copy.deepcopy(CURRENT_JOB.data) if CURRENT_JOB else None, "compression": copy.deepcopy(COMPRESSION_TASK.data) if COMPRESSION_TASK else None, "testing": TESTING, "presets": media.PRESETS, "default_output": DEFAULT_OUTPUT, "session_token": TOKEN, "refreshing": REFRESHING, "device_refresh_seconds": DEVICE_REFRESH_SECONDS}
    diagnostics = LOCAL / "drive-test.json"
    if diagnostics.is_file():
        try:
            result["diagnostics"] = json.loads(diagnostics.read_text(encoding="utf-8-sig"))
        except ValueError:
            pass
    return result


def selected_files(request):
    identifiers = request.get("file_ids")
    if not isinstance(identifiers, list) or any(not isinstance(i, str) for i in identifiers):
        raise ValueError("file_ids must be a list of selected file IDs.")
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("The same file was selected more than once.")
    with LOCK:
        try:
            return [dict(FILES[i]) for i in identifiers]
        except KeyError:
            raise ValueError("A selected file is no longer in the queue.") from None


class Job:
    def __init__(self, request, files):
        self.request = copy.deepcopy(request)
        self.files = files
        self.cancel = threading.Event()
        self.process = None
        self.noninterruptible = False
        self.last_progress = 0
        self.data = {"id": uuid.uuid4().hex, "action": self.request.get("action", "prepare"), "preset": self.request.get("preset"), "state": "queued", "phase": "queued", "progress": 0, "phase_progress": None, "message": "Waiting to prepare media", "logs": [], "output_dir": None, "error": None, "noninterruptible": False}

    def update(self, **changes):
        with LOCK:
            if changes.get("phase") != self.data["phase"] and "phase_progress" not in changes:
                changes["phase_progress"] = 100 if changes.get("state") == "completed" else None
            if changes.get("progress") is not None:
                ceiling = 100 if changes.get("state") == "completed" else 99
                self.last_progress = max(self.last_progress, min(ceiling, changes["progress"]))
                changes["progress"] = self.last_progress
            self.data.update(changes)

    def log(self, message):
        if message:
            with LOCK:
                self.data["logs"].append(message[-2000:])
                self.data["logs"] = self.data["logs"][-120:]

    def run(self):
        try:
            self.update(state="running", phase="preflight", message="Checking sources, disc capacity, and output space")
            # Admission marks this job queued before monitor scans can start; finish an older scan first.
            with DEVICE_READY:
                if not DEVICE_READY.wait_for(lambda: not REFRESHING, timeout=35):
                    raise RuntimeError("Device detection is still busy. Try again after it finishes.")
            if self.cancel.is_set():
                raise media.Cancelled("Cancelled before media preparation.")
            if self.request.get("action") == "burn":
                refresh_devices(allow_active_job=True)
            with LOCK:
                devices = copy.deepcopy(DEVICES)
            plan = media.make_plan(self.request, self.files, devices)
            if not plan["can_start"]:
                raise ValueError(" ".join(plan["errors"]))
            destination = Path(self.request.get("output_dir") or DEFAULT_OUTPUT).expanduser().resolve()
            # Every job gets a new folder. No source or previous output is overwritten.
            if not destination.exists():
                destination.mkdir(parents=True)
            if not destination.is_dir():
                raise ValueError("The output location must be a folder.")
            protected = [ROOT / "app", ROOT / "native", ROOT / "lumaburn", ROOT / "scripts", ROOT / ".git", ROOT / ".agents", ROOT / ".codex"]
            if any(destination == p or p in destination.parents for p in protected):
                raise ValueError("Choose a media output folder outside application source folders.")
            multiplier = 3 if self.request["preset"] == "dvd-video" else 2 if self.request["preset"] == "data-disc" else 1.1
            required = int(plan["estimated_bytes"] * multiplier + 64 * 1024 * 1024)
            if shutil.disk_usage(destination).free < required:
                raise ValueError(f"Not enough free space for conversion and verification. About {required / 1e9:.2f} GB is needed.")
            volume = next((v for v in devices.get("volumes", []) if destination.drive.upper().rstrip("\\") == v.get("letter", "").upper().rstrip("\\")), None)
            if volume and volume.get("filesystem", "").upper() == "FAT32" and self.request["preset"] == "usb-original" and any(f["size"] >= 4_294_967_296 for f in self.files):
                raise ValueError("FAT32 cannot hold files of 4 GiB or larger. Choose an exFAT/NTFS location or convert the media.")
            output = destination / (time.strftime("LumaBurn-%Y%m%d-%H%M%S-") + self.data["id"][:6])
            output.mkdir()
            self.update(output_dir=str(output))
            authored = media.encode_files(self.request, self.files, plan, output, self)
            if self.cancel.is_set():
                raise media.Cancelled("Cancelled before disc writing.")
            if self.request["preset"] in {"dvd-video", "data-disc"}:
                size = sum(p.stat().st_size for p in authored.rglob("*") if p.is_file())
                if size > plan["capacity_bytes"] * 0.98:
                    raise ValueError("The completed media exceeds the disc budget. Nothing was burned; reduce the selection and retry.")
                disc_request = {"recorder_id": self.request.get("recorder_id"), "source_dir": str(authored), "label": media.clean_name(self.request.get("label", "LUMABURN"))[:32], "dvd_video": self.request["preset"] == "dvd-video", "verify": True, "speed": -1, "gentle": self.request.get("performance", "gentle") == "gentle"}
                if self.request.get("action") == "burn":
                    with LOCK:
                        if self.cancel.is_set():
                            raise media.Cancelled("Cancelled before disc writing.")
                        self.noninterruptible = True
                        self.update(phase="burning", progress=None, noninterruptible=True, message="Writing and finalizing the disc. Keep the drive connected.")
                    result = native("burn", disc_request, job=self)
                    self.update(burned=result.get("burned") is True, finalized=result.get("finalized") is True, verification=result)
                    if result.get("burned") is not True or result.get("finalized") is not True:
                        raise RuntimeError("The drive did not confirm writing and finalizing the disc. Check the Activity details.")
                    verification = result.get("verification") or {}
                    if (verification.get("ok") is not True or verification.get("verified") is not True
                            or not verification.get("files_expected")
                            or verification.get("files_verified") != verification.get("files_expected")):
                        raise RuntimeError("Disc read-back did not verify every file. The burn is not marked as successful.")
                    refresh_devices(allow_active_job=True)
                else:
                    self.update(phase="imaging", progress=None, message="Creating an ISO image")
                    disc_request["output_path"] = str(output / "LumaBurn.iso")
                    result = native("image", disc_request, job=self)
                    self.update(image_path=disc_request["output_path"])
            if volume and volume.get("filesystem", "").upper() == "FAT32" and any(p.stat().st_size >= 4_294_967_296 for p in output.rglob("*") if p.is_file()):
                raise ValueError("An output exceeds FAT32's file-size limit. Use exFAT or NTFS.")
            with LOCK:
                if self.cancel.is_set():
                    raise media.Cancelled("Media preparation cancelled.")
                if self.request.get("action") == "burn":
                    message = "Disc written, finalized and read-back verified"
                elif self.data.get("image_path"):
                    message = "ISO saved. No disc was written."
                else:
                    message = "Your media is ready"
                self.update(state="completed", phase="completed", progress=100, message=message)
        except media.Cancelled as exc:
            self.update(state="cancelled", phase="cancelled", message=str(exc), error=None)
        except Exception as exc:
            self.log(str(exc))
            self.update(state="failed", phase="failed", message="The operation stopped; see the details below", error=str(exc))


class CompressionTask:
    def __init__(self, path, engine):
        self.path, self.engine = path, engine
        self.cancel = threading.Event()
        self.thread = None
        self.data = {"id": uuid.uuid4().hex, "state": "queued", "progress": 0, "engine": engine, "path": str(path), "message": "Waiting to sample files", "error": None}

    def update(self, **changes):
        with LOCK:
            self.data.update(changes)

    def run(self):
        try:
            with DEVICE_READY:
                if not DEVICE_READY.wait_for(lambda: not REFRESHING, timeout=35):
                    raise RuntimeError("Device detection is busy. Try again when it finishes.")
            self.update(state="running")
            result = compression.estimate(self.path, self.engine, {"7z": media.tool_path("7z"), "rar": media.tool_path("rar")}, self.update, self.cancel)
            self.update(**result, state="completed", progress=100, message="Compression estimate ready")
        except media.Cancelled as exc:
            self.update(state="cancelled", message=str(exc))
        except Exception as exc:
            self.update(state="failed", error=str(exc), message="Compression estimate stopped")


def require_idle():
    # Called under LOCK so tests, estimation, and media preparation cannot race at admission.
    if TESTING or (CURRENT_JOB and CURRENT_JOB.data["state"] in {"queued", "running"}) or (COMPRESSION_TASK and COMPRESSION_TASK.data["state"] in {"queued", "running"}):
        raise ValueError("Wait for the current media job, estimate, or disc test to finish.")


def picker(name):
    if not PICKER_LOCK.acquire(blocking=False):
        raise ValueError("A file or folder picker is already open. Finish or cancel it first.")
    try:
        result = subprocess.run(["powershell.exe", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "native" / (name + ".ps1"))], capture_output=True, text=True, encoding="utf-8-sig", errors="replace", timeout=600, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "The file picker failed.")
        return json.loads(result.stdout.strip())
    finally:
        PICKER_LOCK.release()


class Handler(BaseHTTPRequestHandler):
    server_version = "LumaBurn/0.1"

    def log_message(self, *_):
        pass

    def send_bytes(self, body, content_type="application/json; charset=utf-8", status=200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(body)

    def json(self, result, status=200):
        self.send_bytes(json.dumps(result, ensure_ascii=True).encode(), status=status)

    def validate(self, mutation=False):
        if self.headers.get("Host") != f"127.0.0.1:{PORT}":
            raise PermissionError("Use the app's 127.0.0.1 address.")
        origin = self.headers.get("Origin")
        if origin and origin != f"http://127.0.0.1:{PORT}":
            raise PermissionError("Cross-origin requests are refused.")
        if mutation and not secrets.compare_digest(self.headers.get("X-Luma-Token", ""), TOKEN):
            raise PermissionError("The app session expired. Reload the page.")

    def read_json(self):
        size = int(self.headers.get("Content-Length", "0"))
        if size < 0 or size > 1_048_576:
            raise ValueError("Request too large.")
        result = json.loads(self.rfile.read(size) or b"{}")
        if not isinstance(result, dict):
            raise ValueError("Expected a JSON object.")
        return result

    def do_GET(self):
        try:
            self.validate()
            path = urlsplit(self.path).path
            if path == "/api/state":
                self.json(snapshot())
            elif path == "/api/diagnostics":
                current = snapshot()
                self.json({"devices": current["devices"], "test": current.get("diagnostics")})
            elif path == "/docs/help":
                self.send_bytes((ROOT / "README.md").read_bytes(), "text/plain; charset=utf-8")
            elif path in {"/", "/index.html", "/style.css", "/app.js"}:
                file = ROOT / "app" / ("index.html" if path == "/" else path[1:])
                types = {".html": "text/html", ".css": "text/css", ".js": "text/javascript"}
                self.send_bytes(file.read_bytes(), types[file.suffix] + "; charset=utf-8")
            else:
                self.json({"error": "Not found"}, 404)
        except PermissionError as exc:
            self.json({"error": str(exc)}, 403)
        except (OSError, ValueError) as exc:
            self.json({"error": str(exc)}, 400)

    def do_POST(self):
        global CURRENT_JOB, COMPRESSION_TASK, TESTING, DEVICES
        try:
            self.validate(mutation=True)
            path = urlsplit(self.path).path
            if path == "/api/upload":
                self.upload()
                return
            request = self.read_json()
            if path == "/api/refresh":
                threading.Thread(target=refresh_devices, daemon=True).start()
                self.json({"ok": True})
            elif path == "/api/pick-files":
                paths = picker("pick-files").get("paths", [])
                for selected in paths:
                    add_file(Path(selected))
                self.json(snapshot())
            elif path == "/api/pick-folder":
                self.json(picker("pick-folder"))
            elif path == "/api/pick-compression":
                if request.get("kind") == "folder":
                    self.json(picker("pick-folder"))
                elif request.get("kind") == "file":
                    paths = picker("pick-files").get("paths", [])
                    self.json({"path": paths[0] if paths else None})
                else:
                    raise ValueError("Choose file or folder.")
            elif path == "/api/compression/start":
                if request.get("engine", "lzma") not in {"lzma", "7z", "winrar"}:
                    raise ValueError("Choose LZMA, 7-Zip, or WinRAR.")
                if not isinstance(request.get("path"), str) or not request["path"].strip():
                    raise ValueError("Choose a file or folder to estimate.")
                with LOCK:
                    require_idle()
                    COMPRESSION_TASK = CompressionTask(Path(request["path"]).expanduser(), request.get("engine", "lzma"))
                    task = COMPRESSION_TASK
                    task.thread = threading.Thread(target=task.run, daemon=True)
                    task.thread.start()
                    self.json({"compression": copy.deepcopy(task.data)}, 202)
            elif path == "/api/compression/cancel":
                with LOCK:
                    if not COMPRESSION_TASK or COMPRESSION_TASK.data["state"] not in {"queued", "running"}:
                        raise ValueError("No estimate is running.")
                    COMPRESSION_TASK.cancel.set()
                self.json({"ok": True})
            elif path == "/api/open-archiver":
                executable = {"7z": "7zFM", "winrar": "WinRAR"}.get(request.get("engine"))
                executable = media.tool_path(executable) if executable else None
                if not executable:
                    raise ValueError("The selected archiver is not installed. See Help for installation instructions.")
                # These are the visible interactive programs the user explicitly chose to open.
                subprocess.Popen([executable])
                self.json({"ok": True})
            elif path == "/api/tests":
                with LOCK:
                    require_idle()
                    TESTING = True
                try:
                    with DEVICE_READY:
                        if not DEVICE_READY.wait_for(lambda: not REFRESHING, timeout=35):
                            raise ValueError("Device detection is still busy.")
                    location = request.get("path")
                    if location is not None and not isinstance(location, str):
                        raise ValueError("The DVD location must be a folder path.")
                    if not location:
                        detected = native("inspect", timeout=30)
                        with LOCK:
                            DEVICES = {key: detected.get(key, []) for key in ["drives", "volumes", "events"]}
                    with LOCK:
                        drives = copy.deepcopy(DEVICES["drives"])
                    drive = next((d for d in drives if d["id"] == request.get("recorder_id")), drives[0] if drives and not request.get("recorder_id") else None)
                    self.json(dvdtests.inspect(Path(location).expanduser() if location else None, drive))
                finally:
                    with LOCK:
                        TESTING = False
            elif path == "/api/plan":
                with LOCK:
                    devices = copy.deepcopy(DEVICES)
                self.json(media.make_plan(request, selected_files(request), devices))
            elif path == "/api/jobs":
                if request.get("action", "prepare") not in {"prepare", "burn"}:
                    raise ValueError("Choose prepare or burn.")
                request.setdefault("action", "prepare")
                request.setdefault("preset", "dvd-video")
                files = selected_files(request)
                with LOCK:
                    require_idle()
                    plan = media.make_plan(request, files, DEVICES)
                    if not plan["can_start"]:
                        raise ValueError(" ".join(plan["errors"]))
                    CURRENT_JOB = Job(request, files)
                    job = CURRENT_JOB
                    threading.Thread(target=job.run, daemon=True).start()
                    self.json({"job": copy.deepcopy(job.data)}, 202)
            elif path.endswith("/cancel") and path.startswith("/api/jobs/"):
                with LOCK:
                    job = CURRENT_JOB
                    if not job or path != f"/api/jobs/{job.data['id']}/cancel":
                        raise ValueError("Job not found.")
                    if job.data["state"] not in {"queued", "running"}:
                        raise ValueError("This job has already finished.")
                    if job.noninterruptible:
                        raise ValueError("Disc writing and verification cannot be interrupted safely. Wait for completion.")
                    job.cancel.set()
                    if job.process and job.process.poll() is None:
                        job.process.terminate()
                    self.json({"ok": True})
            elif path == "/api/open-output":
                with LOCK:
                    output = CURRENT_JOB.data.get("output_dir") if CURRENT_JOB else None
                output = Path(output or DEFAULT_OUTPUT).resolve()
                if not output.is_dir():
                    raise ValueError("There is no output folder yet.")
                os.startfile(str(output))
                self.json({"ok": True})
            else:
                self.json({"error": "Not found"}, 404)
        except PermissionError as exc:
            self.json({"error": str(exc)}, 403)
        except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            self.json({"error": str(exc)}, 400)

    def upload(self):
        size = int(self.headers.get("Content-Length", "0"))
        if size <= 0 or size > 100_000_000_000:
            raise ValueError("Select a nonempty file smaller than 100 GB.")
        name = media.clean_name(unquote(self.headers.get("X-File-Name", "media")))
        staging = LOCAL / "uploads" / uuid.uuid4().hex
        staging.mkdir(parents=True)
        file = staging / name
        if shutil.disk_usage(staging).free < size + 64 * 1024 * 1024:
            raise ValueError("Not enough staging space. Use Add files to select originals without copying them.")
        try:
            self.connection.settimeout(60)
            remaining = size
            with file.open("xb") as output:
                while remaining:
                    block = self.rfile.read(min(4 * 1024 * 1024, remaining))
                    if not block:
                        raise ValueError("The file transfer was interrupted.")
                    output.write(block)
                    remaining -= len(block)
            self.json({"file": add_file(file, uploaded=True)}, 201)
        except Exception:
            file.unlink(missing_ok=True)
            raise

    def do_DELETE(self):
        try:
            self.validate(mutation=True)
            path = urlsplit(self.path).path
            if not path.startswith("/api/files/"):
                self.json({"error": "Not found"}, 404)
                return
            with LOCK:
                removed = FILES.pop(path.rsplit("/", 1)[1], None)
            if not removed:
                raise ValueError("File not found.")
            # Uploaded source data is retained for immutable running job snapshots.
            self.json({"ok": True})
        except PermissionError as exc:
            self.json({"error": str(exc)}, 403)
        except ValueError as exc:
            self.json({"error": str(exc)}, 400)


def main():
    global PORT
    parser = argparse.ArgumentParser(description="LumaBurn local media studio")
    parser.add_argument("--port", type=int, default=9847)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    PORT = args.port
    LOCAL.mkdir(exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    stop_monitor = threading.Event()
    device_thread = threading.Thread(target=monitor_devices, args=(stop_monitor,), daemon=True)
    device_thread.start()
    print(f"LumaBurn is running at http://127.0.0.1:{PORT}", flush=True)
    if not args.no_browser:
        webbrowser.open(f"http://127.0.0.1:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        if CURRENT_JOB and CURRENT_JOB.data["state"] == "running" and CURRENT_JOB.noninterruptible:
            print("Waiting for the disc operation to finish before closing.", flush=True)
            while CURRENT_JOB.data["state"] == "running":
                time.sleep(1)
        elif CURRENT_JOB and CURRENT_JOB.data["state"] in {"queued", "running"}:
            CURRENT_JOB.cancel.set()
            if CURRENT_JOB.process and CURRENT_JOB.process.poll() is None:
                CURRENT_JOB.process.terminate()
    finally:
        stop_monitor.set()
        if COMPRESSION_TASK and COMPRESSION_TASK.data["state"] in {"queued", "running"}:
            COMPRESSION_TASK.cancel.set()
            if COMPRESSION_TASK.thread:
                # Let the worker terminate the compressor and remove its disposable sample folder.
                COMPRESSION_TASK.thread.join()
        server.server_close()


if __name__ == "__main__":
    main()
