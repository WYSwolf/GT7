#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GT7 Rank Fetcher  ·  WYS / GT7 Training Tracker
================================================
從 gran-turismo.com 官方 web API 抓「你目前活動」的世界紀錄 / top100 / top1000 /
總人數 / 你的名次，更新 data.json 的 meta.leaderboards + references（WR）+ goals。

必須在「你自己的電腦」跑（家用 IP）——Sony / gran-turismo 會擋資料中心 IP。

認證二選一：
  A) 直接給 Bearer（最快，先用這個驗證）：
       1. 瀏覽器登入 gran-turismo.com，開你參加的活動頁
       2. F12 → Network → 任一個打向 web-api.gt7.game.gran-turismo.com 的請求
          → Request Headers 複製 Authorization: 後面那串（不含 "Bearer "）
       3. python gt7_rank.py --bearer <貼上> --dry
  B) 用 JSESSIONID（較持久，能自動換 Bearer）：
       1. F12 → Application → Cookies → www.gran-turismo.com → 複製 JSESSIONID 值
       2. python gt7_rank.py --jsessionid <貼上> --dry
     （或設環境變數 GT7_GT_TOKEN / GT7_JSESSIONID）

board_id：寫在 data.json 的 meta.leaderboards.<key>.boardId
  （目前 redbullring__fordgt17 = p_rt_1014277_001）

選項：
  --dry        只顯示、不寫入
  --data       data.json 路徑（預設 ./data.json）
  --locale     gran-turismo 語系路徑，預設 tw
  --max-pages  最多翻幾頁找你的名次（每頁 100，預設 60）
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

try:
    import requests
except ImportError:
    sys.exit("請先安裝 requests：pip install requests")

WEB_API = "https://web-api.gt7.game.gran-turismo.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36")


def get_token_info(jsessionid: str, locale: str = "tw") -> dict:
    """用 gran-turismo.com 的 JSESSIONID 換 token；回傳含 access_token / user_id 的 dict。"""
    url = f"https://www.gran-turismo.com/{locale}/gt7/info/api/token/"
    r = requests.get(url, headers={
        "Accept": "*/*", "User-Agent": UA,
        "Referer": f"https://www.gran-turismo.com/{locale}/gt7/sportmode/",
    }, cookies={"JSESSIONID": jsessionid}, timeout=15)
    r.raise_for_status()
    try:
        data = r.json()
    except ValueError:
        raise RuntimeError(f"token/ 不是 JSON（status={r.status_code}）：{r.text[:200]}")
    if not data.get("access_token"):
        if data.get("is_signed_in") is False:
            raise RuntimeError("token/ 顯示未登入（is_signed_in=false）—— JSESSIONID 可能過期，重新登入再複製。")
        raise RuntimeError(f"token/ 找不到 access_token：{json.dumps(data)[:200]}")
    return data


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://www.gran-turismo.com",
        "Referer": "https://www.gran-turismo.com/",
        "User-Agent": UA,
    }


def get_my_user_id(token: str) -> str:
    r = requests.post(f"{WEB_API}/user/get_sport_profile", headers=_headers(token), timeout=15)
    r.raise_for_status()
    return r.json()["result"]["user_id"]


def get_page(token: str, board_id: str, page: int) -> dict:
    h = _headers(token); h["Content-Type"] = "application/json"
    r = requests.post(f"{WEB_API}/ranking/get_list_by_page", headers=h,
                      data=json.dumps({"board_id": board_id, "page": page}), timeout=15)
    r.raise_for_status()
    return r.json()["result"]


def disp(sec):
    if sec is None:
        return "—"
    m = int(sec // 60)
    return f"{m}:{sec - m * 60:06.3f}"


def fetch_board(token: str, board_id: str, my_id: str, max_pages: int = 60) -> dict:
    """回傳 {wr, top100, top1000, total, rank, pb}（秒）。score 單位是毫秒。"""
    out = {"wr": None, "top100": None, "top1000": None, "total": None, "rank": None, "pb": None}
    page = 0
    while page < max_pages:
        res = get_page(token, board_id, page)
        lst = res.get("list", [])
        if out["total"] is None:
            out["total"] = res.get("total")
        if not lst:
            break
        for e in lst:
            r = e.get("display_rank")
            s = e.get("score")
            if r == 1:
                out["wr"] = round(s / 1000, 3)
            if r == 100:
                out["top100"] = round(s / 1000, 3)
            if r == 1000:
                out["top1000"] = round(s / 1000, 3)
            if e.get("user", {}).get("user_id") == my_id:
                out["rank"] = r
                out["pb"] = round(s / 1000, 3)
        last_rank = lst[-1].get("display_rank") or 0
        got_top1000 = out["top1000"] is not None or (out["total"] or 0) < 1000
        if out["rank"] is not None and got_top1000:
            break
        if out["total"] and last_rank >= out["total"]:
            break
        page += 1
        time.sleep(0.3)   # 尊重速率限制
    # total<1000 時用末位當 top1000 近似（其實就是最後一名）
    return out


def recompute_goals(d: dict, track_key: str, wr: float):
    pol = d.get("meta", {}).get("goalPolicy")
    if not pol or wr is None:
        return
    for g in d.get("goals", []):
        if g.get("trackKey") != track_key:
            continue
        items = []
        for t in pol["tiers"]:
            tgt = round(wr * (1 + t["offsetPct"] / 100), 3)
            items.append({"label": t["label"], "target": tgt, "displayTarget": disp(tgt),
                          "desc": f"世界第一 +{t['offsetPct']}%(門檻 {disp(tgt)},WR {disp(wr)})"})
        g["items"] = items


def main():
    ap = argparse.ArgumentParser(description="GT7 Rank Fetcher (web-api)")
    ap.add_argument("--bearer", default=os.environ.get("GT7_GT_TOKEN"), help="web-api Bearer token（或設 GT7_GT_TOKEN）")
    ap.add_argument("--jsessionid", default=os.environ.get("GT7_JSESSIONID"), help="gran-turismo.com JSESSIONID（或設 GT7_JSESSIONID）")
    ap.add_argument("--locale", default="tw")
    ap.add_argument("--data", default="data.json")
    ap.add_argument("--dry", action="store_true", help="只顯示、不寫入")
    ap.add_argument("--max-pages", type=int, default=60)
    args = ap.parse_args()

    token = args.bearer
    my_id = None
    if not token:
        if not args.jsessionid:
            sys.exit("請給 --bearer 或 --jsessionid（或設環境變數）。用法見檔頭。")
        print("🔑 用 JSESSIONID 換 Bearer…")
        info = get_token_info(args.jsessionid, args.locale)
        token = info["access_token"]
        my_id = info.get("user_id")
        print(f"  ✓ 取得 Bearer（user_id={my_id}）")

    if not my_id:
        print("👤 取得自己的 user_id…")
        my_id = get_my_user_id(token)
        print(f"  ✓ user_id = {my_id}")

    with open(args.data, encoding="utf-8") as f:
        d = json.load(f)
    boards = {k: v["boardId"] for k, v in d.get("meta", {}).get("leaderboards", {}).items() if v.get("boardId")}
    if not boards:
        sys.exit("data.json 沒有任何 meta.leaderboards.<key>.boardId；先填好再跑。")

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for key, board_id in boards.items():
        print(f"📊 {key}  (board_id={board_id})…")
        try:
            r = fetch_board(token, board_id, my_id, args.max_pages)
        except Exception as e:
            print(f"  ✗ 失敗：{e}")
            continue
        pct = round(r["rank"] / r["total"] * 100, 2) if r["rank"] and r["total"] else None
        print(f"  WR={disp(r['wr'])}  top100={disp(r['top100'])}  top1000={disp(r['top1000'])}  "
              f"total={r['total']}  你的名次=#{r['rank']} ({pct}%)  PB={disp(r['pb'])}")

        entry = d["meta"]["leaderboards"][key]
        if r["total"]:   entry["totalPlayers"] = r["total"]
        if r["top100"]:  entry["top100"] = r["top100"]
        if r["top1000"]: entry["top1000"] = r["top1000"]
        if r["wr"]:      entry["wr"] = r["wr"]
        if r["rank"]:
            entry["playerRank"] = {"rank": r["rank"], "pb": r["pb"], "source": "auto(gt7_rank.py)",
                                   "asOf": today, **({"topPct": pct} if pct else {})}
        entry["asOf"] = today
        # 同步更新 WR 參考值 + 依 goalPolicy 重算該賽道目標
        car_slug = key.split("__")[1] if "__" in key else None
        track_key = key.split("__")[0]
        if r["wr"] and car_slug:
            ref = d["meta"].setdefault("references", {}).setdefault(car_slug, {})
            ref["time"] = r["wr"]; ref["displayTime"] = disp(r["wr"])
            ref.setdefault("label", "WR(GT7 全球#1)")
            ref["note"] = f"GT7 官方排行 #1（{today} 自動抓取）"
            recompute_goals(d, track_key, r["wr"])

    d["meta"]["lastUpdated"] = today
    if args.dry:
        print("\n[DRY] 未寫入。確認數字 OK 後拿掉 --dry 再跑。")
    else:
        with open(args.data, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        print(f"\n✓ 已更新 {args.data}")


if __name__ == "__main__":
    main()
