import base64
import hashlib
import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch

import xhs_connector as xhs


class FakeChild:
    def __init__(self, *args):
        self.arguments = args
        self.exited = False
        self.closed = False

    def poll(self):
        return 0 if self.exited else None

    def close(self):
        self.closed = self.exited = True


class FakeFunction:
    def __init__(self, action):
        self.action = action

    def __call__(self, *args):
        return self.action(*args)


class FakeKernel:
    def __init__(self, fail_assignment=False):
        self.calls = []
        self.flags = None
        self.limit_flags = None
        self.fail_assignment = fail_assignment
        for name in ("CreateJobObjectW", "SetInformationJobObject", "CreateProcessW",
                     "AssignProcessToJobObject", "ResumeThread", "TerminateProcess",
                     "TerminateJobObject", "GetExitCodeProcess", "WaitForSingleObject", "CloseHandle"):
            setattr(self, name, FakeFunction(lambda *args, name=name: self.call(name, args)))

    def call(self, name, args):
        self.calls.append((name, args))
        if name == "CreateJobObjectW":
            return 111
        if name == "SetInformationJobObject":
            self.limit_flags = args[2]._obj.BasicLimitInformation.LimitFlags
        if name == "CreateProcessW":
            self.flags = args[5]
            self.inherit_handles = args[4]
            self.window_flags = args[8]._obj.dwFlags
            self.window_show = args[8]._obj.wShowWindow
            info = args[9]._obj
            info.hProcess, info.hThread, info.dwProcessId = 222, 333, 444
        if name == "AssignProcessToJobObject" and self.fail_assignment:
            return 0
        if name == "GetExitCodeProcess":
            args[1]._obj.value = 259
        if name in {"ResumeThread", "WaitForSingleObject"}:
            return 0
        return 1


def envelope(data):
    return {"success": True, "data": data, "message": "PRIVATE_UPSTREAM_MESSAGE"}


class SecurityHoldTests(unittest.TestCase):
    def test_production_hold_blocks_every_entry_point_without_spawning_or_transport(self):
        self.assertEqual(xhs.SECURITY_HOLD, "xiaohongshu-mcp-v2.5.5-leakless")
        with tempfile.TemporaryDirectory() as directory:
            connector = xhs.XhsConnector(Path(directory))
            # Even a cached, previously authenticated process must not be reused.
            child = FakeChild()
            connector._process = child
            connector._token, connector._port = "PRIVATE_TOKEN", 12345
            connector._logged_in, connector._connected = True, True
            connector._account = {"id": "abcdef123456", "name": "PRIVATE_ACCOUNT"}
            with patch.object(xhs, "_WindowsChild") as spawn, \
                    patch.object(xhs.urllib.request, "build_opener") as opener:
                actions = [connector.start, connector.login, connector.check_login,
                           lambda: connector.publish({}),
                           lambda: connector._request("GET", "/health")]
                for action in actions:
                    with self.subTest(action=action), self.assertRaises(xhs.XhsConnectorError) as error:
                        action()
                    self.assertEqual(error.exception.code, "security_hold")
                    self.assertEqual(error.exception.state, "not_sent")
                self.assertTrue(child.closed)
                self.assertIsNone(connector._process)
                self.assertEqual(connector._token, "")
                spawn.assert_not_called()
                opener.assert_not_called()
            self.assertEqual(list(Path(directory).rglob('*')), [])
            connector.close()

    def test_status_returns_specific_hold_without_binary_access_or_health_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            connector = xhs.XhsConnector(Path(directory))
            with patch.object(connector, "_request", side_effect=AssertionError('no probe')):
                for status in (connector.status(), connector.stop(), connector.status()):
                    self.assertTrue(status['blocked'])
                    self.assertEqual(status['code'], 'security_hold')
                    for name in ('running', 'connected', 'logged_in'):
                        self.assertFalse(status[name])
                    self.assertIn('Windows Defender', status['message'])
                    self.assertNotIn('account', status)
            connector.close()


class ConnectorTests(unittest.TestCase):
    def setUp(self):
        # Legacy transport tests use synthetic binaries; production stays blocked.
        self.hold = patch.object(xhs, "SECURITY_HOLD", "")
        self.hold.start()
        self.addCleanup(self.hold.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.connector = xhs.XhsConnector(self.home)
        self.connector.base.mkdir(parents=True)
        self.binary = b"non-executable test fixture"
        self.connector.executable.write_bytes(self.binary)
        self.digest = patch.object(xhs, "WINDOWS_SHA256", hashlib.sha256(self.binary).hexdigest())
        self.spawn = patch.object(xhs, "_WindowsChild", side_effect=FakeChild)
        self.digest.start()
        self.spawn_mock = self.spawn.start()

    def tearDown(self):
        self.connector.close()
        self.spawn.stop()
        self.digest.stop()
        self.tmp.cleanup()

    def ready(self):
        self.connector.start()
        self.connector._connected = True
        self.connector._logged_in = True
        self.connector._account = {"id": "abcdef123456", "name": "测试账号"}

    def payload(self):
        image = self.home / "image.png"
        image.write_bytes(b"local image fixture")
        return {"title": "论文解读", "content": "有来源的正文", "images": [str(image)], "tags": ["论文"]}

    def test_start_verifies_binary_and_never_waits_for_health_or_browser(self):
        with patch.object(self.connector, "_request", side_effect=AssertionError("must not probe")):
            status = self.connector.start()
        self.assertTrue(status["running"])
        self.assertFalse(status["connected"])
        executable, arguments, environment, cwd = self.spawn_mock.call_args.args
        self.assertEqual(executable, self.connector.executable)
        self.assertRegex(arguments[0], r"^-port=127\.0\.0\.1:\d+$")
        self.assertIn("-headless=true", arguments)
        self.assertFalse(any("token" in arg for arg in arguments))
        self.assertEqual(cwd, self.connector.base)
        self.assertGreaterEqual(len(environment["AUTH_TOKEN"]), 32)
        self.assertEqual(environment["COOKIES_PATH"], str(self.connector.base / "session/cookies.json"))
        self.assertNotIn(environment["AUTH_TOKEN"], json.dumps(status))
        # Only fixture binary exists: no token/config/log file was written.
        self.assertEqual([p.name for p in self.home.rglob("*") if p.is_file()], [xhs.WINDOWS_EXE])
        self.connector.start()
        self.assertEqual(self.spawn_mock.call_count, 1)

    def test_bad_or_missing_binary_never_spawns(self):
        self.connector.executable.write_bytes(b"wrong")
        with self.assertRaises(xhs.XhsConnectorError) as error:
            self.connector.start()
        self.assertEqual(error.exception.code, "binary_invalid")
        self.connector.executable.unlink()
        with self.assertRaises(xhs.XhsConnectorError) as error:
            self.connector.start()
        self.assertEqual(error.exception.code, "not_installed")
        self.spawn_mock.assert_not_called()

    def test_environment_does_not_inherit_keys_proxies_or_arbitrary_path(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "PRIVATE_KEY", "ANTHROPIC_API_KEY": "PRIVATE_KEY",
                                     "HTTP_PROXY": "http://PRIVATE_PROXY", "XHS_PROXY": "PRIVATE_PROXY",
                                     "COOKIES_PATH": "PRIVATE_OLD_SESSION", "PATH": "PRIVATE_TOOLCHAIN"}):
            env = xhs._child_environment(self.connector.base, "EPHEMERAL_TOKEN")
        joined = json.dumps(env)
        for value in ("PRIVATE_KEY", "PRIVATE_PROXY", "PRIVATE_OLD_SESSION", "PRIVATE_TOOLCHAIN"):
            self.assertNotIn(value, joined)

    def test_status_only_uses_health_with_subsecond_timeout(self):
        self.ready()
        with patch.object(self.connector, "_request", return_value=envelope({
                "service": "xiaohongshu-mcp", "version": xhs.VERSION, "status": "healthy"})) as request:
            status = self.connector.status()
        self.assertTrue(status["connected"])
        self.assertEqual(status["account"]["id"], "abcdef123456")
        request.assert_called_once_with("GET", "/health", timeout=0.8)
        with patch.object(self.connector, "_request", return_value=envelope({
                "service": "xiaohongshu-mcp", "version": "wrong", "status": "healthy"})):
            self.assertFalse(self.connector.status()["connected"])

    def test_unknown_identity_is_not_eligible_to_publish(self):
        self.ready()
        with patch.object(self.connector, "_request", return_value=envelope({"is_logged_in": True})):
            status = self.connector.check_login()
        self.assertTrue(status["logged_in"])
        self.assertNotIn("account", status)
        with patch.object(self.connector, "_request") as request:
            with self.assertRaises(xhs.XhsConnectorError) as error:
                self.connector.publish(self.payload())
            self.assertEqual(error.exception.code, "identity_required")
            request.assert_not_called()

    def test_identity_comes_only_from_current_login_user(self):
        self.ready()
        response = envelope({"is_logged_in": True, "username": "测试<账号>\n", "user_id": "012345abcdef",
                             "cookies": "PRIVATE_COOKIE", "token": "PRIVATE_TOKEN"})
        with patch.object(self.connector, "_request", return_value=response):
            status = self.connector.check_login()
        self.assertEqual(status["account"], {"id": "012345abcdef", "name": "测试账号"})
        self.assertNotIn("PRIVATE", json.dumps(status))
        with patch.object(self.connector, "_request", return_value=envelope({"is_logged_in": False})):
            status = self.connector.check_login()
        self.assertFalse(status["logged_in"])
        self.assertNotIn("account", status)

    def test_qrcode_whitelist_size_and_mime_validation(self):
        self.ready()
        image = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\nfixture").decode()
        with patch.object(self.connector, "_request", return_value=envelope({
                "is_logged_in": False, "img": image, "timeout": "PRIVATE_COOKIE"})):
            result = self.connector.login()
        self.assertEqual(result, {"img": image, "is_logged_in": False, "timeout": "4m0s", "account": None})
        for invalid in ("https://evil.example/qr.png", "data:image/svg+xml;base64,PHN2Zz4=",
                        "data:image/png;base64,bm90IGEgcG5n", "data:image/png;base64,!!",
                        "data:image/jpeg;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\nfixture").decode(),
                        "x" * 1_400_001):
            with self.subTest(value=invalid[:70]), self.assertRaises(xhs.XhsConnectorError):
                xhs._qr_image(invalid)

    def test_already_logged_in_qrcode_reads_identity(self):
        self.ready()
        with patch.object(self.connector, "_request", side_effect=[
                envelope({"is_logged_in": True, "timeout": "0s"}),
                envelope({"is_logged_in": True, "user_id": "abcdef123456", "username": "测试账号"})]) as request:
            result = self.connector.login()
        self.assertEqual(result["account"], {"id": "abcdef123456", "name": "测试账号"})
        self.assertEqual([call.args[1] for call in request.call_args_list],
                         ["/api/v1/login/qrcode", "/api/v1/login/status"])

    def test_publish_one_post_strips_optional_side_effects_and_never_claims_verification(self):
        self.ready()
        payload = self.payload()
        payload.update(is_original=True, products=["商品"], schedule_at="later", token="PRIVATE_TOKEN")
        response = envelope({"title": payload["title"], "content": payload["content"],
                             "images": 1, "status": "发布完成", "cookies": "PRIVATE_COOKIE"})
        with patch.object(self.connector, "_request", return_value=response) as request:
            result = self.connector.publish(payload)
        request.assert_called_once()
        self.assertEqual(request.call_args.args, ("POST", "/api/v1/publish"))
        self.assertEqual(request.call_args.kwargs["timeout"], 180)
        cleaned = request.call_args.kwargs["payload"]
        self.assertFalse(cleaned["is_original"])
        self.assertNotIn("products", cleaned)
        self.assertNotIn("schedule_at", cleaned)
        self.assertNotIn("token", cleaned)
        self.assertEqual(result["state"], "submitted_unverified")
        self.assertFalse(result["verified"])
        self.assertNotIn("PRIVATE", json.dumps(result))

    def test_publish_timeout_or_malformed_receipt_never_retries(self):
        self.ready()
        for response in (xhs.XhsConnectorError("unavailable", "outcome_unknown"),
                         {"success": True, "data": {"status": "发布完成", "error": "PRIVATE_COOKIE"}}):
            kwargs = {"side_effect": response} if isinstance(response, Exception) else {"return_value": response}
            with patch.object(self.connector, "_request", **kwargs) as request:
                with self.assertRaises(xhs.XhsConnectorError) as error:
                    self.connector.publish(self.payload())
                self.assertEqual(error.exception.state, "outcome_unknown")
                self.assertNotIn("PRIVATE", str(error.exception))
                request.assert_called_once()

    def test_remote_relative_unc_and_missing_images_never_send(self):
        self.ready()
        for images in (["https://example.com/a.png"], ["relative.png"], [r"\\server\share\a.png"],
                       [str(self.home / "missing.png")], [], ["file:///secret.png"]):
            payload = self.payload()
            payload["images"] = images
            with patch.object(self.connector, "_request") as request:
                with self.assertRaises(xhs.XhsConnectorError):
                    self.connector.publish(payload)
                request.assert_not_called()

    def test_invalid_field_types_are_rejected_before_transport(self):
        self.ready()
        for key, value in (("visibility", []), ("title", {}), ("content", None), ("tags", [{}])):
            payload = self.payload()
            payload[key] = value
            with patch.object(self.connector, "_request") as request:
                with self.assertRaises(xhs.XhsConnectorError):
                    self.connector.publish(payload)
                request.assert_not_called()

    def test_transport_is_loopback_only_no_proxy_no_redirect_bounded_and_sanitized(self):
        self.ready()
        opener = Mock()
        opener.open.return_value = io.BytesIO(json.dumps(envelope({"is_logged_in": False})).encode())
        with patch.object(xhs.urllib.request, "build_opener", return_value=opener) as build:
            self.connector._request("GET", "/api/v1/login/status")
        self.assertIsInstance(build.call_args.args[0], xhs.urllib.request.ProxyHandler)
        self.assertEqual(build.call_args.args[0].proxies, {})
        self.assertIsInstance(build.call_args.args[1], xhs._NoRedirect)
        req = opener.open.call_args.args[0]
        self.assertTrue(req.full_url.startswith("http://127.0.0.1:"))
        self.assertEqual(req.get_header("Authorization"), "Bearer " + self.connector._token)
        self.assertIsNone(xhs._NoRedirect().redirect_request(None, None, None, None, None, None))
        with self.assertRaises(xhs.XhsConnectorError):
            self.connector._request("GET", "https://evil.example")
        for value in (b"x" * (xhs.MAX_RESPONSE + 1), b"not-json PRIVATE_COOKIE", b"[]"):
            opener.open.return_value = io.BytesIO(value)
            with patch.object(xhs.urllib.request, "build_opener", return_value=opener):
                with self.assertRaises(xhs.XhsConnectorError) as error:
                    self.connector._request("GET", "/health")
                self.assertNotIn("PRIVATE", str(error.exception))
        opener.open.side_effect = RuntimeError("PRIVATE_TOKEN PRIVATE_COOKIE")
        with patch.object(xhs.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(xhs.XhsConnectorError) as error:
                self.connector._request("POST", "/api/v1/publish", payload={})
        self.assertEqual(error.exception.state, "outcome_unknown")
        self.assertNotIn("PRIVATE", str(error.exception))

    def test_http_failures_do_not_echo_response_and_distinguish_known_rejection(self):
        self.ready()
        for code, state in ((400, "not_sent"), (401, "not_sent"), (302, "outcome_unknown"), (500, "outcome_unknown")):
            exception = urllib.error.HTTPError("http://PRIVATE_URL", code, "PRIVATE_TOKEN", {}, io.BytesIO(b"PRIVATE_COOKIE"))
            with patch.object(xhs.urllib.request.OpenerDirector, "open", side_effect=exception):
                with self.assertRaises(xhs.XhsConnectorError) as error:
                    self.connector._request("POST", "/api/v1/publish", payload={})
            self.assertEqual(error.exception.state, state)
            self.assertNotIn("PRIVATE", str(error.exception))

    def test_stop_only_closes_owned_child_and_resets_account_and_secrets(self):
        self.ready()
        child = self.connector._process
        status = self.connector.stop()
        self.assertTrue(child.closed)
        self.assertFalse(status["running"])
        self.assertFalse(status["logged_in"])
        self.assertNotIn("account", status)
        self.assertIsNone(self.connector._port)
        self.assertEqual(self.connector._token, "")
        self.connector.stop()

    def test_stopped_qr_response_cannot_repopulate_session(self):
        self.ready()
        image = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\nfixture").decode()
        def request(*args, **kwargs):
            self.connector.stop()
            return envelope({"is_logged_in": False, "img": image})
        with patch.object(self.connector, "_request", side_effect=request):
            with self.assertRaises(xhs.XhsConnectorError):
                self.connector.login()
        self.assertFalse(self.connector._connected)

    def test_natural_child_exit_closes_job_and_cached_identity(self):
        self.ready()
        child = self.connector._process
        child.exited = True
        result = self.connector.status()
        self.assertTrue(child.closed)
        self.assertFalse(result["logged_in"])
        self.assertNotIn("account", result)


@unittest.skipUnless(os.name == "nt", "Win32 ctypes layout test")
class WindowsJobTests(unittest.TestCase):
    def test_job_contains_process_before_it_can_start_and_kills_owned_tree(self):
        kernel = FakeKernel()
        with patch.object(xhs.ctypes, "WinDLL", return_value=kernel):
            child = xhs._WindowsChild(Path(r"C:\private\companion.exe"), ["-port=127.0.0.1:12345"],
                                      {"AUTH_TOKEN": "FIXTURE"}, Path(r"C:\private"))
        names = [name for name, _ in kernel.calls]
        self.assertLess(names.index("SetInformationJobObject"), names.index("CreateProcessW"))
        self.assertLess(names.index("CreateProcessW"), names.index("AssignProcessToJobObject"))
        self.assertLess(names.index("AssignProcessToJobObject"), names.index("ResumeThread"))
        self.assertEqual(kernel.limit_flags, 0x2000)
        self.assertEqual(kernel.flags & 0x00000004, 0x00000004)
        self.assertEqual(kernel.flags & 0x08000000, 0x08000000)
        self.assertFalse(kernel.inherit_handles)
        self.assertEqual(kernel.window_show, 0)
        self.assertIsNone(child.poll())
        child.close()
        terminates = [args for name, args in kernel.calls if name == "TerminateJobObject"]
        self.assertEqual(terminates, [(111, 1)])
        self.assertIsNotNone(child.poll())
        child.close()
        self.assertEqual(sum(name == "TerminateJobObject" for name, _ in kernel.calls), 1)

    def test_job_assignment_failure_kills_suspended_child_and_never_resumes_it(self):
        kernel = FakeKernel(fail_assignment=True)
        with patch.object(xhs.ctypes, "WinDLL", return_value=kernel):
            with self.assertRaises(xhs.XhsConnectorError) as error:
                xhs._WindowsChild(Path(r"C:\private\companion.exe"), [], {}, Path(r"C:\private"))
        names = [name for name, _ in kernel.calls]
        self.assertNotIn("ResumeThread", names)
        self.assertIn("TerminateProcess", names)
        self.assertIn("TerminateJobObject", names)
        self.assertEqual(error.exception.code, "start_failed")


if __name__ == "__main__":
    unittest.main()
