from __future__ import annotations

import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPOSITORY_ROOT / "plugins" / "plugin-compass"
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


class StandaloneSkillOrderingTests(unittest.TestCase):
    def setUp(self) -> None:
        original_tempdir = tempfile.tempdir
        tempfile.tempdir = str(Path(tempfile.gettempdir()).resolve(strict=True))
        self.addCleanup(setattr, tempfile, "tempdir", original_tempdir)

    def test_root_order_is_total_when_source_identity_casefolds_collide(self) -> None:
        module = importlib.import_module("plugin_compass.adapters.standalone")
        with tempfile.TemporaryDirectory(prefix="Plugin Compass F2 ") as temp:
            root = Path(temp) / "skills"
            skill = root / "review" / "SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text(
                "---\nname: review\ndescription: Fixture.\n---\n",
                encoding="utf-8",
            )
            upper = module.ConfiguredSkillRoot(
                root, "standalone-user", "User:Fixture",
            )
            lower = module.ConfiguredSkillRoot(
                root, "standalone-user", "user:fixture",
            )

            first = module.discover_standalone_skills(
                [upper, lower], limits=module.DiscoveryLimits(max_skills=1),
            )
            second = module.discover_standalone_skills(
                [lower, upper], limits=module.DiscoveryLimits(max_skills=1),
            )

        self.assertEqual(
            [skill.qualified_identity for skill in first.skills],
            [skill.qualified_identity for skill in second.skills],
        )

    def test_root_order_is_total_when_normalized_slashes_collide(self) -> None:
        module = importlib.import_module("plugin_compass.adapters.standalone")
        with tempfile.TemporaryDirectory(prefix="Plugin Compass F2 ") as temp:
            root = Path(temp) / "skills"
            skill = root / "review" / "SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text(
                "---\nname: review\ndescription: Fixture.\n---\n",
                encoding="utf-8",
            )
            backslash = module.ConfiguredSkillRoot(
                root, "standalone-user", r"user\fixture",
            )
            slash = module.ConfiguredSkillRoot(
                root, "standalone-user", "user/fixture",
            )

            first = module.discover_standalone_skills(
                [backslash, slash], limits=module.DiscoveryLimits(max_skills=1),
            )
            second = module.discover_standalone_skills(
                [slash, backslash], limits=module.DiscoveryLimits(max_skills=1),
            )

        self.assertEqual(
            [skill.qualified_identity for skill in first.skills],
            [skill.qualified_identity for skill in second.skills],
        )

    def test_entry_order_is_total_when_unicode_casefolds_collide(self) -> None:
        module = importlib.import_module("plugin_compass.adapters.standalone")
        with tempfile.TemporaryDirectory(prefix="Plugin Compass F2 ") as temp:
            root = Path(temp) / "skills"
            eszett = root / "ß"
            double_s = root / "ss"
            try:
                eszett.mkdir(parents=True)
                double_s.mkdir()
            except FileExistsError:
                self.skipTest("filesystem does not distinguish the casefold-collision names")
            (eszett / "SKILL.md").write_text(
                "---\nname: eszett\ndescription: Fixture.\n---\n",
                encoding="utf-8",
            )
            (double_s / "SKILL.md").write_text(
                "---\nname: double-s\ndescription: Fixture.\n---\n",
                encoding="utf-8",
            )
            original_iterdir = Path.iterdir

            def discover_with_root_order(entries: list[Path]):
                def ordered_iterdir(path: Path):
                    if path == root:
                        return iter(entries)
                    return original_iterdir(path)

                with patch.object(Path, "iterdir", new=ordered_iterdir):
                    return module.discover_standalone_skills(
                        [module.ConfiguredSkillRoot(
                            root, "standalone-user", "user:fixture",
                        )],
                        limits=module.DiscoveryLimits(max_skills=1),
                    )

            first = discover_with_root_order([eszett, double_s])
            second = discover_with_root_order([double_s, eszett])

        self.assertEqual(
            [skill.qualified_identity for skill in first.skills],
            [skill.qualified_identity for skill in second.skills],
        )

    def test_conflicting_logical_root_identity_scans_neither_path(self) -> None:
        module = importlib.import_module("plugin_compass.adapters.standalone")
        with tempfile.TemporaryDirectory(prefix="Plugin Compass F2 ") as temp:
            base = Path(temp)
            roots = []
            for folder, name in (("root-b", "bravo"), ("root-a", "alpha")):
                root = base / folder
                skill = root / "review" / "SKILL.md"
                skill.parent.mkdir(parents=True)
                skill.write_text(
                    f"---\nname: {name}\ndescription: Fixture.\n---\n",
                    encoding="utf-8",
                )
                roots.append(module.ConfiguredSkillRoot(
                    root, "standalone-project", "project:fixture",
                ))

            first = module.discover_standalone_skills(roots)
            second = module.discover_standalone_skills(reversed(roots))

        self.assertEqual([], [skill.name for skill in first.skills])
        self.assertEqual(first.skills, second.skills)
        self.assertIn(
            "conflicting-root-identity",
            {item.code for item in first.diagnostics},
        )
        self.assertEqual("degraded", first.status)

    def test_exact_duplicate_logical_root_is_scanned_once(self) -> None:
        module = importlib.import_module("plugin_compass.adapters.standalone")
        with tempfile.TemporaryDirectory(prefix="Plugin Compass F3 ") as temp:
            root = Path(temp) / "skills"
            skill = root / "review" / "SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text(
                "---\nname: review\ndescription: Fixture.\n---\n",
                encoding="utf-8",
            )
            configured = module.ConfiguredSkillRoot(
                root, "standalone-user", "user:fixture",
            )

            result = module.discover_standalone_skills([configured, configured])

        self.assertEqual(["review"], [item.name for item in result.skills])
        self.assertIn("duplicate-root", {item.code for item in result.diagnostics})

    def test_unicode_casefold_distinct_root_paths_conflict(self) -> None:
        module = importlib.import_module("plugin_compass.adapters.standalone")
        with tempfile.TemporaryDirectory(prefix="Plugin Compass F3 ") as temp:
            base = Path(temp)
            eszett = base / "ß"
            double_s = base / "ss"
            try:
                eszett.mkdir()
                double_s.mkdir()
            except FileExistsError:
                self.skipTest("filesystem does not distinguish ß and ss root paths")
            for root, name in ((eszett, "eszett"), (double_s, "double-s")):
                skill = root / name / "SKILL.md"
                skill.parent.mkdir()
                skill.write_text(
                    f"---\nname: {name}\ndescription: Fixture.\n---\n",
                    encoding="utf-8",
                )
            roots = [
                module.ConfiguredSkillRoot(
                    eszett, "standalone-project", "project:unicode",
                ),
                module.ConfiguredSkillRoot(
                    double_s, "standalone-project", "project:unicode",
                ),
            ]

            first = module.discover_standalone_skills(roots)
            second = module.discover_standalone_skills(reversed(roots))

        self.assertEqual((), first.skills)
        self.assertEqual(first, second)
        self.assertEqual(
            ["conflicting-root-identity", "conflicting-root-identity"],
            [item.code for item in first.diagnostics],
        )

    def test_does_not_re_resolve_validated_skill_parent_before_append(self) -> None:
        module = importlib.import_module("plugin_compass.adapters.standalone")
        with tempfile.TemporaryDirectory(prefix="Plugin Compass F2 ") as temp:
            root = Path(temp) / "skills"
            skill_path = root / "review" / "SKILL.md"
            skill_path.parent.mkdir(parents=True)
            skill_path.write_text(
                "---\nname: review\ndescription: Fixture.\n---\n",
                encoding="utf-8",
            )
            original_resolve = Path.resolve

            def guarded_resolve(path: Path, *args, **kwargs):
                if path == skill_path.parent:
                    raise AssertionError("validated skill parent was re-resolved")
                return original_resolve(path, *args, **kwargs)

            try:
                with patch.object(Path, "resolve", new=guarded_resolve):
                    result = module.discover_standalone_skills([
                        module.ConfiguredSkillRoot(
                            root, "standalone-project", "project:fixture",
                        )
                    ])
            except AssertionError as exc:
                self.fail(str(exc))

        self.assertEqual(1, len(result.skills))

    def test_root_inspection_expiry_wins_over_terminal_missing_result(self) -> None:
        module = importlib.import_module("plugin_compass.adapters.standalone")
        clock = {"now": 0.0}
        with tempfile.TemporaryDirectory(prefix="Plugin Compass F2 ") as temp:
            root = Path(temp) / "skills"
            root.mkdir()
            original_is_dir = Path.is_dir

            def expiring_is_dir(path: Path) -> bool:
                value = original_is_dir(path)
                if path == root:
                    clock["now"] = 2.0
                    return False
                return value

            with patch.object(Path, "is_dir", new=expiring_is_dir):
                result = module.discover_standalone_skills(
                    [module.ConfiguredSkillRoot(
                        root, "standalone-user", "user:fixture",
                    )],
                    limits=module.DiscoveryLimits(max_runtime_seconds=1.0),
                    monotonic=lambda: clock["now"],
                )

        self.assertEqual((), result.skills)
        self.assertEqual(["runtime-limit"], [item.code for item in result.diagnostics])


if __name__ == "__main__":
    unittest.main()
