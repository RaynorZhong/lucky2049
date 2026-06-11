#!/usr/bin/env python3
"""
Locks Bitcoin Core JSON-RPC access. Three behaviours, all against a tiny local
auth-checking HTTP server (no network, stdlib only):

  1. Basic auth: `urllib` does NOT send credentials from a `http://user:pass@`
     URL (it even mis-parses `user:pass@host` as the hostname), so
     verify._rpc_call must strip the userinfo, add the Authorization header
     itself, and connect to the clean host.
  2. Cloudflare Access: when CF_ACCESS_CLIENT_ID/SECRET are set, _rpc_call sends
     the service-token headers (for a node published through Zero Trust);
     without them, it doesn't.
  3. IBD guard: extend_pages' core provider abstains from the chain tip while
     the node is in initial block download (stale height must not gate draws).
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
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

import verify  # noqa: E402
import extend_pages  # noqa: E402

_EXPECTED = "Basic " + base64.b64encode(b"u:p").decode()


class _Handler(http.server.BaseHTTPRequestHandler):
    responses = {}      # method -> result; reset per test in setUp
    last_headers = None  # the most recent request's headers (case-insensitive)

    def do_POST(self):
        type(self).last_headers = self.headers
        if self.headers.get("Authorization") != _EXPECTED:
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b"unauthorized")
            return
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        out = json.dumps({"result": type(self).responses.get(body["method"]),
                          "error": None, "id": body.get("id")}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


class _RpcServerCase(unittest.TestCase):
    def setUp(self):
        _Handler.responses = {"getblockcount": 800000, "getblockhash": "ab" * 32}
        _Handler.last_headers = None
        self.srv = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = "http://u:p@127.0.0.1:%d/" % self.srv.server_address[1]

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()


class TestCoreRpcAuth(_RpcServerCase):
    def test_authenticates_and_connects(self):
        # creds in the URL userinfo -> Basic auth sent + clean host connected
        self.assertEqual(verify._rpc_call(self.url, "getblockcount", []), 800000)

    def test_wrong_password_rejected(self):
        bad = self.url.replace("u:p@", "u:wrong@")
        with self.assertRaises(Exception):
            verify._rpc_call(bad, "getblockcount", [])

    def test_fetch_from_core_end_to_end(self):
        with mock.patch.dict(os.environ, {"BITCOIN_RPC_URL": self.url}):
            hashes = verify.fetch_from_core(0, 2)  # 3 getblockhash calls
        self.assertEqual(len(hashes), 3)
        self.assertTrue(all(len(h) == 64 for h in hashes))


class TestCloudflareAccessHeaders(_RpcServerCase):
    def test_sent_when_configured(self):
        env = {"CF_ACCESS_CLIENT_ID": "abc.access", "CF_ACCESS_CLIENT_SECRET": "s3cret"}
        with mock.patch.dict(os.environ, env):
            verify._rpc_call(self.url, "getblockcount", [])
        self.assertEqual(_Handler.last_headers.get("CF-Access-Client-Id"), "abc.access")
        self.assertEqual(_Handler.last_headers.get("CF-Access-Client-Secret"), "s3cret")

    def test_absent_when_not_configured(self):
        env = {k: v for k, v in os.environ.items()
               if k not in ("CF_ACCESS_CLIENT_ID", "CF_ACCESS_CLIENT_SECRET")}
        with mock.patch.dict(os.environ, env, clear=True):
            verify._rpc_call(self.url, "getblockcount", [])
        self.assertIsNone(_Handler.last_headers.get("CF-Access-Client-Id"))
        self.assertIsNone(_Handler.last_headers.get("CF-Access-Client-Secret"))


class TestCoreProviderTip(_RpcServerCase):
    def _provider(self):
        with mock.patch.dict(os.environ, {"BITCOIN_RPC_URL": self.url}):
            return extend_pages._core_provider()

    def test_abstains_during_initial_block_download(self):
        _Handler.responses["getblockchaininfo"] = {"blocks": 500000, "initialblockdownload": True}
        with self.assertRaisesRegex(RuntimeError, "initial block download"):
            self._provider()["tip"]()

    def test_serves_tip_when_synced(self):
        _Handler.responses["getblockchaininfo"] = {"blocks": 953000, "initialblockdownload": False}
        self.assertEqual(self._provider()["tip"](), 953000)


if __name__ == "__main__":
    unittest.main()
