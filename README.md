# GT7 Telemetry Tracker

Gran Turismo 7 個人賽車遙測分析系統 —— 純前端靜態網頁,繁體中文介面。
線上版:**https://wyswolf.github.io/GT7/**

把每天在 PS5 跑的時間競賽(time trial)遙測,整理成「進度追蹤 + 圈速/分段/穩定度分析」的儀表板。

---

## 1. 專案結構

```
index.html            # 主網站(總覽 / 目標 / 紀錄 / 各賽道詳細頁 / 遊戲)
telemetry.html        # 單圈遙測檢視(賽道地圖、速度/油門/煞車、WR ghost 比對)
data.json             # 所有訓練資料 + 設定(見 §5)
telemetry/
  gt7-YYYY-MM-DD.csv   # 每日「精簡 CSV」:只存當天各場次的 PB 圈(原生 Hz)
  gt7_YYYY-MM-DD.csv   # 原始 capture(由 gt7_capture.py 自動上傳,未篩選)
  wr-<carSlug>.csv     # 世界紀錄 ghost(逐彎教練用)
gt7_capture.py        # PS5 UDP 遙測擷取;收工自動上傳原始檔到 telemetry/(見 §4)
demos/in-car-dashboard.html  # 即時儀表板展示
```

技術棧:純 HTML/CSS/JS(無建置工具)、Chart.js 4.4 + chartjs-plugin-zoom、PapaParse、Hammer.js。
設計:深色主題,底色 `#0a0d12`、強調色螢光綠 `#00ff88`,CSS 變數定義在 `:root`。

---

## 2. 圖表介紹與判讀

### 總覽頁
- **賽道卡片**:每條賽道一張,最近練的排最上面。顯示
  PB、**+x% vs WR**(≤1% 變綠)、獎牌、全球名次/百分位、極速、下一階目標進度、
  以及 **PB 走勢 sparkline**(每日一點,越新越右)。
- **最新洞察**:最近一場的重點觀察(每場更新,只留最新)。

### 目標頁
- 目標一律以**世界紀錄(WR)為基準**:🎯 主要 = WR +3%、🚀 進階 = WR +2%、🏁 衝刺 = WR +1%。
  PB 達到門檻就標 ✓。WR 紀錄更新時門檻會跟著前移(預期行為)。
- **下次訓練重點**:自動顯示**當前賽道**的行動項目。

### 紀錄頁(都以「日」為單位,X 軸 Day 1/2/3…)
- **vs 世界紀錄**:每天最佳距 WR %,越低越接近 WR。
- **一致性**:每天「中位 − 最佳」秒,越低越穩。
- 配色:**最近 3 條線**用鮮明色(綠/橘/藍),更舊的收斂成中性灰、且越舊越淡 —— 焦點永遠在近期。

### 各賽道詳細頁(重點分析都在這)
- **圈速追蹤**:每圈一點(🟢最速 / 🟠中位 / 🔵有效 / ⚪無效),含 **PB / OPT 參考線 + 趨勢線**(虛線=線性回歸,往下=越來越快)。
- **各分段穩定性軌跡**:每天一條軌跡,X=該段距其最佳、Y=該段中位−最佳。
  全圈=粗實線,S1/S2/S3=細虛線(不同樣式),線身**綠進步/橘退步**、越舊越淡,末端節點分色。
  **▶ 播放**可重播時間順序;**點任一條/圖例可隔離**(其他淡出)。
- **速度 × 分段均衡 象限**(每圈一點):
  - X = 該圈速度 vs 平均(σ,**越左越快**)
  - Y = 各分段之間的落差(σ,**越低越均衡**)
  - 四象限:**全段都快 / 靠單段快 / 全段偏慢 / 單段拖累**。
    判讀:慢圈多落在「單段拖累」→ 代表掉速通常是**某一段崩**而非全面變慢;快圈集中在「全段都快」→ 全面均衡的快。
  - 最新一天=綠,其餘藍漸層、越舊越淡。
- **圈速分布**:圈速直方圖,**按日堆疊**(最新日突出綠、其餘藍漸層)。
- **時間損失來源**:各分段相對最佳的累積損失(圓餅),看時間主要丟在哪一段。
- **各分段穩定性**(折線):每天每段「中位−最佳」,**點線可隔離**。S1藍/S2紅/S3黃 對齊圓餅圖。
- **Sector 損失分解 / 逐圈詳細數據**:逐圈分段堆疊與表格。

> 判讀心法:**速度看 X(vs WR / vs 平均)、穩定看 Y(中位−最佳 / 分段落差)。理想方向是往「快且穩」移動。**
> 多數圖以「日」為單位,因為穩定度需要一群圈才算得出來。

---

## 3. 賽事與目標慣例

- 目標門檻 = WR ×(1 + 3% / 2% / 1%),規則存於 `meta.goalPolicy`。
  **更新某車 WR(`meta.references.<carSlug>.time`)後,要依此重算該賽道 `goals`。**
- 全站「vs WR %」都從 `meta.references.<carSlug>` 即時計算 —— 改這一個欄位,所有卡片/圖表跟著更新。
- 全球名次/WR/門檻可用 `gt7_rank.py` **自動抓取**(見 §4):GT7 官方 API 給
  WR / top100 / top1000 / 你的名次,**母體總人數**從 `eventUrl` 指的 dg-edge 事件頁抓
  「Total players」。官方 API 的 `total` 只是榜上回傳的清單大小(`boardSize`,非母體),
  百分位一律用 dg-edge 母體當分母。沒有來源時**不引用舊名次**。

---

## 4. 資料怎麼進來(維護方式)

```
PS5 ──UDP──> gt7_capture.py ──(收工自動上傳)──> telemetry/gt7_YYYY-MM-DD.csv (原始,未篩選)
                                                       │
                                      George 把當天的檔給 Claude
                                                       ▼
                              Claude 篩選 → data.json + telemetry/gt7-YYYY-MM-DD.csv(精簡)
                                                       ▼
                                      commit/push → GitHub Pages 約 30–60s 生效
```

- **自動上傳**:`gt7_capture.py` 收工(Ctrl+C)後,若環境有 `GT7_GITHUB_TOKEN`(或 `GITHUB_TOKEN`),
  會用 GitHub API 把**整份原始 CSV(不篩選)**上傳到 `telemetry/` → `main`。`--no-push` 可關閉。
  同日多次跑會合併成同一檔;若本機檔被刪/換機,會另存時間後綴避免覆蓋。
- **收工順手更新名次**:收工會 import `gt7_rank.py`,自動更新 leaderboards / WR / 門檻 / 你的名次並
  **推回 GitHub 的 `data.json`**(`push=True`,從遠端取最新版再改,不會蓋掉 Claude 的編輯)。`--no-rank` 關閉。
  - **全自動定位**:缺 `eventUrl` 會打 dg-edge player API,用「你的成績(timeMS)/賽道/車」比對自動補;
    缺 `boardId` 會從 dg-edge 事件頁解出;對不出唯一就記 `eventUrlCandidates`、不亂猜(等對話確認)。
  - **名次算法**:WR/門檻用 GT 官方榜(`result.total` 其實是**頁數**,母體≈pages×100);
    你的名次=用**目前最快時間**(PB/sessions 較快者)回 GT 排序榜**二分搜尋它排第幾**
    (`source:"gt(by-time)"`)——不管成績有沒有同步、深淺皆準,進行中活動 #5000+ 也行;
    dg 舊名次只當二分起點加速,連時間都算不出時才退用 dg-edge `globalPosition`。百分位分母用 dg-edge 母體。
  - **認證(自動續期)**:預設**自動從瀏覽器讀 JSESSIONID**(`pip install browser-cookie3`,
    保持登入 gran-turismo.com 即可,不必手動換)。可用 `GT7_BROWSER` 指定瀏覽器;
    也可改設 `GT7_JSESSIONID` / `GT7_GT_TOKEN` 手動給。
  - 單獨跑:`python gt7_rank.py --browser --dry`(驗證)/ `--browser --push`(寫回 repo);
    `--my-events` 列出你 dg-edge 上所有場次。認證細節見 `gt7_rank.py` 檔頭。
    **須在自家網路跑**(Sony / dg-edge 會擋機房 IP)。
    ⚠ 手動跑**不帶 `--push` 會寫本機 data.json**,且 `locate_data` 會就近抓 cwd 的 `data.json`——
    別在 `telemetry/` 留多餘副本,要寫本機請從 repo 根目錄跑或用 `--data`。收工自動流程用 `push=True`,不受此影響。
- **精簡 CSV**:`telemetry/gt7-YYYY-MM-DD.csv` 只存當天各場次的 PB 圈(原生 Hz、無空行),
  檔名對應 `data.json` 各 session 的 `csv` 欄位,供 `telemetry.html` 檢視。

### 分工:rank 腳本(自動) vs Claude(處理遙測)

兩條更新管道**互相獨立、各管一塊**:

| 管道 | 觸發 | 負責的欄位 |
|---|---|---|
| **`gt7_rank.py`(自動)** | 收工 / 手動 `--push` | `leaderboards`(boardId · eventUrl · wr · top100 · top1000 · playerRank · totalPlayers)、`references.<car>.time`、`goals[].items`(依 goalPolicy 重算)、`meta.lastUpdated` |
| **Claude(處理 capture CSV)** | George 上傳 `gt7_*.csv` | `sessions[]`(best/avg/worst/opt/sectorBest/topSpeed/laps[])、`insights`、`actionItems`、slim CSV、**新賽道的 leaderboard 條目+goal**、確認 `eventUrlCandidates`、`coachNotes`/`sectorCalibration`/goal 的 `eventEnd`·`priority` |

- **只想刷新排名/WR/門檻** → 收工自動跑就完整,**不用找 Claude**。
- **跑了新的一天** → 把 CSV 給 Claude 做 `sessions` 那層分析;WR/名次那塊由 rank 腳本負責。
- rank 腳本**只更新已存在的 leaderboard key**:全新賽道要 Claude 先建條目,之後它才能自動補 boardId/eventUrl/名次。
- **`--replay`(錄 WR ghost)隱含 `--no-rank`**:重播跟你的排名無關,收工只上傳 `wr_` ghost、不刷名次。那份原始 ghost 要變成 `telemetry.html` 用的 `wr-<carSlug>.csv` 仍需 Claude 整理。

---

## 5. data.json 結構

```
{
  "meta": {
    "lastUpdated", "driver", "schema",
    "references":   { "<carSlug>": { time, displayTime, label, note, [csv] } },  // 各車 WR
    "leaderboards": { "<trackKey>__<carSlug>": { totalPlayers, top100, top1000, medals, playerRank... } },
    "sectorCalibration": { "<trackKey>": { 閘門座標 G1/G2 或距離分數 } },
    "coachNotes":   { "<trackKey>": { 逐彎教練註記 } },
    "goalPolicy":   { basis:"wr", tiers:[+3%/+2%/+1%] }
  },
  "goals":       [ { trackKey, car, priority, eventEnd, baseline, items:[{label,target,...}] } ],
  "actionItems": { "<trackKey>": [ {title, desc} ] },   // 「下次訓練重點」,依當前賽道顯示
  "sessions":    [ { date, trackKey, carSlug, carClass, tire, mode, best, avg, worst, opt,
                     sectorBest, topSpeed, pbRl, laps:[{lap,s1,s2,s3,total,invalid,note}], insights:[] } ]
}
```

時間一律存秒(1:32.801 → 92.801)。

---

## 6. Claude 收到每日練習紀錄後的更新步驟

> 觸發:George 上傳原始 capture(`gt7_YYYY-MM-DD.csv`)或說「處理今天的訓練紀錄」。
> (詳細處理邏輯在 `process-gt7` skill;以下是固定流程)

1. **辨識組合**:讀檔頭 `carcode` / Hz,對照出賽道 + 車 + 賽事(dg-edge 活動)。
2. **切 session**:
   - **短衝刺日(目前慣例)**:整天合併成**一場 session**(出現最快圈即視為一場)。
   - 舊式長 stint 日:以 >140s 間隔分段,≥5 圈的 stint 才算一場。
3. **算分段**:用 `meta.sectorCalibration` 的閘門座標法;無校正的賽道用等距三等分並標記。
4. **無效圈**:MAD 離群(>2.5)判定(GT7 紅圈也算)。
5. **每場指標**:best / avg / worst / opt(各段最佳加總) / sectorBest / topSpeed / pbRl / laps[]。
6. **更新 data.json**:append 新 session、更新 PB 與 `meta.lastUpdated`。
7. **洞察**:寫在**最新一場**,清掉前一場的(避免堆積)。
8. **下次訓練重點**:確保 `actionItems.<當前 trackKey>` 有對應內容。
9. **目標**:若 WR 有更新,依 `goalPolicy` 重算該賽道 `goals`。
10. **精簡 CSV**:寫 `telemetry/gt7-YYYY-MM-DD.csv`(各場 PB 圈、原生 Hz、無空行)。
11. **驗證**:`node -e "JSON.parse(...)"`、index 改動則檢查 inline script、精簡 CSV 無空行。
12. **commit + push**(本專案 = 合併到 `main`),GitHub Pages 約 30–60s 生效。

---

## 7. 未來分析方向(規劃中)

**已上線**:「**強弱**」分頁 —— 所有賽道×車的 vs-WR% / 百分位 / 一致性排序表(點欄位排序、綠強紅弱),
會隨組合**自動長大**。這是地基。

**下一步:按類別聚合的「我擅長什麼」剖面**(例「你開 MR/Gr.3 較快」「高速賽道較吃香」)——
注意這**不會隨資料變多自動出現**,需要 (a) 標籤齊全 + (b) 開聚合頁。

- **標籤(每次建檔順手補,定義見 `CLAUDE.md`)**:
  - `meta.carTags.<carSlug>` = `{ class(Gr.1~4/Gr.B/road…), drivetrain(MR/FR/FF/RR/4WD) }`
  - `meta.trackTags.<trackKey>` = `{ profile(high-speed/technical/mixed), lengthKm, dir(cw/ccw) }`
- **呈現**:把各組合的 vs-WR%/百分位/一致性依 carTags/trackTags 聚合,排成強弱排行/雷達。
- **時機**:每個類別累積 **≥3~4 個組合**才看得出傾向;到時 George 說一聲,Claude 開頁(標籤已備齊,不必回頭補標)。

---

## 注意事項

- 無伺服器端,全部在瀏覽器處理;`data.json` 由 Claude 維護後 push。
- 只說資料有的數字,不捏造;推估值(估算名次、距離分段)會標明為估計。
- `meta` 為**累加/保留**:新增 session 時保留既有 leaderboards / references / 校正 / 教練註記。
