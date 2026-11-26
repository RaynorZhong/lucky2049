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


class _CountingHTTPServer(http.server.HTTPServer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.accepted_connections = 0

    def get_request(self):
        sock, addr = super().get_request()
        self.accepted_connections += 1
        return sock, addr


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    responses = {}         # method -> result or callable(params); reset per test in setUp
    last_headers = None    # the most recent request's headers (case-insensitive)
    request_bodies = []    # parsed JSON bodies received in order
    reject_batch = False   # simulate a proxy/endpoint that rejects JSON-RPC batch arrays
    force_close_once = False

    def do_GET(self):
        type(self).last_headers = self.headers
        if self.path == "/redirect-cross":
            self.send_response(302)
            self.send_header("Location", type(self).redirect_target)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        out = b"pong"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(out)))
        if type(self).force_close_once:
            type(self).force_close_once = False
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(out)

    def do_POST(self):
        type(self).last_headers = self.headers
        if self.headers.get("Authorization") != _EXPECTED:
            out = b"unauthorized"
            self.send_response(401)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
            return
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        type(self).request_bodies.append(body)
        if isinstance(body, list):
            if type(self).reject_batch:
                err = json.dumps({"result": None, "error": {"code": -32600, "message": "batch unsupported"}, "id": None}).encode()
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(err)))
                self.end_headers()
                self.wfile.write(err)
                return
            # Deliberately reverse response order to prove caller reassembles by JSON-RPC id
            items = []
            for req in reversed(body):
                val = type(self).responses.get(req["method"])
                res = val(req.get("params", [])) if callable(val) else val
                if isinstance(res, dict) and "__rpc_error__" in res:
                    items.append({"result": None, "error": res["__rpc_error__"], "id": req.get("id")})
                else:
                    items.append({"result": res, "error": None, "id": req.get("id")})
            out = json.dumps(items).encode()
        else:
            val = type(self).responses.get(body["method"])
            res = val(body.get("params", [])) if callable(val) else val
            out = json.dumps({"result": res, "error": None, "id": body.get("id")}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


class _RpcServerCase(unittest.TestCase):
    def setUp(self):
        _Handler.responses = {"getblockcount": 800000, "getblockhash": lambda p: "%064x" % (p[0] + 1)}
        _Handler.last_headers = None
        _Handler.request_bodies = []
        _Handler.reject_batch = False
        _Handler.force_close_once = False
        if hasattr(verify, "close_http_pool"):
            verify.close_http_pool()
        self.srv = _CountingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.port = self.srv.server_address[1]
        self.url = "http://u:p@127.0.0.1:%d/" % self.port
        self.plain_url = "http://127.0.0.1:%d/ping" % self.port

    def tearDown(self):
        if hasattr(verify, "close_http_pool"):
            verify.close_http_pool()
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
        with self.assertRaisesRegex(extend_pages.TipAbstain, "initial block download"):
            self._provider()["tip"]()

    def test_serves_tip_when_synced(self):
        _Handler.responses["getblockchaininfo"] = {"blocks": 953000, "initialblockdownload": False}
        self.assertEqual(self._provider()["tip"](), 953000)


class TestKeepAliveAndBatchRpc(_RpcServerCase):
    def test_http_get_reuses_keep_alive_connection(self):
        for _ in range(4):
            self.assertEqual(verify._http_get(self.plain_url), "pong")
        self.assertEqual(self.srv.accepted_connections, 1)

    def test_http_get_reconnects_after_server_close(self):
        _Handler.force_close_once = True
        self.assertEqual(verify._http_get(self.plain_url), "pong")
        self.assertEqual(verify._http_get(self.plain_url), "pong")
        self.assertEqual(self.srv.accepted_connections, 2)

    def test_rpc_call_reuses_keep_alive_connection(self):
        for _ in range(3):
            self.assertEqual(verify._rpc_call(self.url, "getblockcount", []), 800000)
        self.assertEqual(self.srv.accepted_connections, 1)

    def test_fetch_from_core_batches_in_single_post_and_orders_by_id(self):
        with mock.patch.dict(os.environ, {"BITCOIN_RPC_URL": self.url}):
            hashes = verify.fetch_from_core(10, 14)
        self.assertEqual(hashes, ["%064x" % (h + 1) for h in range(10, 15)])
        self.assertEqual(len(_Handler.request_bodies), 1)
        self.assertIsInstance(_Handler.request_bodies[0], list)
        self.assertEqual(len(_Handler.request_bodies[0]), 5)

    def test_core_provider_hashes_batches_and_falls_back_when_batch_rejected(self):
        with mock.patch.dict(os.environ, {"BITCOIN_RPC_URL": self.url}):
            prov = extend_pages._core_provider()
            hashes = prov["hashes"](0, 3)
        self.assertEqual(hashes, ["%064x" % (h + 1) for h in range(0, 4)])
        self.assertEqual(len(_Handler.request_bodies), 1)

        # Now simulate an RPC proxy that rejects batch JSON-RPC arrays (HTTP 400):
        _Handler.request_bodies = []
        _Handler.reject_batch = True
        with mock.patch.dict(os.environ, {"BITCOIN_RPC_URL": self.url}):
            hashes2 = verify.fetch_from_core(0, 2)
        self.assertEqual(hashes2, ["%064x" % (h + 1) for h in range(0, 3)])
        # 1 rejected batch attempt + 3 individual getblockhash calls
        self.assertEqual(len(_Handler.request_bodies), 4)

    def test_batch_rpc_item_error_raises_immediately_without_fallback(self):
        _Handler.responses["getblockhash"] = lambda p: (
            {"__rpc_error__": {"code": -8, "message": "Block height out of range"}}
            if p[0] == 2 else "%064x" % (p[0] + 1)
        )
        with mock.patch.dict(os.environ, {"BITCOIN_RPC_URL": self.url}):
            with self.assertRaisesRegex(RuntimeError, "Block height out of range"):
                verify.fetch_from_core(0, 2)
        self.assertEqual(len(_Handler.request_bodies), 1)

    def test_cross_origin_redirect_strips_sensitive_auth_headers(self):
        srv2 = _CountingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=srv2.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        try:
            _Handler.redirect_target = "http://127.0.0.1:%d/ping" % srv2.server_address[1]
            raw = verify._http_request_bytes(
                "http://127.0.0.1:%d/redirect-cross" % self.port,
                headers={"Authorization": "Basic secret", "CF-Access-Client-Secret": "tok"},
            )
            self.assertEqual(raw, b"pong")
            self.assertIsNone(_Handler.last_headers.get("Authorization"))
            self.assertIsNone(_Handler.last_headers.get("CF-Access-Client-Secret"))
        finally:
            verify.close_http_pool()
            srv2.shutdown()
            srv2.server_close()


if __name__ == "__main__":
    unittest.main()
