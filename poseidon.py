"""Read-only Poseidon adapter. Credentials and tokens never leave this process."""
from __future__ import annotations

import base64
import os
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

CST = timezone(timedelta(hours=8))


class PoseidonError(Exception):
    """Safe error text; never include raw HTTP responses or credential values."""


@dataclass(frozen=True)
class Settings:
    base_url: str
    username: str = field(repr=False)
    password: str = field(repr=False)
    project: str = ""
    product: str = ""
    env: str = ""

    def __post_init__(self) -> None:
        url = urlsplit(self.base_url)
        if (url.scheme not in {"http", "https"} or not url.netloc or url.username
                or url.password or url.query or url.fragment or url.path not in {"", "/"}):
            raise PoseidonError("POSEIDON_BASE_URL 必须是 http(s) 平台根地址，不能带路径、凭据或查询参数。")

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            os.getenv("POSEIDON_BASE_URL", "http://steel.psd.hbjxgt.com").rstrip("/"),
            os.getenv("POSEIDON_USERNAME", ""), os.getenv("POSEIDON_PASSWORD", ""),
            os.getenv("POSEIDON_PROJECT", ""), os.getenv("POSEIDON_PRODUCT", ""),
            os.getenv("POSEIDON_ENV", ""),
        )


def encrypt_password(password: str) -> str:
    # Exact CryptoJS AES/ECB/PKCS7 format used by this Poseidon frontend.
    padder = padding.PKCS7(128).padder()
    plaintext = padder.update(password.encode("utf-8")) + padder.finalize()
    encryptor = Cipher(algorithms.AES(b"poseidon____salt"), modes.ECB()).encryptor()
    return base64.b64encode(encryptor.update(plaintext) + encryptor.finalize()).decode("ascii")


def positive_id(value: str | int, name: str) -> str:
    result = str(value)
    if not re.fullmatch(r"[1-9][0-9]{0,18}", result):
        raise PoseidonError(f"{name} 必须是正整数 ID。")
    return result


def time_ns(value: str) -> str:
    """ISO 8601 -> integer nanoseconds, without float precision loss."""
    if not isinstance(value, str):
        raise PoseidonError("时间必须是 ISO 8601 字符串。")
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=CST)
        delta = moment.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
        ns = (delta.days * 86400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1000
        if ns <= 0:
            raise ValueError
        return str(ns)
    except ValueError:
        raise PoseidonError("时间格式无效；示例：2026-11-26T15:00:00+08:00。") from None


def query_range(start: str, end: str) -> dict[str, str]:
    first, last = time_ns(start), time_ns(end)
    if int(first) >= int(last):
        raise PoseidonError("开始时间必须早于结束时间。")
    if int(last) - int(first) > 7 * 86400 * 1_000_000_000:
        raise PoseidonError("单次查询最多 7 天，请缩小范围后分段查询。")
    return {"start": first, "end": last}


def keyword_params(keywords: list[str], excludes: list[str]) -> list[tuple[str, str]]:
    pairs = []
    for name, values in (("keywords", keywords), ("exclude_keywords", excludes)):
        if len(values) > 20 or any(not isinstance(v, str) or not v or len(v) > 512 for v in values):
            raise PoseidonError("每组关键词最多 20 个，每个长度为 1–512 字符。")
        # Frontend qs.stringify(..., {indices:false}) repeats the same key.
        pairs.extend((name, v) for v in values)
    return pairs


class PoseidonClient:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self._http = httpx.Client(
            base_url=settings.base_url, timeout=30, follow_redirects=False,
            headers={"Accept": "application/json", "Accept-Language": "zh-CN"},
            transport=transport,
        )
        self._access = ""
        self._refresh = ""
        self._auth_lock = threading.Lock()
        self._login_failed = False

    def close(self) -> None:
        self._access = self._refresh = ""
        self._http.close()

    def scope(self, project: str | None, product: str | None, env: str | None) -> dict[str, str]:
        values = {
            "project": project if project is not None else self.settings.project,
            "product": product if product is not None else self.settings.product,
            "env": env if env is not None else self.settings.env,
        }
        if not all(values.values()):
            raise PoseidonError("请明确提供 project、product、env ID，或设置对应的 POSEIDON_* 环境变量。")
        return {k: positive_id(v, k) for k, v in values.items()}

    def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            return self._http.request(method, path, **kwargs)
        except httpx.RequestError:
            raise PoseidonError("无法连接波塞冬，请检查平台地址、内网/VPN、代理和证书。") from None

    @staticmethod
    def _decode(response: httpx.Response) -> Any:
        if response.status_code in {401, 403}:
            raise PoseidonError(f"认证或权限失败（HTTP {response.status_code}）。")
        if not 200 <= response.status_code < 300:
            raise PoseidonError(f"波塞冬请求失败（HTTP {response.status_code}），未返回原始响应以免泄露凭据。")
        try:
            payload = response.json()
        except ValueError:
            raise PoseidonError("波塞冬未返回 JSON，可能进入了登录页或网关错误页。") from None
        if not isinstance(payload, dict) or "data" not in payload or payload["data"] is None:
            raise PoseidonError("波塞冬响应缺少 data，需核实此版本的接口格式。")
        if payload.get("success") is False:
            raise PoseidonError("波塞冬返回业务失败，请在网页中核实查询权限和条件。")
        return payload["data"]

    def _set_tokens(self, data: Any, *, refreshing: bool = False) -> None:
        if not isinstance(data, dict) or not isinstance(data.get("access"), str) or not data["access"]:
            raise PoseidonError("登录/刷新未返回 access token；可能要求 SSO、验证码或额外登录步骤。")
        refresh = data.get("refresh", self._refresh if refreshing else "")
        if refresh is not None and not isinstance(refresh, str):
            raise PoseidonError("refresh token 格式不符合预期。")
        self._access = data["access"].removeprefix("Bearer ")
        self._refresh = refresh or ""

    def _login(self) -> None:
        if self._login_failed:
            raise PoseidonError("本进程登录已失败；为避免锁号，不自动重试。检查本机凭据后重启服务。")
        if not self.settings.username or not self.settings.password:
            raise PoseidonError("请在本机设置 POSEIDON_USERNAME 和 POSEIDON_PASSWORD，勿在聊天中发送密码。")
        # No retry loops: bad credentials must not trigger account lockouts.
        response = self._send("POST", "/api/auth/login/", json={
            "username": self.settings.username,
            "password": encrypt_password(self.settings.password),
        })
        try:
            self._set_tokens(self._decode(response))
        except PoseidonError:
            self._login_failed = True
            raise

    def authenticate(self) -> None:
        with self._auth_lock:
            if not self._access:
                self._login()

    def _recover(self, old_access: str) -> None:
        with self._auth_lock:
            if self._access and self._access != old_access:
                return
            if self._refresh:
                response = self._send("POST", "/api/auth/token/refresh/",
                                      headers={"Authorization": f"Bearer {old_access}"},
                                      json={"refresh": self._refresh})
                if response.status_code not in {400, 401, 403}:
                    self._set_tokens(self._decode(response), refreshing=True)
                    return
            self._access = self._refresh = ""
            self._login()

    def get(self, path: str, scope: dict[str, str] | None = None,
            params: list[tuple[str, str]] | None = None) -> tuple[Any, httpx.Headers]:
        # Only fixed read-only paths are reachable from tools.
        if not (path == "/api/self/" or path == "/api/devops/apps/"
                or re.fullmatch(r"/api/devops/apps/[1-9][0-9]*/pods/", path)
                or path in {"/api/devops/log/query-range/", "/api/devops/log/query/"}):
            raise PoseidonError("该接口不在只读允许列表中。")
        self.authenticate()
        headers = {f"x-poseidon-{k}": v for k, v in (scope or {}).items()}
        for attempt in range(2):
            access = self._access
            response = self._send("GET", path, headers={**headers, "Authorization": f"Bearer {access}"}, params=params)
            if response.status_code == 401 and attempt == 0:
                self._recover(access)
                continue
            return self._decode(response), response.headers
        raise PoseidonError("认证失败，请重新配置凭据。")

    def search_logs(self, app: str, start: str, end: str, keywords: list[str],
                    exclude_keywords: list[str], pod: str, subset: str, limit: int,
                    scope: dict[str, str], direction: str = "backward",
                    anchor: str = "", labels: dict[str, str] | None = None) -> dict[str, Any]:
        if not app or len(app) > 256:
            raise PoseidonError("app 必须是完整应用名称。")
        if not 1 <= limit <= 1000 or direction not in {"forward", "backward"}:
            raise PoseidonError("limit 必须在 1–1000 之间，direction 必须为 forward/backward。")
        times = query_range(start, end)
        selectors = {"app": app}
        if pod:
            selectors["pod"] = pod
        if subset:
            selectors["subset"] = positive_id(subset, "subset")
        allowed_labels = {"pod", "node", "container", "filename", "job", "namespace", "stream", "instance"}
        for key, value in (labels or {}).items():
            if key not in allowed_labels or not isinstance(value, str) or len(value) > 1024:
                raise PoseidonError("上下文标签无效；只能传日志返回的 pod/node/container/filename/job/namespace/stream/instance。")
            if key in selectors and selectors[key] != value:
                raise PoseidonError("pod 参数与 labels 中的 pod 不一致。")
            selectors[key] = value
        params = list({**selectors, **times, "limit": str(limit), "direction": direction}.items())
        if anchor:
            if not re.fullmatch(r"[0-9]{1,30}", anchor) or not int(times["start"]) <= int(anchor) <= int(times["end"]):
                raise PoseidonError("time_ns 必须是查询时间范围内的原始纳秒字符串。")
            params.append(("time", anchor))
        params.extend(keyword_params(keywords, exclude_keywords))
        path = "/api/devops/log/query/" if anchor else "/api/devops/log/query-range/"
        data, _ = self.get(path, scope, params)
        result = normalize_logs(data, limit)
        return {"scope": scope, "app": app, "start": start, "end": end,
                "direction": direction, **result}


def normalize_logs(data: Any, limit: int) -> dict[str, Any]:
    if not isinstance(data, dict) or not isinstance(data.get("result"), list):
        raise PoseidonError("日志响应不符合预期的 result/stream/values 结构。")
    rows = []
    for stream in data["result"]:
        if not isinstance(stream, dict) or not isinstance(stream.get("stream"), dict) or not isinstance(stream.get("values"), list):
            raise PoseidonError("日志流结构无效。")
        for row in stream["values"]:
            if (not isinstance(row, list) or len(row) < 2 or not isinstance(row[0], str)
                    or not row[0].isdigit() or not isinstance(row[1], str)):
                raise PoseidonError("日志行结构无效。")
            rows.append({"time_ns": row[0], "message": row[1], "labels": stream["stream"]})
    # Ascending presentation; keep raw nanosecond strings for exact context anchors.
    rows.sort(key=lambda row: int(row["time_ns"]))
    original_count = len(rows)
    # A byte budget protects the model context. Preserve truncation information.
    output, used = [], 0
    import json
    for row in rows[:limit]:
        cost = len(json.dumps(row, ensure_ascii=False).encode("utf-8"))
        if used + cost > 200_000:
            break
        output.append(row)
        used += cost
    has_next = data.get("has_next", data.get("hasNext"))
    has_previous = data.get("has_previous", data.get("hasPrevious"))
    return {
        "logs": output, "count": len(output), "order": "ascending",
        "has_next": has_next, "has_previous": has_previous,
        "locally_truncated": len(output) < original_count,
        "possibly_truncated": bool(has_next or has_previous or original_count >= limit or len(output) < original_count),
        "oldest_time_ns": output[0]["time_ns"] if output else None,
        "newest_time_ns": output[-1]["time_ns"] if output else None,
    }
