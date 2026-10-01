#!/usr/bin/env python3
"""Portable, offline patcher for the known coreai-models UTF-8 truncation bug.

Copy this single file to another Mac; no third-party Python dependencies.
Use --dry-run first; --verify needs the Xcode Swift compiler.
"""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import uuid

BASELINE_REVISION = 'df8119879f125ad1e2e4c6249c2cddded75c190a'

MODEL_REL = 'swift/Sources/CoreAILanguageModels/LanguageModel/CoreAILanguageModel.swift'

HELPER_REL = 'swift/Sources/CoreAILanguageModels/LanguageModel/IncrementalTextDecoder.swift'

ORIGINAL_METHOD = r"""        private func respondVanilla(
            engine: any InferenceEngine,
            model: CoreAILanguageModel,
            promptTokens: [Int],
            samplingConfig: SamplingConfiguration,
            maxTokens: Int,
            channel: LanguageModelExecutorGenerationChannel
        ) async throws {
            let tokenizer = model.tokenizer
            let tokenStream = try await engine.generate(
                with: promptTokens.map(Int32.init),
                samplingConfiguration: samplingConfig,
                inferenceOptions: InferenceOptions(maxTokens: maxTokens)
            )

            // All EOS-like tokens: the tokenizer's main EOS plus any additional
            // stop tokens from tokenizer_config.json (e.g. Gemma's <end_of_turn>).
            var eosTokens = Set<Int32>()
            if let id = tokenizer.eosTokenId { eosTokens.insert(Int32(id)) }
            eosTokens.formUnion(model.additionalEosTokenIds)
            // Incremental-decode buffer. After a clean emit, one token is
            // retained as context for the next step (see below). During a
            // multi-byte sequence that hasn't decoded cleanly yet, multiple
            // tokens accumulate until the sequence is complete. In the steady
            // state the buffer holds at most 2 tokens, so tokenizer.decode
            // is O(1) per step.
            var pendingTokens: [Int32] = []
            var previousDecodedText: String = ""
            var tokenStep: Int = 0
            // Segments the decoded stream into `.text` and `.reasoning`
            // events on the fly. Reasoning content (model's chain-of-thought
            // emitted inside the configured open/close markers) is routed
            // to a top-level `.reasoning(...)` channel event so it lands as
            // its own `Transcript.Reasoning` entry, not mixed into the
            // user-facing `Transcript.Response`. Markers were resolved at
            // model init from the tokenizer's known token ids.
            var thinkParser = ThinkTagParser(format: model.thinkingFormat)
            // Routes tool call markup to .toolCalls(...) channel events.
            // nil when the model's tokenizer has no tool call tokens.
            var toolCallParser: ToolCallParser? = model.toolCallDetection.map {
                ToolCallParser(openMarker: $0.openMarker, closeMarker: $0.closeMarker, format: $0.format)
            }
            var generatedTokenCount: Int = 0
            var reasoningTokenCount: Int = 0

            for try await output in tokenStream {
                let token = output.tokenId
                if eosTokens.contains(token) {
                    tokenStream.setStopReason(.eos)
                    break
                }

                pendingTokens.append(token)
                tokenStep += 1
                generatedTokenCount += 1

                let decodeSpan = InstrumentsProfiler.beginDecode(step: tokenStep)
                let decodedText = tokenizer.decode(tokens: pendingTokens.map { Int($0) })
                decodeSpan.end()

                let common = decodedText.commonPrefix(with: previousDecodedText)
                let delta = String(decodedText.dropFirst(common.count))
                // Check for replacement char on the full `decodedText`, not on
                // `delta`. Some tokenizers emit one U+FFFD per attempted decode
                // of an incomplete multi-byte sequence (rather than one per
                // bad byte), so two consecutive partial tokens can produce
                // identical "\u{FFFD}" strings — making `delta` empty and
                // hiding the still-incomplete state. Checking `decodedText`
                // catches that case.
                let hasReplacementChar = decodedText.unicodeScalars.contains { $0 == "\u{FFFD}" }

                if hasReplacementChar {
                    // UTF-8 bytes don't form a clean character yet. Hold the
                    // token and wait for the next iteration to extend the
                    // buffer; don't drop or advance.
                    await channel.send(
                        .response(action: .appendText("", tokenCount: 1))
                    )
                    previousDecodedText = decodedText
                    continue
                }

                for event in thinkParser.consume(delta) {
                    if case .reasoning = event { reasoningTokenCount += 1 }
                    await dispatch(event: event, toolCallParser: &toolCallParser, channel: channel)
                }

                // Retain the last token as O(1) context for the next decode.
                // SentencePiece needs at least one prior token to infer the leading
                // ▁ (space) on the following token; clearing to empty decodes each
                // new token in isolation and drops inter-word spaces.
                // Keeping one token bounds re-decode cost to 2 tokens per step.
                // Safe for all supported tokenizers: decode([last]) is a prefix of
                // decode([last, next]) when addPrefixSpace=true (Mistral, Llama, Qwen)
                // and for ByteLevel tokenizers (GPT-2 style) where spaces are direct bytes.
                if let last = pendingTokens.last {
                    pendingTokens = [last]
                    previousDecodedText = tokenizer.decode(tokens: [Int(last)])
                } else {
                    pendingTokens.removeAll(keepingCapacity: true)
                    previousDecodedText = ""
                }
            }

            // Flush parsers — drains any content held back waiting for a marker.
            // Without this, content right at the EOS boundary (or inside an
            // unclosed block) would be lost.
            for event in thinkParser.flush() {
                await dispatch(event: event, toolCallParser: &toolCallParser, channel: channel)
            }
            if var tcp = toolCallParser {
                for event in tcp.flush() {
                    await dispatchToolCall(for: event, channel: channel)
                }
                toolCallParser = tcp
            }

            await channel.send(
                .response(
                    action: .updateUsage(
                        input: .init(totalTokenCount: promptTokens.count, cachedTokenCount: 0),
                        output: .init(
                            totalTokenCount: generatedTokenCount,
                            reasoningTokenCount: reasoningTokenCount
                        )
                    )))

            // Yield to let the engine's tokenSequence Task finish cleanup
            // (putBackEngine, state reset, etc.) before the next respond().
            await Task.yield()
        }

"""

FIXED_METHOD = r"""        private func respondVanilla(
            engine: any InferenceEngine,
            model: CoreAILanguageModel,
            promptTokens: [Int],
            samplingConfig: SamplingConfiguration,
            maxTokens: Int,
            channel: LanguageModelExecutorGenerationChannel
        ) async throws {
            let tokenizer = model.tokenizer
            let tokenStream = try await engine.generate(
                with: promptTokens.map(Int32.init),
                samplingConfiguration: samplingConfig,
                inferenceOptions: InferenceOptions(maxTokens: maxTokens)
            )

            // All EOS-like tokens: the tokenizer's main EOS plus any additional
            // stop tokens from tokenizer_config.json (e.g. Gemma's <end_of_turn>).
            var eosTokens = Set<Int32>()
            if let id = tokenizer.eosTokenId { eosTokens.insert(Int32(id)) }
            eosTokens.formUnion(model.additionalEosTokenIds)
            // Preserve leading bytes of characters split across tokens.
            var textDecoder = IncrementalTextDecoder()
            var tokenStep: Int = 0
            // Segments the decoded stream into `.text` and `.reasoning`
            // events on the fly. Reasoning content (model's chain-of-thought
            // emitted inside the configured open/close markers) is routed
            // to a top-level `.reasoning(...)` channel event so it lands as
            // its own `Transcript.Reasoning` entry, not mixed into the
            // user-facing `Transcript.Response`. Markers were resolved at
            // model init from the tokenizer's known token ids.
            var thinkParser = ThinkTagParser(format: model.thinkingFormat)
            // Routes tool call markup to .toolCalls(...) channel events.
            // nil when the model's tokenizer has no tool call tokens.
            var toolCallParser: ToolCallParser? = model.toolCallDetection.map {
                ToolCallParser(openMarker: $0.openMarker, closeMarker: $0.closeMarker, format: $0.format)
            }
            var generatedTokenCount: Int = 0
            var reasoningTokenCount: Int = 0

            for try await output in tokenStream {
                let token = output.tokenId
                if eosTokens.contains(token) {
                    tokenStream.setStopReason(.eos)
                    break
                }

                tokenStep += 1
                generatedTokenCount += 1

                let decodeSpan = InstrumentsProfiler.beginDecode(step: tokenStep)
                let delta = textDecoder.append(Int(token)) {
                    tokenizer.decode(tokens: $0)
                }
                decodeSpan.end()

                guard let delta else {
                    await channel.send(
                        .response(action: .appendText("", tokenCount: 1))
                    )
                    continue
                }

                for event in thinkParser.consume(delta) {
                    if case .reasoning = event { reasoningTokenCount += 1 }
                    await dispatch(event: event, toolCallParser: &toolCallParser, channel: channel)
                }


            }

            // Flush parsers — drains any content held back waiting for a marker.
            // Without this, content right at the EOS boundary (or inside an
            // unclosed block) would be lost.
            for event in thinkParser.flush() {
                await dispatch(event: event, toolCallParser: &toolCallParser, channel: channel)
            }
            if var tcp = toolCallParser {
                for event in tcp.flush() {
                    await dispatchToolCall(for: event, channel: channel)
                }
                toolCallParser = tcp
            }

            await channel.send(
                .response(
                    action: .updateUsage(
                        input: .init(totalTokenCount: promptTokens.count, cachedTokenCount: 0),
                        output: .init(
                            totalTokenCount: generatedTokenCount,
                            reasoningTokenCount: reasoningTokenCount
                        )
                    )))

            // Yield to let the engine's tokenSequence Task finish cleanup
            // (putBackEngine, state reset, etc.) before the next respond().
            await Task.yield()
        }

"""

HELPER = r"""// Copyright 2026 Apple Inc.
// Use of this source code is governed by the BSD-3-clause LICENSE file.

/// Preserves token context across UTF-8 boundaries. Partial decodes never
/// advance the emitted prefix. Full-history decoding favors correctness
/// over constant work per token.
struct IncrementalTextDecoder {
    private var tokens: [Int] = []
    private var emittedText = ""

    mutating func append(_ token: Int, decode: ([Int]) -> String) -> String? {
        tokens.append(token)
        let decoded = decode(tokens)
        guard !decoded.unicodeScalars.contains(where: { $0.value == 0xFFFD }) else {
            return nil
        }
        // A later token can extend a grapheme (combining marks or emoji).
        // Compare UTF-8 prefixes instead of Character counts.
        guard decoded.utf8.starts(with: emittedText.utf8) else { return nil }
        let delta = String(decoding: decoded.utf8.dropFirst(emittedText.utf8.count), as: UTF8.self)
        emittedText = decoded
        return delta
    }
}
"""

REGRESSION = r"""import Foundation

@main
struct IncrementalTextDecoderRegression {
    static func main() {
        // Qwen fragments: 長 / 頸 (two tokens) / 鹿 / continuation.
        let fragments: [[UInt8]] = [Array("長".utf8), [0xE9, 0xA0], [0xB8], Array("鹿".utf8), Array("的英文是 giraffe。".utf8)]
        var decoder = IncrementalTextDecoder()
        var result = ""
        for index in fragments.indices {
            result += decoder.append(index) { ids in
                String(decoding: ids.flatMap { fragments[$0] }, as: UTF8.self)
            } ?? ""
        }
        precondition(result == "長頸鹿的英文是 giraffe。", result)
        for expected in ["長頸鹿的英文是 giraffe。", "Hello world!", "你好🙂，完成。", "e\u{301} 👍🏽 👩‍💻 done", "<think>中文思考</think>答案"] {
            var decoder = IncrementalTextDecoder()
            var output = ""
            for byte in expected.utf8 {
                output += decoder.append(Int(byte)) { ids in
                    String(decoding: ids.map { UInt8($0) }, as: UTF8.self)
                } ?? ""
            }
            precondition(output == expected, "Expected \(expected), got \(output)")
        }
        var partial = IncrementalTextDecoder()
        precondition(partial.append(65) { String(decoding: $0.map { UInt8($0) }, as: UTF8.self) } == "A")
        precondition(partial.append(0xE9) { String(decoding: $0.map { UInt8($0) }, as: UTF8.self) } == nil)
        print("PASS: Qwen split tokens, Chinese, ASCII, emoji, combining marks, reasoning markers, incomplete UTF-8")
    }
}
"""

class PatchError(Exception):
    pass


def read(path):
    return path.read_text(encoding="utf-8")


def project_path(value):
    path = Path(value).expanduser().resolve()
    if path.is_dir() and path.suffix != ".xcodeproj":
        choices = sorted(path.glob("*.xcodeproj"))
        if len(choices) != 1:
            raise PatchError("Specify one .xcodeproj; the directory has zero or multiple projects.")
        path = choices[0]
    if path.suffix != ".xcodeproj" or not (path / "project.pbxproj").is_file():
        raise PatchError("--project must be an existing .xcodeproj or its containing directory.")
    return path


def parse_project(path):
    result = subprocess.run(["plutil", "-convert", "json", "-o", "-", str(path)],
                            capture_output=True, text=True)
    if result.returncode:
        raise PatchError("Cannot parse Xcode project: " + result.stderr + result.stdout)
    return json.loads(result.stdout)


def reference(document, root):
    objects = document["objects"]
    project = objects[document["rootObject"]]
    matches = []
    for identity in project.get("packageReferences", []):
        obj = objects[identity]
        if obj.get("isa") == "XCRemoteSwiftPackageReference":
            url = obj.get("repositoryURL", "").rstrip("/").removesuffix(".git")
            if url.endswith("/coreai-models"):
                matches.append((identity, obj, None))
        elif obj.get("isa") == "XCLocalSwiftPackageReference":
            local = (root / obj["relativePath"]).resolve()
            products = [x for x in objects.values() if x.get("package") == identity]
            if local.name == "coreai-models" or any(x.get("productName") == "CoreAILM" for x in products):
                matches.append((identity, obj, local))
    if len(matches) != 1:
        raise PatchError("Expected exactly one coreai-models package reference; refusing ambiguous edits.")
    identity, obj, local = matches[0]
    if not any(x.get("package") == identity and x.get("productName") == "CoreAILM"
               for x in objects.values()):
        raise PatchError("The selected package has no CoreAILM product dependency.")
    return identity, obj, local


def discover_source(project, explicit, local):
    if explicit:
        return Path(explicit).expanduser().resolve()
    if local:
        if not local.is_dir():
            raise PatchError("Referenced local package is missing. Supply --package-source.")
        return local
    matches = []
    derived = Path.home() / "Library/Developer/Xcode/DerivedData"
    for info in derived.glob("*/info.plist"):
        try:
            data = plistlib.loads(info.read_bytes())
            workspace = Path(data.get("WorkspacePath", "")).resolve()
        except (OSError, ValueError, plistlib.InvalidFileException):
            continue
        # Match this exact project, never a similarly named project's checkout.
        if workspace not in (project, project / "project.xcworkspace"):
            continue
        candidate = info.parent / "SourcePackages/checkouts/coreai-models"
        if candidate.is_dir():
            matches.append(candidate.resolve())
    matches = sorted(set(matches))
    if len(matches) != 1:
        raise PatchError("Cannot identify one checkout for this project. Open/resolve it in Xcode, "
                         "or pass --package-source /path/to/coreai-models (also for workspace/custom DerivedData builds).")
    return matches[0]


def patched_model(package):
    if not (package / "Package.swift").is_file() or not (package / "LICENSE").is_file():
        raise PatchError("Package.swift or LICENSE is missing from the package source.")
    model = package / MODEL_REL
    if not model.is_file():
        raise PatchError("Unsupported package layout: CoreAILanguageModel.swift is missing.")
    source = read(model)
    if source.count(ORIGINAL_METHOD) == 1:
        helper = package / HELPER_REL
        if helper.exists() and read(helper) != HELPER:
            raise PatchError("An unrelated IncrementalTextDecoder already exists; refusing to overwrite it.")
        return source.replace(ORIGINAL_METHOD, FIXED_METHOD, 1)
    if source.count(FIXED_METHOD) == 1 and (package / HELPER_REL).is_file() and read(package / HELPER_REL) == HELPER:
        return source
    raise PatchError("Unsupported or partially modified respondVanilla implementation. "
                     "This patch matches the known baseline " + BASELINE_REVISION +
                     " or this tool's completed fix. Review other versions manually; no files were changed.")


def rewire(source, identity, relative):
    # Edit only the selected package object, retaining all IDs and product links.
    pattern = re.compile(r"(?m)^(?P<indent>[ \t]*)" + re.escape(identity) +
                         r"(?: /\*[^\n]*?\*/)?\s*=\s*\{")
    matches = list(pattern.finditer(source))
    if len(matches) != 1:
        raise PatchError("Cannot uniquely locate the package object in project.pbxproj.")
    match = matches[0]
    depth = 1
    index = match.end()
    quoted = False
    escaped = False
    comment = None
    while index < len(source) and depth:
        char = source[index]
        pair = source[index:index + 2]
        if comment == "block":
            if pair == "*/":
                comment = None
                index += 2
                continue
        elif comment == "line":
            if char == "\n":
                comment = None
        elif quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif pair in ("/*", "//"):
            comment = "block" if pair == "/*" else "line"
            index += 2
            continue
        elif char == '"':
            quoted = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        index += 1
    if depth or not source[index:].startswith(";"):
        raise PatchError("Unexpected package object syntax.")
    if any(c in relative for c in '\n\r\t"\\'):
        raise PatchError("Package paths containing quotes, backslashes or control characters are unsupported.")
    indent = match.group("indent")
    replacement = (f'{indent}{identity} /* XCLocalSwiftPackageReference "coreai-models" */ = {{\n'
                   f'{indent}\tisa = XCLocalSwiftPackageReference;\n'
                   f'{indent}\trelativePath = "{relative}";\n{indent}}};')
    return source[:match.start()] + replacement + source[index + 1:]


def swift_code_mask(source):
    # Hide comments and strings while retaining offsets into the original file.
    pattern = re.compile(r'/\*.*?\*/|//[^\n]*|""".*?"""|"(?:\\.|[^"\\])*"', re.DOTALL)
    return pattern.sub(lambda m: "".join("\n" if c == "\n" else " " for c in m[0]), source)


def budget_edit(path, minimum):
    source = read(path)
    pattern = re.compile(r"(\bmaximumResponseTokens\s*:\s*)([^,\n)]+)")
    mask = swift_code_mask(source)
    found = 0
    def change(match):
        nonlocal found
        if not mask[match.start():match.start() + len("maximumResponseTokens")].strip():
            return match[0]
        found += 1
        expression = match.group(2)
        stripped = expression.strip()
        if re.fullmatch(r"[0-9]+", stripped):
            changed = str(max(int(stripped), minimum))
        else:
            ternary = re.fullmatch(r"([^?:]+\?\s*)([0-9]+)(\s*:\s*)([0-9]+)", stripped)
            if not ternary:
                raise PatchError(f"{path}: unsupported token-budget expression {stripped!r}; edit it manually.")
            changed = (ternary[1] + str(max(int(ternary[2]), minimum)) +
                       ternary[3] + str(max(int(ternary[4]), minimum)))
        return match[1] + expression.replace(stripped, changed, 1)
    result = pattern.sub(change, source)
    if not found:
        raise PatchError(f"{path}: no supported maximumResponseTokens argument found.")
    return result


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    original_mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    descriptor, temporary = tempfile.mkstemp(prefix=".coreai-patch-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(content)
        os.chmod(temporary, original_mode | 0o200)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def verify(package):
    with tempfile.TemporaryDirectory(prefix="coreai-regression-") as directory:
        directory = Path(directory)
        test = directory / "Regression.swift"
        test.write_text(REGRESSION, encoding="utf-8")
        binary = directory / "regression"
        subprocess.run(["swiftc", str(package / HELPER_REL), str(test), "-o", str(binary)], check=True)
        subprocess.run([str(binary)], check=True)


def apply(args):
    project = project_path(args.project)
    root = project.parent
    pbx = project / "project.pbxproj"
    identity, obj, local = reference(parse_project(pbx), root)
    source = discover_source(project, args.package_source, local)
    destination = (root / args.package_dir).resolve()
    if not destination.is_relative_to(root) or destination == root:
        raise PatchError("--package-dir must stay inside the project directory.")
    if destination.is_relative_to(project):
        raise PatchError("Place the package beside the .xcodeproj, not inside it.")
    if destination.exists() and (not local or destination != local):
        raise PatchError("Destination already exists but is not the referenced local package; refusing to overwrite it.")
    if destination.exists() and source != destination:
        raise PatchError("An existing local destination must be its own package source.")
    new_model = patched_model(source)
    relative = destination.relative_to(root).as_posix()
    new_pbx = read(pbx) if local == destination else rewire(read(pbx), identity, relative)
    changes = {pbx: new_pbx}
    for filename in args.response_file:
        path = (root / filename).resolve()
        if not path.is_relative_to(root) or not path.is_file() or path.suffix != ".swift":
            raise PatchError("--response-file must be an existing Swift file inside the project directory.")
        changes[path] = budget_edit(path, args.min_response_tokens)
    is_new_package = not destination.exists()
    if not is_new_package:
        changes[destination / MODEL_REL] = new_model
        changes[destination / HELPER_REL] = HELPER
    changes = {p: s for p, s in changes.items() if not p.exists() or read(p) != s}
    print(f"Project: {project}\nSource:  {source}\nLocal:   {destination}")
    if is_new_package:
        print("CREATE local Swift package with UTF-8 decoder fix")
    for path in changes:
        print("UPDATE " + str(path.relative_to(root)))
    if args.dry_run:
        print("DRY RUN: no files written; verification not run.")
        return
    if not changes and not is_new_package:
        print("Already patched; no files changed.")
        if args.verify:
            verify(destination)
        return
    backup = root / ".coreai-patch-backups" / (datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8])
    originals = {p: p.read_bytes() if p.exists() else None for p in changes}
    modes = {p: p.stat().st_mode & 0o777 for p in changes if p.exists()}
    written = []
    created_package = False
    stage = None
    try:
        backup.mkdir(parents=True)
        for path, content in originals.items():
            if content is not None:
                saved = backup / path.relative_to(root)
                saved.parent.mkdir(parents=True, exist_ok=True)
                saved.write_bytes(content)
        (backup / "manifest.json").write_text(json.dumps({
            "project": str(project), "source": str(source),
            "baseline_method_revision": BASELINE_REVISION,
            "created_package": str(destination) if is_new_package else None,
            "changed_files": [str(p.relative_to(root)) for p in changes],
            "new_files": [str(p.relative_to(root)) for p, c in originals.items() if c is None],
        }, indent=2), encoding="utf-8")
        if is_new_package:
            destination.parent.mkdir(parents=True, exist_ok=True)
            stage = Path(tempfile.mkdtemp(prefix=".coreai-stage-", dir=destination.parent))
            # Copy Swift package content, never model exports or Git/build caches.
            shutil.copytree(source / "swift", stage / "swift")
            for name in ("Package.swift", "Package.resolved", "LICENSE", "README.md"):
                if (source / name).is_file():
                    shutil.copy2(source / name, stage / name)
            atomic_write(stage / MODEL_REL, new_model)
            atomic_write(stage / HELPER_REL, HELPER)
            (stage / "IncrementalTextDecoderRegression.swift").write_text(REGRESSION, encoding="utf-8")
            (stage / "LOCAL_PATCH.md").write_text(
                "# CoreAI UTF-8 decoding patch\n\n"
                "Matched respondVanilla baseline: " + BASELINE_REVISION + "\n\n"
                "Uses full token history and advances the UTF-8 output prefix only after a valid decode.\n"
                "Full-history decoding has increasing cost for long responses. U+FFFD or rewritten\n"
                "prefixes suspend emission; incomplete final UTF-8 is not displayed.\n"
                "Rebuild the app after patching. Verify any future upstream replacement before use.\n",
                encoding="utf-8")
            os.rename(stage, destination)
            stage = None
            created_package = True
        for path, content in changes.items():
            atomic_write(path, content)
            written.append(path)
        document = parse_project(pbx)
        if reference(document, root)[2] != destination:
            raise PatchError("Post-patch project does not reference the intended local package.")
        if patched_model(destination) != read(destination / MODEL_REL):
            raise PatchError("Post-patch decoder validation failed.")
        if args.verify:
            verify(destination)
    except BaseException:
        for path in reversed(written):
            content = originals[path]
            if content is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, content.decode("utf-8"))
                os.chmod(path, modes[path])
        if created_package:
            shutil.rmtree(destination)
        print("Patch failed; restored changed files. Backup: " + str(backup), file=sys.stderr)
        raise
    finally:
        if stage and stage.exists():
            shutil.rmtree(stage)
    print("Applied. Backup: " + str(backup))
    print("Rebuild in Xcode. Regression verification is not an actual model inference test.")


def main():
    parser = argparse.ArgumentParser(description="Patch the known CoreAI split UTF-8 response truncation bug. Python 3.9+; macOS/Xcode required.")
    parser.add_argument("--project", default=".", help=".xcodeproj or directory containing exactly one project")
    parser.add_argument("--package-source", help="coreai-models checkout; otherwise detect this project's local package or DerivedData checkout")
    parser.add_argument("--package-dir", default="Packages/coreai-models", help="local destination relative to project directory")
    parser.add_argument("--response-file", action="append", default=[], help="Swift file relative to project directory; explicitly raise numeric maximumResponseTokens values (repeatable)")
    parser.add_argument("--min-response-tokens", type=int, default=1024, help="minimum for --response-file numeric literals; never reduces existing values")
    parser.add_argument("--dry-run", action="store_true", help="validate and preview without writing any files")
    parser.add_argument("--verify", action="store_true", help="compile and execute the Swift decoder regression after patching")
    args = parser.parse_args()
    if args.min_response_tokens < 1:
        parser.error("--min-response-tokens must be positive")
    try:
        apply(args)
    except (PatchError, OSError, ValueError, subprocess.CalledProcessError) as error:
        print("ERROR: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
