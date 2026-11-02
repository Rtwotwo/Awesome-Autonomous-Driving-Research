"""Private, loopback-only adapter for the pinned xiaohongshu-mcp companion.

The companion saves PLAINTEXT login cookies under private_home. They are not
encrypted by this adapter and must never be exported, synchronized, or served.
Bearer credentials stay in memory and the child environment. A successful
publish only means the companion observed a navigation, not a verified note.
"""
import atexit
import base64
import ctypes
import hashlib
import json
import os
import re
import secrets
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

VERSION = "v2.5.5"
WINDOWS_EXE = "xiaohongshu-mcp-windows-amd64.exe"
WINDOWS_SHA256 = "cb55674f90c1649c875be9c4cde3af193b02fc34ffc860c40c8d711e6ca53e27"
# This version releases a browser helper detected by Windows Defender locally.
# Do not re-enable it via settings/environment or ask users to allowlist it.
# A replacement needs a reviewed launch chain and an actual QR-login test.
SECURITY_HOLD = "xiaohongshu-mcp-v2.5.5-leakless"
MAX_RESPONSE = 2_000_000
_ERRORS = {
    "security_hold": "当前小红书连接器的浏览器辅助组件触发了 Windows Defender 告警，自动登录和发布已暂停。请勿放行或关闭防护。图文设计与导出仍可使用，可到官方创作平台上传图文包。",
    "not_installed": "请先安装小红书本地连接器。",
    "binary_invalid": "小红书连接器完整性校验未通过，请重新安装。",
    "private_path": "小红书私有会话目录无效，请检查本机安装。",
    "unsupported_system": "小红书本地连接器目前仅支持 Windows x64。",
    "start_failed": "小红书连接器启动失败，请检查本机安装与浏览器下载网络。",
    "not_running": "请先启动小红书本地连接器，等待浏览器准备完成。",
    "unavailable": "小红书连接器暂未就绪，首次启动需要下载浏览器，请稍后检测。",
    "unauthorized": "小红书本地连接器认证失败，请停止后重新启动。",
    "invalid_response": "小红书连接器返回了无法识别的数据，请重新检测连接。",
    "login_failed": "无法检查小红书登录状态，请稍后重试或重新扫码。",
    "qr_invalid": "连接器没有返回受支持的二维码图片，请重新获取。",
    "identity_required": "请先检测登录并确认小红书账号身份。",
    "invalid_payload": "小红书图文数据无效，请重新保存并审核。",
    "invalid_images": "小红书配图必须是可读取的本地图片文件，请重新生成并审核。",
    "publish_rejected": "小红书连接器未接受发布请求，请重新检查图文与连接。",
    "publish_unknown": "发布结果尚未确认。请先在小红书中检查，不要重复提交。",
}


class XhsConnectorError(ValueError):
    """Only fixed public messages escape this boundary; never upstream errors."""
    def __init__(self, code, state="not_sent"):
        self.code = code if code in _ERRORS else "unavailable"
        self.state = state if state in {"not_sent", "outcome_unknown"} else "not_sent"
        super().__init__(_ERRORS[self.code])


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class _WindowsChild:
    """Create suspended, assign a private kill-on-close job, then resume.

    No process-name matching or PID-tree enumeration is used. Windows closes
    the non-inherited job handle even if PaperStudio is abruptly terminated.
    """
    def __init__(self, executable, arguments, environment, cwd):
        if os.name != "nt":
            raise XhsConnectorError("unsupported_system")
        from ctypes import wintypes as w

        class BasicLimits(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                        ("PerJobUserTimeLimit", ctypes.c_longlong), ("LimitFlags", w.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", w.DWORD), ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", w.DWORD), ("SchedulingClass", w.DWORD)]

        class IOCounts(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in
                        ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                         "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BasicLimits), ("IoInfo", IOCounts),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        class StartupInfo(ctypes.Structure):
            _fields_ = [("cb", w.DWORD), ("lpReserved", w.LPWSTR), ("lpDesktop", w.LPWSTR),
                        ("lpTitle", w.LPWSTR), ("dwX", w.DWORD), ("dwY", w.DWORD),
                        ("dwXSize", w.DWORD), ("dwYSize", w.DWORD), ("dwXCountChars", w.DWORD),
                        ("dwYCountChars", w.DWORD), ("dwFillAttribute", w.DWORD), ("dwFlags", w.DWORD),
                        ("wShowWindow", w.WORD), ("cbReserved2", w.WORD), ("lpReserved2", ctypes.c_void_p),
                        ("hStdInput", w.HANDLE), ("hStdOutput", w.HANDLE), ("hStdError", w.HANDLE)]

        class ProcessInfo(ctypes.Structure):
            _fields_ = [("hProcess", w.HANDLE), ("hThread", w.HANDLE),
                        ("dwProcessId", w.DWORD), ("dwThreadId", w.DWORD)]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([ctypes.c_void_p, w.LPCWSTR], w.HANDLE),
            "SetInformationJobObject": ([w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD], w.BOOL),
            "CreateProcessW": ([w.LPCWSTR, w.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, w.BOOL,
                                w.DWORD, ctypes.c_void_p, w.LPCWSTR,
                                ctypes.POINTER(StartupInfo), ctypes.POINTER(ProcessInfo)], w.BOOL),
            "AssignProcessToJobObject": ([w.HANDLE, w.HANDLE], w.BOOL),
            "ResumeThread": ([w.HANDLE], w.DWORD),
            "TerminateProcess": ([w.HANDLE, w.UINT], w.BOOL),
            "TerminateJobObject": ([w.HANDLE, w.UINT], w.BOOL),
            "GetExitCodeProcess": ([w.HANDLE, ctypes.POINTER(w.DWORD)], w.BOOL),
            "WaitForSingleObject": ([w.HANDLE, w.DWORD], w.DWORD),
            "CloseHandle": ([w.HANDLE], w.BOOL),
        }
        for name, (arguments_types, return_type) in signatures.items():
            function = getattr(kernel, name)
            function.argtypes, function.restype = arguments_types, return_type
        self._kernel, self._handle, self._job = kernel, None, None
        self._dword = w.DWORD
        process = ProcessInfo()
        try:
            self._job = kernel.CreateJobObjectW(None, None)
            limits = ExtendedLimits()
            limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not self._job or not kernel.SetInformationJobObject(self._job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise OSError()
            startup = StartupInfo()
            startup.cb = ctypes.sizeof(startup)
            startup.dwFlags = 0x00000001  # STARTF_USESHOWWINDOW, SW_HIDE = 0
            command = ctypes.create_unicode_buffer(subprocess.list2cmdline([str(executable), *arguments]))
            env_block = ctypes.create_unicode_buffer("\0".join(f"{key}={value}" for key, value in
                                                     sorted(environment.items(), key=lambda item: item[0].upper())) + "\0\0")
            # Suspended + Unicode environment + no console window. Handles are not inherited.
            if not kernel.CreateProcessW(str(executable), command, None, None, False,
                                         0x00000004 | 0x00000400 | 0x08000000, env_block,
                                         str(cwd), ctypes.byref(startup), ctypes.byref(process)):
                raise OSError()
            self._handle, self.pid = process.hProcess, process.dwProcessId
            if not kernel.AssignProcessToJobObject(self._job, self._handle):
                raise OSError()
            if kernel.ResumeThread(process.hThread) == 0xFFFFFFFF:
                raise OSError()
        except Exception:
            if self._handle:
                kernel.TerminateProcess(self._handle, 1)
            self.close()
            raise XhsConnectorError("start_failed") from None
        finally:
            if process.hThread:
                kernel.CloseHandle(process.hThread)

    def poll(self):
        if not self._handle:
            return 0
        code = self._dword()
        if not self._kernel.GetExitCodeProcess(self._handle, ctypes.byref(code)):
            return 1
        return None if code.value == 259 else code.value

    def close(self):
        if self._job:
            self._kernel.TerminateJobObject(self._job, 1)
            self._kernel.CloseHandle(self._job)
            self._job = None
        if self._handle:
            self._kernel.WaitForSingleObject(self._handle, 2000)
            self._kernel.CloseHandle(self._handle)
            self._handle = None


def _child_environment(base, token):
    allowed = {"SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "USERPROFILE", "LOCALAPPDATA", "APPDATA",
               "HOMEDRIVE", "HOMEPATH", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)",
               "COMMONPROGRAMFILES", "COMMONPROGRAMFILES(X86)", "NUMBER_OF_PROCESSORS",
               "PROCESSOR_ARCHITECTURE", "COMSPEC", "PATHEXT"}
    environment = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    system_root = environment.get("SystemRoot") or environment.get("SYSTEMROOT") or r"C:\Windows"
    environment["PATH"] = os.pathsep.join((str(Path(system_root) / "System32"), system_root))
    environment.update(AUTH_TOKEN=token, COOKIES_PATH=str(base / "session" / "cookies.json"),
                       TEMP=str(base / "tmp"), TMP=str(base / "tmp"))
    return environment


def _qr_image(value):
    if not isinstance(value, str) or len(value) > 1_400_000:
        raise XhsConnectorError("qr_invalid")
    match = re.fullmatch(r"data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/]+={0,2})", value)
    if not match:
        raise XhsConnectorError("qr_invalid")
    try:
        data = base64.b64decode(match[2], validate=True)
    except (ValueError, base64.binascii.Error):
        raise XhsConnectorError("qr_invalid") from None
    kind = match[1]
    valid = ((kind == "png" and data.startswith(b"\x89PNG\r\n\x1a\n")) or
             (kind == "jpeg" and data.startswith(b"\xff\xd8\xff")) or
             (kind == "webp" and data.startswith(b"RIFF") and data[8:12] == b"WEBP"))
    if not valid or not 12 <= len(data) <= 1_000_000:
        raise XhsConnectorError("qr_invalid")
    return value


class XhsConnector:
    def __init__(self, private_home):
        self.home = Path(private_home).resolve()
        self.base = self.home / "connectors" / "xiaohongshu"
        self.executable = self.base / WINDOWS_EXE
        self._lock = threading.RLock()
        self._operations = threading.RLock()
        self._process = None
        self._port = None
        self._token = ""
        self._logged_in = False
        self._account = None
        self._connected = False
        self._message = ""
        atexit.register(self.close)

    def _clear_session(self):
        self._logged_in = False
        self._account = None
        self._connected = False

    def _running(self):
        return self._process is not None and self._process.poll() is None

    def _require_supported_connector(self):
        if SECURITY_HOLD:
            self.stop()
            raise XhsConnectorError("security_hold")

    def _public(self):
        if SECURITY_HOLD:
            return {"installed": self.executable.is_file(), "running": False,
                    "connected": False, "logged_in": False, "blocked": True,
                    "code": "security_hold", "message": _ERRORS["security_hold"]}
        running = self._running()
        installed = self.executable.is_file()
        connected = running and self._connected
        result = {"installed": installed, "running": running, "connected": connected,
                  "logged_in": connected and self._logged_in,
                  "message": self._message or (
                      "已连接小红书本地服务。" if connected else
                      _ERRORS["unavailable"] if running else
                      "连接器已安装，尚未启动。" if installed else _ERRORS["not_installed"])}
        if connected and self._account:
            result["account"] = dict(self._account)
        return result

    def status(self):
        """Cheap health probe only; cached account information is not revalidated."""
        if SECURITY_HOLD:
            return self.stop()
        with self._lock:
            if not self._running():
                self._clear_session()
                self._message = ""
                if self._process:
                    self._process.close()
                    self._process = None
                return self._public()
            token = self._token
        try:
            response = self._request("GET", "/health", timeout=0.8)
            data = response.get("data", {})
            ready = (response.get("success") is True and isinstance(data, dict) and
                     data.get("service") == "xiaohongshu-mcp" and data.get("version") == VERSION and
                     data.get("status") == "healthy")
        except XhsConnectorError:
            ready = False
        with self._lock:
            if token == self._token:
                self._connected = ready
                self._message = "" if ready else _ERRORS["unavailable"]
            return self._public()

    def start(self):
        """Return immediately after spawning; browser download happens in child."""
        self._require_supported_connector()
        with self._lock:
            if self._running():
                return self._public()
            if self._process:
                self._process.close()
                self._process = None
            self._clear_session()
            if not self.executable.is_file():
                raise XhsConnectorError("not_installed")
            try:
                for path in (self.base, self.executable, self.base / "session", self.base / "tmp",
                             self.base / "session" / "cookies.json"):
                    if not path.resolve().is_relative_to(self.home):
                        raise XhsConnectorError("private_path")
                digest = hashlib.sha256()
                with self.executable.open("rb") as binary:
                    for block in iter(lambda: binary.read(1024 * 1024), b""):
                        digest.update(block)
                if digest.hexdigest() != WINDOWS_SHA256:
                    raise XhsConnectorError("binary_invalid")
                (self.base / "session").mkdir(parents=True, exist_ok=True)
                (self.base / "tmp").mkdir(parents=True, exist_ok=True)
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
                    reservation.bind(("127.0.0.1", 0))
                    port = reservation.getsockname()[1]
                token = secrets.token_urlsafe(32)
                child = _WindowsChild(self.executable, [f"-port=127.0.0.1:{port}", "-headless=true"],
                                      _child_environment(self.base, token), self.base)
            except XhsConnectorError:
                raise
            except Exception:
                raise XhsConnectorError("start_failed") from None
            self._process, self._port, self._token = child, port, token
            self._message = ""
            return self._public()

    def _request(self, method, path, payload=None, timeout=30):
        self._require_supported_connector()
        allowed = {("GET", "/health"), ("GET", "/api/v1/login/status"),
                   ("GET", "/api/v1/login/qrcode"), ("POST", "/api/v1/publish")}
        if (method, path) not in allowed:
            raise XhsConnectorError("invalid_payload")
        with self._lock:
            if not self._running() or not self._token or not self._port:
                raise XhsConnectorError("not_running")
            token, port = self._token, self._port
        headers = {"Accept": "application/json", "User-Agent": "PaperStudio/1.0"}
        if path != "/health":
            headers["Authorization"] = "Bearer " + token
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body,
                                         headers=headers, method=method)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        deadline = time.monotonic() + timeout
        try:
            with opener.open(request, timeout=timeout) as response:
                chunks, size = [], 0
                read = getattr(response, "read1", response.read)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError()
                    # read1 performs at most one buffered/raw read; bound each by remaining budget.
                    raw_socket = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
                    if raw_socket is not None:
                        raw_socket.settimeout(remaining)
                    chunk = read(min(65536, MAX_RESPONSE + 1 - size))
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_RESPONSE:
                        raise ValueError()
                    chunks.append(chunk)
                result = json.loads(b"".join(chunks))
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
            if status == 401:
                raise XhsConnectorError("unauthorized") from None
            if method == "POST" and status == 400:
                raise XhsConnectorError("publish_rejected") from None
            raise XhsConnectorError("unavailable", "outcome_unknown" if method == "POST" else "not_sent") from None
        except Exception:
            raise XhsConnectorError("unavailable", "outcome_unknown" if method == "POST" else "not_sent") from None

    def check_login(self):
        self._require_supported_connector()
        with self._operations:
            with self._lock:
                token = self._token
            try:
                result = self._request("GET", "/api/v1/login/status", timeout=45)
                data = result.get("data")
                if result.get("success") is not True or not isinstance(data, dict) or type(data.get("is_logged_in")) is not bool:
                    raise XhsConnectorError("invalid_response")
                logged_in = data["is_logged_in"]
                account = None
                identity, name = data.get("user_id"), data.get("username")
                if (logged_in and isinstance(identity, str) and
                        re.fullmatch(r"[A-Za-z0-9_-]{6,128}", identity) and
                        isinstance(name, str) and 0 < len(name.strip()) <= 80 and
                        token and token not in name and token not in identity):
                    name = re.sub(r"[\x00-\x1f\x7f<>]", "", name).strip()
                    if name:
                        account = {"id": identity, "name": name}
                with self._lock:
                    if token != self._token or not self._running():
                        raise XhsConnectorError("not_running")
                    self._logged_in, self._account, self._connected = logged_in, account, True
                    self._message = ("已登录并读取当前账号。" if account else
                                     "已登录，但账号身份尚未确认，请重新检测。" if logged_in else
                                     "尚未登录，请获取二维码并用小红书 App 扫码。")
                    return self._public()
            except XhsConnectorError:
                with self._lock:
                    if token == self._token:
                        self._clear_session()
                        self._message = _ERRORS["login_failed"]
                raise

    def login(self):
        self._require_supported_connector()
        with self._operations:
            with self._lock:
                token = self._token
            result = self._request("GET", "/api/v1/login/qrcode", timeout=45)
            data = result.get("data")
            if result.get("success") is not True or not isinstance(data, dict) or type(data.get("is_logged_in")) is not bool:
                raise XhsConnectorError("invalid_response")
            if data["is_logged_in"]:
                status = self.check_login()
                return {"img": "", "is_logged_in": status["logged_in"], "timeout": "0s",
                        "account": status.get("account")}
            image = _qr_image(data.get("img"))
            with self._lock:
                if token != self._token or not self._running():
                    raise XhsConnectorError("not_running")
                self._clear_session()
                self._connected = True
                self._message = "请用小红书 App 扫码，并在手机上确认登录。"
            return {"img": image, "is_logged_in": False, "timeout": "4m0s", "account": None}

    @staticmethod
    def _publish_payload(payload):
        if not isinstance(payload, dict):
            raise XhsConnectorError("invalid_payload")
        title, content, tags = payload.get("title"), payload.get("content"), payload.get("tags", [])
        visibility = payload.get("visibility", "公开可见")
        if (not isinstance(title, str) or not title.strip() or len(title) > 20 or
                not isinstance(content, str) or not content.strip() or len(content) > 1000 or
                not isinstance(tags, list) or len(tags) > 10 or
                any(not isinstance(tag, str) or not re.fullmatch(r"[\w\u3400-\u9fff-]{1,20}", tag) for tag in tags) or
                len(content + " ".join("#" + tag for tag in tags)) > 1000 or
                not isinstance(visibility, str) or visibility not in {"公开可见", "仅自己可见", "仅互关好友可见"}):
            raise XhsConnectorError("invalid_payload")
        images = payload.get("images")
        if not isinstance(images, list) or not 1 <= len(images) <= 9:
            raise XhsConnectorError("invalid_images")
        paths = []
        try:
            for image in images:
                if not isinstance(image, str) or image.startswith(("\\\\", "//")) or "://" in image:
                    raise ValueError()
                path = Path(image)
                if not path.is_absolute():
                    raise ValueError()
                path = path.resolve(strict=True)
                if (str(path).startswith(("\\\\", "//")) or not path.is_file() or
                        path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"} or
                        not 0 < path.stat().st_size <= 20_000_000):
                    raise ValueError()
                paths.append(str(path))
        except Exception:
            raise XhsConnectorError("invalid_images") from None
        return {"title": title, "content": content, "images": paths, "tags": list(tags),
                "visibility": visibility, "is_original": False}

    def publish(self, payload):
        """One POST, no retry. Caller must freshly check/approve the account first."""
        self._require_supported_connector()
        cleaned = self._publish_payload(payload)
        with self._operations:
            with self._lock:
                if not self._running():
                    raise XhsConnectorError("not_running")
                if not self._logged_in or not self._account:
                    raise XhsConnectorError("identity_required")
            try:
                result = self._request("POST", "/api/v1/publish", payload=cleaned, timeout=180)
            except XhsConnectorError as error:
                if error.state == "not_sent":
                    raise
                raise XhsConnectorError("publish_unknown", "outcome_unknown") from None
            data = result.get("data")
            if (result.get("success") is not True or not isinstance(data, dict) or
                    data.get("title") != cleaned["title"] or data.get("content") != cleaned["content"] or
                    type(data.get("images")) is not int or data["images"] != len(cleaned["images"]) or
                    data.get("status") != "发布完成"):
                raise XhsConnectorError("publish_unknown", "outcome_unknown")
            return {"state": "submitted_unverified", "verified": False,
                    "message": "已向小红书提交，尚未核验笔记链接与审核状态，请在小红书 App 中确认。"}

    def stop(self):
        with self._lock:
            child, self._process = self._process, None
            self._port, self._token = None, ""
            self._clear_session()
            self._message = "连接器已停止；本机登录会话仍保留。"
        if child:
            child.close()
        with self._lock:
            return self._public()

    def close(self):
        self.stop()
