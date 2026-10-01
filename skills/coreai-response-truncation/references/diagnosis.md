# CoreAI 截斷問題診斷參考

以下路徑指目標專案，不是 SKILL 所在目錄。正常套用請依 [SKILL.md](../SKILL.md) 呼叫內附工具；遇到不相容版本或需追查其他截斷原因時，再使用本文件。

## 先辨認實際執行版本

1. 讀取專案的 Package.resolved；記錄套件 identity、revision 和相依 tokenizer 版本。
2. 核對 project.pbxproj 與 build log。本機 package 不一定出現在 Package.resolved 的 pins，不能只靠 lockfile 判斷來源。
3. 若仍使用遠端套件，從該專案的 DerivedData/SourcePackages 找 checkout，核對 workspace-state.json；不要選到其他專案的同名套件。
4. 讀取模型 bundle 的 metadata.json、tokenizer.json、tokenizer_config.json 和 chat_template.jinja。核對模型名稱、context 長度、decoder 類型、EOS 與 enable_thinking。
5. 保留使用者尚未提交的修改；使用 rg 限縮到該專案和相關 Sources，避免掃描整個 DerivedData。

## 區分停止生成與停止顯示

沿著 prompt → chat template → token generation → tokenizer.decode → thinking/tool parser → channel → response/UI 追蹤。

- **生成預算**：maximumResponseTokens 限制 token 數，不是中文字數。核對實際參數、套件預設值與剩餘 context。
- **停止條件**：核對 EOS、額外 stop tokens、取消、例外與底層停止原因。不要假設 response 一定公開 finishReason。
- **解碼卡住**：生成 token 數增加，但文字不增加，尤其剛出現拆分中文字後，檢查 UTF-8 緩衝與 U+FFFD 分支。
- **thinking 分流**：reasoning 可能不進 response.content。檢查 transcript 與 template，不能把隱藏 reasoning 當作沒有生成。
- **顯示問題**：response.content 和 rawContent 都已經過套件處理，rawContent 不等於原始生成 token；比較它們不能排除解碼層遺失。

能取得實際 token 紀錄時優先使用；否則以模型詞彙表中的合法 token 序列重現，明確標示為演算法測試，不能聲稱已重現使用者那次推論。

## 已知 UTF-8 缺陷與修正原則

本案例原始來源為 apple/coreai-models revision `df8119879f125ad1e2e4c6249c2cddded75c190a`。

原 respondVanilla 每次成功解碼後只保留最後一個 token。若「頸」由兩個 token 組成，最後 token 僅含 UTF-8 尾端 byte `B8`；下一次解碼失去前導 bytes `E9 A0`，得到 `�鹿`。程式看到 U+FFFD 就 continue，而缺少的前導 bytes 已被丟掉，因此後續回答一直無法送出。

代表性重現：完整 token 序列可解碼為「長頸鹿的英文是 giraffe。」，舊流程只輸出「長頸」。增加 maximumResponseTokens 無法修復此缺陷。

維護目前修正時遵守以下條件：

- 保存完整 token history，解碼成功後才更新「已送出的文字」；未完成的 UTF-8 不得推進此基準。
- 將完整解碼結果與已送出的 UTF-8 byte prefix 比較，只送出新增部分。不要使用 Character 數切割，後續 combining mark 或 emoji modifier 可能延伸前一個 grapheme。
- 沿用原本 thinking/tool parser、EOS 與 usage 管線；不要繞過 parser 直接顯示全部文字。
- 後續若改成有界 buffer，必須保留完整 UTF-8 邊界和 tokenizer 所需上下文；不得恢復成無條件只保留最後 token。

目前全量解碼是正確性優先的修正：總解碼工作量可能隨長度呈平方成長。現行 helper 遇到任何 U+FFFD 或非單調 prefix 會暫停輸出；合法的替代字元、tokenizer cleanup 改寫前文仍需另行設計與測試，不能宣稱支援所有 tokenizer。結束時不顯示未完成 UTF-8。

## 讓修正持續生效

若本機修正版已存在，直接維護，不重新複製覆蓋。若需要從 checkout 建立本機版本：

1. 使用已核對 revision 的來源，保留 Swift package 所需檔案、LICENSE 與來源紀錄；不複製 .git、build cache 或模型權重。
2. 放入專案的 Packages/coreai-models。複製檔案可能保留唯讀權限，只調整需要編輯的副本。
3. 將 Xcode package reference 接到本機路徑，保留 CoreAILM product dependency；重新解析後檢查 lockfile，不手工編造 revision。
4. 編譯 log 必須顯示本機 package 路徑與修正檔案實際參與編譯。只修改 DerivedData checkout 不算持久修復。
5. 在 LOCAL_PATCH.md 記錄來源、修正原因、限制與驗證方式；切回上游前先確認上游含等效修正。

本案例將非思考模式預算從 128 改為 1024，思考模式維持 4096。這是專案設定範例，其他任務應依 context、預期回答長度與延遲選擇，不保證任何長度都能完成。

