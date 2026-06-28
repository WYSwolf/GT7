# GT7 Telemetry Tracker

Gran Turismo 7 個人賽車遙測分析系統，純前端靜態網頁，繁體中文介面。

## 專案結構

```
index.html          # 主頁：訓練追蹤系統，記錄賽事圈速與進度
telemetry.html      # 遙測分析：賽道地圖、速度/油門/煞車折線圖
demos/
  in-car-dashboard.html  # 即時儀表板展示（轉速燈、輪胎溫度、油量）
data.json           # 訓練記錄資料（手動維護）
telemetry/          # CSV 遙測原始資料，從 GT7 匯出
  *.csv             # 各場次遙測數據
```

## 技術棧

- 純 HTML/CSS/JS，無建置工具、無框架
- Chart.js 4.4 + chartjs-plugin-zoom（遙測圖表）
- PapaParse 5.4（CSV 解析）
- Hammer.js（觸控手勢）
- Google Fonts：Bebas Neue、JetBrains Mono、Noto Sans TC

## 設計系統

深色主題，主色調 `#0a0d12`，強調色 `#00ff88`（螢光綠）。CSS 變數定義在 `:root`，各頁面共用相同色盤。

## 遙測 CSV 格式

從 GT7 匯出的 CSV，欄位包含時間戳、速度、油門、煞車、轉向、引擎轉速、檔位、輪胎溫度等。`telemetry.html` 直接讀取並在瀏覽器解析。

完整的圖表說明、判讀方式、資料流與維護方式見 `README.md`。

## 注意事項

- 無伺服器端，所有資料處理在瀏覽器完成
- `data.json` 需手動更新以新增訓練記錄
- CSV 檔案命名慣例：精簡檔 `gt7-YYYY-MM-DD.csv`（dash，存各場 PB 圈）；原始 capture `gt7_YYYY-MM-DD.csv`（底線，gt7_capture.py 自動上傳）
- 目標設定慣例：各賽道目標一律以世界第一（WR）為基準 —— 🎯 主要 = WR +3%、🚀 進階 = WR +2%、🏁 衝刺 = WR +1%（規則存於 `meta.goalPolicy`）。更新 `meta.references.<carSlug>` 的 WR 後，需依此重算該賽道 `goals`；球門隨 WR 紀錄移動是預期行為，不要改成更寬鬆或個人化的門檻。
- 全球名次/WR/門檻可由 `gt7_rank.py` 自動抓：GT7 官方 API 給 WR/top100/top1000/你的名次；母體總人數從 `eventUrl` 指的 dg-edge 事件頁抓「Total players」。官方 API 的 `total` 只是榜上清單大小（存 `boardSize`，非母體），百分位一律用 dg-edge 母體當分母。`gt7_capture.py` 收工會順手跑（需 `GT7_JSESSIONID`+GitHub token，`--no-rank` 關閉）。沒有來源時不要引用舊名次。
  - **eventUrl/boardId 自動定位**：缺 `eventUrl` 時打 dg-edge player API，用「你的成績(timeMS)/賽道/車」比對自動補；缺 `boardId` 從 dg-edge 事件頁解出。對不出唯一就記 `eventUrlCandidates`、**不亂猜**——由 Claude 在處理當天 session 時列候選給 George 拍板再填。dg 還沒收錄的（第一次玩）配不到，去 `dg-edge.com/events` 反查。
  - **名次 fallback**：你的名次優先 GT 榜；活動結束/榜只回前段抓不到你時，改用 dg-edge `globalPosition`（標 `source:"dg-edge"`）。
- **更新分工（重要）**：`gt7_rank.py`（自動）只管 `leaderboards`(boardId/eventUrl/wr/top100/top1000/playerRank/totalPlayers)+`references.<car>.time`+`goals[].items`+`meta.lastUpdated`；**Claude（處理 capture CSV）管** `sessions[]`/`insights`/`actionItems`/slim CSV/**新賽道的 leaderboard 條目與 goal**/確認 `eventUrlCandidates`/`coachNotes`/`sectorCalibration`。rank 腳本**只更新已存在的 leaderboard key**——全新賽道要 Claude 先建條目。兩條管道獨立：只刷排名→自動跑即可，不用找 Claude；跑了新的一天→把 CSV 給 Claude 補 sessions 那層。

## 收到每日練習紀錄後的更新步驟

詳細邏輯在 `process-gt7` skill；固定流程如下（完整版見 `README.md` §6）：

1. 讀檔頭 `carcode`/Hz → 辨識賽道+車+賽事。
2. 切 session：**短衝刺日整天併為一場**（目前慣例，出現最快圈即一場）；舊式長 stint 日才用 >140s 間隔、≥5 圈為一場。
3. 閘門座標法算分段（`meta.sectorCalibration`）；MAD>2.5 判無效圈。
4. 算每場 best/avg/worst/opt/sectorBest/topSpeed/pbRl/laps[]，append 進 `sessions`，更新 PB 與 `meta.lastUpdated`。
5. 洞察只寫最新一場、清掉前一場；確保 `actionItems.<當前 trackKey>`（「下次訓練重點」）有內容。
6. WR 有更新就依 `goalPolicy` 重算該賽道 `goals`。
7. 寫精簡 CSV `telemetry/gt7-YYYY-MM-DD.csv`（各場 PB 圈、原生 Hz、無空行）。
8. 驗證（JSON parse、index 改動檢查 inline script、CSV 無空行）後 commit/push（合併到 `main`）。

> WR/門檻/名次（步驟 6 的 `goals` 與 leaderboards）平常由 `gt7_rank.py` 自動維護；Claude 處理時聚焦 `sessions` 那層，遇到 WR 有變才順手對齊 `goals`。新賽道記得先建 `meta.leaderboards.<key>`（填 `eventUrl` 或留空讓 rank 腳本配），rank 腳本才接得上。

## 未來分析方向（規劃中）

要做「擅長哪種車/賽道」的長期剖面，需先在資料補標籤：每個組合標上車種/傳動(MR/FR/4WD)/級別、賽道屬性(高速/技術彎、長度、順逆向)，再把各組合的 vs-WR% / 百分位 / 一致性依類別聚合，排出強弱。樣本夠了再開分析頁；屆時於 `sessions`/`meta` 補標籤即可。詳見 `README.md` §7。
