"""シミュレーションモードの小さなサーバー（標準ライブラリの http.server だけを使う）。

- 127.0.0.1 だけで待ち受ける（ほかのアドレスは指定させない）。
- 受け取ったジョブと画面の状態（Session）はメモリの中だけで扱い、ディスクに保存しない。サーバーから外へは通信しない。
- 書き出すのは、ボタンで承認したシミュレーション用のジョブ（合成ジョブだけ）を output/sim/ に出す /api/export だけ。
  実機用の出力・転送・ロボットの起動の経路は無い。
- 承認（/api/approve）は画面の承認ボタンからだけ呼ぶ。指示（/api/hear）の経路からは呼ばない。
- 別のサイトから呼ばれないように、Host と Origin を確かめる。
"""
from __future__ import annotations

import base64
import binascii
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources

from . import api
from .flow import WebFlow

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MAX_BODY = 8 * 1024 * 1024  # 受け取る本文の上限（バイト）

_STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/static/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/static/style.css": ("style.css", "text/css; charset=utf-8"),
    # 音声入力（引き継ぎ fd03d84 の speech.js を無変更で配信）
    "/static/speech.js": ("speech.js", "text/javascript; charset=utf-8"),
    # 3D の点と経路の表示（依存なし）
    "/static/view3d.js": ("view3d.js", "text/javascript; charset=utf-8"),
}
_CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


def _static_bytes(name: str) -> bytes:
    return resources.files(__package__).joinpath("static", name).read_bytes()


class Handler(BaseHTTPRequestHandler):
    server_version = "VoiceTeachingView"
    sys_version = ""

    # ---- 共通 ----
    def _allowed_hosts(self) -> set[str]:
        port = self.server.server_address[1]
        return {f"127.0.0.1:{port}", f"localhost:{port}"}

    def _host_ok(self) -> bool:
        return self.headers.get("Host", "") in self._allowed_hosts()

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        return origin is None or origin in {f"http://{h}" for h in self._allowed_hosts()}

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", _CSP)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, obj: dict) -> None:
        self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"ok": False, "error": message})

    # ---- GET ----
    def do_GET(self) -> None:
        if not self._host_ok():
            return self._error(403, "このサーバーは 127.0.0.1 からだけ使えます")
        entry = _STATIC.get(self.path.split("?", 1)[0])
        if entry is None:
            return self._error(404, "ありません")
        self._send(200, _static_bytes(entry[0]), entry[1])

    # ---- POST ----
    def _read_body(self) -> bytes | None:
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._error(411, "本文の長さが分かりません")
            return None
        if length < 0 or length > MAX_BODY:
            self._error(413, "本文が大きすぎます")
            self.close_connection = True
            return None
        return self.rfile.read(length)

    def _read_json(self) -> tuple[dict, bytes] | None:
        body = self._read_body()
        if body is None:
            return None
        try:
            req = json.loads(body.decode("utf-8"))
            if not isinstance(req, dict):
                raise ValueError
            job = base64.b64decode(req.get("job_base64", ""), validate=True)
        except (ValueError, UnicodeDecodeError, binascii.Error, TypeError):
            self._error(400, "要求が読めません")
            return None
        return req, job

    def _read_obj(self) -> dict | None:
        body = self._read_body()
        if body is None:
            return None
        try:
            req = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            req = None
        if not isinstance(req, dict):
            self._error(400, "要求が読めません")
            return None
        return req

    def _flow_post(self, path: str) -> bool:
        flow: WebFlow = self.server.flow
        if path == "/api/load":
            body = self._read_body()
            if body is not None:
                self._json(200, flow.load(body))
            return True
        routes = {
            "/api/hear": lambda r: flow.hear(r.get("session_id"), r.get("text"), r.get("source", "text")),
            "/api/select": lambda r: flow.select(r.get("session_id"), r.get("position")),
            "/api/compare": lambda r: flow.compare(r.get("session_id")),
            "/api/undo": lambda r: flow.undo(r.get("session_id")),
            "/api/state": lambda r: flow.state(r.get("session_id")),
            # 承認は画面の承認ボタンからだけ。/api/hear からは呼ばない
            "/api/approve": lambda r: flow.approve(r.get("session_id"), r.get("approver")),
            "/api/export": lambda r: flow.export(r.get("session_id")),
        }
        fn = routes.get(path)
        if fn is None:
            return False
        req = self._read_obj()
        if req is not None:
            self._json(200, fn(req))
        return True

    def do_POST(self) -> None:
        if not self._host_ok() or not self._origin_ok():
            return self._error(403, "このサーバーは 127.0.0.1 の画面からだけ使えます")
        path = self.path.split("?", 1)[0]
        if self._flow_post(path):
            return
        if path == "/api/parse":
            body = self._read_body()
            if body is not None:
                self._json(200, api.job_view(body))
        elif path == "/api/preview/shift":
            got = self._read_json()
            if got is not None:
                req, job = got
                self._json(200, api.preview_shift(job, req.get("position"), req.get("axis"), req.get("delta_mm")))
        elif path == "/api/preview/speed":
            got = self._read_json()
            if got is not None:
                req, job = got
                self._json(200, api.preview_speed(job, req.get("line_no"), req.get("speed")))
        else:
            # 本文を読んでから断る（読まずに閉じると、Windows では相手側で接続が中断されることがある）
            if self._read_body() is not None:
                self._error(404, "ありません")


def make_server(host: str = HOST, port: int = DEFAULT_PORT, interpreter=None) -> ThreadingHTTPServer:
    """シミュレーション用の出力先は sim.DEFAULT_OUT_DIR（output/sim/）に固定。呼び出し側からは選べない。"""
    if host != HOST:
        raise ValueError("127.0.0.1 以外では待ち受けません")
    srv = ThreadingHTTPServer((HOST, port), Handler)
    srv.daemon_threads = True
    srv.flow = WebFlow(interpreter=interpreter)
    return srv
