#!/usr/bin/env python3
"""경매 모니터 화면 서버 (http://localhost:8765).

- GET  /            DB 내용으로 화면을 그려서 보여줌 (매일 9시 수집 결과가 바로 반영)
- POST /api/view    {"m_codes": [...]}            눌러 본 물건을 '확인함'으로 저장
- POST /api/star    {"m_code": "...", "starred": true|false}  집중 모니터링 표시
이 컴퓨터(127.0.0.1)에서만 접속됩니다.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import collector

HOST, PORT = "127.0.0.1", 8765
ALLOWED_ORIGINS = {f"http://localhost:{PORT}", f"http://127.0.0.1:{PORT}"}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def do_GET(self) -> None:
        if self.path.split("?")[0] not in ("/", "/index.html"):
            return self._send(404, b"not found", "text/plain")
        con = collector.db()
        try:
            html = collector.render_page(con)
        finally:
            con.close()
        self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")

    def do_POST(self) -> None:
        # 다른 사이트가 이 서버로 요청을 보내지 못하도록 출처 확인
        origin = self.headers.get("Origin")
        if origin and origin not in ALLOWED_ORIGINS:
            return self._json(403, {"error": "forbidden origin"})
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self._json(415, {"error": "json only"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except ValueError:
            return self._json(400, {"error": "bad json"})
        con = collector.db()
        try:
            if self.path == "/api/view":
                codes = [str(c) for c in body.get("m_codes") or []][:1000]
                now = dt.datetime.now().isoformat(timespec="seconds")
                con.executemany("UPDATE items SET viewed_at=? WHERE m_code=? AND viewed_at IS NULL",
                                [(now, c) for c in codes])
            elif self.path == "/api/star":
                con.execute("UPDATE items SET starred=? WHERE m_code=?",
                            (1 if body.get("starred") else 0, str(body.get("m_code"))))
            else:
                return self._json(404, {"error": "not found"})
            con.commit()
        finally:
            con.close()
        self._json(200, {"ok": True})

    def log_message(self, fmt, *args) -> None:  # 조용히
        pass


def main() -> int:
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"경매 모니터: http://localhost:{PORT}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
