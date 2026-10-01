"""Isolated patcher tests: no real project edits, model files, or network."""
import argparse
import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("fix_coreai", ROOT / "scripts/fix_coreai.py")
fix = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fix)

PBX = """// !$*UTF8*$!
{
 archiveVersion = 1;
 objectVersion = 77;
 objects = {
  AAA = { isa = PBXProject; packageReferences = (BBB,); };
  BBB /* package */ = {
   isa = XCRemoteSwiftPackageReference;
   repositoryURL = "https://github.com/apple/coreai-models.git";
   requirement = { branch = main; kind = branch; };
  };
  CCC = { isa = XCSwiftPackageProductDependency; package = BBB; productName = CoreAILM; };
 };
 rootObject = AAA;
}
"""


class PatchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="coreai-patcher-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.project = self.root / "Example App.xcodeproj"
        self.project.mkdir()
        self.pbx = self.project / "project.pbxproj"
        self.pbx.write_text(PBX)
        self.source = self.root / "upstream"
        model = self.source / fix.MODEL_REL
        model.parent.mkdir(parents=True)
        model.write_text("// Fixture\n" + fix.ORIGINAL_METHOD)
        model.chmod(0o444)  # Xcode checkouts are commonly read-only.
        (self.source / "Package.swift").write_text("// Fixture manifest\n")
        (self.source / "LICENSE").write_text("Fixture license\n")
        (self.source / "exports").mkdir()
        (self.source / "exports/model.bin").write_bytes(b"must not copy")
        self.response = self.root / "ContentView.swift"
        self.response.write_text("maximumResponseTokens: enabled ? 4096 : 128\n")
        self.destination = self.root / "Packages/coreai-models"
        self.args = argparse.Namespace(project=str(self.project), package_source=str(self.source),
            package_dir="Packages/coreai-models", response_file=["ContentView.swift"],
            min_response_tokens=1024, dry_run=False, verify=False)

    def run_patch(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            fix.apply(self.args)

    def snapshot(self):
        return {str(p.relative_to(self.root)): p.read_bytes()
                for p in self.root.rglob("*") if p.is_file()}

    def test_dry_run_has_no_writes(self):
        before = self.snapshot()
        self.args.dry_run = True
        self.run_patch()
        self.assertEqual(before, self.snapshot())

    def test_apply_backup_source_preservation_and_repeat(self):
        original = (self.source / fix.MODEL_REL).read_bytes()
        self.run_patch()
        self.assertEqual((self.source / fix.MODEL_REL).read_bytes(), original)
        self.assertEqual(fix.reference(fix.parse_project(self.pbx), self.root)[2], self.destination)
        self.assertIn(fix.FIXED_METHOD, (self.destination / fix.MODEL_REL).read_text())
        self.assertEqual((self.destination / fix.HELPER_REL).read_text(), fix.HELPER)
        self.assertFalse((self.destination / "exports").exists())
        self.assertIn("4096 : 1024", self.response.read_text())
        backups = list((self.root / ".coreai-patch-backups").iterdir())
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "Example App.xcodeproj/project.pbxproj").read_text(), PBX)
        before = self.snapshot()
        self.args.package_source = None
        self.run_patch()
        self.assertEqual(before, self.snapshot())

    def test_unknown_version_rejected_before_writes(self):
        path = self.source / fix.MODEL_REL
        path.chmod(0o644)
        path.write_text("Different implementation")
        before = self.snapshot()
        with self.assertRaises(fix.PatchError):
            self.run_patch()
        self.assertEqual(before, self.snapshot())

    def test_unrelated_destination_is_not_overwritten(self):
        self.destination.mkdir(parents=True)
        sentinel = self.destination / "mine"
        sentinel.write_text("keep me")
        before = self.snapshot()
        with self.assertRaises(fix.PatchError):
            self.run_patch()
        self.assertEqual(before, self.snapshot())

    def test_unsupported_budget_rejects_before_writes(self):
        self.response.write_text("maximumResponseTokens: config.limit\n")
        before = self.snapshot()
        with self.assertRaises(fix.PatchError):
            self.run_patch()
        self.assertEqual(before, self.snapshot())

    def test_budget_preserves_comments_strings_and_larger_values(self):
        self.response.write_text('// maximumResponseTokens: 4\nlet s = "maximumResponseTokens: 8"\nmaximumResponseTokens: 2048\nmaximumResponseTokens: 128\n')
        self.run_patch()
        self.assertEqual(self.response.read_text(), '// maximumResponseTokens: 4\nlet s = "maximumResponseTokens: 8"\nmaximumResponseTokens: 2048\nmaximumResponseTokens: 1024\n')

    def test_optional_budget_does_not_edit_app(self):
        self.args.response_file = []
        original = self.response.read_bytes()
        self.run_patch()
        self.assertEqual(self.response.read_bytes(), original)

    def test_failure_rolls_back_project_and_new_package(self):
        original = self.response.read_bytes()
        self.args.verify = True
        with patch.object(fix, "verify", side_effect=fix.PatchError("Injected verification failure")):
            with self.assertRaises(fix.PatchError):
                self.run_patch()
        self.assertEqual(self.pbx.read_text(), PBX)
        self.assertEqual(self.response.read_bytes(), original)
        self.assertFalse(self.destination.exists())

    def test_external_local_package_is_copied_not_modified(self):
        self.pbx.write_text(fix.rewire(PBX, "BBB", "upstream"))
        self.args.package_source = None
        original = (self.source / fix.MODEL_REL).read_bytes()
        self.run_patch()
        self.assertEqual((self.source / fix.MODEL_REL).read_bytes(), original)
        self.assertEqual(fix.reference(fix.parse_project(self.pbx), self.root)[2], self.destination)

    def test_verify_compiles_real_helper_and_runs_regression(self):
        self.args.verify = True
        self.run_patch()


if __name__ == "__main__":
    unittest.main()
