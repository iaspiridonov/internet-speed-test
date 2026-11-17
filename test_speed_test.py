import contextlib
import io
import subprocess
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import speed_test


BODY = b"x" * (speed_test.CHUNK_SIZE * 2 + 123)
SCRIPT = Path(__file__).with_name("speed_test.py")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.server.paths.append(self.path)
        self.server.active += 1
        self.server.max_active = max(self.server.max_active, self.server.active)
        try:
            if self.path == "/error":
                self.send_error(503)
                return
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/file")
                self.end_headers()
                return
            self.send_response(200)
            if self.path == "/chunked":
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                self.wfile.write(b"3\r\nabc\r\n2\r\nde\r\n0\r\n\r\n")
                return
            if self.path == "/broken-chunk":
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                self.wfile.write(b"10\r\nshort")
                return
            body = b"" if self.path == "/empty" else BODY
            if self.path != "/no-length":
                self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.path == "/truncated":
                self.wfile.write(body[:20])
            elif self.path in ("/slow", "/timeout"):
                self.wfile.write(body[:1])
                self.wfile.flush()
                time.sleep(0.12)
                self.wfile.write(body[1:])
            else:
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            self.server.active -= 1


class DownloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.paths = []
        cls.server.active = 0
        cls.server.max_active = 0
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def run_cli(self, endpoint, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), self.base + endpoint, *args],
            text=True, capture_output=True, timeout=10,
        )

    def test_ten_complete_sequential_requests(self):
        self.server.paths.clear()
        self.server.max_active = 0
        result = self.run_cli("/slow")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.server.paths, ["/slow"] * 10)
        self.assertEqual(self.server.max_active, 1)
        self.assertIn(f"Всего скачано: {10 * len(BODY)} байт", result.stdout)
        self.assertIn("Скорость скачивания:", result.stdout)
        self.assertIn("MiB/s", result.stdout)

    def test_timer_includes_full_body(self):
        size, elapsed = speed_test.download(self.base + "/slow", 2)
        self.assertEqual(size, len(BODY))
        self.assertGreaterEqual(elapsed, 0.1)

    def test_without_content_length(self):
        size, _ = speed_test.download(self.base + "/no-length", 2)
        self.assertEqual(size, len(BODY))

    def test_chunked_body_excludes_chunk_framing(self):
        size, _ = speed_test.download(self.base + "/chunked", 2)
        self.assertEqual(size, 5)

    def test_redirect(self):
        size, _ = speed_test.download(self.base + "/redirect", 2)
        self.assertEqual(size, len(BODY))

    def test_empty_body(self):
        result = self.run_cli("/empty")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Скорость скачивания: 0.000 MiB/s", result.stdout)

    def test_failed_downloads_have_no_success_summary(self):
        for endpoint in ("/error", "/truncated", "/broken-chunk", "/timeout"):
            with self.subTest(endpoint=endpoint):
                result = self.run_cli(endpoint, "--timeout", "0.05")
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertNotIn("Скорость скачивания:", result.stdout)
                self.assertIn("Замер не завершён", result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_invalid_arguments(self):
        for args in (
            [], ["file:///tmp/file"], ["https://example.com:bad/file"],
            [self.base, "--timeout", "0"], [self.base, "--timeout", "nan"],
            [self.base, "--timeout", "inf"],
        ):
            with self.subTest(args=args):
                result = subprocess.run(
                    [sys.executable, str(SCRIPT), *args],
                    text=True, capture_output=True, timeout=5,
                )
                self.assertEqual(result.returncode, 2)
                self.assertNotIn("Traceback", result.stderr)

    def test_summary_uses_total_bytes_over_total_time(self):
        # Different sizes and durations distinguish weighted throughput
        # from an arithmetic mean of individual request speeds.
        downloads = [(speed_test.MIB, 1.0)] * 5 + [(9 * speed_test.MIB, 3.0)] * 5
        output = io.StringIO()
        with patch.object(sys, "argv", [str(SCRIPT), self.base]), \
                patch.object(speed_test, "download", side_effect=downloads), \
                contextlib.redirect_stdout(output):
            self.assertEqual(speed_test.main(), 0)
        self.assertIn("Среднее время запроса: 2.000 с", output.getvalue())
        self.assertIn("Скорость скачивания: 2.500 MiB/s", output.getvalue())


if __name__ == "__main__":
    unittest.main()
