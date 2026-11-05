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
from pathlib import Path

from .. import __version__
from . import api
from ..updown import DEFAULT_ROBOT
from .flow import USE_DEFAULT, WebFlow

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
    # 読めなかった言葉を目立たせる分け方・マイナスの表示（依存なし）
    "/static/highlight.js": ("highlight.js", "text/javascript; charset=utf-8"),
    "/static/player.js": ("player.js", "text/javascript; charset=utf-8"),
    # Claude に聞く（読み取りだけ）の小さな欄
    "/static/explain.js": ("explain.js", "text/javascript; charset=utf-8"),
}
# 「サンプルで試す」で読み込む、決まった1つの架空のジョブ。画面からは名前も場所も渡させない
SAMPLE_JOB = Path(__file__).resolve().parents[2] / "samples" / "dummy" / "DEMO_C_PLACE.JBI"
_VERSION_MARK = b"__VT_VERSION__"  # index.html の中の版の印（配信するときに __version__ を入れる）
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
        body = _static_bytes(entry[0])
        if entry[0] == "index.html":
            body = body.replace(_VERSION_MARK, __version__.encode("ascii"))
        self._send(200, body, entry[1])

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

    def _previous(self) -> str | None:
        """前に見ていた画面の番号（読み直したとき、同じ中身なら人が教えた意味を引き継ぐため）。見出しの X-VT-Previous-Session。"""
        v = self.headers.get("X-VT-Previous-Session")
        return v if isinstance(v, str) and 0 < len(v) <= 64 else None

    def _load(self, flow: WebFlow, data: bytes) -> dict:
        prev = self._previous()
        return flow.load(data, prev) if prev is not None else flow.load(data)

    def _flow_post(self, path: str) -> bool:
        flow: WebFlow = self.server.flow
        if path == "/api/load":
            body = self._read_body()
            if body is not None:
                self._json(200, self._load(flow, body))
            return True
        if path == "/api/sample":
            # 本文は読むだけで使わない（問い合わせの文字列も見ない）。決まった1つのファイルを、アップロードと同じ flow.load に渡す
            if self._read_body() is not None:
                try:
                    data = SAMPLE_JOB.read_bytes()
                except OSError:
                    self._error(404, "サンプルのジョブが見つかりません")
                else:
                    self._json(200, self._load(flow, data))
            return True
        routes = {
            "/api/hear": lambda r: flow.hear(r.get("session_id"), r.get("text"), r.get("source", "text")),
            "/api/talk": lambda r: flow.talk(r.get("session_id"), r.get("text"), r.get("source", "text")),  # Step F：入口を1つに
            "/api/select": lambda r: flow.select(r.get("session_id"), r.get("position")),
            "/api/compare": lambda r: flow.compare(r.get("session_id")),
            "/api/undo": lambda r: flow.undo(r.get("session_id")),
            "/api/state": lambda r: flow.state(r.get("session_id")),
            # 会話の選択肢のボタン（位置の候補・ほかの位置・方向）。今の選択肢の番号（turn）が合うときだけ。承認はしない
            "/api/dialog": lambda r: flow.choose(r.get("session_id"), r.get("choice"), r.get("turn")),
            # 回数の変更の範囲の確認のボタン（［この範囲を繰り返す］［違う］）。候補を作るだけで、承認はしない
            "/api/repeat": lambda r: flow.repeat_choose(r.get("session_id"), r.get("choice"), r.get("turn")),
            # 承認は画面の承認ボタンからだけ。/api/hear からは呼ばない
            "/api/approve": lambda r: flow.approve(r.get("session_id"), r.get("approver"), candidate_gen=r.get("candidate_gen")),
            "/api/export": lambda r: flow.export(r.get("session_id"), candidate_gen=r.get("candidate_gen")),
            # Claude に聞く（読み取りだけ）。答えでジョブ・候補・承認は変わらない。キーは返さない
            # 人が教えた意味（この画面の中だけ）：sync で確かめ直し、choose・token で「どの動きのことですか？」のボタン
            "/api/ask": lambda r: flow.ask(r.get("session_id"), r.get("text"), r.get("reset", False), r.get("info", False),
                                           r.get("source", "text"), r.get("choose"), r.get("token"), r.get("sync", False)),
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


def make_server(host: str = HOST, port: int = DEFAULT_PORT, interpreter=None,
                updown_config=USE_DEFAULT, robot: str = DEFAULT_ROBOT, explainer=None) -> ThreadingHTTPServer:
    """シミュレーション用の出力先は sim.DEFAULT_OUT_DIR（output/sim/）に固定。呼び出し側からは選べない。

    updown_config：上下の向きの設定ファイル（None なら設定なし＝未確認。渡さなければ flow.DEFAULT_UPDOWN_CONFIG）。robot：アプリで使うロボットの名前（照合で比べるだけ）。
    """
    if host != HOST:
        raise ValueError("127.0.0.1 以外では待ち受けません")
    srv = ThreadingHTTPServer((HOST, port), Handler)
    srv.daemon_threads = True
    srv.flow = WebFlow(interpreter=interpreter, updown_config=updown_config, robot=robot, explainer=explainer)
    return srv
