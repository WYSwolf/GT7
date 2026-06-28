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
  （目前 redbullring__fordgt17 = p_rt_1014277_001）

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


def disp(sec):
    if sec is None:
        return "—"
    m = int(sec // 60)
    return f"{m}:{sec - m * 60:06.3f}"


def fetch_dgedge_total(event_url: str):
    """從 dg-edge 事件頁抓「Total players」真正母體人數（官方 API 抓不到）。失敗回 None。"""
    try:
        r = requests.get(event_url, headers={"User-Agent": UA, "Accept": "text/html"}, timeout=15)
        r.raise_for_status()
    except Exception as e:
        print(f"  · dg-edge 母體抓取失敗（沿用既有 totalPlayers）：{e}")
        return None
    # 頁面 server-render：<span>Total players</span> <b>150118</b>
    m = re.search(r"Total\s*players\s*</span>\s*<b[^>]*>\s*([\d,]+)", r.text, re.I)
    if not m:
        print("  · dg-edge 頁面找不到 Total players（版面可能改了，沿用既有 totalPlayers）。")
        return None
    return int(m.group(1).replace(",", ""))


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
    boards = {k: v["boardId"] for k, v in d.get("meta", {}).get("leaderboards", {}).items() if v.get("boardId")}
    if not boards:
        raise RuntimeError("data.json 沒有任何 meta.leaderboards.<key>.boardId；先填好再跑。")
    changed = False
    for key, board_id in boards.items():
        if verbose: print(f"📊 {key}  (board_id={board_id})…")
        try:
            r = fetch_board(token, board_id, my_id, max_pages)
        except Exception as e:
            print(f"  ✗ 失敗：{e}")
            continue
        entry = d["meta"]["leaderboards"][key]
        # 母體人數：官方 API 的 total 只是「榜上回傳的清單大小」（cap），不是參賽總數。
        # 真正母體去 dg-edge 事件頁抓 Total players；抓不到就沿用既有 totalPlayers。
        ev = entry.get("eventUrl", "")
        if "dg-edge.com" in ev:
            dg_total = fetch_dgedge_total(ev)
            if dg_total:
                entry["totalPlayers"] = dg_total
                if verbose: print(f"  · dg-edge 母體：{dg_total} 人")
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
        gh_token=None, data_path="data.json", dry=False, verbose=True):
    """抓榜並更新 data.json。push=True → 從 GitHub 抓最新、改、推回（不碰本機、不會蓋掉他人改動）；
    否則讀寫本機 data.json。dry=True 只顯示不寫。browser → 自動從瀏覽器讀 JSESSIONID。"""
    today = datetime.now().strftime("%Y-%m-%d")   # 用本機日期（=玩家當天）
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
            gh_path=args.gh_path, gh_token=args.gh_token, data_path=args.data, dry=args.dry)
    except (RuntimeError, FileNotFoundError) as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
