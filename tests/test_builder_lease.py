from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "plugins" / "compass-builder"
if str(BUILDER) not in sys.path:
    sys.path.insert(0, str(BUILDER))

from compass_builder.lease import (  # noqa: E402
    LeaseError, acquire_lease, inspect_lease, release_lease,
)


class BuilderLeaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.common = (Path(self.temporary.name) / "common.git").resolve()
        self.common.mkdir()
        self.arguments = {
            "owner_id": "controller-one", "evidence_digest": "sha256:" + "a" * 64,
            "acquired_at": "2026-09-01T12:00:00Z", "expires_at": "2026-09-01T12:05:00Z",
            "owner_pid": 1234, "token": "b" * 64,
        }

    def tearDown(self):
        self.temporary.cleanup()

    def test_exclusive_acquire_and_exact_owner_release(self):
        handle = acquire_lease(self.common, "cb/integration", **self.arguments)
        record = inspect_lease(self.common, "cb/integration", now="2026-09-01T12:01:00Z")
        self.assertEqual("controller-one", record["ownerId"])
        with self.assertRaisesRegex(LeaseError, "already leased"):
            acquire_lease(self.common, "cb/integration", **self.arguments)
        release_lease(handle)
        self.assertFalse(handle.path.exists())

    def test_stale_and_malformed_records_fail_closed_without_stealing(self):
        handle = acquire_lease(self.common, "cb/integration", **self.arguments)
        with self.assertRaisesRegex(LeaseError, "no safe automatic stale-recovery"):
            inspect_lease(self.common, "cb/integration", now="2026-09-01T12:06:00Z")
        self.assertTrue(handle.path.exists())
        later = dict(self.arguments)
        later.update(acquired_at="2026-09-01T12:06:00Z", expires_at="2026-09-01T12:11:00Z")
        with self.assertRaisesRegex(LeaseError, "no safe automatic stale-recovery"):
            acquire_lease(self.common, "cb/integration", **later)
        self.assertTrue(handle.path.exists())
        handle.path.write_text("{bad", encoding="utf-8")
        with self.assertRaisesRegex(LeaseError, "malformed"):
            inspect_lease(self.common, "cb/integration", now=None)
        with self.assertRaisesRegex(LeaseError, "malformed"):
            acquire_lease(self.common, "cb/integration", **self.arguments)
        self.assertTrue(handle.path.exists())

    def test_release_refuses_changed_record_or_replaced_file_identity(self):
        handle = acquire_lease(self.common, "cb/integration", **self.arguments)
        altered = dict(handle.record)
        altered["ownerId"] = "controller-two"
        handle.path.write_text(json.dumps(altered), encoding="utf-8")
        with self.assertRaisesRegex(LeaseError, "ownership|record"):
            release_lease(handle)
        self.assertTrue(handle.path.exists())

        handle.path.unlink()
        handle.path.write_text(json.dumps(handle.record), encoding="utf-8")
        with self.assertRaisesRegex(LeaseError, "identity changed"):
            release_lease(handle)
        self.assertTrue(handle.path.exists())

    def test_replacement_after_release_ownership_check_survives(self):
        handle = acquire_lease(self.common, "cb/integration", **self.arguments)
        def inject_replacement(_tombstone: Path):
            handle.path.write_text(json.dumps(handle.record), encoding="utf-8")

        with patch(
            "compass_builder.lease._before_tombstone_delete",
            side_effect=inject_replacement,
        ):
            with self.assertRaisesRegex(LeaseError, "changed|replacement|identity"):
                release_lease(handle)
        self.assertTrue(handle.path.exists())

    def test_common_directory_and_lease_root_reparse_points_are_rejected(self):
        with patch("compass_builder.lease._is_reparse", return_value=True):
            with self.assertRaisesRegex(LeaseError, "reparse point"):
                acquire_lease(self.common, "cb/integration", **self.arguments)


if __name__ == "__main__":
    unittest.main()
