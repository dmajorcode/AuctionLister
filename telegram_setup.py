#!/usr/bin/env python3
"""텔레그램 알림 받을 사람 정하기.

1) 알림을 받을 사람이 텔레그램에서 봇(@AuctionCrawlerBot)을 열고 '시작'(/start)을 누른다.
2) 이 스크립트를 실행하면 봇에게 메시지를 보낸 사람 목록(채팅 ID)을 보여준다.
3) 받을 사람의 ID만 쉼표로 이어서 TELEGRAM_CHAT_IDS 에 넣는다 (.env 와 GitHub Secrets).
   목록에 없는 사람에게는 알림이 가지 않는다. 봇은 누구의 메시지에도 답하지 않는다.

    python3 telegram_setup.py          # 목록 보기
    python3 telegram_setup.py --test   # 허용 목록 전원에게 테스트 메시지
"""
from __future__ import annotations

import json
import sys
import urllib.request

import collector


def call(token: str, method: str) -> dict:
    with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/{method}", timeout=20) as r:
        return json.load(r)


def main() -> int:
    env = collector.load_env()
    token = env.get("TELEGRAM_TOKEN")
    if not token:
        print(".env 에 TELEGRAM_TOKEN=<BotFather 가 준 토큰> 을 먼저 넣어 주세요.")
        return 1
    me = call(token, "getMe")["result"]
    print(f"봇: @{me.get('username')}")
    allowed = set(collector.telegram_recipients(env))

    if "--test" in sys.argv:
        if not allowed:
            print("TELEGRAM_CHAT_IDS 가 비어 있습니다.")
            return 1
        for chat in allowed:
            try:
                collector.telegram_send(token, chat, "✅ 경매 모니터 알림 테스트입니다.")
                print(f"  보냄: {chat}")
            except Exception as e:
                print(f"  실패: {chat} → {e} (그 사람이 봇에서 '시작'을 눌렀는지 확인)")
        return 0

    seen: dict[int, str] = {}
    for u in call(token, "getUpdates").get("result", []):
        msg = u.get("message") or u.get("my_chat_member") or {}
        chat = msg.get("chat") or {}
        if chat.get("id") is not None:
            name = " ".join(filter(None, [chat.get("first_name"), chat.get("last_name")])) or chat.get("title") or ""
            user = f"@{chat['username']}" if chat.get("username") else ""
            seen[chat["id"]] = f"{name} {user} ({chat.get('type')})".strip()
    if not seen:
        print("최근 24시간 안에 봇에게 메시지를 보낸 사람이 없습니다. 받을 사람이 봇에서 '시작'을 누른 뒤 다시 실행하세요.")
    else:
        print("봇에게 메시지를 보낸 사람:")
        for cid, desc in seen.items():
            mark = "✓ 허용됨" if str(cid) in allowed else "  (허용 안 됨)"
            print(f"  {mark}  {cid}  {desc}")
    print(f"\n현재 허용 목록 TELEGRAM_CHAT_IDS: {', '.join(sorted(allowed)) or '(비어 있음)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
