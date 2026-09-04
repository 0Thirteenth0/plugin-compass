from __future__ import annotations

import importlib
import importlib.util
import inspect
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPOSITORY_ROOT / "plugins" / "plugin-compass"
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

class StandaloneSkillDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        original_tempdir = tempfile.tempdir
        tempfile.tempdir = str(Path(tempfile.gettempdir()).resolve(strict=True))
        self.addCleanup(setattr, tempfile, "tempdir", original_tempdir)

    def test_python311_windows_reparse_attribute_detects_junction(self) -> None:
        module = importlib.import_module("plugin_compass.adapters.standalone")

        class LegacyWindowsPath:
            def is_symlink(self) -> bool:
                return False

            def lstat(self):
                return SimpleNamespace(st_file_attributes=0x400)

        with patch.object(module.os, "name", "nt"):
            self.assertTrue(module._is_reparse_point(LegacyWindowsPath()))

    def test_python312_windows_is_junction_remains_authoritative(self) -> None:
        module = importlib.import_module("plugin_compass.adapters.standalone")

        class ModernWindowsPath:
            def is_symlink(self) -> bool:
                return False

            def is_junction(self) -> bool:
                return True

            def lstat(self):
                raise AssertionError("junction result should short-circuit lstat")

        with patch.object(module.os, "name", "nt"):
            self.assertTrue(module._is_reparse_point(ModernWindowsPath()))

    def test_windows_reparse_classification_fails_closed_without_attributes(self) -> None:
        module = importlib.import_module("plugin_compass.adapters.standalone")

        class UnclassifiableWindowsPath:
            def is_symlink(self) -> bool:
                return False

            def lstat(self):
                return SimpleNamespace()

        with patch.object(module.os, "name", "nt"):
            with self.assertRaises(OSError):
                module._is_reparse_point(UnclassifiableWindowsPath())

    def _make_directory_reparse_point(self, target: Path, link: Path) -> None:
        try:
            os.symlink(target, link, target_is_directory=True)
        except OSError as exc:
            completed = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(target)],
                check=False,
                capture_output=True,
                text=True,
                shell=False,
            )
            if completed.returncode != 0:
                self.skipTest(
                    f"symlink and junction creation are unavailable: {exc}; "
                    f"{completed.stderr.strip()}"
                )

    def test_package_exports_the_standalone_adapter_without_cli_integration(self) -> None:
        package = importlib.import_module("plugin_compass")
        self.assertTrue(
            hasattr(package, "discover_standalone_skills"),
            "standalone discovery is not exported by the package",
        )
        self.assertTrue(hasattr(package, "ConfiguredSkillRoot"))

    def test_discovers_configured_roots_and_preserves_duplicate_names(self) -> None:
        module_name = "plugin_compass.adapters.standalone"
        self.assertIsNotNone(
            importlib.util.find_spec(module_name),
            "standalone skill discovery adapter is not implemented",
        )
        module = importlib.import_module(module_name)
        ConfiguredSkillRoot = module.ConfiguredSkillRoot
        discover_standalone_skills = module.discover_standalone_skills

        with tempfile.TemporaryDirectory(prefix="Plugin Compass F2 ") as temp:
            base = Path(temp)
            roots = []
            for source_type, identity, folder in (
                ("standalone-user", "codex-user-skills", "User Skills"),
                ("standalone-project", "project:fixture", "Project Skills"),
                ("system", "codex-system-skills", "System Skills"),
            ):
                root = base / folder
                skill = root / "review"
                skill.mkdir(parents=True)
                (skill / "scripts").mkdir()
                (skill / "scripts" / "review.py").write_text("", encoding="utf-8")
                (skill / "SKILL.md").write_text(
                    "---\n"
                    "name: review\n"
                    f"description: Review from {source_type}.\n"
                    "---\n"
                    "Run `$SKILL_DIR/scripts/review.py`.\n",
                    encoding="utf-8",
                )
                roots.append(ConfiguredSkillRoot(root, source_type, identity))

            result = discover_standalone_skills(roots)

        self.assertEqual("complete", result.status)
        self.assertEqual(3, len(result.skills))
        self.assertEqual(["review", "review", "review"], sorted(s.name for s in result.skills))
        self.assertEqual(
            {"standalone-user", "standalone-project", "system"},
            {skill.source_type for skill in result.skills},
        )
        self.assertEqual(3, len({skill.qualified_identity for skill in result.skills}))
        self.assertTrue(all(skill.relative_path == "review/SKILL.md" for skill in result.skills))
        self.assertTrue(all(skill.metadata_status == "complete" for skill in result.skills))
        self.assertTrue(all(skill.readiness.status == "files_present" for skill in result.skills))
        self.assertTrue(all(skill.readiness.references == ("scripts/review.py",) for skill in result.skills))

    def test_oversized_metadata_is_not_parsed(self) -> None:
        module = importlib.import_module("plugin_compass.adapters.standalone")
        self.assertTrue(
            hasattr(module, "DiscoveryLimits"),
            "standalone discovery bounds are not implemented",
        )
        with tempfile.TemporaryDirectory(prefix="Plugin Compass F2 ") as temp:
            root = Path(temp) / "Skills With Spaces"
            skill_path = root / "large" / "SKILL.md"
            skill_path.parent.mkdir(parents=True)
            skill_path.write_text(
                "---\nname: should-not-be-read\ndescription: hidden\n---\n" + ("x" * 128),
                encoding="utf-8",
            )

            result = module.discover_standalone_skills(
                [module.ConfiguredSkillRoot(root, "standalone-user", "user:fixture")],
                limits=module.DiscoveryLimits(max_skill_bytes=32),
            )

        self.assertEqual("degraded", result.status)
        self.assertEqual(1, len(result.skills))
        self.assertEqual("large", result.skills[0].name)
        self.assertEqual("oversized", result.skills[0].metadata_status)
        self.assertEqual("unknown", result.skills[0].readiness.status)
        self.assertIn("skill-oversized", {item.code for item in result.diagnostics})

    def test_rejects_configured_root_reparse_point(self) -> None:
        module = importlib.import_module("plugin_compass.adapters.standalone")
        with tempfile.TemporaryDirectory(prefix="Plugin Compass F2 ") as temp:
            base = Path(temp)
            outside = base / "outside" / "escaped"
            outside.mkdir(parents=True)
            (outside / "SKILL.md").write_text(
                "---\nname: escaped\ndescription: Must not be read.\n---\n",
                encoding="utf-8",
            )
            root = base / "configured-link"
            self._make_directory_reparse_point(outside.parent, root)

            result = module.discover_standalone_skills(
                [module.ConfiguredSkillRoot(root, "standalone-project", "project:fixture")]
            )

        self.assertEqual((), result.skills)
        self.assertIn("path-rejected", {item.code for item in result.diagnostics})

    def test_reports_and_does_not_follow_nested_reparse_point(self) -> None:
        module = importlib.import_module("plugin_compass.adapters.standalone")
        with tempfile.TemporaryDirectory(prefix="Plugin Compass F2 ") as temp:
            base = Path(temp)
            root = base / "configured"
            root.mkdir()
            outside = base / "outside"
            escaped = outside / "escaped"
            escaped.mkdir(parents=True)
            (escaped / "SKILL.md").write_text(
                "---\nname: escaped\ndescription: Must not be read.\n---\n",
                encoding="utf-8",
            )
            self._make_directory_reparse_point(outside, root / "linked")

            result = module.discover_standalone_skills(
                [module.ConfiguredSkillRoot(root, "standalone-user", "user:fixture")]
            )

        self.assertEqual((), result.skills)
        self.assertIn("path-rejected", {item.code for item in result.diagnostics})

    def test_skips_skills_beyond_the_configured_traversal_depth(self) -> None:
        module = importlib.import_module("plugin_compass.adapters.standalone")
        self.assertIn(
            "max_depth",
            inspect.signature(module.DiscoveryLimits).parameters,
            "traversal depth bound is not implemented",
        )
        with tempfile.TemporaryDirectory(prefix="Plugin Compass F2 ") as temp:
            root = Path(temp) / "skills"
            shallow = root / "one" / "SKILL.md"
            deep = root / "one" / "two" / "SKILL.md"
            shallow.parent.mkdir(parents=True)
            deep.parent.mkdir(parents=True)
            shallow.write_text(
                "---\nname: shallow\ndescription: Included.\n---\n",
                encoding="utf-8",
            )
            deep.write_text(
                "---\nname: deep\ndescription: Excluded.\n---\n",
                encoding="utf-8",
            )

            result = module.discover_standalone_skills(
                [module.ConfiguredSkillRoot(root, "system", "system:fixture")],
                limits=module.DiscoveryLimits(max_depth=1),
            )

        self.assertEqual(["shallow"], [skill.name for skill in result.skills])
        self.assertIn("depth-limit", {item.code for item in result.diagnostics})

    def test_stops_at_the_configured_skill_count(self) -> None:
        module = importlib.import_module("plugin_compass.adapters.standalone")
        self.assertIn(
            "max_skills",
            inspect.signature(module.DiscoveryLimits).parameters,
            "skill count bound is not implemented",
        )
        with tempfile.TemporaryDirectory(prefix="Plugin Compass F2 ") as temp:
            root = Path(temp) / "skills"
            for name in ("charlie", "alpha", "bravo"):
                path = root / name / "SKILL.md"
                path.parent.mkdir(parents=True)
                path.write_text(
                    f"---\nname: {name}\ndescription: Fixture.\n---\n",
                    encoding="utf-8",
                )

            result = module.discover_standalone_skills(
                [module.ConfiguredSkillRoot(root, "standalone-user", "user:fixture")],
                limits=module.DiscoveryLimits(max_skills=2),
            )

        self.assertEqual(["alpha", "bravo"], sorted(skill.name for skill in result.skills))
        self.assertIn("skill-limit", {item.code for item in result.diagnostics})

    def test_stops_when_the_runtime_budget_is_observed(self) -> None:
        module = importlib.import_module("plugin_compass.adapters.standalone")
        self.assertIn(
            "max_runtime_seconds",
            inspect.signature(module.DiscoveryLimits).parameters,
            "runtime bound is not implemented",
        )
        with tempfile.TemporaryDirectory(prefix="Plugin Compass F2 ") as temp:
            root = Path(temp) / "skills"
            skill_path = root / "late" / "SKILL.md"
            skill_path.parent.mkdir(parents=True)
            skill_path.write_text(
                "---\nname: late\ndescription: Must not be inspected.\n---\n",
                encoding="utf-8",
            )
            ticks = iter((0.0, 2.0))

            result = module.discover_standalone_skills(
                [module.ConfiguredSkillRoot(root, "standalone-user", "user:fixture")],
                limits=module.DiscoveryLimits(max_runtime_seconds=1.0),
                monotonic=lambda: next(ticks),
            )

        self.assertEqual((), result.skills)
        self.assertIn("runtime-limit", {item.code for item in result.diagnostics})

    def test_malformed_frontmatter_is_preserved_as_degraded_metadata(self) -> None:
        module = importlib.import_module("plugin_compass.adapters.standalone")
        with tempfile.TemporaryDirectory(prefix="Plugin Compass F2 ") as temp:
            root = Path(temp) / "skills"
            skill_path = root / "broken" / "SKILL.md"
            skill_path.parent.mkdir(parents=True)
            skill_path.write_text(
                "---\nname: misleading\ndescription: No closing delimiter.\n",
                encoding="utf-8",
            )

            result = module.discover_standalone_skills(
                [module.ConfiguredSkillRoot(root, "standalone-project", "project:fixture")]
            )

        self.assertEqual(1, len(result.skills))
        self.assertEqual("broken", result.skills[0].name)
        self.assertEqual("malformed", result.skills[0].metadata_status)
        self.assertEqual("degraded", result.status)
        self.assertIn("skill-metadata-malformed", {item.code for item in result.diagnostics})

    def test_invalid_encoding_and_duplicate_frontmatter_keys_fail_closed(self) -> None:
        module = importlib.import_module("plugin_compass.adapters.standalone")
        with tempfile.TemporaryDirectory(prefix="Plugin Compass F4 ") as temp:
            root = Path(temp) / "User Name With Spaces" / "Standalone Skills"
            invalid_encoding = root / "invalid-encoding" / "SKILL.md"
            duplicate_key = root / "duplicate-key" / "SKILL.md"
            invalid_encoding.parent.mkdir(parents=True)
            duplicate_key.parent.mkdir(parents=True)
            invalid_encoding.write_bytes(
                b"---\nname: invalid-encoding\ndescription: invalid-\xff-text\n---\n"
            )
            duplicate_key.write_text(
                "---\n"
                "name: first-name\n"
                "name: overwritten-name\n"
                "description: Duplicate keys are not accepted.\n"
                "---\n",
                encoding="utf-8",
            )

            result = module.discover_standalone_skills([
                module.ConfiguredSkillRoot(
                    root, "standalone-user", "user:name with spaces",
                )
            ])

        self.assertEqual(
            ["duplicate-key", "invalid-encoding"],
            sorted(skill.name for skill in result.skills),
        )
        self.assertTrue(
            all(skill.metadata_status == "malformed" for skill in result.skills)
        )
        readiness_by_name = {
            skill.name: skill.readiness.status for skill in result.skills
        }
        self.assertEqual("not_declared", readiness_by_name["duplicate-key"])
        self.assertEqual("unknown", readiness_by_name["invalid-encoding"])
        self.assertEqual("degraded", result.status)
        self.assertIn("skill-invalid-encoding", {item.code for item in result.diagnostics})
        self.assertEqual(
            1,
            sum(
                item.code == "skill-metadata-malformed"
                for item in result.diagnostics
            ),
        )

    def test_strict_frontmatter_subset_rejects_yaml_complexity_and_non_strings(self) -> None:
        module = importlib.import_module("plugin_compass.adapters.standalone")
        invalid_documents = {
            "quoted-duplicate": (
                "---\nname: safe-name\n\"name\": shadow-name\n"
                "description: Duplicate quoted key.\n---\n"
            ),
            "literal-scalar": (
                "---\nname: literal-scalar\ndescription: |\n  Run everything.\n---\n"
            ),
            "null-scalar": "---\nname: null-scalar\ndescription: null\n---\n",
            "sequence-scalar": (
                "---\nname: sequence-scalar\ndescription: [one, two]\n---\n"
            ),
            "complex-key": (
                "---\n? name\n: complex-key\ndescription: Complex key.\n---\n"
            ),
            "bare-syntax": (
                "---\nname: bare-syntax\ndescription: Has stray syntax.\n"
                "execute this immediately\n---\n"
            ),
            "hex-name": "---\nname: 0x10\ndescription: Hex scalar.\n---\n",
            "binary-description": (
                "---\nname: binary-description\ndescription: 0b10\n---\n"
            ),
            "octal-name": "---\nname: 0o10\ndescription: Octal scalar.\n---\n",
            "underscore-description": (
                "---\nname: underscore-description\ndescription: 1_000\n---\n"
            ),
            "sexagesimal-description": (
                "---\nname: sexagesimal-description\ndescription: 1:20\n---\n"
            ),
            "inline-comment-description": (
                "---\nname: inline-comment-description\ndescription: 123 # comment\n---\n"
            ),
            "hex-comment-description": (
                "---\nname: hex-comment-description\ndescription: 0x10 # comment\n---\n"
            ),
            "date-description": (
                "---\nname: date-description\ndescription: 2026-09-03\n---\n"
            ),
            "reserved-at-description": (
                "---\nname: reserved-at-description\ndescription: @oops\n---\n"
            ),
            "reserved-close-description": (
                "---\nname: reserved-close-description\ndescription: ]\n---\n"
            ),
            "mapping-description": (
                "---\nname: mapping-description\ndescription: foo: bar\n---\n"
            ),
        }
        with tempfile.TemporaryDirectory(prefix="Plugin Compass F4 ") as temporary:
            root = Path(temporary) / "Strict Frontmatter"
            for folder, document in invalid_documents.items():
                path = root / folder / "SKILL.md"
                path.parent.mkdir(parents=True)
                path.write_text(document, encoding="utf-8")

            result = module.discover_standalone_skills([
                module.ConfiguredSkillRoot(
                    root, "standalone-project", "project:strict-frontmatter",
                )
            ])
            valid_root = Path(temporary) / "Valid Strict Frontmatter"
            plain = valid_root / "plain" / "SKILL.md"
            quoted = valid_root / "quoted" / "SKILL.md"
            plain.parent.mkdir(parents=True)
            quoted.parent.mkdir(parents=True)
            plain.write_text(
                "---\nname: plain\ndescription: Plain string metadata.\n---\n",
                encoding="utf-8",
            )
            quoted.write_text(
                "---\nname: '0x10'\n"
                "description: \"1_000 and 2026-09-03: quoted\"\n---\n",
                encoding="utf-8",
            )
            valid_result = module.discover_standalone_skills([
                module.ConfiguredSkillRoot(
                    valid_root, "standalone-user", "user:strict-frontmatter",
                )
            ])

        self.assertEqual(len(invalid_documents), len(result.skills))
        self.assertEqual("degraded", result.status)
        self.assertTrue(
            all(skill.metadata_status == "malformed" for skill in result.skills)
        )
        self.assertEqual(
            len(invalid_documents),
            sum(
                item.code == "skill-metadata-malformed"
                for item in result.diagnostics
            ),
        )
        decision_module = importlib.import_module("plugin_compass.skill_decision")
        for skill in result.skills:
            with self.subTest(selection=skill.qualified_identity):
                automatic = decision_module.build_skill_decision(
                    [skill], f"{skill.name} {skill.description}",
                )
                self.assertEqual((), automatic.recommendations)
                exact = decision_module.build_skill_decision(
                    result.skills,
                    "unrelated task",
                    requested_skills=[skill.qualified_identity],
                )
                self.assertEqual((), exact.recommendations)
        self.assertEqual("complete", valid_result.status)
        self.assertEqual(
            [
                ("0x10", "1_000 and 2026-09-03: quoted"),
                ("plain", "Plain string metadata."),
            ],
            sorted((skill.name, skill.description) for skill in valid_result.skills),
        )

    def test_unreadable_skill_is_preserved_without_crashing_discovery(self) -> None:
        module = importlib.import_module("plugin_compass.adapters.standalone")
        with tempfile.TemporaryDirectory(prefix="Plugin Compass F2 ") as temp:
            root = Path(temp) / "skills"
            skill_path = root / "unreadable" / "SKILL.md"
            skill_path.parent.mkdir(parents=True)
            skill_path.write_text(
                "---\nname: unreadable\ndescription: Fixture.\n---\n",
                encoding="utf-8",
            )

            try:
                with patch.object(module.os, "open", side_effect=OSError("access denied")):
                    result = module.discover_standalone_skills(
                        [module.ConfiguredSkillRoot(
                            root, "standalone-user", "user:fixture",
                        )]
                    )
            except OSError as exc:
                self.fail(f"unreadable SKILL.md escaped discovery: {exc}")

        self.assertEqual(1, len(result.skills))
        self.assertEqual("unreadable", result.skills[0].name)
        self.assertEqual("unreadable", result.skills[0].metadata_status)
        self.assertEqual("unknown", result.skills[0].readiness.status)
        self.assertIn("skill-unreadable", {item.code for item in result.diagnostics})

    def test_missing_optional_root_returns_truthful_degraded_empty_result(self) -> None:
        module = importlib.import_module("plugin_compass.adapters.standalone")
        with tempfile.TemporaryDirectory(prefix="Plugin Compass F2 ") as temp:
            missing = Path(temp) / "missing root"
            result = module.discover_standalone_skills(
                [module.ConfiguredSkillRoot(missing, "standalone-user", "user:fixture")]
            )

        self.assertEqual((), result.skills)
        self.assertEqual("degraded", result.status)
        self.assertIn("root-missing", {item.code for item in result.diagnostics})

    def test_configured_roots_reject_plugin_and_session_only_provenance(self) -> None:
        module = importlib.import_module("plugin_compass.adapters.standalone")
        for source_type in ("plugin", "session-only"):
            with self.subTest(source_type=source_type):
                with self.assertRaisesRegex(ValueError, "standalone source type"):
                    module.ConfiguredSkillRoot(
                        Path("unused"), source_type, "unsupported:fixture",
                    )

    def test_rejects_readiness_reference_that_traverses_outside_skill(self) -> None:
        module = importlib.import_module("plugin_compass.adapters.standalone")
        with tempfile.TemporaryDirectory(prefix="Plugin Compass F2 ") as temp:
            root = Path(temp) / "skills"
            skill = root / "review"
            skill.mkdir(parents=True)
            (root / "outside.py").write_text("", encoding="utf-8")
            (skill / "SKILL.md").write_text(
                "---\nname: review\ndescription: Fixture.\n---\n"
                "Run `$SKILL_DIR/../outside.py`.\n",
                encoding="utf-8",
            )

            result = module.discover_standalone_skills(
                [module.ConfiguredSkillRoot(root, "standalone-user", "user:fixture")]
            )

        self.assertEqual("unknown", result.skills[0].readiness.status)
        self.assertIn(
            "readiness-reference-rejected",
            {item.code for item in result.diagnostics},
        )

    def test_rejects_readiness_reference_through_nested_reparse_point(self) -> None:
        module = importlib.import_module("plugin_compass.adapters.standalone")
        with tempfile.TemporaryDirectory(prefix="Plugin Compass F2 ") as temp:
            base = Path(temp)
            root = base / "skills"
            skill = root / "review"
            skill.mkdir(parents=True)
            outside = base / "outside"
            outside.mkdir()
            (outside / "runner.py").write_text("", encoding="utf-8")
            self._make_directory_reparse_point(outside, skill / "linked")
            (skill / "SKILL.md").write_text(
                "---\nname: review\ndescription: Fixture.\n---\n"
                "Run `$SKILL_DIR/linked/runner.py`.\n",
                encoding="utf-8",
            )

            result = module.discover_standalone_skills(
                [module.ConfiguredSkillRoot(root, "standalone-project", "project:fixture")]
            )

        self.assertEqual("unknown", result.skills[0].readiness.status)
        self.assertIn(
            "readiness-reference-rejected",
            {item.code for item in result.diagnostics},
        )

    def test_identity_swaps_at_skill_read_and_readiness_use_fail_closed(self) -> None:
        module = importlib.import_module("plugin_compass.adapters.standalone")
        with tempfile.TemporaryDirectory(prefix="Plugin Compass F4 race ") as temporary:
            base = Path(temporary)
            root = base / "Skill Root With Spaces"
            victim = root / "victim"
            victim.mkdir(parents=True)
            skill_path = victim / "SKILL.md"
            skill_path.write_text(
                "---\nname: victim\ndescription: Safe metadata.\n---\n",
                encoding="utf-8",
            )
            external_skill = base / "external-SKILL.md"
            external_skill.write_text(
                "---\nname: escaped\ndescription: Must never be consumed.\n---\n",
                encoding="utf-8",
            )
            original_path_open = Path.open
            original_os_open = os.open

            def raced_path_open(path: Path, *args, **kwargs):
                if path == skill_path:
                    return original_path_open(external_skill, *args, **kwargs)
                return original_path_open(path, *args, **kwargs)

            def raced_os_open(path, flags, mode=0o777, *, dir_fd=None):
                candidate = Path(path)
                if candidate == skill_path:
                    return original_os_open(external_skill, flags, mode)
                return original_os_open(path, flags, mode, dir_fd=dir_fd)

            with (
                patch.object(Path, "open", new=raced_path_open),
                patch.object(module.os, "open", new=raced_os_open),
            ):
                skill_race = module.discover_standalone_skills([
                    module.ConfiguredSkillRoot(
                        root, "standalone-user", "user:race fixture",
                    )
                ])

            readiness_skill = root / "readiness"
            scripts = readiness_skill / "scripts"
            scripts.mkdir(parents=True)
            readiness_path = readiness_skill / "SKILL.md"
            runner = scripts / "runner.py"
            readiness_path.write_text(
                "---\nname: readiness\ndescription: Safe readiness.\n---\n"
                "Run `$SKILL_DIR/scripts/runner.py`.\n",
                encoding="utf-8",
            )
            runner.write_text("safe = True\n", encoding="utf-8")
            external_runner = base / "external-runner.py"
            external_runner.write_text("escaped = True\n", encoding="utf-8")
            original_is_file = Path.is_file

            def raced_is_file(path: Path) -> bool:
                if path == runner:
                    return external_runner.is_file()
                return original_is_file(path)

            def readiness_raced_os_open(path, flags, mode=0o777, *, dir_fd=None):
                candidate = Path(path)
                if candidate == runner:
                    return original_os_open(external_runner, flags, mode)
                return original_os_open(path, flags, mode, dir_fd=dir_fd)

            with (
                patch.object(Path, "is_file", new=raced_is_file),
                patch.object(module.os, "open", new=readiness_raced_os_open),
            ):
                readiness_race = module.discover_standalone_skills([
                    module.ConfiguredSkillRoot(
                        root, "standalone-user", "user:race fixture",
                    )
                ])

        victim_record = next(
            item for item in skill_race.skills if item.relative_path == "victim/SKILL.md"
        )
        self.assertEqual("victim", victim_record.name)
        self.assertEqual("unreadable", victim_record.metadata_status)
        self.assertIn("path-rejected", {item.code for item in skill_race.diagnostics})
        readiness_record = next(
            item for item in readiness_race.skills if item.name == "readiness"
        )
        self.assertEqual("unknown", readiness_record.readiness.status)
        self.assertIn(
            "readiness-reference-rejected",
            {item.code for item in readiness_race.diagnostics},
        )

    def test_containment_is_revalidated_after_open_before_first_byte_read(self) -> None:
        module = importlib.import_module("plugin_compass.adapters.standalone")
        with tempfile.TemporaryDirectory(prefix="Plugin Compass F4 pre-read ") as temporary:
            root = Path(temporary) / "Root With Spaces"
            skill_path = root / "Skill With Spaces" / "SKILL.md"
            skill_path.parent.mkdir(parents=True)
            payload = b"---\nname: ordinary\ndescription: Ordinary file.\n---\n"
            skill_path.write_bytes(payload)

            self.assertEqual(
                payload,
                module._read_bounded_no_follow(skill_path, root, len(payload)),
            )
            self.assertTrue(module._regular_file_no_follow(skill_path, root))

            original_safe_path = module._safe_resolved_path
            original_read = module.os.read
            checks = {"count": 0}
            reads: list[int] = []

            def changed_containment(safe_root: Path, candidate: Path):
                checks["count"] += 1
                if checks["count"] == 1:
                    return original_safe_path(safe_root, candidate)
                return None

            def observed_read(descriptor: int, size: int) -> bytes:
                reads.append(descriptor)
                return original_read(descriptor, size)

            with (
                patch.object(module, "_safe_resolved_path", new=changed_containment),
                patch.object(module.os, "read", new=observed_read),
                self.assertRaises(module._UnsafePathAccess),
            ):
                module._read_bounded_no_follow(skill_path, root, len(payload))

        self.assertEqual([], reads, "unsafe descriptor was read before containment check")

    def test_directory_enumeration_errors_are_reported_at_root_and_nested_levels(self) -> None:
        module = importlib.import_module("plugin_compass.adapters.standalone")
        with tempfile.TemporaryDirectory(prefix="Plugin Compass F2 ") as temp:
            base = Path(temp)
            blocked_root = base / "blocked-root"
            blocked_root.mkdir()
            healthy_root = base / "healthy-root"
            blocked_nested = healthy_root / "blocked-nested"
            blocked_nested.mkdir(parents=True)
            skill_path = healthy_root / "healthy" / "SKILL.md"
            skill_path.parent.mkdir()
            skill_path.write_text(
                "---\nname: healthy\ndescription: Fixture.\n---\n",
                encoding="utf-8",
            )
            original_iterdir = Path.iterdir

            def guarded_iterdir(path: Path):
                if path in {blocked_root, blocked_nested}:
                    raise PermissionError("fixture access denied")
                return original_iterdir(path)

            with patch.object(Path, "iterdir", new=guarded_iterdir):
                result = module.discover_standalone_skills([
                    module.ConfiguredSkillRoot(
                        blocked_root, "standalone-user", "user:blocked",
                    ),
                    module.ConfiguredSkillRoot(
                        healthy_root, "standalone-project", "project:fixture",
                    ),
                ])

        self.assertEqual(["healthy"], [skill.name for skill in result.skills])
        unreadable = [
            item for item in result.diagnostics if item.code == "directory-unreadable"
        ]
        self.assertEqual(2, len(unreadable))
        self.assertEqual("degraded", result.status)

    def test_rejects_root_collection_that_exceeds_the_configured_bound(self) -> None:
        module = importlib.import_module("plugin_compass.adapters.standalone")
        self.assertIn(
            "max_roots",
            inspect.signature(module.DiscoveryLimits).parameters,
            "configured root count bound is not implemented",
        )
        with tempfile.TemporaryDirectory(prefix="Plugin Compass F2 ") as temp:
            base = Path(temp)
            roots = []
            for index in range(2):
                root = base / f"root-{index}"
                skill = root / "skill" / "SKILL.md"
                skill.parent.mkdir(parents=True)
                skill.write_text(
                    f"---\nname: skill-{index}\ndescription: Fixture.\n---\n",
                    encoding="utf-8",
                )
                roots.append(module.ConfiguredSkillRoot(
                    root, "standalone-user", f"user:{index}",
                ))

            result = module.discover_standalone_skills(
                iter(roots),
                limits=module.DiscoveryLimits(max_roots=1),
            )

        self.assertEqual((), result.skills)
        self.assertIn("root-limit", {item.code for item in result.diagnostics})

    def test_discards_root_when_directory_entry_bound_is_exceeded(self) -> None:
        module = importlib.import_module("plugin_compass.adapters.standalone")
        self.assertIn(
            "max_directory_entries",
            inspect.signature(module.DiscoveryLimits).parameters,
            "directory entry bound is not implemented",
        )
        with tempfile.TemporaryDirectory(prefix="Plugin Compass F2 ") as temp:
            root = Path(temp) / "skills"
            for name in ("alpha", "bravo", "charlie"):
                skill = root / name / "SKILL.md"
                skill.parent.mkdir(parents=True)
                skill.write_text(
                    f"---\nname: {name}\ndescription: Fixture.\n---\n",
                    encoding="utf-8",
                )

            result = module.discover_standalone_skills(
                [module.ConfiguredSkillRoot(root, "standalone-user", "user:fixture")],
                limits=module.DiscoveryLimits(max_directory_entries=2),
            )

        self.assertEqual((), result.skills)
        self.assertIn("entry-limit", {item.code for item in result.diagnostics})

    def test_bounds_readiness_references_before_file_checks(self) -> None:
        module = importlib.import_module("plugin_compass.adapters.standalone")
        self.assertIn(
            "max_readiness_references",
            inspect.signature(module.DiscoveryLimits).parameters,
            "readiness reference bound is not implemented",
        )
        with tempfile.TemporaryDirectory(prefix="Plugin Compass F2 ") as temp:
            root = Path(temp) / "skills"
            skill = root / "review"
            scripts = skill / "scripts"
            scripts.mkdir(parents=True)
            for name in ("alpha.py", "bravo.py"):
                (scripts / name).write_text("", encoding="utf-8")
            (skill / "SKILL.md").write_text(
                "---\nname: review\ndescription: Fixture.\n---\n"
                "`$SKILL_DIR/scripts/alpha.py`\n"
                "`$SKILL_DIR/scripts/bravo.py`\n",
                encoding="utf-8",
            )

            result = module.discover_standalone_skills(
                [module.ConfiguredSkillRoot(root, "standalone-user", "user:fixture")],
                limits=module.DiscoveryLimits(max_readiness_references=1),
            )

        self.assertEqual("unknown", result.skills[0].readiness.status)
        self.assertLessEqual(len(result.skills[0].readiness.references), 1)
        self.assertIn(
            "readiness-reference-limit",
            {item.code for item in result.diagnostics},
        )

    def test_file_growth_after_stat_is_still_read_with_a_hard_byte_bound(self) -> None:
        module = importlib.import_module("plugin_compass.adapters.standalone")

        with tempfile.TemporaryDirectory(prefix="Plugin Compass F2 ") as temp:
            root = Path(temp) / "skills"
            skill_path = root / "racy" / "SKILL.md"
            skill_path.parent.mkdir(parents=True)
            skill_path.write_bytes(b"x")
            def bounded_read(_descriptor: int, size: int) -> bytes:
                if size != 17:
                    raise AssertionError("SKILL.md read did not use max_skill_bytes + 1")
                return b"x" * 64

            try:
                with patch.object(module.os, "read", new=bounded_read):
                    result = module.discover_standalone_skills(
                        [module.ConfiguredSkillRoot(
                            root, "standalone-user", "user:fixture",
                        )],
                        limits=module.DiscoveryLimits(max_skill_bytes=16),
                    )
            except AssertionError as exc:
                self.fail(str(exc))

        self.assertEqual("oversized", result.skills[0].metadata_status)
        self.assertIn("skill-oversized", {item.code for item in result.diagnostics})

    def test_rejects_drive_and_rooted_readiness_references(self) -> None:
        module = importlib.import_module("plugin_compass.adapters.standalone")
        for reference in ("C:/outside.py", "/server/share/outside.py"):
            with self.subTest(reference=reference):
                with tempfile.TemporaryDirectory(prefix="Plugin Compass F2 ") as temp:
                    root = Path(temp) / "skills"
                    skill = root / "review"
                    skill.mkdir(parents=True)
                    (skill / "SKILL.md").write_text(
                        "---\nname: review\ndescription: Fixture.\n---\n"
                        f"Run `$SKILL_DIR/{reference}`.\n",
                        encoding="utf-8",
                    )
                    result = module.discover_standalone_skills([
                        module.ConfiguredSkillRoot(
                            root, "standalone-user", "user:fixture",
                        )
                    ])

                self.assertEqual("unknown", result.skills[0].readiness.status)
                self.assertIn(
                    "readiness-reference-rejected",
                    {item.code for item in result.diagnostics},
                )

    def test_root_resolution_error_is_reported_without_crashing(self) -> None:
        module = importlib.import_module("plugin_compass.adapters.standalone")
        with tempfile.TemporaryDirectory(prefix="Plugin Compass F2 ") as temp:
            root = Path(temp) / "skills"
            root.mkdir()
            original_resolve = Path.resolve

            def guarded_resolve(path: Path, *args, **kwargs):
                if path == root:
                    raise PermissionError("fixture access denied")
                return original_resolve(path, *args, **kwargs)

            try:
                with patch.object(Path, "resolve", new=guarded_resolve):
                    result = module.discover_standalone_skills([
                        module.ConfiguredSkillRoot(
                            root, "standalone-project", "project:fixture",
                        )
                    ])
            except OSError as exc:
                self.fail(f"configured root error escaped discovery: {exc}")

        self.assertEqual((), result.skills)
        self.assertIn("root-unreadable", {item.code for item in result.diagnostics})

    def test_observes_runtime_expiry_immediately_after_bounded_skill_read(self) -> None:
        module = importlib.import_module("plugin_compass.adapters.standalone")
        clock = {"now": 0.0}

        with tempfile.TemporaryDirectory(prefix="Plugin Compass F2 ") as temp:
            root = Path(temp) / "skills"
            skill_path = root / "late" / "SKILL.md"
            skill_path.parent.mkdir(parents=True)
            payload = b"---\nname: late\ndescription: Must not be emitted.\n---\n"
            skill_path.write_bytes(payload)
            original_read = module.os.read

            def expiring_read(descriptor: int, size: int) -> bytes:
                value = original_read(descriptor, size)
                clock["now"] = 2.0
                return value

            with patch.object(module.os, "read", new=expiring_read):
                result = module.discover_standalone_skills(
                    [module.ConfiguredSkillRoot(
                        root, "standalone-user", "user:fixture",
                    )],
                    limits=module.DiscoveryLimits(max_runtime_seconds=1.0),
                    monotonic=lambda: clock["now"],
                )

        self.assertEqual((), result.skills)
        self.assertEqual("degraded", result.status)
        self.assertIn("runtime-limit", {item.code for item in result.diagnostics})

    def test_observes_runtime_expiry_immediately_after_entry_classification(self) -> None:
        module = importlib.import_module("plugin_compass.adapters.standalone")
        clock = {"now": 0.0}
        with tempfile.TemporaryDirectory(prefix="Plugin Compass F2 ") as temp:
            root = Path(temp) / "skills"
            skill_path = root / "late" / "SKILL.md"
            skill_path.parent.mkdir(parents=True)
            skill_path.write_text(
                "---\nname: late\ndescription: Must not be emitted.\n---\n",
                encoding="utf-8",
            )
            original_is_file = Path.is_file
            original_is_dir = Path.is_dir

            def expiring_is_file(path: Path) -> bool:
                value = original_is_file(path)
                if path == skill_path:
                    clock["now"] = 2.0
                return value

            def guarded_is_dir(path: Path) -> bool:
                if path == skill_path and clock["now"] >= 1.0:
                    raise AssertionError("classification continued after deadline")
                return original_is_dir(path)

            try:
                with (
                    patch.object(Path, "is_file", new=expiring_is_file),
                    patch.object(Path, "is_dir", new=guarded_is_dir),
                ):
                    result = module.discover_standalone_skills(
                        [module.ConfiguredSkillRoot(
                            root, "standalone-user", "user:fixture",
                        )],
                        limits=module.DiscoveryLimits(max_runtime_seconds=1.0),
                        monotonic=lambda: clock["now"],
                    )
            except AssertionError as exc:
                self.fail(str(exc))

        self.assertEqual((), result.skills)
        self.assertEqual("degraded", result.status)
        self.assertIn("runtime-limit", {item.code for item in result.diagnostics})

    def test_skips_skill_when_single_readiness_path_resolution_expires(self) -> None:
        module = importlib.import_module("plugin_compass.adapters.standalone")
        clock = {"now": 0.0}
        with tempfile.TemporaryDirectory(prefix="Plugin Compass F2 ") as temp:
            root = Path(temp) / "skills"
            skill = root / "review"
            runner = skill / "scripts" / "runner.py"
            runner.parent.mkdir(parents=True)
            runner.write_text("", encoding="utf-8")
            (skill / "SKILL.md").write_text(
                "---\nname: review\ndescription: Must not be emitted.\n---\n"
                "`$SKILL_DIR/scripts/runner.py`\n",
                encoding="utf-8",
            )
            original_resolve = Path.resolve

            def expiring_resolve(path: Path, *args, **kwargs):
                value = original_resolve(path, *args, **kwargs)
                if path == runner:
                    clock["now"] = 2.0
                return value

            with patch.object(Path, "resolve", new=expiring_resolve):
                result = module.discover_standalone_skills(
                    [module.ConfiguredSkillRoot(
                        root, "standalone-user", "user:fixture",
                    )],
                    limits=module.DiscoveryLimits(max_runtime_seconds=1.0),
                    monotonic=lambda: clock["now"],
                )

        self.assertEqual((), result.skills)
        self.assertIn("runtime-limit", {item.code for item in result.diagnostics})

    def test_skips_skill_when_multi_reference_is_file_check_expires(self) -> None:
        module = importlib.import_module("plugin_compass.adapters.standalone")
        clock = {"now": 0.0}
        with tempfile.TemporaryDirectory(prefix="Plugin Compass F2 ") as temp:
            root = Path(temp) / "skills"
            skill = root / "review"
            skill.mkdir(parents=True)
            alpha = skill / "scripts" / "alpha.py"
            (skill / "SKILL.md").write_text(
                "---\nname: review\ndescription: Must not be emitted.\n---\n"
                "`$SKILL_DIR/scripts/alpha.py`\n"
                "`$SKILL_DIR/scripts/bravo.py`\n",
                encoding="utf-8",
            )
            original_check = module._regular_file_no_follow

            def expiring_check(path: Path, checked_root: Path) -> bool | None:
                value = original_check(path, checked_root)
                if path == alpha:
                    clock["now"] = 2.0
                return value

            with patch.object(module, "_regular_file_no_follow", new=expiring_check):
                result = module.discover_standalone_skills(
                    [module.ConfiguredSkillRoot(
                        root, "standalone-project", "project:fixture",
                    )],
                    limits=module.DiscoveryLimits(max_runtime_seconds=1.0),
                    monotonic=lambda: clock["now"],
                )

        self.assertEqual((), result.skills)
        self.assertIn("runtime-limit", {item.code for item in result.diagnostics})

if __name__ == "__main__":
    unittest.main()
