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

## 注意事項

- 無伺服器端，所有資料處理在瀏覽器完成
- `data.json` 需手動更新以新增訓練記錄
- CSV 檔案命名慣例：`gt7-YYYY-MM-DD.csv`
- 目標設定慣例：各賽道目標一律以世界第一（WR）為基準 —— 🎯 主要 = WR +3%、🚀 進階 = WR +2%、🏁 衝刺 = WR +1%（規則存於 `meta.goalPolicy`）。更新 `meta.references.<carSlug>` 的 WR 後，需依此重算該賽道 `goals`；球門隨 WR 紀錄移動是預期行為，不要改成更寬鬆或個人化的門檻。
