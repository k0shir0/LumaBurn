"""Local HTTP trust boundaries, without drive operations."""
import http.client
import json
from pathlib import Path
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lumaburn import server


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.previous_port = server.PORT
        cls.http = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        server.PORT = cls.http.server_port
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join()
        server.PORT = cls.previous_port

    def request(self, method, path, headers=None, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.http.server_port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_mutations_require_current_token(self):
        status, _ = self.request("POST", "/api/plan", body=b"{}")
        self.assertEqual(status, 403)
        status, response = self.request("POST", "/api/plan", headers={"X-Luma-Token": server.TOKEN}, body=b'{"preset":"usb-original","file_ids":[]}')
        self.assertEqual(status, 200)
        self.assertFalse(response["can_start"])

    def test_host_and_origin_are_rejected(self):
        for headers in [{"Host": "evil.example"}, {"Origin": "https://evil.example"}]:
            status, _ = self.request("GET", "/api/state", headers=headers)
            self.assertEqual(status, 403)

    def test_state_does_not_disclose_source_paths(self):
        record = {"id": "http-fixture", "name": "media.mkv", "path": "C:\\private\\source.mkv", "uploaded": False, "size": 1}
        with server.LOCK:
            server.FILES[record["id"]] = record
        try:
            status, state = self.request("GET", "/api/state")
            self.assertEqual(status, 200)
            exposed = next(f for f in state["files"] if f["id"] == record["id"])
            self.assertNotIn("path", exposed)
            self.assertNotIn("uploaded", exposed)
        finally:
            server.FILES.pop(record["id"], None)

if __name__ == "__main__":
    unittest.main()
