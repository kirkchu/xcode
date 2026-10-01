# CoreAI 回應截斷修正工具

[fix_coreai.py](scripts/fix_coreai.py) 用來修正 Swift 專案使用 `FoundationModels` 與 `CoreAILanguageModels` 時，中文或 emoji 跨越多個 token 後，後續回應無法輸出的已知 UTF-8 解碼問題。

Python 負責修改原始碼和 Xcode 套件設定。**套用後必須重新編譯 App 才會生效；App 執行時不需要 Python。** 這個工具可直接處理其他符合條件的 Xcode 專案。

## SKILL 目錄

```text
coreai-response-truncation/
├── SKILL.md
├── README.md
├── scripts/
│   └── fix_coreai.py
├── tests/
│   ├── test_fix_coreai.py
│   └── IncrementalTextDecoderRegression.swift
└── references/
    └── diagnosis.md
```

整個目錄可以搬到其他位置，工具和測試不依賴外層 Xcode 專案。SKILL 指示代理以自身位置定位 `scripts/fix_coreai.py`，並明確傳入目標專案路徑執行。若只需要命令列工具，也可單獨複製該 Python 檔案。

App 使用的本機 `Packages/coreai-models` 留在目標專案內，供 Xcode 編譯；它是套用結果，不是 SKILL 的依賴。

## 全域使用

此 SKILL 已安裝至 Codex 的全域 skills 目錄。開啟目標專案後，可直接將以下提示詞貼到 Codex 對話中；這些是給 Codex 的指令，不是送給 App 內模型的 prompt。

### 檢查並修正目前專案

```text
使用 $coreai-response-truncation 檢查並修正這個 Xcode 專案的 AI 回應截斷問題。
請呼叫 SKILL 內附的 Python 工具，先預覽變更；確認相容後套用修正並執行解碼回歸測試，再編譯專案驗證。
請說明修改檔案、備份位置，以及是否完成實際模型推論測試。
```

### 只診斷，不修改

```text
使用 $coreai-response-truncation 診斷這個 Xcode 專案的 AI 回應為什麼不完整。
請讀取實際使用的 package 原始碼，並執行 Python 工具的 --dry-run；不要修改檔案。
請區分 token 上限、UTF-8 解碼、thinking 分流與顯示層的問題，提供判斷依據。
```

### 指定其他專案與套件來源

將下列示範路徑換成實際位置；若要讓工具自動尋找套件，刪除套件來源那一行。

```text
使用 $coreai-response-truncation 修正以下 Xcode 專案的 AI 回應截斷問題。
專案路徑：/path/to/OtherApp.xcodeproj
套件來源：/path/to/coreai-models
請用內附 Python 工具先預覽，確認相容後套用並執行 --verify，再編譯專案。
```

### 修正並提高回應預算

Swift 檔案路徑相對於目標 .xcodeproj 所在目錄。

```text
使用 $coreai-response-truncation 修正這個 Xcode 專案的 AI 回應截斷問題。
請同時使用 --response-file OtherApp/ContentView.swift --min-response-tokens 1024，將該檔案支援的數字回應預算提高至至少 1024 tokens，保留原本更大的值。
請先預覽，確認相容後套用、執行解碼回歸測試並編譯驗證。
```

### 命令列呼叫

命令列也可從任意目錄呼叫工具：

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/skills/coreai-response-truncation/scripts/fix_coreai.py" \
  --project "/path/to/OtherApp.xcodeproj" \
  --dry-run
```

預覽成功後，將 `--dry-run` 換成 `--verify` 套用並驗證。若自訂 `CODEX_HOME`，以該目錄內的 `skills` 為準。

## 環境與適用範圍

- macOS、Python 3.9 以上，以及系統的 `plutil`。
- 執行 `--verify` 需要可用的 Xcode Swift 編譯器 `swiftc`。
- 目標 `.xcodeproj` 已引用 `coreai-models`，且有 `CoreAILM` product dependency。
- 已下載的套件原始碼，或可讀取的本機 package。工具不會自行下載套件。

不需要安裝第三方 Python 套件。可單獨複製 `fix_coreai.py` 到其他 Mac，Swift 修正與解碼回歸測試都已內嵌在程式內。

此工具針對 `apple/coreai-models` revision `df8119879f125ad1e2e4c6249c2cddded75c190a` 的已知 `respondVanilla` 實作。它檢查程式碼內容，而不是只看版本號；不相容或部分修改的實作會被拒絕。不同版本需要先人工核對，不是任意版本都能直接套用。

## 快速使用

以下命令在此 `README.md` 所在的 SKILL 目錄執行；目標專案位置由 `--project` 指定。請將示範路徑換成實際路徑；路徑有空白時保留引號。

### 1. 預覽變更

```bash
python3 scripts/fix_coreai.py --project "/path/to/OtherApp.xcodeproj" --dry-run
```

`--dry-run` 會檢查來源與相容性，列出預計修改，不寫入任何檔案。即使同時指定 `--verify`，預覽模式也不會執行測試。

也可指定只有一個 `.xcodeproj` 的資料夾：

```bash
python3 scripts/fix_coreai.py --project "/path/to/OtherApp" --dry-run
```

### 2. 套用並驗證解碼器

```bash
python3 scripts/fix_coreai.py --project "/path/to/OtherApp.xcodeproj" --verify
```

工具會建立專案內的本機 package、修正解碼邏輯、調整 Xcode 引用，並編譯執行 Swift 解碼回歸測試。若已完成相同修正，會顯示 `Already patched; no files changed.`，不重複寫入。

### 3. 重新編譯與測試 App

在 Xcode 開啟目標專案，重新 Build／Run，使用原本會截斷的 prompt 測試。確認 build log 使用目標專案內的 `Packages/coreai-models`。

`--verify` 驗證中文、emoji 等 token 解碼；它不編譯整個 App，也不執行模型推論。

## 手動指定套件來源

工具先使用專案已引用的本機 package；遠端引用則會尋找預設 DerivedData 內、屬於同一個專案的 checkout。

若尚未解析套件、使用自訂 DerivedData，或是透過 `.xcworkspace` 建置，可能需要手動指定：

```bash
python3 scripts/fix_coreai.py \
  --project "/path/to/OtherApp.xcodeproj" \
  --package-source "/path/to/coreai-models" \
  --verify
```

來源應是包含 `Package.swift`、`LICENSE` 與 `swift/` 的套件根目錄，不是模型匯出資料夾。

首次建立本機 package 時會複製必要的 Swift 套件檔案，不修改來源，也不複製模型權重或 Git 目錄。若專案已引用目的地內的本機 package，則直接維護該副本；此時不要另外指定不同來源覆蓋它。

## 同時提高回應 token 上限

解碼修正不會自動調整 App 的生成設定。如需提高上限，明確指定 Swift 檔案：

```bash
python3 scripts/fix_coreai.py \
  --project "/path/to/OtherApp.xcodeproj" \
  --response-file "OtherApp/ContentView.swift" \
  --min-response-tokens 1024 \
  --verify
```

`--response-file` 相對於目標 `.xcodeproj` 所在資料夾，不是 Python 工具所在資料夾。可以重複指定多個檔案。

支援數字常數與兩個分支都是數字的簡單三元運算式，例如：

```swift
// 修改前
maximumResponseTokens: 128
maximumResponseTokens: thinkingMode == .enabled ? 4096 : 128

// 使用 --min-response-tokens 1024 後
maximumResponseTokens: 1024
maximumResponseTokens: thinkingMode == .enabled ? 4096 : 1024
```

不降低原本較大的值。若使用變數或其他不支援的運算式，例如 `maximumResponseTokens: config.limit`，工具會在寫入前停止；可自行修改該設定，並移除 `--response-file` 只套用解碼修正。

token 數不是中文字數。提高預算能處理長回答碰到生成上限的情況，但單獨提高預算不能修復 UTF-8 解碼問題。

## 參數一覽

| 參數 | 用途 | 預設值 |
| --- | --- | --- |
| `--project` | `.xcodeproj`，或只有一個專案的資料夾 | 目前目錄 |
| `--package-source` | 手動指定 `coreai-models` 原始碼根目錄 | 自動尋找 |
| `--package-dir` | 目標專案內的本機 package 路徑 | `Packages/coreai-models` |
| `--response-file` | 要調高 token 預算的 Swift 檔案，可重複指定 | 不修改 App 預算 |
| `--min-response-tokens` | 指定檔案內數字預算的最低值，必須大於 0 | `1024` |
| `--dry-run` | 只檢查並預覽，不寫入 | 關閉 |
| `--verify` | 套用後或已套用時執行 Swift 解碼回歸測試 | 關閉 |
| `--help` | 顯示命令說明 | — |

`--package-dir` 必須位於目標專案資料夾內，且不能放進 `.xcodeproj` 裡。已存在但未被該專案引用的目的地會被拒絕，避免覆蓋其他檔案。

## 變更與備份

首次套用時會：

1. 建立 `Packages/coreai-models`，保留套件授權與來源說明。
2. 修正 `CoreAILanguageModel.swift`，新增 `IncrementalTextDecoder.swift`。
3. 將 `project.pbxproj` 的對應 package reference 改為本機路徑，保留 product 關聯。
4. 若有指定 `--response-file`，提高該檔案中的數字預算。
5. 保存原始檔備份與變更清單。

備份位於目標專案：

```text
.coreai-patch-backups/
└── 時間與識別碼/
    ├── manifest.json
    ├── OtherApp.xcodeproj/project.pbxproj
    └── …其他被修改檔案的原始版本
```

一般執行例外（包含此次套用的 `--verify` 失敗）會嘗試還原已修改檔案並移除此次新建的 package，備份仍保留。斷電、強制終止或還原期間的檔案系統錯誤，仍可能需要手動復原。

工具沒有 `--restore` 參數。需要手動復原時，依該次 `manifest.json` 核對變更，將備份檔放回對應位置；若套用後又有其他修改，先比較差異再還原。確認專案不再引用此次新建的 package 後，才處理該目錄。工具不直接修改 `Package.resolved`；後續由 Xcode 解析套件時可能更新它。

## 常見訊息

| 訊息／情況 | 處理方式 |
| --- | --- |
| 找不到唯一的 checkout | 先在 Xcode 解析套件，或傳入 `--package-source`。 |
| 資料夾中有多個 `.xcodeproj` | 用 `--project` 明確指定其中一個。 |
| `Unsupported or partially modified` | 實作不符已知版本；人工核對差異，不強制套用。 |
| 目的地已存在但未被引用 | 檢查目錄用途，或用 `--package-dir` 選擇新的專案內路徑。 |
| `unsupported token-budget expression` | 手動調整該運算式，再移除 `--response-file` 套用解碼修正。 |
| `swiftc` 或 `plutil` 無法執行 | 確認在 macOS 執行，並檢查 Xcode／開發工具設定。 |
| 修正後長回答仍在上限停止 | 檢查 token 預算、剩餘 context 與停止原因；解碼修正不移除生成限制。 |

## 測試與相關文件

在此 SKILL 目錄執行工具自身的隔離測試：

```bash
python3 -B -m unittest discover -s tests -p test_fix_coreai.py -v
```

測試使用暫存專案，不修改真實專案；涵蓋首次套用、再次執行、來源保留、備份、失敗還原、不相容版本與 Swift 解碼回歸。這組測試使用 SKILL 內的 `tests/test_fix_coreai.py`；單獨攜帶工具時仍可用內嵌的 `--verify`。

指定任一符合條件的 Xcode 專案實際位置即可執行：

```bash
python3 scripts/fix_coreai.py \
  --project "/path/to/ExampleApp/ExampleApp.xcodeproj" \
  --response-file ExampleApp/ContentView.swift \
  --verify
```

- [診斷與修正 SKILL](SKILL.md)
- [診斷原理與限制](references/diagnosis.md)
- [Python 工具測試](tests/test_fix_coreai.py)

目前解碼器保存完整 token history，以正確性為優先，長回答會增加解碼成本。遇到 U+FFFD 或 tokenizer 改寫已送出的前綴仍會暫停輸出，末尾不完整 UTF-8 不會顯示；這個修正不代表支援所有 tokenizer 行為。
