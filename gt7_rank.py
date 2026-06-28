#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GT7 Rank Fetcher  ·  WYS / GT7 Training Tracker
================================================
從 gran-turismo.com 抓取你目前參加的所有 Time Trial / Daily Race 的
實際名次與排行榜門檻，自動更新 data.json。

需求：Python 3.8+，需要 requests（pip install requests）。
NPSSO 取得方式：
    1. 瀏覽器登入 PlayStation.com
    2. 開新分頁前往 https://ca.account.sony.com/api/v1/ssocookie
    3. 複製 npsso 值（勿分享他人）

用法：
    python gt7_rank.py --npsso <你的npsso> [--data data.json]

選項：
    --npsso   NPSSO token（必填；或設定環境變數 GT7_NPSSO）
    --data    data.json 路徑（預設 ./data.json）
    --dry     只顯示結果，不寫入 data.json
"""

import argparse
import base64
import json
import os
import sys
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

try:
    import requests
except ImportError:
    sys.exit("請先安裝 requests：pip install requests")

# ── PSN OAuth 常數（公開於多個社群 PSN API 專案）──
_PSN_CLIENT_ID     = "09515159-7237-4370-9b40-3806e67c0891"
_PSN_CLIENT_SECRET = "ucPjka5tntB2KqsP"
_PSN_REDIRECT_URI  = "com.scee.psxandroid.scecompcall://redirect"
_PSN_SCOPE         = "psn:mobile.v2.core psn:clientapp"

GT_API = "https://www.gran-turismo.com/us/api/gt7sp"


def _psn_auth_code(npsso: str) -> str:
    r = requests.get(
        "https://ca.account.sony.com/api/authz/v3/oauth/authorize",
        params={
            "access_type": "offline",
            "client_id": _PSN_CLIENT_ID,
            "redirect_uri": _PSN_REDIRECT_URI,
            "response_type": "code",
            "scope": _PSN_SCOPE,
        },
        cookies={"npsso": npsso},
        headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"},
        allow_redirects=False,
        timeout=10,
    )
    loc = r.headers.get("Location", "")
    code = parse_qs(urlparse(loc).query).get("code", [None])[0]
    if not code:
        raise RuntimeError(f"無法取得授權碼（status={r.status_code}）；NPSSO 可能過期，請重新取得。")
    return code


def _psn_access_token(code: str) -> str:
    creds = base64.b64encode(f"{_PSN_CLIENT_ID}:{_PSN_CLIENT_SECRET}".encode()).decode()
    r = requests.post(
        "https://ca.account.sony.com/api/authz/v3/oauth/token",
        data={
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": _PSN_REDIRECT_URI,
            "token_format": "jwt",
        },
        headers={
            "Authorization": f"Basic {creds}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        timeout=10,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def _gt_post(path: str, access_token: str, **params) -> dict:
    """呼叫 gran-turismo.com gt7sp API（POST + access_token）。"""
    r = requests.post(
        f"{GT_API}/{path}/",
        data=params,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Referer": "https://www.gran-turismo.com/",
            "User-Agent": "Mozilla/5.0",
        },
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


import re

_USER_NO_PATTERNS = [
    r'/user/(\d{4,})',
    r'user_no["\s:=]+(\d{3,})',
    r'"userNo"\s*:\s*"?(\d{3,})',
    r'data-user-?no["\s:=]+(\d{3,})',
    r'profile/(\d{4,})',
]

def get_user_no(access_token: str, psn_id: str) -> int:
    """用 PSN ID 查 gran-turismo.com 的 user_no。"""
    r = requests.get(
        f"https://www.gran-turismo.com/us/gt7/user/{psn_id}/",
        headers={
            "Authorization": f"Bearer {access_token}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
        },
        timeout=10,
    )
    final_url = r.url
    parts = [p for p in final_url.rstrip("/").split("/") if p.isdigit() and len(p) >= 4]
    if parts:
        return int(parts[-1])
    for pat in _USER_NO_PATTERNS:
        m = re.search(pat, r.text)
        if m:
            return int(m.group(1))
    raise RuntimeError(f"找不到 user_no（status={r.status_code}, url={final_url}）— 跑 --debug-user 把回應貼給我")


def debug_user(access_token: str, psn_id: str):
    """Dump the raw profile response so we can see how to extract user_no."""
    url = f"https://www.gran-turismo.com/us/gt7/user/{psn_id}/"
    r = requests.get(url, headers={
        "Authorization": f"Bearer {access_token}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
    }, timeout=15)
    print(f"--- GET {url}")
    print(f"status   = {r.status_code}")
    print(f"final_url= {r.url}")
    print(f"ctype    = {r.headers.get('Content-Type')}")
    print(f"len      = {len(r.text)}")
    for pat in _USER_NO_PATTERNS:
        hits = re.findall(pat, r.text)
        if hits:
            print(f"pattern {pat!r} -> {hits[:6]}")
    # canonical / og:url often carry the numeric profile id
    for m in re.findall(r'<link[^>]+canonical[^>]*>', r.text): print("canonical:", m.strip())
    for m in re.findall(r'og:url"[^>]*content="([^"]+)"', r.text): print("og:url:", m)
    # context around id-ish keywords
    for kw in ['user_no', 'userNo', 'user-no', '/user/', 'board_id', 'boardId', 'data-']:
        for mm in list(re.finditer(re.escape(kw), r.text))[:4]:
            s = max(0, mm.start() - 45)
            print(f"  [{kw}] …{r.text[s:mm.start()+70].strip()}…")
    # context around each long number candidate
    print("----- number contexts -----")
    for num in sorted(set(re.findall(r'\b\d{6,}\b', r.text))):
        i = r.text.find(num); s = max(0, i - 55)
        print(f"  [{num}] …{r.text[s:i+len(num)+15].strip()}…")
    print("----- body head (1200 chars) -----")
    print(r.text[:1200])


def get_sport_mode_stats(access_token: str, user_no: int) -> dict:
    """取得 Sport Mode 統計（含各時間試煉成績與名次）。"""
    return _gt_post("profile", access_token, job=13, user_no=user_no)


def get_ranking_around_player(access_token: str, user_no: int, board_id: int) -> dict:
    """取得玩家附近的排名（含玩家名次）。"""
    return _gt_post("ranking", access_token, job=1, user_no=user_no, board_id=board_id)


def get_ranking_list(access_token: str, board_id: int, begin: int = 1, end: int = 1000) -> dict:
    """取得排行榜範圍（用來抓門檻）。"""
    return _gt_post("ranking", access_token, job=3, board_id=board_id, begin=begin, end=end)


def discover_board_id(access_token: str, user_no: int) -> list[dict]:
    """從 Sport Mode stats 反推目前有成績的 board_id 清單。"""
    stats = get_sport_mode_stats(access_token, user_no)
    boards = []
    # gt7sp API 回傳結構依版本不同；嘗試常見欄位
    for entry in stats.get("stats", stats.get("result", [])):
        bid = entry.get("board_id") or entry.get("boardId")
        if bid:
            boards.append({"board_id": bid, "raw": entry})
    return boards


def fmt_ms(ms: int) -> str:
    if ms is None or ms < 0:
        return "—"
    s = ms / 1000.0
    m = int(s // 60)
    return f"{m}:{s - m * 60:06.3f}"


def update_data_json(path: str, updates: list[dict], dry: bool = False):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    lb = data.setdefault("meta", {}).setdefault("leaderboards", {})
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    for u in updates:
        key = u["key"]
        if key not in lb:
            print(f"  ⚠ {key} 不在 leaderboards，略過（先在 data.json 建好 entry 再跑）")
            continue
        entry = lb[key]
        changed = []

        if u.get("total_players") and entry.get("totalPlayers") != u["total_players"]:
            entry["totalPlayers"] = u["total_players"]
            changed.append(f"totalPlayers={u['total_players']}")

        if u.get("top100"):
            entry["top100"] = u["top100"]
            changed.append(f"top100={u['top100']}")

        if u.get("top1000"):
            entry["top1000"] = u["top1000"]
            changed.append(f"top1000={u['top1000']}")

        if u.get("player_rank") is not None:
            pr = entry.setdefault("playerRank", {})
            pr["rank"] = u["player_rank"]
            pr["pb"] = u.get("pb", pr.get("pb"))
            pr["source"] = "auto(gt7-rank.py)"
            pr["asOf"] = today
            pr.pop("rankStale", None)
            top_pct = round(u["player_rank"] / u["total_players"] * 100, 2) if u.get("total_players") else None
            if top_pct:
                pr["topPct"] = top_pct
            pr["note"] = (
                f"自動抓取 Global #{u['player_rank']}"
                + (f" top {top_pct}%" if top_pct else "")
                + f"（{today}，共 {u.get('total_players','?')} 人）"
            )
            changed.append(f"rank=#{u['player_rank']} ({top_pct}%)")

        entry["asOf"] = today
        print(f"  {'[DRY]' if dry else '✓'} {key}: {', '.join(changed) if changed else '無變動'}")

    data["meta"]["lastUpdated"] = today

    if not dry:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"已寫入 {path}")


# ── 已知 board_id 對應表（每次新活動跑一次 --discover 更新）──
KNOWN_BOARDS: dict[str, int] = {
    # key（data.json leaderboards key）: gran-turismo.com board_id
    # 首次執行請先跑 --discover 填入
    # "redbullring__fordgt17": 99999,
}


def main():
    ap = argparse.ArgumentParser(description="GT7 Rank Fetcher")
    ap.add_argument("--npsso", default=os.environ.get("GT7_NPSSO"), help="NPSSO token（或設 GT7_NPSSO 環境變數）")
    ap.add_argument("--psn-id", default="b95208010", help="PSN ID（預設 b95208010）")
    ap.add_argument("--data", default="data.json", help="data.json 路徑")
    ap.add_argument("--dry", action="store_true", help="只顯示，不寫入")
    ap.add_argument("--discover", action="store_true", help="列出目前有成績的 board_id（用於建立對應表）")
    ap.add_argument("--debug-user", action="store_true", help="印出 user 個人頁的原始回應（用來校正 user_no 抓法）")
    args = ap.parse_args()

    if not args.npsso:
        sys.exit("請提供 --npsso 或設定環境變數 GT7_NPSSO\n"
                 "取得方式：瀏覽器登入 PS，開 https://ca.account.sony.com/api/v1/ssocookie")

    print("🔑 PSN 認證中…")
    code = _psn_auth_code(args.npsso)
    token = _psn_access_token(code)
    print("  ✓ 取得 access token")

    if args.debug_user:
        debug_user(token, args.psn_id)
        return

    print(f"👤 查詢 user_no ({args.psn_id})…")
    user_no = get_user_no(token, args.psn_id)
    print(f"  ✓ user_no = {user_no}")

    if args.discover:
        print("🔍 探索 board_id（有成績的活動）…")
        boards = discover_board_id(token, user_no)
        if boards:
            for b in boards:
                print(f"  board_id={b['board_id']}  raw={json.dumps(b['raw'])[:120]}")
        else:
            print("  找不到，可能 API 結構不同；請改查 Sport Mode stats 原始資料：")
            raw = get_sport_mode_stats(token, user_no)
            print(json.dumps(raw, ensure_ascii=False, indent=2)[:2000])
        return

    # 從 data.json 補充 boardId（優先使用 KNOWN_BOARDS，data.json 作為備援）
    boards: dict[str, int] = {}
    try:
        with open(args.data, encoding="utf-8") as f:
            _d = json.load(f)
        for k, v in _d.get("meta", {}).get("leaderboards", {}).items():
            if v.get("boardId"):
                boards[k] = int(v["boardId"])
    except Exception:
        pass
    boards.update(KNOWN_BOARDS)  # KNOWN_BOARDS 覆蓋 data.json

    if not boards:
        print("⚠ 找不到 board_id。請先跑 --discover，")
        print("  然後在 data.json meta.leaderboards.<key>.boardId 填入對應的 board_id。")
        return

    updates = []
    for key, board_id in boards.items():
        print(f"📊 抓取 {key} (board_id={board_id})…")
        try:
            rank_data = get_ranking_around_player(token, user_no, board_id)
            top_data  = get_ranking_list(token, board_id, 1, 1)
            top1000   = get_ranking_list(token, board_id, 999, 1001)

            # 從回傳資料解析（實際欄位名稱待確認，依 API 回傳調整）
            player_entry = next(
                (e for e in rank_data.get("entries", rank_data.get("result", []))
                 if str(e.get("user_no", "")) == str(user_no)),
                None
            )
            total = rank_data.get("total_count") or rank_data.get("totalCount")
            pb_ms = player_entry.get("best_time") or player_entry.get("bestTime") if player_entry else None
            rank  = player_entry.get("rank") if player_entry else None

            top1000_time_ms = None
            for e in top1000.get("entries", top1000.get("result", [])):
                if e.get("rank") in (999, 1000, 1001):
                    top1000_time_ms = e.get("best_time") or e.get("bestTime")
                    break

            print(f"  rank=#{rank}  PB={fmt_ms(pb_ms)}  total={total}  top1000={fmt_ms(top1000_time_ms)}")
            updates.append({
                "key": key,
                "player_rank": rank,
                "pb": round(pb_ms / 1000, 3) if pb_ms else None,
                "total_players": total,
                "top1000": round(top1000_time_ms / 1000, 3) if top1000_time_ms else None,
            })
        except Exception as e:
            print(f"  ✗ {key} 失敗：{e}")

    if updates:
        print(f"\n📝 更新 {args.data}…")
        update_data_json(args.data, updates, dry=args.dry)


if __name__ == "__main__":
    main()
