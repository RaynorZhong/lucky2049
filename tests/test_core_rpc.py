#!/usr/bin/env python3
"""
Locks Bitcoin Core JSON-RPC auth. `urllib` does NOT send Basic auth from a
`http://user:pass@host` URL (it even mis-parses `user:pass@host` as the
hostname), so verify._rpc_call must add the Authorization header itself and
connect to the clean host. This stands up a tiny auth-checking HTTP server and
confirms it -- guarding both `_rpc_call` and `fetch_from_core`. Stdlib only.
"""
import base64
import http.server
import json
import os
import sys
import threading
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
import verify  # noqa: E402

_EXPECTED = "Basic " + base64.b64encode(b"u:p").decode()


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        if self.headers.get("Authorization") != _EXPECTED:
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b"unauthorized")
            return
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        result = {"getblockcount": 800000, "getblockhash": "ab" * 32}.get(body["method"])
        out = json.dumps({"result": result, "error": None, "id": body.get("id")}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


class TestCoreRpcAuth(unittest.TestCase):
    def setUp(self):
        self.srv = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.port = self.srv.server_address[1]

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def test_authenticates_and_connects(self):
        # right creds in the URL userinfo -> Basic auth is sent + clean host is connected
        url = "http://u:p@127.0.0.1:%d/" % self.port
        self.assertEqual(verify._rpc_call(url, "getblockcount", []), 800000)

    def test_wrong_password_rejected(self):
        with self.assertRaises(Exception):
            verify._rpc_call("http://u:wrong@127.0.0.1:%d/" % self.port, "getblockcount", [])

    def test_fetch_from_core_end_to_end(self):
        with mock.patch.dict(os.environ, {"BITCOIN_RPC_URL": "http://u:p@127.0.0.1:%d/" % self.port}):
            hashes = verify.fetch_from_core(0, 2)  # 3 getblockhash calls
        self.assertEqual(len(hashes), 3)
        self.assertTrue(all(len(h) == 64 for h in hashes))


if __name__ == "__main__":
    unittest.main()
