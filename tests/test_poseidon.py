import base64
import json
import unittest

import httpx
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from poseidon import (PoseidonClient, PoseidonError, Settings, encrypt_password,
                      normalize_logs, query_range, time_ns)


class ClientTests(unittest.TestCase):
    def make_client(self, handler):
        client = PoseidonClient(Settings("https://poseidon.test", "demo", "test-secret", "63", "56", "100"),
                                httpx.MockTransport(handler))
        self.addCleanup(client.close)
        return client

    def test_login_matches_frontend_crypto_and_scoped_query(self):
        requests = []

        def handler(request):
            requests.append(request)
            if request.url.path == "/api/auth/login/":
                body = json.loads(request.content)
                self.assertEqual(body["username"], "demo")
                self.assertNotEqual(body["password"], "test-secret")
                decryptor = Cipher(algorithms.AES(b"poseidon____salt"), modes.ECB()).decryptor()
                padded = decryptor.update(base64.b64decode(body["password"])) + decryptor.finalize()
                unpadder = padding.PKCS7(128).unpadder()
                self.assertEqual(unpadder.update(padded) + unpadder.finalize(), b"test-secret")
                return httpx.Response(200, json={"data": {"access": "access-one", "refresh": "refresh-one"}})
            self.assertEqual(request.headers["Authorization"], "Bearer access-one")
            self.assertEqual(request.headers["x-poseidon-project"], "63")
            self.assertEqual(request.headers["x-poseidon-product"], "56")
            self.assertEqual(request.headers["x-poseidon-env"], "100")
            self.assertEqual(request.url.params.get_list("keywords"), ["开浇", '"602"'])
            self.assertEqual(request.url.params["exclude_keywords"], "噪声")
            self.assertNotIn("excludeKeywords", request.url.params)
            self.assertEqual(request.url.params["start"], time_ns("2026-11-26T15:00:00+08:00"))
            return httpx.Response(200, json={"data": {"result": [
                {"stream": {"app": "service", "pod": "pod-1"},
                 "values": [["1795676400000000002", "后"], ["1795676400000000001", "前"]]}
            ], "has_next": False, "has_previous": True}})

        client = self.make_client(handler)
        result = client.search_logs("service", "2026-11-26T15:00:00+08:00", "2026-11-26T15:30:00+08:00",
                                    ["开浇", '"602"'], ["噪声"], "", "", 20, client.scope(None, None, None))
        self.assertEqual([row["message"] for row in result["logs"]], ["前", "后"])
        self.assertTrue(result["possibly_truncated"])
        self.assertEqual(len(requests), 2)

    def test_401_refresh_rotation_and_no_password_resend(self):
        calls = []
        reads = 0

        def handler(request):
            nonlocal reads
            calls.append(request.url.path)
            if request.url.path == "/api/auth/login/":
                return httpx.Response(200, json={"data": {"access": "expired", "refresh": "r1"}})
            if request.url.path == "/api/auth/token/refresh/":
                self.assertEqual(json.loads(request.content), {"refresh": "r1"})
                return httpx.Response(200, json={"data": {"access": "new", "refresh": "r2"}})
            reads += 1
            if reads == 1:
                return httpx.Response(401)
            self.assertEqual(request.headers["Authorization"], "Bearer new")
            return httpx.Response(200, json={"data": {}})

        client = self.make_client(handler)
        client.get("/api/self/")
        self.assertEqual(calls.count("/api/auth/login/"), 1)
        self.assertEqual(client._refresh, "r2")

    def test_failed_login_does_not_repeat_or_leak(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(400, json={"message": "test-secret access-token"})

        client = self.make_client(handler)
        for _ in range(2):
            with self.assertRaises(PoseidonError) as error:
                client.get("/api/self/")
            self.assertNotIn("test-secret", str(error.exception))
            self.assertNotIn("access-token", str(error.exception))
        self.assertEqual(len(calls), 1)

    def test_403_does_not_refresh(self):
        paths = []

        def handler(request):
            paths.append(request.url.path)
            if request.url.path == "/api/auth/login/":
                return httpx.Response(200, json={"data": {"access": "a", "refresh": "r"}})
            return httpx.Response(403)

        client = self.make_client(handler)
        with self.assertRaisesRegex(PoseidonError, "403"):
            client.get("/api/devops/apps/", client.scope(None, None, None))
        self.assertEqual(len(paths), 2)

    def test_refresh_rejection_allows_one_relogin(self):
        logins = 0

        def handler(request):
            nonlocal logins
            if request.url.path == "/api/auth/login/":
                logins += 1
                return httpx.Response(200, json={"data": {"access": str(logins), "refresh": "r"}})
            if request.url.path == "/api/auth/token/refresh/":
                return httpx.Response(401)
            if request.headers["Authorization"] == "Bearer 1":
                return httpx.Response(401)
            return httpx.Response(200, json={"data": {}})

        self.make_client(handler).get("/api/self/")
        self.assertEqual(logins, 2)

    def test_redirect_does_not_forward_credentials(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(302, headers={"Location": "https://other.test/"})

        client = self.make_client(handler)
        with self.assertRaisesRegex(PoseidonError, "302"):
            client.get("/api/self/")
        self.assertEqual(len(calls), 1)

    def test_context_uses_exact_nanoseconds_and_stream_labels(self):
        anchor = time_ns("2026-11-26T15:01:00+08:00")

        def handler(request):
            if request.url.path == "/api/auth/login/":
                return httpx.Response(200, json={"data": {"access": "a"}})
            self.assertEqual(request.url.path, "/api/devops/log/query/")
            self.assertEqual(request.url.params["time"], anchor)
            self.assertEqual(request.url.params["pod"], "pod-1")
            self.assertEqual(request.url.params["direction"], "forward")
            return httpx.Response(200, json={"data": {"result": []}})

        client = self.make_client(handler)
        result = client.search_logs("service", "2026-11-26T15:00:00+08:00", "2026-11-26T15:30:00+08:00",
                                    [], [], "", "", 50, client.scope(None, None, None),
                                    "forward", anchor, {"pod": "pod-1"})
        self.assertEqual(result["count"], 0)

    def test_invalid_scope_range_or_path_makes_no_request(self):
        calls = []
        client = self.make_client(lambda request: calls.append(request))
        with self.assertRaises(PoseidonError):
            client.scope("", None, None)
        with self.assertRaises(PoseidonError):
            client.get("/api/devops/apps/252/instances/restart/")
        with self.assertRaises(PoseidonError):
            client.search_logs("service", "2026-11-26T15:30:00", "2026-11-26T15:00:00",
                               [], [], "", "", 20, client.scope(None, None, None))
        self.assertEqual(calls, [])


class DataTests(unittest.TestCase):
    def test_times_and_precision(self):
        self.assertEqual(time_ns("2026-11-26T15:00:00"), time_ns("2026-11-26T07:00:00Z"))
        self.assertEqual(int(time_ns("2026-11-26T07:00:00.123456Z")) % 1_000_000_000, 123456000)
        with self.assertRaises(PoseidonError):
            query_range("2026-11-01", "2026-11-26")

    def test_response_budget_reports_truncation(self):
        result = normalize_logs({"result": [{"stream": {}, "values": [["1", "x" * 210_000]]}]}, 20)
        self.assertEqual(result["count"], 0)
        self.assertTrue(result["locally_truncated"])
        self.assertTrue(result["possibly_truncated"])

    def test_unknown_response_is_error_not_empty_logs(self):
        with self.assertRaises(PoseidonError):
            normalize_logs({"unexpected": []}, 20)

    def test_settings_repr_hides_credentials(self):
        self.assertNotIn("test-secret", repr(Settings("https://test", "demo", "test-secret")))
        self.assertEqual(encrypt_password("中文"), encrypt_password("中文"))


if __name__ == "__main__":
    unittest.main()
