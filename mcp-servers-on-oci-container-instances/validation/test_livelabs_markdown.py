#!/usr/bin/env python3
"""Unit tests for the local LiveLabs Markdown validator."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


VALIDATOR_PATH = Path(__file__).with_name("livelabs_markdown.py")
SPEC = importlib.util.spec_from_file_location("livelabs_markdown", VALIDATOR_PATH)
assert SPEC is not None
assert SPEC.loader is not None
livelabs_markdown = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(livelabs_markdown)


class ImageReferenceValidationTests(unittest.TestCase):
    def test_relative_image_reference_resolves_from_markdown_file(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            project_root = Path(workspace)
            module_dir = project_root / "deploy"
            image_dir = project_root / "images"
            module_dir.mkdir()
            image_dir.mkdir()
            (image_dir / "01-create-stack-package.png").write_bytes(b"png")
            markdown_file = module_dir / "deploy.md"
            markdown_file.write_text(
                "![Create stack](../images/01-create-stack-package.png)\n",
                encoding="utf-8",
            )

            original_project_root = livelabs_markdown.PROJECT_ROOT
            livelabs_markdown.PROJECT_ROOT = project_root
            try:
                failures = livelabs_markdown.validate_image_references([markdown_file])
            finally:
                livelabs_markdown.PROJECT_ROOT = original_project_root

            self.assertEqual([], failures)

    def test_missing_relative_image_reference_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            project_root = Path(workspace)
            module_dir = project_root / "deploy"
            module_dir.mkdir()
            markdown_file = module_dir / "deploy.md"
            markdown_file.write_text(
                "![Missing image](../images/missing.png)\n",
                encoding="utf-8",
            )

            original_project_root = livelabs_markdown.PROJECT_ROOT
            livelabs_markdown.PROJECT_ROOT = project_root
            try:
                failures = livelabs_markdown.validate_image_references([markdown_file])
            finally:
                livelabs_markdown.PROJECT_ROOT = original_project_root

            self.assertEqual(1, len(failures))
            self.assertIn("deploy/deploy.md:1", failures[0])
            self.assertIn("../images/missing.png", failures[0])


class ChangedFileDiscoveryTests(unittest.TestCase):
    def test_untracked_project_markdown_is_included_for_local_validation(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            repo_root = Path(workspace)
            project_root = repo_root / "developer" / "mcp"
            module_dir = project_root / "deploy"
            module_dir.mkdir(parents=True)
            markdown_file = module_dir / "deploy.md"
            markdown_file.write_text("# Lab\n", encoding="utf-8")

            subprocess.run(
                ["git", "init"],
                cwd=repo_root,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=repo_root,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Validator Test"],
                cwd=repo_root,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            subprocess.run(
                ["git", "commit", "--allow-empty", "-m", "init"],
                cwd=repo_root,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            original_project_root = livelabs_markdown.PROJECT_ROOT
            livelabs_markdown.PROJECT_ROOT = project_root
            try:
                files, failures = livelabs_markdown.changed_markdown_files(
                    repo_root,
                    "HEAD",
                    None,
                )
            finally:
                livelabs_markdown.PROJECT_ROOT = original_project_root

            self.assertEqual([], failures)
            self.assertEqual([markdown_file], files)


class TimingConsistencyValidationTests(unittest.TestCase):
    def write_workshop(
        self,
        project_root: Path,
        workshop_minutes: int,
        lab_minutes: list[int],
    ) -> None:
        introduction_dir = project_root / "introduction"
        introduction_dir.mkdir(parents=True)
        (introduction_dir / "introduction.md").write_text(
            f"# Introduction\n\nEstimated Workshop Time: {workshop_minutes} minutes\n",
            encoding="utf-8",
        )

        tutorials = [
            {
                "title": "Introduction",
                "description": "Workshop overview.",
                "filename": "../../introduction/introduction.md",
            },
            {
                "title": "Get Started",
                "description": "Common LiveLabs page.",
                "filename": "https://livelabs.oracle.com/cdn/common/labs/cloud-login/cloud-login.md",
            },
        ]

        for index, minutes in enumerate(lab_minutes, start=1):
            lab_dir = project_root / f"lab-{index}"
            lab_dir.mkdir()
            lab_file = lab_dir / f"lab-{index}.md"
            lab_file.write_text(
                f"# Lab {index}\n\nEstimated Time: {minutes} minutes\n",
                encoding="utf-8",
            )
            tutorials.append(
                {
                    "title": f"Lab {index}",
                    "description": f"Lab {index}.",
                    "filename": f"../../lab-{index}/lab-{index}.md",
                }
            )

        tutorials.append(
            {
                "title": "Need Help?",
                "description": "Common LiveLabs help page.",
                "filename": "https://livelabs.oracle.com/cdn/common/labs/need-help/need-help-livelabs.md",
            }
        )

        manifest_dir = project_root / "workshops" / "tenancy"
        manifest_dir.mkdir(parents=True)
        (manifest_dir / "manifest.json").write_text(
            json.dumps({"workshoptitle": "Test Workshop", "tutorials": tutorials}),
            encoding="utf-8",
        )

    def test_timing_mismatch_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            project_root = Path(workspace)
            self.write_workshop(project_root, workshop_minutes=60, lab_minutes=[15, 10])

            original_project_root = livelabs_markdown.PROJECT_ROOT
            livelabs_markdown.PROJECT_ROOT = project_root
            try:
                failures = livelabs_markdown.validate_timing_consistency()
            finally:
                livelabs_markdown.PROJECT_ROOT = original_project_root

            self.assertEqual(1, len(failures))
            self.assertIn("local lab timing total is 25 minutes", failures[0])
            self.assertIn("workshop estimate is 60 minutes", failures[0])

    def test_non_60_matching_timing_total_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            project_root = Path(workspace)
            self.write_workshop(project_root, workshop_minutes=45, lab_minutes=[20, 25])

            original_project_root = livelabs_markdown.PROJECT_ROOT
            livelabs_markdown.PROJECT_ROOT = project_root
            try:
                failures = livelabs_markdown.validate_timing_consistency()
            finally:
                livelabs_markdown.PROJECT_ROOT = original_project_root

            self.assertEqual([], failures)

    def test_timing_validation_runs_without_changed_markdown(self) -> None:
        with mock.patch.object(
            livelabs_markdown,
            "parse_args",
            return_value=mock.Mock(base_ref="origin/main", head_ref=None),
        ), mock.patch.object(
            livelabs_markdown, "resolve_repo_root", return_value=(Path("/tmp/repo"), [])
        ), mock.patch.object(
            livelabs_markdown,
            "changed_markdown_files",
            return_value=([], []),
        ), mock.patch.object(
            livelabs_markdown,
            "validate_tracked_path_hygiene",
            return_value=[],
        ), mock.patch.object(
            livelabs_markdown,
            "validate_timing_consistency",
            return_value=["workshop timing mismatch"],
        ):
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = livelabs_markdown.main()

        self.assertEqual(1, code)
        self.assertIn("workshop timing mismatch", stdout.getvalue())


class ManifestMetadataValidationTests(unittest.TestCase):
    def test_manifest_help_email_must_match_workshop_stakeholder(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            project_root = Path(workspace)
            manifest_dir = project_root / "workshops" / "tenancy"
            manifest_dir.mkdir(parents=True)
            (manifest_dir / "manifest.json").write_text(
                json.dumps({"help": "livelabs-help-aiservices_us@oracle.com"}),
                encoding="utf-8",
            )

            original_project_root = livelabs_markdown.PROJECT_ROOT
            livelabs_markdown.PROJECT_ROOT = project_root
            try:
                failures = livelabs_markdown.validate_manifest_metadata()
            finally:
                livelabs_markdown.PROJECT_ROOT = original_project_root

        self.assertEqual(1, len(failures))
        self.assertIn("help email must be livelabs-help-oci_us@oracle.com", failures[0])

    def test_manifest_help_email_accepts_oci_stakeholder(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            project_root = Path(workspace)
            manifest_dir = project_root / "workshops" / "sandbox"
            manifest_dir.mkdir(parents=True)
            (manifest_dir / "manifest.json").write_text(
                json.dumps({"help": "livelabs-help-oci_us@oracle.com"}),
                encoding="utf-8",
            )

            original_project_root = livelabs_markdown.PROJECT_ROOT
            livelabs_markdown.PROJECT_ROOT = project_root
            try:
                failures = livelabs_markdown.validate_manifest_metadata()
            finally:
                livelabs_markdown.PROJECT_ROOT = original_project_root

        self.assertEqual([], failures)


if __name__ == "__main__":
    unittest.main()
