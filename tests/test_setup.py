"""Download trust boundaries, interrupted setup, and first-run orchestration."""
import hashlib
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from lumaburn import media, server, setup


def archive(files):
    stream = io.BytesIO()
    with ZipFile(stream, "w") as bundle:
        for name, content in files.items():
            bundle.writestr(name, content)
    return stream.getvalue()


def response(payload):
    stream = io.BytesIO(payload)
    stream.headers = {"Content-Length": str(len(payload))}
    return stream


class SetupTests(unittest.TestCase):
    def test_verified_cache_is_reused_without_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            (cache / "tool.zip").write_bytes(b"verified")
            package = ("tool.zip", "https://example.test/tool", hashlib.sha256(b"verified").hexdigest())
            with patch("lumaburn.setup.urlopen") as network:
                self.assertEqual(setup.download(package, cache, lambda **_: None), cache / "tool.zip")
                network.assert_not_called()

    def test_bad_download_never_becomes_a_cached_executable(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            package = ("tool.exe", "https://example.test/tool", "0" * 64)
            with patch("lumaburn.setup.urlopen", return_value=response(b"corrupt")):
                with self.assertRaisesRegex(RuntimeError, "verification failed"):
                    setup.download(package, cache, lambda **_: None)
            self.assertFalse((cache / "tool.exe").exists())
            self.assertFalse((cache / "tool.exe.part").exists())

    def test_interrupted_download_can_retry_and_replace_a_bad_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            target = cache / "tool.zip"
            target.write_bytes(b"bad cache")
            package = (target.name, "https://example.test/tool", hashlib.sha256(b"good").hexdigest())
            with patch("lumaburn.setup.urlopen", side_effect=OSError("offline")):
                with self.assertRaises(OSError):
                    setup.download(package, cache, lambda **_: None)
            with patch("lumaburn.setup.urlopen", return_value=response(b"good")):
                setup.download(package, cache, lambda **_: None)
            self.assertEqual(target.read_bytes(), b"good")

    def test_archive_cannot_write_outside_staging(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = root / "unsafe.zip"
            bundle.write_bytes(archive({"../outside.exe": b"bad"}))
            with self.assertRaisesRegex(ValueError, "unsafe path"):
                setup.extract_zip(bundle, root / "staging")
            self.assertFalse((root / "outside.exe").exists())

    def test_interrupted_install_is_not_detected_as_ready(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            (source / "bin").mkdir(parents=True)
            (source / "bin/ffmpeg.exe").write_bytes(b"tool")
            destination = root / "tools/ffmpeg"
            destination.mkdir(parents=True)
            (destination / ".ready").touch()
            with patch("lumaburn.setup.shutil.copytree", side_effect=OSError("interrupted")):
                with self.assertRaises(OSError):
                    setup.publish(source, destination, ("ffmpeg",))
            self.assertFalse((destination / ".ready").exists())

    def test_first_setup_and_second_offline_launch(self):
        ffmpeg = archive({"ffmpeg-9.0.2-essentials_build/bin/ffmpeg.exe": b"encoder", "ffmpeg-9.0.2-essentials_build/bin/ffprobe.exe": b"probe", "ffmpeg-9.0.2-essentials_build/LICENSE": b"license"})
        extractor = archive({"innoextract.exe": b"extractor"})
        payloads = {"https://example.test/ffmpeg": ffmpeg, "https://example.test/dvd": b"installer", "https://example.test/inno": extractor}
        packages = [(name, url, hashlib.sha256(payloads[url]).hexdigest()) for name, url in [("ffmpeg.zip", "https://example.test/ffmpeg"), ("dvd.exe", "https://example.test/dvd"), ("inno.zip", "https://example.test/inno")]]

        def extract(command, **_):
            self.assertTrue(command[0].endswith("innoextract.exe"))
            destination = Path(command[command.index("--output-dir") + 1]) / "app/bin"
            destination.mkdir(parents=True)
            (destination / "dvdauthor.exe").write_bytes(b"author")
            (destination / "required.dll").write_bytes(b"dependency")
            return subprocess.CompletedProcess(command, 0)

        with tempfile.TemporaryDirectory() as temporary, patch.object(media, "ROOT", Path(temporary)), patch.dict("os.environ", {}, clear=True), patch("lumaburn.media.shutil.which", return_value=None), patch("lumaburn.setup.platform.machine", return_value="AMD64"), patch.object(setup, "FFMPEG", packages[0]), patch.object(setup, "DVDSTYLER", packages[1]), patch.object(setup, "INNOEXTRACT", packages[2]), patch("lumaburn.setup.urlopen", side_effect=lambda request, **_: response(payloads[request.full_url])) as network, patch("lumaburn.setup.subprocess.run", side_effect=extract) as process:
            setup.install(lambda **_: None)
            self.assertTrue(all(media.tool_path(name) for name in setup.REQUIRED))
            self.assertTrue((media.ROOT / "tools/authoring/bin/required.dll").is_file())
            self.assertEqual(network.call_count, 3)
            process.assert_called_once()
            setup.install(lambda **_: None)
            self.assertEqual(network.call_count, 3, "A second launch must not download anything")
            self.assertEqual(process.call_count, 1)

    def test_setup_admission_status_and_stale_media_metadata(self):
        state = {"state": "idle", "message": "", "progress": None}
        record = {"id": "source", "path": "video.mkv", "error": "FFprobe is missing", "duration": 0}
        with patch.object(server, "TOOL_SETUP", state), patch.object(server, "FILES", {"source": record}), patch("lumaburn.server.threading.Thread") as thread:
            server.start_tool_setup()
            server.start_tool_setup()
            thread.assert_called_once()
            with self.assertRaisesRegex(ValueError, "setup to finish"):
                server.require_idle()
            with patch("lumaburn.setup.install"), patch("lumaburn.media.probe", return_value={"duration": 12, "video": True}):
                server.prepare_tools()
            self.assertEqual(state["state"], "completed")
            self.assertEqual(record["duration"], 12)
            self.assertNotIn("error", record)
            with patch("lumaburn.setup.install", side_effect=OSError("offline")):
                server.prepare_tools()
            self.assertEqual(state["state"], "failed")
            self.assertIn("offline", state["message"])


if __name__ == "__main__":
    unittest.main()
