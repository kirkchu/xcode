---
name: coreai-response-truncation
description: 診斷 Swift FoundationModels 搭配 CoreAILanguageModels 的回應截斷，並呼叫內附 Python 工具修正已知中文與 emoji 跨 token 的 UTF-8 解碼缺陷、接入本機 package 及驗證結果。
---

# CoreAI 回應截斷修正

使用內附 [scripts/fix_coreai.py](scripts/fix_coreai.py) 完成相容版本的修正，不重寫臨時 Python patch 或徒手修改 DerivedData。此目錄可獨立搬移；不要假設它的上一層就是目標專案。

## 定位工具與目標

1. 以目前讀取的 `SKILL.md` 所在目錄定位 `scripts/fix_coreai.py`，取得絕對路徑。
2. 從使用者指定的位置或目前工作區辨認目標 `.xcodeproj`。若有多個候選且無法從上下文判定，先釐清目標，不任選專案。
3. 所有工具呼叫都明確傳入 `--project` 絕對路徑，避免目前工作目錄不同而修正錯專案。
4. 需要 Python 3.9+ 與 macOS `plutil`；`--verify` 需要 `swiftc`。Python 無第三方相依套件，工具不自行下載 package。

## 呼叫 Python 工具

以下為命令格式；實際執行前，以已確認的絕對路徑取代示範值。若執行環境支援參數陣列，直接傳入各參數；透過 shell 時正確引用含空白的路徑。

先執行唯讀預覽：

```sh
python3 "/absolute/path/coreai-response-truncation/scripts/fix_coreai.py" --project "/absolute/path/OtherApp.xcodeproj" --dry-run
```

檢查退出碼、來源路徑、本機目的地與預計變更。使用者只要求診斷時，停在預覽並報告發現；使用者已要求修正時，預覽成功後繼續套用與驗證，不將預覽視為完成：

```sh
python3 "/absolute/path/coreai-response-truncation/scripts/fix_coreai.py" --project "/absolute/path/OtherApp.xcodeproj" --verify
```

- 若找不到該專案的 checkout，從其 package 狀態定位來源；用 `--package-source "/absolute/path/coreai-models"` 明確指定。自訂 DerivedData 或 workspace 建置常需要此參數，不借用另一專案的同名 checkout。
- 若診斷確認數字預算太低且屬於本次修正範圍，可加 `--response-file "OtherApp/ContentView.swift" --min-response-tokens 1024`。先將相同參數用於預覽。檔案相對於目標 `.xcodeproj` 所在目錄，可重複指定；不指定時不修改生成預算。
- 目的地預設為目標專案的 `Packages/coreai-models`。已有本機修正時沿用它，不拿不同來源覆蓋。
- `--dry-run` 不寫入；`--verify` 編譯並執行正式解碼 helper 的回歸測試，不是 App 推論測試。
- 若顯示 `Already patched; no files changed.`，表示無需再次修改；確認驗證結果後繼續必要的 App 驗證。

完整參數、路徑範例與備份還原方式見 [README.md](README.md)。

## 失敗與相容性

工具僅接受已知 baseline `df8119879f125ad1e2e4c6249c2cddded75c190a` 的 `respondVanilla` 內容或此工具的完整修正。非零退出碼代表失敗；讀取錯誤後解決具體原因，不繞過內容檢查強制替換。

- 缺少來源、版本不符、目的地衝突或不支援的預算運算式，先核對實際檔案。需要追查時讀 [references/diagnosis.md](references/diagnosis.md)。
- 工具會保存 `.coreai-patch-backups/時間識別碼/`；一般套用例外會嘗試還原。強制終止後先檢查檔案與備份，不假設已成功或已完全還原。
- 環境若限制寫入或編譯快取，使用執行工具的權限核准機制。既有修正授權不代表可繞過沙箱。

## 驗證與交付

1. 確認 Python 命令成功，並記錄 `--verify` 結果。
2. 使用目標專案實際的 scheme 與平台編譯，確認 log 指向修正後的本機 package。不要把其他專案的 scheme 硬套到目標專案。沒有程式碼變更且已有有效編譯證據時，不必重複建置。
3. 能執行模型時，以原 prompt 和較長的繁體中文 prompt 驗證。分別報告「解碼回歸通過」「App 編譯成功」「實際推論已驗證」，不得互相代替。
4. 說明變更位置、備份位置與剩餘限制。完整 token history 解碼增加長回答成本；U+FFFD 或 tokenizer 改寫已送出前綴仍可能暫停輸出，末尾不完整 UTF-8 不顯示。提高預算也不保證任意長度回答都能完成。

## 維護 SKILL 工具

搬移或修改此 SKILL 後，用絕對路徑執行隔離測試；測試依自身位置載入工具，不依賴外層專案：

```sh
python3 -B -m unittest discover -s "/absolute/path/coreai-response-truncation/tests" -p test_fix_coreai.py -v
```

測試包含首次套用、重複執行、來源保留、失敗還原與 Swift 解碼。另附 [tests/IncrementalTextDecoderRegression.swift](tests/IncrementalTextDecoderRegression.swift) 供獨立編譯檢查；工具的 `--verify` 已內嵌相同案例，正常使用不必重複編譯外部測試檔。
