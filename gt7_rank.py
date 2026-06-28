#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GT7 Rank Fetcher  ·  WYS / GT7 Training Tracker
================================================
從 gran-turismo.com 官方 web API 抓「你目前活動」的世界紀錄 / top100 / top1000 /
你的名次，更新 data.json 的 meta.leaderboards + references（WR）+ goals。
母體總人數（官方 API 抓不到，它只回榜上清單大小）改從 leaderboards.<key>.eventUrl
指向的 dg-edge 事件頁抓「Total players」。

必須在「你自己的電腦」跑（家用 IP）——Sony / gran-turismo 會擋資料中心 IP。

認證三選一：
  A) --browser：自動從瀏覽器讀 JSESSIONID（推薦，等於自動續期，免手動複製）
       1. pip install browser-cookie3
       2. 用平常的瀏覽器登入 https://www.gran-turismo.com（保持登入即可）
       3. python gt7_rank.py --browser --dry          # 不帶值=掃所有瀏覽器
          或 --browser chrome / edge / firefox / brave …（或設 GT7_BROWSER）
       只要瀏覽器還登入著，每次跑都抓到當前有效的 cookie，不必再手動換。
  B) --jsessionid：手動貼一次（會過期，幾天～兩週要重貼）
       F12 → Application → Cookies → www.gran-turismo.com → 複製 JSESSIONID 值
       python gt7_rank.py --jsessionid <貼上> --dry
  C) --bearer：直接給短效 Bearer（驗證用，~1 小時就過期）
       F12 → Network → 任一打向 web-api.gt7.game.gran-turismo.com 的請求
       → Request Headers 複製 Authorization: 後面那串（不含 "Bearer "）
     （也可設環境變數 GT7_GT_TOKEN / GT7_JSESSIONID / GT7_BROWSER）

board_id：寫在 data.json 的 meta.leaderboards.<key>.boardId
  ★ 通常不必手填 ★ —— 只要該 leaderboard 有 dg-edge 的 eventUrl，本檔抓榜時會自動
  從 dg-edge 事件頁解出 board_id 並補進 boardId（dg-edge 頁面內嵌
  "<dgId>",<num>,"p_rt_..._...","TT"，用事件 id 錨定取得）。所以新賽道一般只要填
  eventUrl 即可，boardId 留空，跑一次就自動補上。

  board_id 的組成（從官方 sportmode 前端解出，供理解/手動 fallback）：
    · 一般時間競賽（registration_key 為空）→ board_id 直接 = 活動的 ranking_id，
      沒有任何後綴運算。p_rt_1014277_001 整串就是 ranking_id（_001 是後端字串的一部分，
      不是區碼）。
    · 分區報名賽事（registration_key 非空）才接區碼：board_id = ranking_id + "_" + 區碼，
      區碼 = 成人:2 位數補零、非成人:(區+100)，且 Oita/Kumamoto 互換。個人 TT 不碰這條。

手動找 board_id 的 fallback（沒有 dg-edge eventUrl 時，打 web-api，認證與抓榜共用）：
  --event <id>     打 POST /event/get_parameter {"event_id": id}（id = 活動頁網址尾數，
                   如 .../sportmode/event/14277/ → 14277），取回應的 ranking_id 當 board_id
                   印出來。配 --key <trackKey__carSlug> 可直接寫進 data.json。
  --probe-event <id>   Dump /event/get_parameter 的完整回應 JSON（欄位變了時對欄位）。
  --probe-dgedge <url> Dump dg-edge 事件頁裡的 board_id/活動 id 線索（版面變了時對結構）。

選項：
  --dry        只顯示、不寫入（驗證數字用）
  --push       不碰本機，直接從 GitHub 抓最新 data.json → 更新 → 推回
               （需 GitHub token：GT7_GITHUB_TOKEN / GITHUB_TOKEN 或 --gh-token）
               這條會自動取最新版再改，不會蓋掉 Claude 那邊對 data.json 的編輯。
  --data       本機 data.json 路徑（不加 --push 時用，預設 ./data.json，會自動往上層找）
  --repo/--branch/--gh-path   --push 目標（預設 WYSwolf/GT7 / main / data.json）
  --locale     gran-turismo 語系路徑，預設 tw
  --max-pages  最多翻幾頁找你的名次（每頁 100，預設 60）

收工一條龍：gt7_capture.py 收工後會自動 import 本檔 run(push=True,...)，
  只要環境同時有 GT7_JSESSIONID（或 GT7_GT_TOKEN）+ GitHub token 就會一起更新名次。
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime

try:
    import requests
except ImportError:
    sys.exit("請先安裝 requests：pip install requests")

WEB_API = "https://web-api.gt7.game.gran-turismo.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36")


def jsessionid_from_browser(browser="auto"):
    """自動從本機瀏覽器讀 gran-turismo.com 的 JSESSIONID（免手動複製，等於自動續期）。
    只要瀏覽器還登入著 gran-turismo.com，就抓得到當前有效的 cookie。
    browser: auto / chrome / edge / firefox / brave / chromium / opera / vivaldi。"""
    try:
        import browser_cookie3 as bc3
    except ImportError:
        raise RuntimeError("自動讀瀏覽器 cookie 需要 browser-cookie3：pip install browser-cookie3")
    dom = "gran-turismo.com"
    try:
        if browser and browser != "auto":
            fn = getattr(bc3, browser, None)
            if fn is None:
                raise RuntimeError(f"不支援的瀏覽器 {browser}（可用 auto/chrome/edge/firefox/brave/...）。")
            cj = fn(domain_name=dom)
        else:
            cj = bc3.load(domain_name=dom)   # 掃所有已安裝瀏覽器
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"讀瀏覽器 cookie 失敗（{browser}）：{e}")
    # 取最新的 JSESSIONID（cookie jar 可能有多筆，挑 gran-turismo 網域的）
    cand = [c for c in cj if c.name == "JSESSIONID" and "gran-turismo" in (c.domain or "")]
    if not cand:
        raise RuntimeError("瀏覽器裡找不到 gran-turismo.com 的 JSESSIONID —— "
                           "先用該瀏覽器登入 https://www.gran-turismo.com 再跑。")
    # 有 expires 的挑最大（最新）；否則取第一筆
    cand.sort(key=lambda c: c.expires or 0, reverse=True)
    return cand[0].value


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


# ---------------- 由活動 ID 自動解出 board_id ----------------
# 活動詳情打 web-api 的 event 模組：POST /event/get_parameter {"event_id": <id>}
# （由官方前端 thunk Ze.getParameter({event_id}) 解出）。回應 result.event 內含
# online.ranking_id 與 registration_key。需要 Bearer（與排行榜同一把）。
EVENT_PARAM_API = f"{WEB_API}/event/get_parameter"


def _find_key(obj, key):
    """遞迴找巢狀 dict/list 裡第一個出現的 key 值（找不到回 None）。"""
    if isinstance(obj, dict):
        if key in obj and obj[key] not in (None, ""):
            return obj[key]
        for v in obj.values():
            r = _find_key(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_key(v, key)
            if r is not None:
                return r
    return None


def fetch_event_parameter(token: str, event_id) -> dict:
    """POST /event/get_parameter，回傳 result（活動詳情 dict）。"""
    h = _headers(token); h["Content-Type"] = "application/json"
    eid = int(event_id) if str(event_id).isdigit() else event_id
    r = requests.post(EVENT_PARAM_API, headers=h,
                      data=json.dumps({"event_id": eid}), timeout=15)
    r.raise_for_status()
    j = r.json()
    return j.get("result", j)


def resolve_board_id(token, event_id, verbose=True):
    """由活動 ID 解出 board_id（= ranking_id；分區賽事另需區碼後綴）。回傳 (board_id, result)。"""
    result = fetch_event_parameter(token, event_id)
    ranking_id = _find_key(result, "ranking_id")
    if not ranking_id:
        raise RuntimeError(f"活動 {event_id} 詳情裡找不到 ranking_id —— 用 --probe-event 看回應結構。")
    reg = _find_key(result, "registration_key")
    if reg and verbose:
        print(f"  ⚠ registration_key={reg!r}（分區報名賽事）—— board_id 可能要接區碼後綴，"
              "見檔頭區域規則；個人 TT 用不到。")
    if verbose:
        print(f"  · event {event_id} → ranking_id = {ranking_id}（board_id 直接用它）")
    return str(ranking_id), result


def _nuxt_unflatten(values):
    """把 Nuxt/devalue 扁平化陣列還原成正常巢狀結構（物件/陣列的數字=指向陣列索引）。"""
    n = len(values)
    hydrated = [None] * n
    done = [False] * n
    SPECIAL = {-1: None, -2: float("nan"), -3: float("inf"), -4: float("-inf"), -5: -0.0}
    WRAP = {"Reactive", "Ref", "ShallowRef", "ShallowReactive", "EmptyRef", "EmptyShallowRef"}

    def hyd(i):
        if not isinstance(i, int) or isinstance(i, bool):
            return i
        if i < 0:
            return SPECIAL.get(i)
        if i >= n:
            return None
        if done[i]:
            return hydrated[i]
        done[i] = True
        v = values[i]
        if isinstance(v, list):
            if v and isinstance(v[0], str):
                tag = v[0]
                if tag in WRAP or tag == "Date":
                    hydrated[i] = hyd(v[1]) if len(v) > 1 else None
                elif tag == "Set":
                    hydrated[i] = [hyd(x) for x in v[1:]]
                elif tag == "Map":
                    it = v[1:]; hydrated[i] = {str(hyd(it[k])): hyd(it[k + 1]) for k in range(0, len(it) - 1, 2)}
                else:
                    hydrated[i] = hyd(v[1]) if len(v) == 2 else [hyd(x) for x in v[1:]]
            else:
                arr = []; hydrated[i] = arr
                for x in v:
                    arr.append(hyd(x))
        elif isinstance(v, dict):
            obj = {}; hydrated[i] = obj
            for k, idx in v.items():
                obj[k] = hyd(idx)
        else:
            hydrated[i] = v
        return hydrated[i]

    return hyd(0)


def _walk_collect(o, want_keys, out, depth=0):
    """蒐集所有「鍵含結果欄位」的 dict（比賽成績通常長這樣）。"""
    if depth > 14 or len(out) > 60:
        return
    if isinstance(o, dict):
        if any(k in o for k in want_keys):
            out.append(o)
        for v in o.values():
            _walk_collect(v, want_keys, out, depth + 1)
    elif isinstance(o, list):
        for v in o:
            _walk_collect(v, want_keys, out, depth + 1)


def probe_dgedge_page(event_url):
    """Debug：抓 dg-edge 事件頁，dump 出可能藏 GT board_id / 活動ID 的線索。
    （dg-edge 擋機房 IP，須在自家網路跑。）找它有沒有 p_rt_ / ranking_id /
    連到 gran-turismo sportmode/event/<id> / 或自家 JSON API。"""
    r = requests.get(event_url, headers={"User-Agent": UA, "Accept": "text/html,*/*"}, timeout=20)
    print(f"\n=== dg-edge {event_url} （HTTP {r.status_code}, len={len(r.text)}）===")
    t = r.text
    pats = {
        "p_rt_ (board/ranking id)": r"p_[a-z]{1,3}_\d+_\d+",
        "dg-edge 事件連結 /events/.../id": r"/events/[a-z\-]+/\d+",
        "ranking_id": r"ranking_id",
        "board_id": r"board_id",
        "gran-turismo sportmode/event": r"sportmode/event/(\d+)",
        "選手/racer 連結": r"/(?:racer|racers|player|players|profile)/[A-Za-z0-9_\-]+",
        "圈速 1:23.456": r"\b\d:\d{2}\.\d{3}\b",
        "dg-edge API 路徑": r"/api/[A-Za-z0-9_/\-]+",
    }
    for label, pat in pats.items():
        seen = set()
        for m in re.finditer(pat, t, re.I):
            s = t[max(0, m.start() - 50):m.end() + 50].replace("\n", " ")
            if s in seen:
                continue
            seen.add(s)
            print(f"  [{label}] …{s}…")
            if len(seen) >= 3:
                break

    # 內嵌資料線索：事件列以 ...,"TT",... / isEnded / competition 標記；dump 寬一點看整列
    print("  --- 內嵌資料標記（事件列長相）---")
    for kw in ('"TT"', '"isEnded"', '"competition"', '"course"', '"car"'):
        ms = list(re.finditer(re.escape(kw), t))
        if ms:
            i = ms[0].start()
            print(f"  [{kw} x{len(ms)}] …{t[max(0,i-140):i+60]}…".replace('\n', ' '))

    # 對外 API / 資料來源（前端 fetch 的對象）
    print("  --- 可能的資料來源 ---")
    urls = set(re.findall(r'(?:fetch\(|axios|https?://)[^\s"\'<>]*?(?:/api/|graphql|/v\d/|/players/|/events/)[^\s"\'<>]*', t, re.I))
    for u in list(urls)[:10]:
        print(f"    {u[:140]}")
    for marker in ('__NUXT__', '__next_f', 'window.__', 'application/json', 'apollo', 'buildId'):
        if marker in t:
            j = t.find(marker)
            print(f"    [{marker}] …{t[j:j+120]}…".replace('\n', ' '))
    for s in re.findall(r'<script[^>]*\bsrc=["\']([^"\']+)["\']', t)[:6]:
        print(f"    <script src> {s}")

    # Nuxt SSR：資料其實內嵌在 <script id="__NUXT_DATA__"> 的 JSON 陣列裡（扁平化、用索引互參）。
    nm = re.search(r'id="__NUXT_DATA__"[^>]*>(.*?)</script>', t, re.S)
    if nm:
        print("  --- __NUXT_DATA__ 內的賽事線索 ---")
        try:
            arr = json.loads(nm.group(1))
        except Exception as e:
            print(f"    （解析 __NUXT_DATA__ 失敗：{e}）")
            return t
        strs = [x for x in arr if isinstance(x, str)]
        print(f"    陣列元素 {len(arr)} 個、字串 {len(strs)} 個")
        cats = {
            "事件 slug/連結": re.compile(r'(?:time-trials|dailies|/events/|/event/)', re.I),
            "賽事 slug-結尾id": re.compile(r'^[a-z0-9][a-z0-9\-]+-\d+$', re.I),
            "圈速 1:23.456": re.compile(r'^\d:\d{2}\.\d{3}$'),
            "board/ranking id": re.compile(r'p_[a-z]{1,4}_\d+_\d+'),
        }
        for label, rgx in cats.items():
            hits = sorted({s for s in strs if rgx.search(s)})
            if hits:
                print(f"    [{label}] {len(hits)} 筆：", hits[:12])
        # 還原 Nuxt 巢狀結構，撈出「成績列」dict（含 time/rank/event/track 等鍵）
        try:
            root = _nuxt_unflatten(arr)
            want = {"time", "lap_time", "best_time", "best_lap_time", "score", "rank",
                    "position", "event", "event_id", "eventId", "event_slug", "slug",
                    "ranking_id", "board_id", "track", "car", "course", "gap", "result"}
            found = []
            _walk_collect(root, want, found)
            print(f"    --- 還原後的成績列（{len(found)} 筆，dump 前 12）---")
            for rec in found[:12]:
                small = {k: v for k, v in rec.items() if not isinstance(v, (dict, list))}
                print("     ", json.dumps(small, ensure_ascii=False)[:280])
        except Exception as e:
            print(f"    （Nuxt 還原失敗：{e}）字串樣本：", strs[:40])
    print("  若仍看不出 schema：DevTools→Network 重整此頁，找回應含你成績/賽事清單的請求，貼 URL+回應。")
    return t


def probe_event_page(token, event_id):
    """Debug：dump /event/get_parameter 回應，幫忙定位 ranking_id。"""
    result = fetch_event_parameter(token, event_id)
    print(f"\n=== 活動 {event_id} 詳情（POST /event/get_parameter）===")
    print(json.dumps(result, ensure_ascii=False, indent=2)[:4000])
    rid = _find_key(result, "ranking_id")
    print(f"\n[自動偵測] ranking_id = {rid or '（找不到 —— 把上面 JSON 貼回來）'}")
    return result


def disp(sec):
    if sec is None:
        return "—"
    m = int(sec // 60)
    return f"{m}:{sec - m * 60:06.3f}"


DGEDGE_BOARD_RE = re.compile(r'"(p_[a-z]{1,4}_\d+_\d+)"')


def fetch_dgedge(event_url: str) -> dict:
    """抓一次 dg-edge 事件頁，回傳 {total, board_id}（抓不到的欄位為 None）。
    · total：母體人數（官方 API 抓不到），版面 <span>Total players</span><b>150118</b>。
    · board_id：頁面內嵌資料含 "<dgId>",<num>,"p_rt_..._...","TT" —— 用 dg-edge 事件 id
      錨定取它後面那個 board_id（取不到再退求頁面第一個 p_rt_ 字串）。"""
    out = {"total": None, "board_id": None}
    try:
        r = requests.get(event_url, headers={"User-Agent": UA, "Accept": "text/html"}, timeout=20)
        r.raise_for_status()
    except Exception as e:
        print(f"  · dg-edge 抓取失敗（沿用既有值）：{e}")
        return out
    t = r.text
    m = re.search(r"Total\s*players\s*</span>\s*<b[^>]*>\s*([\d,]+)", t, re.I)
    if m:
        out["total"] = int(m.group(1).replace(",", ""))
    else:
        print("  · dg-edge 頁面找不到 Total players（版面可能改了，沿用既有 totalPlayers）。")
    dg_id = re.search(r"/(\d+)/?$", event_url)
    if dg_id:
        am = re.search(r'"%s"\s*,\s*\d+\s*,\s*"(p_[a-z]{1,4}_\d+_\d+)"' % re.escape(dg_id.group(1)), t)
        if am:
            out["board_id"] = am.group(1)
    if not out["board_id"]:
        fm = DGEDGE_BOARD_RE.search(t)
        if fm:
            out["board_id"] = fm.group(1)
    return out


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


def resolve_token(bearer=None, jsessionid=None, locale="tw", browser=None, verbose=True):
    """回傳 (token, my_id)。優先序：bearer → jsessionid → 從瀏覽器自動讀 JSESSIONID（browser）。"""
    token, my_id = bearer, None
    if not token:
        if not jsessionid and browser:
            if verbose: print(f"🍪 從瀏覽器自動讀 JSESSIONID（{browser}）…")
            jsessionid = jsessionid_from_browser(browser)
            if verbose: print("  ✓ 已從瀏覽器取得 JSESSIONID")
        if not jsessionid:
            raise RuntimeError("請給 --bearer / --jsessionid，或用 --browser 自動讀瀏覽器 cookie。用法見檔頭。")
        if verbose: print("🔑 用 JSESSIONID 換 Bearer…")
        info = get_token_info(jsessionid, locale)
        token = info["access_token"]
        my_id = info.get("user_id")
        if verbose: print(f"  ✓ 取得 Bearer（user_id={my_id}）")
    if not my_id:
        if verbose: print("👤 取得自己的 user_id…")
        my_id = get_my_user_id(token)
        if verbose: print(f"  ✓ user_id = {my_id}")
    return token, my_id


def apply_updates(d: dict, token: str, my_id: str, today: str, max_pages=60, verbose=True):
    """就地更新 data.json 的 leaderboards / references / goals。回傳是否有任何榜更新成功。"""
    lbs = d.get("meta", {}).get("leaderboards", {})
    if not lbs:
        raise RuntimeError("data.json 沒有 meta.leaderboards；先填好（至少要有 eventUrl 或 boardId）再跑。")
    changed = False
    for key, entry in lbs.items():
        # 母體人數 + board_id 都從 dg-edge 事件頁一次抓（boardId 缺就自動補）。
        # 官方 API 的 total 只是「榜上清單大小」（cap），非參賽總數，母體一律用 dg-edge。
        ev = entry.get("eventUrl", "")
        dg = fetch_dgedge(ev) if "dg-edge.com" in ev else {"total": None, "board_id": None}
        board_id = entry.get("boardId") or dg["board_id"]
        if not board_id:
            print(f"  · {key}：沒有 boardId、也無法從 dg-edge 解出（檢查 eventUrl），跳過。")
            continue
        if not entry.get("boardId") and dg["board_id"]:
            entry["boardId"] = board_id
            if verbose: print(f"  · {key} 自動補 boardId={board_id}（取自 dg-edge）")
        if verbose: print(f"📊 {key}  (board_id={board_id})…")
        try:
            r = fetch_board(token, board_id, my_id, max_pages)
        except Exception as e:
            print(f"  ✗ 失敗：{e}")
            continue
        if dg["total"]:
            entry["totalPlayers"] = dg["total"]
            if verbose: print(f"  · dg-edge 母體：{dg['total']} 人")
        population = entry.get("totalPlayers")
        pct = round(r["rank"] / population * 100, 2) if r["rank"] and population else None
        if verbose:
            print(f"  WR={disp(r['wr'])}  top100={disp(r['top100'])}  top1000={disp(r['top1000'])}  "
                  f"你的名次=#{r['rank']}"
                  + (f" (前 {pct}% / {population} 人)" if pct else "")
                  + f"  PB={disp(r['pb'])}  [API 榜清單大小={r['total']}]")

        if r["total"]:   entry["boardSize"] = r["total"]   # 參考用，非母體
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
        changed = changed or bool(r["wr"] or r["rank"])
    d["meta"]["lastUpdated"] = today
    return changed


# ---------------- 本機 / GitHub 讀寫 ----------------
def locate_data(path="data.json"):
    """自動定位 data.json（腳本可能放在 telemetry/ 子資料夾跑）。"""
    if os.path.exists(path):
        return path
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(os.getcwd(), "..", "data.json"),
                 os.path.join(here, "data.json"),
                 os.path.join(here, "..", "data.json")):
        if os.path.exists(cand):
            return cand
    raise FileNotFoundError(f"找不到 data.json（試過 {path} 與上層）；用 --data 指定路徑。")


def _gh_headers(gh_token):
    return {"Authorization": f"token {gh_token}", "Accept": "application/vnd.github+json",
            "User-Agent": "gt7_rank", "X-GitHub-Api-Version": "2022-11-28"}


def gh_get(repo, path, branch, gh_token):
    """回傳 (data_dict, sha)。"""
    import base64
    api = f"https://api.github.com/repos/{repo}/contents/{path}"
    r = requests.get(api, params={"ref": branch}, headers=_gh_headers(gh_token), timeout=30)
    r.raise_for_status()
    j = r.json()
    raw = base64.b64decode(j["content"]).decode("utf-8")
    return json.loads(raw), j["sha"]


def gh_put(repo, path, branch, gh_token, d, sha, message):
    import base64
    api = f"https://api.github.com/repos/{repo}/contents/{path}"
    raw = json.dumps(d, ensure_ascii=False, indent=1).encode("utf-8")
    body = {"message": message, "branch": branch, "sha": sha,
            "content": base64.b64encode(raw).decode("ascii")}
    r = requests.put(api, headers={**_gh_headers(gh_token), "Content-Type": "application/json"},
                     data=json.dumps(body), timeout=60)
    r.raise_for_status()
    return r.json().get("content", {}).get("html_url", "")


def run(*, bearer=None, jsessionid=None, browser=None, locale="tw", max_pages=60,
        push=False, repo="WYSwolf/GT7", branch="main", gh_path="data.json",
        gh_token=None, data_path="data.json", dry=False, verbose=True,
        event=None, key=None, probe_event=None, probe_dgedge=None):
    """抓榜並更新 data.json。push=True → 從 GitHub 抓最新、改、推回（不碰本機、不會蓋掉他人改動）；
    否則讀寫本機 data.json。dry=True 只顯示不寫。browser → 自動從瀏覽器讀 JSESSIONID。
    probe_event=<id> → 只 dump 活動詳情原始 JSON。
    event=<id> → 自動解出 board_id 印出；配 key=<trackKey__carSlug> 寫進 data.json。"""
    today = datetime.now().strftime("%Y-%m-%d")   # 用本機日期（=玩家當天）

    # --- 找新賽道 board_id 的輔助流程 ---
    if probe_dgedge is not None:   # 看 dg-edge 頁面藏了什麼（不需認證）
        return probe_dgedge_page(probe_dgedge)
    if probe_event is not None:
        token, _ = resolve_token(bearer, jsessionid, locale, browser, verbose)
        return probe_event_page(token, probe_event)

    if event is not None:
        token, _ = resolve_token(bearer, jsessionid, locale, browser, verbose)
        board_id, _ = resolve_board_id(token, event, verbose)
        if not key:
            print(f"\n✓ board_id = {board_id}")
            print(f"  把它填進 data.json → meta.leaderboards.<trackKey__carSlug>.boardId；"
                  "或重跑時加 --key <key> 自動寫入。")
            return board_id
        # 寫進 data.json（本機 / --push），遵守 --dry
        sha = None
        if push:
            if not gh_token:
                raise RuntimeError("--push 需要 GitHub token（GT7_GITHUB_TOKEN / GITHUB_TOKEN，或 --gh-token）。")
            d, sha = gh_get(repo, gh_path, branch, gh_token)
        else:
            data_path = locate_data(data_path)
            with open(data_path, encoding="utf-8") as f:
                d = json.load(f)
        entry = d.setdefault("meta", {}).setdefault("leaderboards", {}).setdefault(key, {})
        old = entry.get("boardId")
        entry["boardId"] = board_id
        print(f"\n  meta.leaderboards.{key}.boardId: {old!r} → {board_id!r}")
        if dry:
            print("[DRY] 未寫入。確認 OK 後拿掉 --dry 再跑。")
        elif push:
            url = gh_put(repo, gh_path, branch, gh_token, d, sha,
                         f"rank: set boardId for {key} (event {event})")
            print(f"✓ 已推回 GitHub {repo}/{gh_path}@{branch}" + (f"\n   {url}" if url else ""))
        else:
            with open(data_path, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=1)
            print(f"✓ 已寫入 {os.path.abspath(data_path)}")
        return board_id

    token, my_id = resolve_token(bearer, jsessionid, locale, browser, verbose)

    sha = None
    if push:
        if not gh_token:
            raise RuntimeError("--push 需要 GitHub token（設 GT7_GITHUB_TOKEN / GITHUB_TOKEN，或 --gh-token）。")
        if verbose: print(f"☁ 從 GitHub 取最新 {repo}/{gh_path}@{branch}…")
        d, sha = gh_get(repo, gh_path, branch, gh_token)
    else:
        data_path = locate_data(data_path)
        if verbose: print(f"📄 data.json = {os.path.abspath(data_path)}")
        with open(data_path, encoding="utf-8") as f:
            d = json.load(f)

    apply_updates(d, token, my_id, today, max_pages, verbose)

    if dry:
        if verbose: print("\n[DRY] 未寫入。確認數字 OK 後拿掉 --dry 再跑。")
    elif push:
        url = gh_put(repo, gh_path, branch, gh_token, d, sha,
                     f"rank: auto update leaderboards/WR ({today})")
        if verbose: print(f"\n✓ 已推回 GitHub {repo}/{gh_path}@{branch}" + (f"\n   {url}" if url else ""))
    else:
        with open(data_path, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        if verbose: print(f"\n✓ 已更新 {os.path.abspath(data_path)}")
    return d


def main():
    ap = argparse.ArgumentParser(description="GT7 Rank Fetcher (web-api)")
    ap.add_argument("--bearer", default=os.environ.get("GT7_GT_TOKEN"), help="web-api Bearer token（或設 GT7_GT_TOKEN）")
    ap.add_argument("--jsessionid", default=os.environ.get("GT7_JSESSIONID"), help="gran-turismo.com JSESSIONID（或設 GT7_JSESSIONID）")
    ap.add_argument("--browser", nargs="?", const="auto", default=os.environ.get("GT7_BROWSER"),
                    help="自動從瀏覽器讀 JSESSIONID（免手動複製＝自動續期）。不帶值=auto；"
                         "可指定 chrome/edge/firefox/brave/...（或設 GT7_BROWSER）")
    ap.add_argument("--locale", default="tw")
    ap.add_argument("--event", help="由 sportmode 活動 ID 自動解出 board_id（配 --key 可寫進 data.json）")
    ap.add_argument("--key", help="--event 寫入目標：meta.leaderboards.<trackKey__carSlug>")
    ap.add_argument("--probe-event", dest="probe_event",
                    help="只 dump 活動詳情原始 JSON（debug 用，找不到 ranking_id 時貼回來）")
    ap.add_argument("--probe-dgedge", dest="probe_dgedge",
                    help="抓 dg-edge 事件頁 URL，dump 出可能藏 GT board_id/活動ID 的線索（debug）")
    ap.add_argument("--data", default="data.json", help="本機 data.json 路徑（不加 --push 時用）")
    ap.add_argument("--dry", action="store_true", help="只顯示、不寫入")
    ap.add_argument("--max-pages", type=int, default=60)
    ap.add_argument("--push", action="store_true",
                    help="直接從 GitHub 抓最新 data.json、更新、推回（用 GitHub token；不碰本機檔）")
    ap.add_argument("--repo", default="WYSwolf/GT7", help="--push 目標 repo（owner/name）")
    ap.add_argument("--branch", default="main", help="--push 目標分支")
    ap.add_argument("--gh-path", default="data.json", help="data.json 在 repo 內的路徑")
    ap.add_argument("--gh-token", default=os.environ.get("GT7_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN"),
                    help="GitHub token（或設 GT7_GITHUB_TOKEN / GITHUB_TOKEN）")
    args = ap.parse_args()
    try:
        run(bearer=args.bearer, jsessionid=args.jsessionid, browser=args.browser, locale=args.locale,
            max_pages=args.max_pages, push=args.push, repo=args.repo, branch=args.branch,
            gh_path=args.gh_path, gh_token=args.gh_token, data_path=args.data, dry=args.dry,
            event=args.event, key=args.key, probe_event=args.probe_event,
            probe_dgedge=args.probe_dgedge)
    except (RuntimeError, FileNotFoundError) as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
