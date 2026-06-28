#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GT7 Telemetry Capture  ·  WYS / GT7 Training Tracker   (v3 · CSV 一天一檔)
====================================================================
擷取 Gran Turismo 7 的即時遙測,**邊跑邊寫成 CSV**(一天一檔),
可直接丟進 telemetry.html / overview.html 分析走線。整天不關、跨場次都行。

逐筆欄位(CSV 每列一筆取樣):
    run_lap   全程連號的圈(跨場次也不重置,用來切 stint)
    inlap     遊戲內圈號(換場次會重置)
    carcode   車輛碼(換車會變→切 stint / 對車)
    lap_time  該圈精準總時間(秒)
    wall      該圈起始時刻(距開始秒數,偵測場次間空檔用)
    t         本圈經過秒數
    spd       速度 km/h
    thr/brk   油門 / 煞車 %
    rpm/gear  轉速 / 檔位
    x, z      賽道座標
    yaw       偏航率 rad/s            ← 基本封包就有
    latG/lonG 橫向 / 縱向 G            ← 大封包(sway/surge)
    steer     方向盤轉角(度)         ← 大封包(0x128)
    tFL/tFR/tRL/tRR  四輪胎溫 °C       ← 基本封包(0x60)
大封包沒到時自動降級:latG/lonG/steer 留空,其餘照寫,不會抓到錯資料。

需求:Python 3.8+,純標準庫,免裝任何套件。
裝置:跟 PS5 同一個區網,遊戲開著(任何有 HUD 的畫面都會送遙測)。

用法(Windows PowerShell / cmd):
    python gt7_capture.py <PS5的IP>

兩種日常用法:
    ① 訓練模式(自己開車,開即時儀表板):
        python gt7_capture.py 192.168.88.109 --live --track "Laguna Seca" --car "911 GT3 R"
    ② 抓世界排名(播 WR ghost 重播存檔):
        python gt7_capture.py 192.168.88.109 --replay --track "Laguna Seca" --car "WR ghost"

選項:
    --live                     同時開本機即時儀表板網站(訓練看 RPM/G/胎溫/側滑)
    --replay                   重播模式:不管在不在跑道都錄,輸出檔自動加 wr_ 前綴
    --track / --car            這次的賽道名 / 車名(寫進檔頭,可省略事後補)
    --hz    20                 逐筆取樣頻率(預設 20Hz;0 = 全收 ~60Hz)
    --port  8080               即時看板連接埠(配 --live)
    --out   .                  輸出資料夾(預設目前目錄)
    --packet C                 心跳封包型別(預設 C=最完整;抓不到可試 A / B / ~)

輸出:gt7_<日期>.csv(同日多次跑會自動加時間後綴;--replay 會多 wr_ 前綴)。
停止:Ctrl+C。每跑完一圈即時 append 寫檔,中途斷了不會整碗丟。

自動上傳:收工後,若偵測到環境變數 GT7_GITHUB_TOKEN(或 GITHUB_TOKEN),
    會把整份原始檔(不篩選)透過 GitHub API 上傳到 repo 的 telemetry/ → main。
    設定一次即可:export GT7_GITHUB_TOKEN=<具 repo 權限的 GitHub token>
    關閉用 --no-push。篩選成 slim CSV / data.json 之後再另外請 Claude 處理。

收工一條龍(名次更新):若同時設了 GT7_JSESSIONID(或 GT7_GT_TOKEN),收工會再 import
    同目錄的 gt7_rank.py,自動抓世界紀錄/門檻/你的名次,推回 GitHub 的 data.json。
    需 pip install requests。關閉用 --no-rank。缺 token / requests 會自動略過,不影響 CSV 上傳。

PS5 IP 怎麼找:PS5 → 設定 → 網路 → 連線狀態 → 查看連線狀態,看 IP 位址。
防火牆:第一次跑若收不到,Windows 防火牆要放行 UDP 連接埠 33740(輸入)/ 33739(輸出)。
"""

import socket, struct, sys, os, json, time, argparse, math, threading, http.server
from datetime import datetime

# ---------------- Salsa20 (純 Python,已用 ECRYPT 測試向量驗證) ----------------
def _rotl(v, c): return ((v << c) | (v >> (32 - c))) & 0xffffffff
def _qr(x, a, b, c, d):
    x[b] ^= _rotl((x[a] + x[d]) & 0xffffffff, 7)
    x[c] ^= _rotl((x[b] + x[a]) & 0xffffffff, 9)
    x[d] ^= _rotl((x[c] + x[b]) & 0xffffffff, 13)
    x[a] ^= _rotl((x[d] + x[c]) & 0xffffffff, 18)
def _core(block):
    x = list(block)
    for _ in range(10):
        _qr(x, 0, 4, 8, 12); _qr(x, 5, 9, 13, 1); _qr(x, 10, 14, 2, 6); _qr(x, 15, 3, 7, 11)
        _qr(x, 0, 1, 2, 3); _qr(x, 5, 6, 7, 4); _qr(x, 10, 11, 8, 9); _qr(x, 15, 12, 13, 14)
    return [(x[i] + block[i]) & 0xffffffff for i in range(16)]
_SIGMA = [0x61707865, 0x3320646e, 0x79622d32, 0x6b206574]
def _ks_block(key, nonce, counter):
    k = struct.unpack('<8I', key); n = struct.unpack('<2I', nonce)
    c = struct.unpack('<2I', struct.pack('<Q', counter))
    s = [_SIGMA[0], k[0], k[1], k[2], k[3], _SIGMA[1], n[0], n[1],
         c[0], c[1], _SIGMA[2], k[4], k[5], k[6], k[7], _SIGMA[3]]
    return struct.pack('<16I', *_core(s))
def salsa20_xor(msg, nonce, key):
    res = bytearray(len(msg)); ctr = 0; i = 0
    while i < len(msg):
        ks = _ks_block(key, nonce, ctr); ctr += 1
        for j in range(min(64, len(msg) - i)):
            res[i + j] = msg[i + j] ^ ks[j]
        i += 64
    return bytes(res)

# ---------------- GT7 packet ----------------
KEY = b'Simulator Interface Packet GT7 ver 0.0'[:32]
SEND_PORT = 33739      # 送心跳給 PS5 的埠
RECV_PORT = 33740      # PS5 回傳遙測的埠


def _local_ip():
    """取得本機在區網的 IP(用來推算掃描網段)。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80)); ip = s.getsockname()[0]
    except OSError:
        ip = '127.0.0.1'
    finally:
        s.close()
    return ip


def _cache_path():
    """記住上次成功的 PS5 IP,放使用者家目錄。"""
    return os.path.join(os.path.expanduser('~'), '.gt7_capture_ps5')


def _load_cached_ip():
    try:
        ip = open(_cache_path(), encoding='utf-8').read().strip()
        return ip or None
    except OSError:
        return None


def _save_ip(ip):
    if not ip or ip.startswith('127.'):
        return
    try:
        open(_cache_path(), 'w', encoding='utf-8').write(ip)
    except OSError:
        pass


def _probe(heartbeat, ip, timeout=1.6):
    """對單一 IP 送心跳,收到遙測回應(>100 bytes)就算在線。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(('0.0.0.0', RECV_PORT))
    except OSError:
        s.close(); return False
    s.settimeout(0.4)
    end = time.time() + timeout
    ok = False
    try:
        s.sendto(heartbeat, (ip, SEND_PORT))
        while time.time() < end:
            try:
                data, addr = s.recvfrom(2048)
            except socket.timeout:
                try: s.sendto(heartbeat, (ip, SEND_PORT))
                except OSError: pass
                continue
            except OSError:
                break
            if addr[0] == ip and data and len(data) > 100:
                ok = True; break
    finally:
        s.close()
    return ok


def _max_run_lap(path):
    """讀現有 CSV 的最大 run_lap,接續寫入時用(同一天再跑不重號)。"""
    mx = 0
    try:
        with open(path, encoding='utf-8') as f:
            for line in f:
                if not line or line[0] == '#' or line.startswith('run_lap'):
                    continue
                head = line.split(',', 1)[0]
                if head.isdigit():
                    mx = max(mx, int(head))
    except OSError:
        pass
    return mx


def _prevent_sleep():
    """執行期間阻止電腦/螢幕休眠。回傳 token 供結束還原。"""
    try:
        if sys.platform == 'win32':
            import ctypes
            # ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x1 | 0x2)
            return 'win'
        if sys.platform == 'darwin':
            import subprocess
            return subprocess.Popen(['caffeinate', '-dimsu'])
    except Exception:
        pass
    return None


def _keep_awake(token):
    if token == 'win':
        try:
            import ctypes
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x1 | 0x2)
        except Exception:
            pass


def _restore_sleep(token):
    try:
        if token == 'win':
            import ctypes
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)  # 只留 ES_CONTINUOUS = 還原
        elif token is not None and hasattr(token, 'terminate'):
            token.terminate()
    except Exception:
        pass


def detect_ps5(heartbeat, timeout=4.0):
    """沒給 IP 時,對同網段 /24 每台主機送心跳,聽 33740 第一個回應就是 PS5。
    需 GT7 正在前景執行。找到回傳 IP,否則回傳 None。"""
    cached = _load_cached_ip()
    if cached:
        print(f'🔍 先試上次的 PS5 IP {cached} …(請先讓 GT7 在前景)')
        if _probe(heartbeat, cached):
            print('  ✓ 仍在線')
            return cached
        print('  上次 IP 沒回應,改掃描整個網段…')
    ip = _local_ip()
    base = ip.rsplit('.', 1)[0]
    private = ip.startswith('192.168.') or ip.startswith('10.') or ip.startswith('172.')
    print(f'🔍 未指定 IP,自動偵測 PS5(本機 {ip},掃描 {base}.1–254)… 請先讓 GT7 在前景執行')
    if not private:
        print(f'  ⚠ 本機 IP {ip} 不是區網位址(應為 192.168 / 10 / 172 開頭)。'
              'PS5 可能沒接在同一台路由器下,偵測大概會失敗。')
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(('0.0.0.0', RECV_PORT))
    except OSError:
        s.close(); return None
    s.settimeout(0.4)

    def sweep():
        for i in range(1, 255):
            cand = f'{base}.{i}'
            if cand == ip:
                continue
            try: s.sendto(heartbeat, (cand, SEND_PORT))
            except OSError: pass

    sweep()
    end = time.time() + timeout
    found = None
    while time.time() < end:
        try:
            data, addr = s.recvfrom(2048)
        except socket.timeout:
            sweep()             # 沒回應就再掃一輪(有些要多敲幾下)
            continue
        except OSError:
            break
        if data and len(data) > 100:   # GT7 遙測封包遠大於 100 bytes
            found = addr[0]; break
    s.close()
    if found:
        _save_ip(found)
    return found
MAGIC = 0x47375330     # 'G7S0'
G = 9.80665            # m/s^2 → G

# 大封包預期長度(用來判斷某欄位在不在):A=296 B=316 ~=344 C=368
LEN_A, LEN_B, LEN_T, LEN_C = 0x128, 0x13C, 0x158, 0x170
# IV XOR 常數會隨封包版本不同;自動試解,用 magic 驗證後鎖定
IV_CONSTS = (0xDEADBEAF, 0xDEADBEEF, 0x55FABB4F)
_iv_const = [None]     # 找到後快取,避免每包都試三次

def _try_decrypt(dat, const):
    iv1 = struct.unpack_from('<I', dat, 0x40)[0]
    iv2 = iv1 ^ const
    nonce = struct.pack('<I', iv2) + struct.pack('<I', iv1)
    d = salsa20_xor(dat, nonce, KEY)
    return d if struct.unpack_from('<I', d, 0)[0] == MAGIC else None

def decrypt(dat):
    """回傳解密後的 bytes,失敗回 None。第一次會自動找出正確的 IV 常數並鎖定。"""
    if len(dat) < 0x44:
        return None
    if _iv_const[0] is not None:
        d = _try_decrypt(dat, _iv_const[0])
        if d is not None:
            return d
        _iv_const[0] = None            # 鎖定的常數失效,重新偵測
    for const in IV_CONSTS:
        d = _try_decrypt(dat, const)
        if d is not None:
            _iv_const[0] = const
            return d
    return None

def _f(d, o):  return struct.unpack_from('<f', d, o)[0]
def _i(d, o):  return struct.unpack_from('<i', d, o)[0]
def _h(d, o):  return struct.unpack_from('<h', d, o)[0]

def parse(d):
    n = len(d)
    bits = d[0x90]
    p = {
        # --- 基本封包 A(偏移已逐欄對齊官方結構,確認無誤)---
        'x':   _f(d, 0x04), 'y': _f(d, 0x08), 'z': _f(d, 0x0C),
        'vx':  _f(d, 0x10), 'vz': _f(d, 0x18),     # 世界速度向量(m/s)→ 甩尾角推算
        'yaw': _f(d, 0x30),                        # 角速度 Y(rad/s)= 偏航率
        'rpm': _f(d, 0x3C),
        'fuel': _f(d, 0x44),                       # 目前油量(公升)
        'fuelCap': _f(d, 0x48),                    # 油箱容量(公升)
        'mps': _f(d, 0x4C),                        # 速度 公尺/秒
        'pid': _i(d, 0x70),                        # packet id
        'lap': _h(d, 0x74),                        # 目前圈數(-1=不在跑)
        'best': _i(d, 0x78),                       # 最佳圈 ms
        'last': _i(d, 0x7C),                       # 上一圈 ms
        'flags': _h(d, 0x8E),
        'gear': bits & 0x0F,
        'sugg': bits >> 4,
        'thr': d[0x91],                            # 0-255
        'brk': d[0x92],                            # 0-255
        'hb':  1 if (_h(d, 0x8E) & (1 << 6)) else 0,   # 手煞車旗標(bit6)
        'tcs': 1 if (_h(d, 0x8E) & (1 << 11)) else 0,  # TCS 介入中(bit11)
        'asm': 1 if (_h(d, 0x8E) & (1 << 10)) else 0,  # ASM 介入中(bit10)
        'paused': 1 if (_h(d, 0x8E) & (1 << 1)) else 0, # 遊戲暫停中(bit1)
        'car': _i(d, 0x124),                           # 車輛碼(偵測換車→切 stint)
        'rpmMin': _h(d, 0x88), 'rpmMax': _h(d, 0x8A),  # 轉速燈起點 / 紅線(即時看板用)
        'tFL': _f(d, 0x60), 'tFR': _f(d, 0x64),        # 胎溫 °C(基本封包)
        'tRL': _f(d, 0x68), 'tRR': _f(d, 0x6C),
        # --- 大封包欄位(沒到就是 None,絕不亂讀)---
        'latG': None, 'lonG': None, 'steer': None,
    }
    if n >= LEN_B:                                  # B/~/C 才有 motion 欄位
        p['latG'] = _f(d, 0x130) / G                # 橫向 G(sway)
        p['lonG'] = _f(d, 0x138) / G                # 縱向 G(surge)
        p['steer'] = math.degrees(_f(d, 0x128))     # 方向盤轉角(WheelRotationRadians)→ 度
    return p

# ---------------- 即時看板(--live)----------------
LIVE = {'d': {}}
REF = {'dist': [], 't': [], 'st': [], 'v': []}   # 最佳圈的 距離→時間(+轉向/速度,供段界吸附直線)

def ref_time(dist):
    D = REF['dist']; Tt = REF['t']
    if not D: return None
    if dist <= D[0]: return Tt[0]
    if dist >= D[-1]: return Tt[-1]
    lo, hi = 0, len(D) - 1
    while lo < hi:
        m = (lo + hi) // 2
        if D[m] < dist: lo = m + 1
        else: hi = m
    d0, d1 = D[lo-1], D[lo]; t0, t1 = Tt[lo-1], Tt[lo]
    f = (dist - d0) / (d1 - d0) if d1 > d0 else 0.0
    return t0 + f * (t1 - t0)

def sec_bounds(sec_arg):
    """回傳分段界距離 [d1, d2]。--sec 給百分比就用校正值;否則依參考圈等時間三等分(真實賽道的慣例)。"""
    D = REF['dist']; Tt = REF['t']
    if not D or D[-1] <= 0: return None
    L = D[-1]
    if sec_arg:
        try:
            p1, p2 = [float(x) for x in sec_arg.split(',')]
            return [L * p1 / 100.0, L * p2 / 100.0]
        except Exception:
            pass
    T = Tt[-1]
    ST = REF.get('st') or []; V = REF.get('v') or []
    snap = len(ST) == len(D) and len(V) == len(D) and len(D) > 20
    if snap:                                  # 平滑 |轉向|(5 點),去抖動
        sm = [sum(ST[max(0, i-2):i+3]) / len(ST[max(0, i-2):i+3]) for i in range(len(ST))]
    out = []
    for tgt in (T / 3.0, 2.0 * T / 3.0):
        lo, hi = 0, len(Tt) - 1
        while lo < hi:
            m = (lo + hi) // 2
            if Tt[m] < tgt: lo = m + 1
            else: hi = m
        if not snap:
            out.append(D[lo]); continue
        # 段界吸附直線:±8% 圈長內找「最直、最快」的點(真實賽道把分段線放直線的慣例)
        lo_d, hi_d = D[lo] - 0.08 * L, D[lo] + 0.08 * L
        best_i, best_s = lo, 1e18
        for i in range(len(D)):
            if D[i] < lo_d: continue
            if D[i] > hi_d: break
            score = sm[i] - 0.05 * V[i]       # 轉向角(度)為主、速度(km/h)為輔
            if score < best_s:
                best_s = score; best_i = i
        out.append(D[best_i])
    return out


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80)); ip = s.getsockname()[0]; s.close(); return ip
    except OSError:
        return '127.0.0.1'

LIVE_HTML = r'''<!DOCTYPE html><html lang="zh-Hant"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>GT7 即時儀表板</title>
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=JetBrains+Mono:wght@400;500;700&family=Noto+Sans+TC:wght@500;700&display=swap" rel="stylesheet">
<style>
:root{--bg:#05070a;--panel:#0d1219;--panel2:#0a0e14;--line:#1b232f;--txt:#eef3f9;--muted:#6b7787;--green:#00ff88;--gold:#ffd43b;--red:#ff3b5c;--blue:#36b3ff;--cyan:#33e0ff;--mag:#c86bff;--mono:'JetBrains Mono',monospace;--disp:'Bebas Neue',sans-serif;--body:'Noto Sans TC',sans-serif;}
*{box-sizing:border-box;margin:0;padding:0;-webkit-user-select:none;user-select:none}
html,body{height:100%}
body{background:radial-gradient(1200px 800px at 50% -10%,#0a121b,var(--bg));color:var(--txt);font-family:var(--body);overflow:hidden;display:flex;flex-direction:column;padding:1.4vh 1.2vw;gap:1vh}
.grp-t{font-family:var(--mono);font-size:1.25vh;letter-spacing:.18em;color:var(--muted);text-transform:uppercase;margin-bottom:.6vh}
.card{background:linear-gradient(180deg,var(--panel),var(--panel2));border:1px solid var(--line);border-radius:1.4vh;padding:1.2vh}
.main{flex:1;display:grid;grid-template-columns:0.98fr 1.05fr 1.2fr;gap:1vw;min-height:0}
.col{display:flex;flex-direction:column;min-height:0}
/* 左:車況 */
.car{flex:1.5 1 0;display:grid;grid-template-columns:1fr 1.9fr 1fr;grid-template-rows:1.75fr 0.4fr 1.75fr;gap:1vh 1vw;min-height:0;align-items:stretch}
.ty{position:relative;border-radius:1vh/1.5vh;background:#0c121a;border:.28vh solid var(--muted);display:flex;flex-direction:column;align-items:center;justify-content:center;font-family:var(--mono);transition:.15s}
.ty .pos{position:absolute;top:.6vh;font-size:1.2vh;letter-spacing:.1em;color:var(--muted)}
.ty .deg{font-size:4vh;font-weight:700;color:var(--muted)}
.fl{grid-column:1;grid-row:1}.fr{grid-column:3;grid-row:1}.rl{grid-column:1;grid-row:3}.rr{grid-column:3;grid-row:3}
.tank{grid-column:2;grid-row:1/4;position:relative;border-radius:2vh;overflow:hidden;background:#0a0f16;border:1px solid var(--line)}
.tank .fill{position:absolute;left:0;right:0;bottom:0;height:0;background:linear-gradient(0deg,#1c5,#2ea869 70%,#2ea86930);transition:height .25s,background .3s;opacity:.85}
.tank .inner{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:.3vh;text-align:center}
.tank .lab{font-family:var(--mono);font-size:1.25vh;letter-spacing:.2em;color:#aeb9c6;text-transform:uppercase;text-shadow:0 1px 3px #000}
.tank .laps{font-family:var(--mono);font-weight:700;font-size:7.8vh;line-height:.85;color:#fff;white-space:nowrap;letter-spacing:-1px;text-shadow:0 2px 6px rgba(0,0,0,.9),0 0 3px rgba(0,0,0,.95)}
.tank .unit{font-family:var(--mono);font-size:1.2vh;color:#aeb9c6;letter-spacing:.1em;text-shadow:0 1px 3px #000}
.tank .fuel{position:absolute;bottom:1vh;left:0;right:0;text-align:center;font-family:var(--mono);font-size:1.3vh;color:var(--muted)}
.tank .fuel b{color:var(--txt)}
/* 中:轉速燈 + 時速 + 圈 */
.center{justify-content:space-between}
.lights{display:flex;gap:.4vw;height:4.8vh}
.topbar{padding:1vh 1.2vw}
.topbar .rpmtop{margin:.6vh 0 0}
.lights .c{flex:1;border-radius:.5vh;background:#0d141c;border:1px solid #151d27}
.rpmtop{display:flex;justify-content:space-between;align-items:baseline;font-family:var(--mono);margin-bottom:.5vh}
.rpmtop .lab{font-size:1.2vh;letter-spacing:.2em;color:var(--muted)}
.rpmtop b{font-size:1.7vh;color:var(--txt)}.rpmtop b small{color:var(--muted);font-size:.62em}
.spdwrap{text-align:center}
.center .spdwrap{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center}
.center{justify-content:space-between;border:1.5px dashed rgba(255,255,255,.16);border-radius:1.4vh;padding:1.2vh 1.4vh}
.spd{font-family:var(--mono);font-weight:700;font-size:16.5vh;line-height:.82;color:var(--green);letter-spacing:-3px}
.spd small{font-size:.16em;color:var(--muted);letter-spacing:.14em;font-weight:500}
.laprow{text-align:center;margin-top:1vh}
.laprow .ll{font-family:var(--mono);font-size:1.15vh;letter-spacing:.2em;color:var(--muted);text-transform:uppercase;margin-right:.6em}
.lapt2{font-family:var(--mono);font-weight:700;font-size:3vh;color:var(--txt);letter-spacing:-1px}
.dblk{text-align:center;margin-top:1.3vh}
.dblk .ll{font-family:var(--mono);font-size:1.15vh;letter-spacing:.2em;color:var(--muted);text-transform:uppercase;display:block;margin-bottom:.5vh}
.drow{display:flex;align-items:center;justify-content:center;gap:.4vw}
.dnum{font-family:var(--mono);font-weight:700;font-size:8.2vh;line-height:1;color:var(--muted);letter-spacing:-2px}
.darrow{font-family:var(--mono);font-weight:700;font-size:4.4vh;line-height:1;color:var(--muted)}
.dwide{position:relative;height:1.5vh;margin:.7vh 0 0;background:#0d141c;border:1px solid #161d27;border-radius:.8vh;overflow:hidden}
.dwide .m{position:absolute;left:50%;top:0;bottom:0;width:1px;background:#36404e;z-index:2}
.dwide i{position:absolute;top:0;bottom:0;left:50%;width:0;transition:left .12s,width .12s,background .12s}
.dwide .l,.dwide .r{position:absolute;top:0;bottom:0;display:flex;align-items:center;font-family:var(--mono);font-size:1vh;color:#3a4655;z-index:3;letter-spacing:.1em}
.dwide .l{left:.6vw}.dwide .r{right:.6vw}
.secrow{display:flex;gap:.4vw;margin-top:.5vh}
.secrow .sec{flex:1;text-align:center;font-family:var(--mono);font-weight:700;font-size:1.5vh;line-height:1;padding:.55vh 0;border:1px solid #161d27;border-radius:.6vh;background:#0d141c;color:#3a4655;transition:background .15s,color .15s,border-color .15s}
.lapstack{flex:1.2 1 0;display:flex;flex-direction:column;gap:.8vh;margin-top:1vh;min-height:0}
.lr{flex:1;display:flex;flex-direction:column;justify-content:center;gap:.6vh;background:#0c121a;border:1px solid var(--line);border-radius:1vh;padding:.6vh 1.1vw}
.lr-top{display:flex;justify-content:space-between;align-items:baseline}
.lr .lab{font-family:var(--mono);font-size:1.5vh;letter-spacing:.12em;color:var(--muted);text-transform:uppercase}
.lr b{font-family:var(--mono);font-weight:700;font-size:4.2vh;line-height:1}
.lr-bar{height:1vh;background:#0d141c;border:1px solid #161d27;border-radius:.6vh;overflow:hidden}
.lr-bar i{display:block;height:100%;width:0;border-radius:.6vh;transition:width .25s}
#lastB{background:#cdd8e3}#bestB{background:var(--mag)}#optB{background:var(--gold)}
/* 右:轉向操控圓 + TCS */
.right{align-items:center;justify-content:space-between}
.ring{width:100%;display:flex;justify-content:center}
.ring svg{width:auto;height:42vh;max-width:100%}
.ped{width:100%;margin-top:.4vh}
.ped svg{width:100%;height:9vh;display:block}
.pedlab{display:flex;justify-content:space-between;font-family:var(--mono);font-size:1.1vh;color:var(--muted);margin-top:.2vh}
.aids{display:flex;gap:1vw;width:100%;margin-top:.4vh}
.aid{flex:1;text-align:center;font-family:var(--mono);font-size:1.4vh;letter-spacing:.16em;padding:.5vh 0;border-radius:2vh;border:1px solid var(--line);color:var(--muted);transition:.1s}
.aid.on{font-weight:700}
.tcs.on{background:var(--blue);color:#04222b;border-color:var(--blue);box-shadow:0 0 1.4vh var(--blue)}
.asm.on{background:var(--gold);color:#2a2102;border-color:var(--gold);box-shadow:0 0 1.4vh var(--gold)}
.status{font-family:var(--mono);font-size:1.4vh;color:var(--muted);text-align:center}.status b{color:var(--green)}.off #conn{color:var(--red)!important}
.center .status{width:100%;border-top:1px solid var(--line);padding-top:.9vh;margin-top:.6vh}
.status #info{color:var(--muted)}.status #info b{color:var(--txt)}
</style></head><body>

<div class="topbar card">
  <div class="lights" id="lights"></div>
  <div class="dwide"><span class="l">慢</span><div class="m"></div><i id="dwI"></i><span class="r">快</span></div>
  <div class="secrow"><div class="sec" id="sec0">—</div><div class="sec" id="sec1">—</div><div class="sec" id="sec2">—</div></div>
  <div class="rpmtop"><span class="lab">轉速 RPM</span><b><span id="rpmN">0</span><small> / <span id="rpmMax">0</span></small></b></div>
</div>
<div class="main">
  <!-- 左:車況 -->
  <div class="col card">
    <div class="grp-t">車況 · 胎溫 / 油箱 / 剩餘圈</div>
    <div class="car">
      <div class="ty fl" id="fl"><span class="pos">FL</span><span class="deg">—</span></div>
      <div class="ty fr" id="fr"><span class="pos">FR</span><span class="deg">—</span></div>
      <div class="tank">
        <div class="fill" id="fuelFill"></div>
        <div class="inner"><span class="lab">剩餘圈</span><span class="laps" id="laps">—</span><span class="unit">LAPS LEFT</span></div>
        <div class="fuel">油量 <b id="fuelL">—</b> L</div>
      </div>
      <div class="ty rl" id="rl"><span class="pos">RL</span><span class="deg">—</span></div>
      <div class="ty rr" id="rr"><span class="pos">RR</span><span class="deg">—</span></div>
    </div>
    <div class="lapstack">
      <div class="lr"><div class="lr-top"><span class="lab">上一圈</span><b id="lastT" style="color:var(--txt)">—</b></div><div class="lr-bar"><i id="lastB"></i></div></div>
      <div class="lr"><div class="lr-top"><span class="lab">最佳</span><b id="bestT" style="color:var(--mag)">—</b></div><div class="lr-bar"><i id="bestB"></i></div></div>
      <div class="lr"><div class="lr-top"><span class="lab">OPT 潛力</span><b id="optT" style="color:var(--gold)">—</b></div><div class="lr-bar"><i id="optB"></i></div></div>
    </div>
  </div>

  <!-- 中:時速 + 圈 -->
  <div class="col center">
    <div>
      <div class="dblk"><span class="ll">Δ VS 最佳</span>
        <div class="drow"><div class="dnum" id="dnum">—</div><span class="darrow" id="darrow"></span></div></div>
      <div class="laprow"><span class="ll">本圈</span><span class="lapt2" id="lapt">0:00.000</span></div>
    </div>
    <div class="spdwrap"><div class="spd"><span id="spd">0</span><small>km/h</small></div></div>
    <div class="status" id="status"><span id="conn">等待遙測…</span><span id="info"></span></div>
  </div>

  <!-- 右:轉向操控圓 -->
  <div class="col card right" id="gripCard">
    <div class="grp-t">轉向 / 操控</div>
    <div class="ring">
      <svg id="gripSvg" viewBox="0 0 340 340" style="overflow:visible">
        <defs>
          <radialGradient id="bowl" cx="50%" cy="50%" r="50%"><stop offset="0%" stop-color="#142231" stop-opacity="0"/><stop offset="60%" stop-color="#142231" stop-opacity="0.18"/><stop offset="100%" stop-color="#0b1622" stop-opacity="0.5"/></radialGradient>
          <radialGradient id="glow" cx="50%" cy="50%" r="50%"><stop offset="0%" stop-color="#1d3247" stop-opacity="0.5"/><stop offset="70%" stop-color="#1d3247" stop-opacity="0.12"/><stop offset="100%" stop-color="#1d3247" stop-opacity="0"/></radialGradient>
          <radialGradient id="warnGold" cx="50%" cy="50%" r="50%"><stop offset="0%" stop-color="#ffd43b" stop-opacity="0.34"/><stop offset="55%" stop-color="#ffd43b" stop-opacity="0.2"/><stop offset="100%" stop-color="#ffd43b" stop-opacity="0"/></radialGradient>
          <radialGradient id="warnRed" cx="50%" cy="50%" r="50%"><stop offset="0%" stop-color="#ff3b5c" stop-opacity="0.5"/><stop offset="55%" stop-color="#ff3b5c" stop-opacity="0.3"/><stop offset="100%" stop-color="#ff3b5c" stop-opacity="0"/></radialGradient>
        </defs>
        <circle id="warnGlow" cx="170" cy="170" r="170" fill="url(#warnRed)" opacity="0"/>
        <circle cx="170" cy="170" r="124" fill="url(#glow)"/><circle cx="170" cy="170" r="124" fill="url(#bowl)"/>
        <text id="ctrGear" x="170" y="200" fill="var(--gold)" opacity="0.6" font-size="120" font-family="Bebas Neue" text-anchor="middle">N</text>
        <circle cx="170" cy="170" r="42" fill="none" stroke="#141b24" stroke-opacity="0.5"/><circle cx="170" cy="170" r="83" fill="none" stroke="#27384a" stroke-opacity="0.8"/><circle cx="170" cy="170" r="124" fill="none" stroke="#3a5167"/>
        <line x1="170" y1="46" x2="170" y2="294" stroke="#141b24" stroke-opacity="0.6"/><line x1="46" y1="170" x2="294" y2="170" stroke="#141b24" stroke-opacity="0.6"/>
        <path d="M170 308 A138 138 0 0 0 170 32" fill="none" stroke="#0e2018" stroke-width="10"/><path d="M170 308 A138 138 0 0 1 170 32" fill="none" stroke="#22121a" stroke-width="10"/>
        <path id="thrFill" d="M170 308 A138 138 0 0 0 170 32" pathLength="100" fill="none" stroke="var(--green)" stroke-width="10" stroke-linecap="round" stroke-dasharray="100" stroke-dashoffset="100"/>
        <path id="brkFill" d="M170 308 A138 138 0 0 1 170 32" pathLength="100" fill="none" stroke="var(--red)" stroke-width="10" stroke-linecap="round" stroke-dasharray="100" stroke-dashoffset="100"/>
        <circle id="thrHalo" r="11" fill="var(--green)" opacity="0"/><circle id="brkHalo" r="11" fill="var(--red)" opacity="0"/><circle id="thrTip" r="4" fill="#caffe6" opacity="0"/><circle id="brkTip" r="4" fill="#ffd0d9" opacity="0"/>
        <text x="312" y="174" fill="#2f8a5e" font-size="10" font-family="JetBrains Mono" text-anchor="middle">油</text><text x="28" y="174" fill="#9c3046" font-size="10" font-family="JetBrains Mono" text-anchor="middle">煞</text>
        <g id="steerRing"><circle cx="170" cy="170" r="158" fill="none" stroke="#223040" stroke-width="2"/><circle cx="170" cy="170" r="158" fill="none" stroke="var(--cyan)" stroke-width="3" stroke-dasharray="2 22" opacity="0.4"/><line x1="170" y1="6" x2="170" y2="40" stroke="var(--cyan)" stroke-width="5" stroke-linecap="round"/><circle cx="170" cy="22" r="3.5" fill="var(--cyan)"/></g>
        <text x="170" y="58" fill="#3a4655" font-size="9" font-family="JetBrains Mono" text-anchor="middle">加速</text><text x="170" y="288" fill="#3a4655" font-size="9" font-family="JetBrains Mono" text-anchor="middle">減速</text>
        <g id="trail"></g>
        <line id="slipArrow" x1="170" y1="170" x2="170" y2="170" stroke="var(--gold)" stroke-width="3" stroke-linecap="round" opacity="0"/>
        <circle id="ggDot" cx="170" cy="170" r="9" fill="var(--green)"/>
        <circle id="warnRing" cx="170" cy="170" r="124" fill="none" stroke="#ff3b5c" stroke-width="7" opacity="0"/>
      </svg>
    </div>
    <div class="ped">
      <svg viewBox="0 0 300 70" preserveAspectRatio="none">
        <line x1="0" y1="35" x2="300" y2="35" stroke="#161d27" stroke-width="1"/>
        <path id="pthrA" fill="var(--green)" fill-opacity="0.18" stroke="none" d=""/>
        <path id="pbrkA" fill="var(--red)" fill-opacity="0.18" stroke="none" d=""/>
        <polyline id="pthrL" fill="none" stroke="var(--green)" stroke-width="1.6" vector-effect="non-scaling-stroke" points=""/>
        <polyline id="pbrkL" fill="none" stroke="var(--red)" stroke-width="1.6" vector-effect="non-scaling-stroke" points=""/>
      </svg>
      <div class="pedlab"><span style="color:var(--green)">油門</span><span>近 10 秒</span><span style="color:var(--red)">煞車</span></div>
    </div>
    <div class="aids"><div class="aid tcs" id="tcs">TCS</div><div class="aid asm" id="asm">ASM</div></div>
  </div>
</div>

<script>
const $=id=>document.getElementById(id);
const fmtT=s=>{if(s==null)return'—';const m=Math.floor(s/60);return m+':'+(s-m*60).toFixed(3).padStart(6,'0');};
const LATMAX=3.4,LONMAX=2.2,RAD=124,R0=170,PR=138,SLIPMAX=12;
let REDLINE=8000;
let dHist=[];
// 轉速燈
const NL=24;let lh='';for(let i=0;i<NL;i++)lh+='<div class="c"></div>';$('lights').innerHTML=lh;
const cells=[...document.querySelectorAll('#lights .c')];
const GRN='#00ff88',GLD='#ffd43b',RED='#ff3b5c',BLU='#36b3ff',CYN='#33e0ff',MAG='#c86bff',TXT='#eef3f9',MUT='#6b7787';
const lightColor=i=>{const f=i/NL;return f<0.55?GRN:f<0.82?RED:MAG;};
const tyColor=c=>c==null?MUT:c<60?BLU:c<90?GRN:c<105?GLD:RED;
const slipColor=a=>a<2?GRN:a<5?GLD:RED;
const clamp=(v,m)=>Math.max(-1,Math.min(1,v/m));
const trail=[];
const pedHist=[];
function setTy(id,c){const e=$(id),b=e.querySelector('.deg');if(c==null||isNaN(c)){b.textContent='—';e.style.borderColor=MUT;b.style.color=MUT;e.style.boxShadow='none';return;}const col=tyColor(c);b.textContent=Math.round(c)+'°';b.style.color=col;e.style.borderColor=col;e.style.boxShadow='inset 0 0 1.4vh -.6vh '+col;}
function tip(side,frac){const a=frac*Math.PI;return[R0+side*PR*Math.sin(a),R0+PR*Math.cos(a)];}
function render(d){
  // 轉速燈:用 GT7 的 rpmMin=換檔警示RPM 當該升檔的閃燈點(遠低於限轉),rpmMax=限轉只當顯示
  const rpm=d.rpm||0;
  REDLINE=(d.rpmMax&&d.rpmMax>3000)?d.rpmMax:Math.max(REDLINE,rpm);
  const warn=(d.rpmMin&&d.rpmMin>2000)?d.rpmMin:REDLINE*0.9;   // 該升檔的轉速
  const lo=warn*0.7,shift=warn;
  const lit=Math.round(Math.max(0,Math.min(1,(rpm-lo)/((shift-lo)||1)))*NL),over=rpm>=shift,flash=over&&(performance.now()%140<80);
  cells.forEach((c,i)=>{if(flash){c.style.background=BLU;c.style.boxShadow='0 0 1vh '+BLU;}else if(i<lit){const col=lightColor(i);c.style.background=col;c.style.boxShadow='0 0 .8vh '+col;}else{c.style.background='#0d141c';c.style.boxShadow='none';}});
  $('rpmN').textContent=Math.round(rpm);$('rpmMax').textContent=Math.round(REDLINE);
  // 時速 + 圈
  $('spd').textContent=Math.round(d.spd||0);
  $('lapt').textContent=fmtT(d.lapt!=null?d.lapt:0);
  {const dv=d.delta,dn=$('dnum'),ar=$('darrow'),wi=$('dwI');
   if(dv==null){dn.textContent='—';dn.style.color=MUT;ar.textContent='';wi.style.width='0';dHist.length=0;}
   else{const col=dv<=0?GRN:RED;
     dn.textContent=(dv>=0?'+':'')+dv.toFixed(3);dn.style.color=col;
     const w=Math.min(1,Math.abs(dv)/2)*50;          // 上排全寬條:右=快(綠) 左=慢(紅)
     if(dv<=0){wi.style.left='50%';wi.style.width=w+'%';wi.style.background=GRN;}
     else{wi.style.left=(50-w)+'%';wi.style.width=w+'%';wi.style.background=RED;}
     const now=performance.now();dHist.push([now,dv]);while(dHist.length>2&&now-dHist[0][0]>1000)dHist.shift();
     const trend=dv-dHist[0][1];                       // 趨勢:越來越快▲綠 / 越來越慢▼紅
     if(trend<-0.03){ar.textContent='▲';ar.style.color=GRN;}
     else if(trend>0.03){ar.textContent='▼';ar.style.color=RED;}
     else{ar.textContent='▬';ar.style.color=MUT;}}}
  for(let i=0;i<3;i++){const el=$('sec'+i),v=d.sec?d.sec[i]:null;
    if(v==null){el.textContent='—';el.style.color='#3a4655';el.style.background='#0d141c';el.style.borderColor=(d.sec&&i===d.secI)?'#5a6878':'#161d27';continue;}
    el.textContent=(v>=0?'+':'')+v.toFixed(2);
    const neg=v<=0;el.style.color=neg?GRN:RED;
    el.style.background=neg?'rgba(0,255,136,.13)':'rgba(255,59,92,.13)';
    el.style.borderColor=(i===d.secI)?'#aeb9c6':(neg?'rgba(0,255,136,.4)':'rgba(255,59,92,.4)');}
  $('bestT').textContent=fmtT(d.best);$('optT').textContent=fmtT(d.opt);$('lastT').textContent=fmtT(d.last);
  {const vs=[['lastB',d.last],['bestB',d.best],['optB',d.opt]];
   const pr=vs.filter(x=>x[1]!=null).map(x=>x[1]);
   if(pr.length){const mn=Math.min(...pr),mx=Math.max(...pr),sp=mx-mn;
     for(const x of vs){const el=$(x[0]);if(x[1]==null){el.style.width='0';continue;}
       el.style.width=((sp>0?0.35+0.65*(x[1]-mn)/sp:1)*100).toFixed(1)+'%';}}}
  // 左:輪胎 + 油箱
  setTy('fl',d.tFL);setTy('fr',d.tFR);setTy('rl',d.tRL);setTy('rr',d.tRR);
  const cap=(d.fuelCap&&d.fuelCap>0)?d.fuelCap:100;   // 容量缺漏時假設 100(GT7 常以 100 為滿)
  const pct=d.fuel!=null?Math.max(0,Math.min(1,d.fuel/cap)):0;
  $('fuelFill').style.height=(pct*100)+'%';
  $('fuelL').textContent=d.fuel!=null?d.fuel.toFixed(1):'—';
  if(d.lapsLeft!=null){const L=d.lapsLeft;$('laps').textContent=L.toFixed(1);$('laps').style.color=L>6?GRN:L>3?GLD:RED;
    $('fuelFill').style.background=L>6?'linear-gradient(0deg,#1c5,#2ea869 70%,#2ea86930)':L>3?'linear-gradient(0deg,#caa12a,#caa12a70 70%,#caa12a20)':'linear-gradient(0deg,#c0314a,#c0314a70 70%,#c0314a20)';}
  else{$('laps').textContent='—';$('laps').style.color=MUT;}
  // 右:轉向操控圓
  $('steerRing').setAttribute('transform','rotate('+(d.steer||0).toFixed(1)+' '+R0+' '+R0+')');
  $('thrFill').setAttribute('stroke-dashoffset',(100-(d.thr||0)).toFixed(1));
  $('brkFill').setAttribute('stroke-dashoffset',(100-(d.brk||0)).toFixed(1));
  {const g=d.gear;$('ctrGear').textContent=(g==null||g>=15)?'N':(g===0)?'R':g;}
  const setTip=(halo,tipc,side,frac)=>{const p=tip(side,frac),o=Math.max(0,(frac-0.55)/0.45);$(halo).setAttribute('cx',p[0].toFixed(1));$(halo).setAttribute('cy',p[1].toFixed(1));$(halo).setAttribute('opacity',(o*0.55).toFixed(2));$(halo).setAttribute('r',(8+o*5).toFixed(1));$(tipc).setAttribute('cx',p[0].toFixed(1));$(tipc).setAttribute('cy',p[1].toFixed(1));$(tipc).setAttribute('opacity',frac>0.04?Math.min(1,0.3+o).toFixed(2):0);};
  setTip('thrHalo','thrTip',1,(d.thr||0)/100);setTip('brkHalo','brkTip',-1,(d.brk||0)/100);
  const lat=d.latG||0,lon=d.lonG||0,slip=d.slip||0;
  const dx=R0+clamp(lat,LATMAX)*RAD,dy=R0-clamp(lon,LONMAX)*RAD;
  if(!d.paused){trail.push([dx,dy]);if(trail.length>16)trail.shift();}
  let tg='';for(let i=0;i<trail.length;i++){const f=i/trail.length;tg+='<circle cx="'+trail[i][0].toFixed(1)+'" cy="'+trail[i][1].toFixed(1)+'" r="'+(1.5+f*4).toFixed(1)+'" fill="'+CYN+'" opacity="'+(f*0.3).toFixed(2)+'"/>';}
  $('trail').innerHTML=tg;
  const col=slipColor(Math.abs(slip)),mag=Math.min(1,Math.hypot(lat/LATMAX,lon/LONMAX)),dist=Math.hypot(dx-R0,dy-R0);
  const dot=$('ggDot');dot.setAttribute('cx',dx.toFixed(1));dot.setAttribute('cy',dy.toFixed(1));dot.setAttribute('r',(8+mag*5).toFixed(1));dot.setAttribute('fill',col);dot.setAttribute('opacity',(0.35+0.65*Math.min(1,dist/58)).toFixed(2));
  const aw=Math.min(1,Math.abs(slip)/SLIPMAX),a=$('slipArrow');a.setAttribute('x1',dx.toFixed(1));a.setAttribute('y1',dy.toFixed(1));a.setAttribute('x2',(dx+Math.sign(slip)*aw*46).toFixed(1));a.setAttribute('y2',dy.toFixed(1));a.setAttribute('stroke',col);a.setAttribute('opacity',aw>0.05?0.85:0);
  {const wr=$('warnRing'),wg=$('warnGlow'),gs=$('gripSvg'),as=Math.abs(slip);
   if(as<2){wr.setAttribute('opacity','0');wg.setAttribute('opacity','0');gs.style.filter='';}
   else if(as<5){wr.setAttribute('stroke',GLD);wr.setAttribute('opacity','0.55');
     wg.setAttribute('fill','url(#warnGold)');wg.setAttribute('opacity','0.45');
     gs.style.filter='drop-shadow(0 0 14px rgba(255,212,59,.7)) drop-shadow(0 0 40px rgba(255,212,59,.45))';}
   else{const pulse=0.5+0.5*Math.sin(performance.now()/90);
     wr.setAttribute('stroke',RED);wr.setAttribute('opacity',(0.35+0.6*pulse).toFixed(2));
     wg.setAttribute('fill','url(#warnRed)');wg.setAttribute('opacity',(0.25+0.35*pulse).toFixed(2));
     const a1=(0.5+0.4*pulse).toFixed(2),a2=(0.3+0.4*pulse).toFixed(2);
     gs.style.filter='drop-shadow(0 0 16px rgba(255,59,92,'+a1+')) drop-shadow(0 0 52px rgba(255,59,92,'+a2+'))';}}
  // TCS / ASM
  $('tcs').className='aid tcs'+(d.tcs?' on':'');
  $('asm').className='aid asm'+(d.asm?' on':'');
  // 油門/煞車 近10秒曲線(暫停時凍結,不再進資料)
  if(!d.paused){
    const PW=300,PH=70,WIN=10,tn=performance.now()/1000;
    pedHist.push([tn,d.thr||0,d.brk||0]);
    while(pedHist.length>1&&tn-pedHist[0][0]>WIN)pedHist.shift();
    let tpt='',bpt='';
    for(const e of pedHist){const x=(PW*(1-(tn-e[0])/WIN)).toFixed(1);tpt+=x+','+(PH-e[1]/100*PH).toFixed(1)+' ';bpt+=x+','+(PH-e[2]/100*PH).toFixed(1)+' ';}
    $('pthrL').setAttribute('points',tpt.trim());$('pbrkL').setAttribute('points',bpt.trim());
    const area=p=>{const a=p.trim().split(' ');if(a.length<2||!a[0])return'';const x0=a[0].split(',')[0],xN=a[a.length-1].split(',')[0];return 'M'+x0+','+PH+' L'+a.join(' L')+' L'+xN+','+PH+' Z';};
    $('pthrA').setAttribute('d',area(tpt));$('pbrkA').setAttribute('d',area(bpt));
  }
  // 底部資訊列
  let info=[];
  if(d.track)info.push(d.track);
  if(d.car)info.push(d.car);
  if(d.lap!=null&&d.lap>0)info.push('第 '+d.lap+' 圈');
  $('info').innerHTML=info.length?('&nbsp;&nbsp;·&nbsp;&nbsp;'+info.join('&nbsp;&nbsp;·&nbsp;&nbsp;')):'';
}
async function poll(){
  try{const r=await fetch('/data',{cache:'no-store'});const d=await r.json();
    if(d&&d.spd!=null){render(d);$('conn').innerHTML='<b>● 即時連線中</b>';$('status').classList.remove('off');}
  }catch(e){$('conn').textContent='連線中斷,重試中…';$('status').classList.add('off');}
}
setInterval(poll,33);
</script></body></html>'''

def start_live_server(port):
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def _send(self, body, ctype):
            self.send_response(200); self.send_header('Content-Type', ctype)
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body))); self.end_headers()
            try: self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError): pass
        def do_GET(self):
            if self.path.startswith('/data'):
                self._send(json.dumps(LIVE['d']).encode('utf-8'), 'application/json')
            elif self.path == '/' or self.path.startswith('/index'):
                self._send(LIVE_HTML.encode('utf-8'), 'text/html; charset=utf-8')
            else:
                self.send_response(404); self.end_headers()
    srv = http.server.ThreadingHTTPServer(('0.0.0.0', port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv

# ---------------- main loop ----------------
def _github_upload(path, repo, branch, dest_dir, note='', local_is_fresh=False):
    """收工後把整份原始紀錄檔(不篩選)透過 GitHub Contents API 上傳到 repo。
    不需本機裝 git;token 由環境變數 GT7_GITHUB_TOKEN / GITHUB_TOKEN 提供。
    local_is_fresh=True 表示本機這份是全新檔(非接續);若遠端已有同名檔,
    為避免蓋掉先前那次,會改用時間後綴另存。"""
    import base64, urllib.request, urllib.error
    token = os.environ.get('GT7_GITHUB_TOKEN') or os.environ.get('GITHUB_TOKEN')
    if not token:
        print('  ⓘ 未設定 token,略過自動上傳。設定後每次收工會自動推上 GitHub:')
        print('     export GT7_GITHUB_TOKEN=<具 repo 權限的 GitHub token>')
        return
    try:
        with open(path, 'rb') as f:
            content = f.read()
    except OSError as e:
        print(f'  ✗ 讀取檔案失敗,略過上傳:{e}')
        return
    name = os.path.basename(path)
    api = f'https://api.github.com/repos/{repo}/contents/{dest_dir.strip("/")}/{name}'
    hdrs = {'Authorization': f'token {token}',
            'Accept': 'application/vnd.github+json',
            'User-Agent': 'gt7_capture',
            'X-GitHub-Api-Version': '2022-11-28'}
    # 同日續寫會覆蓋同一檔名 → 需要現有檔的 sha 才能更新
    sha = None
    try:
        req = urllib.request.Request(api + f'?ref={branch}', headers=hdrs)
        with urllib.request.urlopen(req, timeout=20) as r:
            sha = json.loads(r.read()).get('sha')
    except urllib.error.HTTPError as e:
        if e.code != 404:
            print(f'  ⚠ 查詢現有檔失敗(HTTP {e.code}),仍嘗試上傳…')
    except Exception:
        pass
    # 防呆:本機是全新檔、但遠端同名已存在 → 不覆蓋,改用時間後綴另存(避免蓋掉先前那次)
    if sha and local_is_fresh:
        base, ext = os.path.splitext(name)
        name = f"{base}_{datetime.now().strftime('%H%M%S')}{ext}"
        api = f'https://api.github.com/repos/{repo}/contents/{dest_dir.strip("/")}/{name}'
        print(f'  ⚠ 遠端已有同名檔,但本機這份是全新內容 → 另存為 {name},不覆蓋舊檔')
        sha = None
    body = {'message': f'capture: {name}' + (f' — {note}' if note else ''),
            'content': base64.b64encode(content).decode('ascii'),
            'branch': branch}
    if sha:
        body['sha'] = sha
    data = json.dumps(body).encode('utf-8')
    try:
        req = urllib.request.Request(api, data=data,
                                     headers={**hdrs, 'Content-Type': 'application/json'},
                                     method='PUT')
        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.loads(r.read())
        kb = len(content) / 1024
        print(f'  ☁ 已自動上傳 → {repo}/{dest_dir}/{name}  ({kb:.0f} KB,{"更新" if sha else "新建"})')
        url = resp.get('content', {}).get('html_url', '')
        if url:
            print(f'     {url}')
    except urllib.error.HTTPError as e:
        msg = ''
        try: msg = json.loads(e.read()).get('message', '')
        except Exception: pass
        print(f'  ✗ 自動上傳失敗(HTTP {e.code} {msg})。原始檔仍在本機:{path}')
    except Exception as e:
        print(f'  ✗ 自動上傳失敗({e})。原始檔仍在本機:{path}')


def _rank_update(repo, branch):
    """收工後順手更新世界排名 / WR / 你的名次 → 推回 GitHub 的 data.json。
    需要:① GT7 認證(GT7_JSESSIONID 或 GT7_GT_TOKEN)② GitHub token ③ 同目錄 gt7_rank.py + requests。
    任何一項缺就略過,不影響已上傳的 CSV。"""
    js = os.environ.get('GT7_JSESSIONID')
    bearer = os.environ.get('GT7_GT_TOKEN')
    gh = os.environ.get('GT7_GITHUB_TOKEN') or os.environ.get('GITHUB_TOKEN')
    if not (js or bearer):
        print('  ⓘ 未設定 GT7_JSESSIONID / GT7_GT_TOKEN,略過名次更新(只上傳了 CSV)。')
        return
    if not gh:
        print('  ⓘ 未設定 GitHub token,略過名次更新。')
        return
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import gt7_rank
    except Exception as e:
        print(f'  ⓘ 名次更新需要同目錄的 gt7_rank.py 與 requests(pip install requests);略過。({e})')
        return
    try:
        print('🏁 收工順手更新世界排名 / WR / 你的名次…')
        gt7_rank.run(jsessionid=js, bearer=bearer, push=True, repo=repo, branch=branch, gh_token=gh)
    except Exception as e:
        print(f'  ✗ 名次更新失敗(CSV 已上傳,不受影響):{e}')


def main():
    ap = argparse.ArgumentParser(description='GT7 telemetry capture (v2)')
    ap.add_argument('ps_ip', nargs='?', default=None,
                    help='PS5 的 IP(例 192.168.88.109);省略則自動偵測同網段')
    ap.add_argument('--track', default='', help='賽道名(可省略)')
    ap.add_argument('--car', default='', help='車輛名(可省略)')
    ap.add_argument('--hz', type=float, default=20.0, help='取樣頻率,預設 20;0=全收(~60Hz)')
    ap.add_argument('--out', default='.', help='輸出資料夾')
    ap.add_argument('--packet', default='C', choices=['A', 'B', '~', 'C'],
                    help="心跳封包型別,預設 C(最完整);抓不到可改 A/B/~")
    ap.add_argument('--live', action='store_true', help='同時開本機即時儀表板網站')
    ap.add_argument('--port', type=int, default=8080, help='即時看板連接埠(預設 8080)')
    ap.add_argument('--sec', default=None, help='分段界距離百分比,如 "25.72,72.36"(用官方分段校正值);不給=依參考圈等時間三等分')
    ap.add_argument('--replay', action='store_true',
                    help='重播模式:不管在不在跑道都錄,收工自動寫出最後一段(抓 WR ghost 走線用)')
    ap.add_argument('--no-push', action='store_true',
                    help='關閉收工自動上傳(預設:偵測到 GT7_GITHUB_TOKEN 就自動把原始檔上傳到 GitHub)')
    ap.add_argument('--repo', default='WYSwolf/GT7', help='自動上傳目標 repo(owner/name),預設 WYSwolf/GT7')
    ap.add_argument('--branch', default='main', help='自動上傳目標分支,預設 main')
    ap.add_argument('--dest-dir', default='telemetry', help='原始檔在 repo 內的資料夾,預設 telemetry')
    ap.add_argument('--no-rank', action='store_true',
                    help='關閉收工自動更新世界排名/名次(預設:有 GT7_JSESSIONID + GitHub token 就一起更新 data.json)')
    args = ap.parse_args()

    heartbeat = args.packet.encode('ascii')

    if not args.ps_ip:
        found = detect_ps5(heartbeat)
        if found:
            args.ps_ip = found
            print(f'  ✓ 找到 PS5:{found}')
        else:
            print('  ✗ 找不到 PS5。請確認:GT7 正在前景執行、PS5 與電腦在同一台路由器'
                  '(IP 192.168 開頭)、防火牆放行 UDP 33739/33740。')
            print('    或手動指定:python gt7_capture.py <PS5_IP>')
            return
    _save_ip(args.ps_ip)   # 記住這次的 IP,下次自動偵測先試它

    os.makedirs(args.out, exist_ok=True)
    date = datetime.now().strftime('%Y-%m-%d')
    pfx = 'wr_' if args.replay else ''     # 重播(世界排名)→ wr_ 前綴,日後比較好分辨對象
    out_path = os.path.join(args.out, f'{pfx}gt7_{date}.csv')
    append = (not args.replay) and os.path.exists(out_path)
    if args.replay and os.path.exists(out_path):     # WR 重播不合併,另存帶時間
        out_path = os.path.join(args.out, f"{pfx}gt7_{date}_{datetime.now().strftime('%H%M%S')}.csv")
    seed_run_lap = _max_run_lap(out_path) if append else 0
    min_dt = (1.0 / args.hz) if args.hz and args.hz > 0 else 0.0

    COLS = ['run_lap','inlap','carcode','lap_time','wall',
            't','spd','thr','brk','rpm','gear','x','z','yaw','latG','lonG','steer',
            'tFL','tFR','tRL','tRR','vx','vz']

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('0.0.0.0', RECV_PORT))
    sock.settimeout(1.0)

    def beat():
        try: sock.sendto(heartbeat, (args.ps_ip, SEND_PORT))
        except OSError: pass

    if append:
        fh = open(out_path, 'a', encoding='utf-8', newline='')
        print(f'  ↻ 當天已有檔,接續寫入(圈號從 {seed_run_lap + 1} 起):{out_path}')
    else:
        fh = open(out_path, 'w', encoding='utf-8', newline='')
        fh.write(f'# gt7 capture | captured={datetime.now().isoformat(timespec="seconds")}'
                 f' | track={args.track} | car={args.car} | packet={args.packet} | hz={args.hz}\n')
        fh.write(','.join(COLS) + '\n')
    fh.flush()

    print(f'▶ 連線 PS5 {args.ps_ip} … 心跳封包 "{args.packet}"  (Ctrl+C 結束並存檔)')
    print(f'  輸出:{out_path}   取樣:{("全收 ~60Hz" if min_dt==0 else str(args.hz)+"Hz")}')
    beat()

    if args.live:
        start_live_server(args.port)
        print(f'◉ 即時看板:本機 http://localhost:{args.port}  |  同網手機 http://{lan_ip()}:{args.port}')

    t0 = time.monotonic()
    sleep_token = _prevent_sleep()
    if sleep_token:
        print('🛡 執行期間已阻止電腦/螢幕休眠')
    cur_lap = None; buf = []; lap_start = None; last_sample_t = -1e9
    last_pid = -1; pkt_count = 0; got_any = False; last_rx = time.time()
    run_lap = seed_run_lap; lap_count = 0; best_time = None; cur_car = 0; fields = ['basic']
    px = None; pz = 0.0; lap_dist = 0.0; cur_pd = []; cur_pt = []; cur_ps = []; cur_pv = []
    fuel_lap_start = None; fuel_per_lap = None      # 每圈耗油(算剩餘圈)
    clean_laps = []; avg_lap = None  # 近 5 圈乾淨圈(最佳圈 6% 內)平均
    opt_seg = [None, None, None]; opt_total = None; opt_b = None  # 潛力最佳:三段(直線段界)各取最佳相加
    prev_psi = None; prev_pt = -1.0; beta = 0.0; dpsi_ema = 0.0
    lap_t = 0.0; prev_now = None      # 遊戲時間累加器(暫停/選單不計)
    # live 看板專用(不受 在跑道/圈數 限制,重播也能即時顯示)
    last_live = 0.0; live_prev_psi = None; live_prev_now = 0.0
    live_beta = 0.0; live_dpsi = 0.0; live_delta = None
    sec_vals = [None, None, None]; sec_base = 0.0; sec_idx = 0; sec_live = None; sec_b = None  # 三段 delta(PMR 風格)

    def fmt_ms(ms):
        if ms is None or ms < 0: return '—'
        s = ms / 1000.0; m = int(s // 60); return f'{m}:{s - m*60:06.3f}'

    def flush_lap(lap_time, wall_start, inlap, car):
        nonlocal run_lap, lap_count, best_time
        run_lap += 1; lap_count += 1
        head = [run_lap, inlap, car, lap_time, round(wall_start, 1)]
        for s in buf:
            fh.write(','.join('' if c is None else str(c) for c in head + s) + '\n')
        fh.flush()
        if best_time is None or lap_time < best_time:
            best_time = lap_time

    try:
        while True:
            try:
                data, _ = sock.recvfrom(4096)
            except socket.timeout:
                beat()
                _keep_awake(sleep_token)
                if got_any and time.time() - last_rx > 5:
                    print('… 等待遙測中(確認 GT7 在前景、IP/防火牆正確)')
                continue

            d = decrypt(data)
            if d is None:
                continue
            p = parse(d)
            if p['pid'] == last_pid:
                continue
            last_pid = p['pid']
            pkt_count += 1
            last_rx = time.time()
            if not got_any:
                got_any = True
                klass = 'C(完整)' if len(d) >= LEN_C else ('B/~(含G)' if len(d) >= LEN_B else 'A(基本)')
                print(f'✓ 已連上,封包大小 {len(d)} bytes → {klass}')
            if p['latG'] is not None and fields[0] == 'basic':
                fields[0] = 'rich'           # G + 轉向都有

            if pkt_count % 100 == 0:
                beat()

            if args.replay and pkt_count % 120 == 0:
                print(f'  〔重播錄製中〕spd={p["mps"]*3.6:.0f} km/h  本段 {len(buf)} 筆')

            lap = p['lap']

            if lap != cur_lap:
                _normal_done = (cur_lap is not None and cur_lap >= 1 and p['last'] and p['last'] > 0)
                _replay_done = (args.replay and len(buf) > 5)
                if _normal_done or _replay_done:
                    lt = round(p['last'] / 1000.0, 3) if (p['last'] and p['last'] > 0) else round(lap_t, 3)
                    was_best = (best_time is None or lt < best_time)
                    flush_lap(lt, (lap_start - t0) if lap_start else 0.0, cur_lap or 0, cur_car)
                    print(f'  ✓ 第 {run_lap} 圈完成:{fmt_ms(lt*1000)}  (本圈 {len(buf)} 筆)')
                    if was_best and len(cur_pd) > 10:
                        REF['dist'] = cur_pd[:]; REF['t'] = cur_pt[:]
                        REF['st'] = cur_ps[:]; REF['v'] = cur_pv[:]
                    # 每圈耗油
                    if fuel_lap_start is not None and p['fuel'] is not None:
                        used = fuel_lap_start - p['fuel']
                        if used > 0.05:
                            fuel_per_lap = used
                    # 近 5 圈乾淨圈平均(乾淨=最佳圈 6% 內)
                    if best_time is not None and lt <= best_time * 1.06:
                        clean_laps.append(lt)
                        recent = clean_laps[-5:]
                        avg_lap = round(sum(recent) / len(recent), 3)
                        # 潛力最佳:三段(段界吸附直線/校正)各取歷史最佳相加;段界變了就重置
                        if sec_b and len(cur_pd) > 10 and cur_pd[-1] > 0:
                            if opt_b is None or abs(opt_b[0] - sec_b[0]) > 5 or abs(opt_b[1] - sec_b[1]) > 5:
                                opt_b = sec_b[:]; opt_seg = [None, None, None]; opt_total = None
                            ts = []
                            for db in sec_b:               # 內插過段界的時間
                                lo2, hi2 = 0, len(cur_pd) - 1
                                while lo2 < hi2:
                                    m2 = (lo2 + hi2) // 2
                                    if cur_pd[m2] < db: lo2 = m2 + 1
                                    else: hi2 = m2
                                if lo2 == 0: ts.append(cur_pt[0])
                                else:
                                    span = cur_pd[lo2] - cur_pd[lo2-1]
                                    fr = (db - cur_pd[lo2-1]) / span if span > 0 else 0
                                    ts.append(cur_pt[lo2-1] + (cur_pt[lo2] - cur_pt[lo2-1]) * fr)
                            for k, v in enumerate((ts[0], ts[1] - ts[0], lt - ts[1])):
                                if v > 0 and (opt_seg[k] is None or v < opt_seg[k]):
                                    opt_seg[k] = v
                            if all(x is not None for x in opt_seg):
                                opt_total = round(sum(opt_seg), 3)
                cur_lap = lap
                buf = []
                lap_start = time.monotonic()
                last_sample_t = -1e9
                lap_dist = 0.0; px = None; cur_pd = []; cur_pt = []; cur_ps = []; cur_pv = []
                sec_vals = [None, None, None]; sec_base = 0.0; sec_idx = 0; sec_live = None
                sec_b = sec_bounds(args.sec)
                lap_t = 0.0; prev_psi = None; prev_pt = -1.0; beta = 0.0; dpsi_ema = 0.0
                if p['fuel'] is not None:
                    fuel_lap_start = p['fuel']        # 記錄新圈起始油量

            paused = bool(p['flags'] & 0x02)
            on_track = bool(p['flags'] & 0x01)
            now = time.monotonic()
            dt_frame = (now - prev_now) if prev_now is not None else 0.0
            prev_now = now
            if (on_track or args.replay) and not paused and 0 < dt_frame < 0.5:
                lap_t += dt_frame                      # 只在進行中累加,暫停/選單/卡頓不計

            # ── live 看板:只要收到封包就以 ~25Hz 推送,不受 在跑道/圈數/暫停 限制 ──
            #    開車、重播、選單背景車 都看得到資料
            if args.live and (now - last_live) >= 0.04:
                last_live = now
                sl = None
                if p['latG'] is not None and p['vx'] is not None:
                    _psi = math.atan2(p['vx'], p['vz'])
                    _dt = now - live_prev_now
                    spd = p['mps'] * 3.6
                    if live_prev_psi is not None and 0 < _dt < 0.2 and not paused:
                        _dp = ((_psi - live_prev_psi + math.pi) % (2 * math.pi) - math.pi) / _dt
                        live_dpsi = 0.4 * live_dpsi + 0.6 * _dp
                        if spd < 20 or (abs(p['latG']) < 0.15 and spd > 100 and (p['steer'] is None or abs(p['steer']) < 3)):
                            live_beta = 0.0
                        else:
                            live_beta = max(-0.6, min(0.6, live_beta * 0.99 + (live_dpsi - p['yaw']) * _dt))
                        sl = round(math.degrees(live_beta), 1)
                    else:
                        live_beta = 0.0; sl = 0.0      # 暫停/卡頓/首樣 → 歸零
                    live_prev_psi = _psi; live_prev_now = now
                LIVE['d'] = {
                    'spd': round(p['mps']*3.6, 1), 'gear': p['gear'], 'rpm': round(p['rpm']),
                    'thr': round(p['thr']/255*100), 'brk': round(p['brk']/255*100),
                    'latG': round(p['latG'], 3) if p['latG'] is not None else None,
                    'lonG': round(p['lonG'], 3) if p['lonG'] is not None else None,
                    'steer': round(p['steer'], 1) if p['steer'] is not None else None,
                    'yaw': round(p['yaw'], 3),
                    'tFL': round(p['tFL'], 1), 'tFR': round(p['tFR'], 1),
                    'tRL': round(p['tRL'], 1), 'tRR': round(p['tRR'], 1),
                    'lapt': round(lap_t, 2), 'best': best_time,
                    'rpmMin': p['rpmMin'], 'rpmMax': p['rpmMax'], 'slip': sl, 'delta': live_delta,
                    'fuel': round(p['fuel'], 1) if p['fuel'] is not None else None,
                    'fuelCap': round(p['fuelCap'], 1) if p['fuelCap'] is not None else None,
                    'lapsLeft': (round(p['fuel'] / fuel_per_lap, 1)
                                 if (fuel_per_lap and fuel_per_lap > 0 and p['fuel'] is not None) else None),
                    'avg': avg_lap, 'opt': opt_total, 'tcs': p['tcs'], 'asm': p['asm'], 'paused': p['paused'],
                    'sec': sec_live, 'secI': sec_idx,
                    'last': round(p['last'] / 1000.0, 3) if (p['last'] and p['last'] > 0) else None,
                    'lap': cur_lap if cur_lap else None,
                    'track': args.track, 'car': args.car,
                }

            rec_ok = (not paused) and (cur_lap is not None)
            if args.replay:
                rec_ok = rec_ok                       # 重播:不管在不在跑道、圈號 0 也錄
            else:
                rec_ok = rec_ok and on_track and cur_lap >= 1
            if rec_ok:
                t = lap_t
                if min_dt == 0.0 or (t - last_sample_t) >= min_dt:
                    last_sample_t = t
                    cur_car = p['car']
                    if px is not None:
                        lap_dist += math.hypot(p['x'] - px, p['z'] - pz)
                    px = p['x']; pz = p['z']
                    cur_pd.append(round(lap_dist, 1)); cur_pt.append(round(t, 3))
                    cur_ps.append(abs(p['steer']) if p['steer'] is not None else 0.0); cur_pv.append(p['mps'] * 3.6)
                    dlt = None
                    if REF['dist']:
                        _rt = ref_time(lap_dist)
                        dlt = round(t - _rt, 3) if _rt is not None else None
                    live_delta = dlt        # 給 live 看板用(下一次推送會帶上)
                    if REF['dist'] and dlt is not None and REF['dist'][-1] > 0:
                        if sec_b is None:
                            sec_b = sec_bounds(args.sec)
                        _si = 0 if lap_dist < sec_b[0] else (1 if lap_dist < sec_b[1] else 2)
                        while sec_idx < _si:                  # 過段界:定格該段盈虧
                            sec_vals[sec_idx] = dlt - sec_base
                            sec_base = dlt; sec_idx += 1
                        _disp = [None if v is None else round(v, 2) for v in sec_vals]
                        _disp[sec_idx] = round(dlt - sec_base, 2)   # 當前段即時值
                        sec_live = _disp
                    buf.append([
                        round(t, 3), round(p['mps'] * 3.6, 1),
                        round(p['thr'] / 255 * 100), round(p['brk'] / 255 * 100),
                        round(p['rpm']), p['gear'], round(p['x'], 2), round(p['z'], 2),
                        round(p['yaw'], 4),
                        round(p['latG'], 3) if p['latG'] is not None else None,
                        round(p['lonG'], 3) if p['lonG'] is not None else None,
                        round(p['steer'], 1) if p['steer'] is not None else None,
                        round(p['tFL'], 1), round(p['tFR'], 1),
                        round(p['tRL'], 1), round(p['tRR'], 1),
                        round(p['vx'], 2), round(p['vz'], 2),
                    ])
    except KeyboardInterrupt:
        print('\n■ 結束,關閉檔案…')
    finally:
        # 重播模式:把還在記憶體、尚未寫出的最後一段補寫(否則單圈 ghost 會消失)
        if args.replay and buf and len(buf) > 5:
            lt = round(lap_t, 3) if lap_t > 0 else 0.0
            flush_lap(lt, (lap_start - t0) if lap_start else 0.0, cur_lap or 0, cur_car)
            print(f'  ✓ 收工補寫最後一段:{len(buf)} 筆  (時長 {lt:.3f}s)')
        try: fh.close()
        except Exception: pass
        tag = '轉向+G 齊全' if fields[0] == 'rich' else '僅基本欄位'
        print(f'已存 {lap_count} 圈 → {out_path}   ({tag})')
        if fields[0] == 'basic':
            print('  ※ 沒收到大封包:若想要轉向/G,確認遊戲在前景,或改 --packet B / ~ 再試。')
        if best_time is not None:
            bm = int(best_time // 60)
            print(f'本次最佳圈:{bm}:{best_time - bm*60:06.3f}')
        sock.close()
        _restore_sleep(sleep_token)
        # 收工自動上傳整份原始檔(不篩選)到 GitHub;--no-push 可關閉
        if not args.no_push:
            note = f'{lap_count} 圈'
            if best_time is not None:
                bm = int(best_time // 60)
                note += f',best {bm}:{best_time - bm*60:06.3f}'
            _github_upload(out_path, args.repo, args.branch, args.dest_dir, note,
                           local_is_fresh=not append)
        # 接著順手更新世界排名 / 名次(條件齊全才跑;缺 token 或 requests 就略過)
        if not args.no_rank:
            _rank_update(args.repo, args.branch)

if __name__ == '__main__':
    main()
