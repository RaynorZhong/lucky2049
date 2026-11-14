#!/usr/bin/env python3
"""Exercise the actual manual publisher with an isolated export and fake remotes.

The script runs from a disposable repository, never the real site directory.
Python/export, curl and git are intercepted; the guard itself runs unchanged.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")


@unittest.skipUnless(BASH, "bash not available")
class TestManualPublishGuard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        (self.repo / "scripts").mkdir()
        shutil.copy2(REPO_ROOT / "scripts/publish-pages.sh", self.repo / "scripts/publish-pages.sh")
        self.bin = self.repo / "bin"
        self.bin.mkdir()
        (self.repo / ".venv/bin").mkdir(parents=True)
        self.calls = self.repo / "calls.jsonl"
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ.get("PATH", ""),
                        PUBLISH_TEST_CALLS=str(self.calls))

        self._program(self.repo / ".venv/bin/python", """
import json, os, sys
with open(os.environ['PUBLISH_TEST_CALLS'], 'a') as f:
    f.write(json.dumps(['python'] + sys.argv[1:]) + '\\n')
if sys.argv[1] == 'scripts/export_static.py':
    out = sys.argv[sys.argv.index('--out') + 1]
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, 'index.json'), 'w') as f:
        f.write(os.environ['PUBLISH_TEST_INDEX'])
else:
    os.execv(sys.executable, [sys.executable] + sys.argv[1:])
""")
        self._program(self.bin / "curl", """
import os, sys
sys.stdout.write(os.environ['PUBLISH_TEST_HEAD'])
sys.exit(int(os.environ.get('PUBLISH_TEST_CURL_RC', '0')))
""")
        self._program(self.bin / "git", """
import json, os, sys
with open(os.environ['PUBLISH_TEST_CALLS'], 'a') as f:
    f.write(json.dumps(['git'] + sys.argv[1:]) + '\\n')
if sys.argv[1:] == ['remote', 'get-url', 'origin']:
    print('https://github.com/example/repo.git')
elif sys.argv[1] not in ('init', 'checkout', 'add', '-c', 'push'):
    sys.exit('unexpected git call: ' + repr(sys.argv[1:]))
""")

    def _program(self, path, source):
        path.write_text("#!" + sys.executable + "\n" + source)
        path.chmod(0o755)

    def _run(self, draws, head, *args, curl_rc=0):
        self.env.update(PUBLISH_TEST_INDEX=json.dumps({"draws": draws}),
                        PUBLISH_TEST_HEAD=head if isinstance(head, str) else json.dumps(head),
                        PUBLISH_TEST_CURL_RC=str(curl_rc))
        result = subprocess.run([BASH, "scripts/publish-pages.sh", *args], cwd=self.repo,
                                env=self.env, text=True, capture_output=True, timeout=10)
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        pushed = any(call[:2] == ["git", "push"] for call in calls)
        return result, pushed

    def test_identical_snapshot_passes_and_reaches_publish(self):
        result, pushed = self._run([{"id": 0, "commitment": "c0"}],
                                   {"draw_id": 0, "head": "c0"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Guard OK", result.stdout)
        self.assertTrue(pushed)

    def test_extended_snapshot_passes_and_reaches_publish(self):
        result, pushed = self._run([{"id": 0, "commitment": "c0"},
                                   {"id": 1, "commitment": "c1"}],
                                   {"draw_id": 0, "head": "c0"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Guard OK", result.stdout)
        self.assertTrue(pushed)

    def test_stale_snapshot_aborts_before_publish(self):
        result, pushed = self._run([{"id": 0, "commitment": "c0"}],
                                   {"draw_id": 1, "head": "c1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ABORT", result.stderr)
        self.assertFalse(pushed)

    def test_diverged_snapshot_aborts_before_publish(self):
        result, pushed = self._run([{"id": 0, "commitment": "changed"}],
                                   {"draw_id": 0, "head": "c0"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ABORT", result.stderr)
        self.assertFalse(pushed)

    def test_malformed_live_head_aborts_before_publish(self):
        result, pushed = self._run([{"id": 0, "commitment": "c0"}], "not JSON")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ABORT", result.stderr)
        self.assertFalse(pushed)

    def test_force_still_explicitly_bypasses_guard(self):
        result, pushed = self._run([], "not JSON", "--force")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--force given", result.stdout)
        self.assertTrue(pushed)

    def test_unreachable_live_head_keeps_first_deploy_behavior(self):
        result, pushed = self._run([], "", curl_rc=22)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("No live head.json reachable", result.stdout)
        self.assertTrue(pushed)


if __name__ == "__main__":
    unittest.main()
