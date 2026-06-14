#!/usr/bin/env python3
"""
Locks the homepage's next-draw ETA window (inline in web/index.html). The live
economic-security disclosure that used to live here was removed from the homepage
(the argument now lives only in SPEC.md §8, linked from the footer), so this just
guards the ETA target: the last block of the next 144-block window + 6 confirmations.
"""
import os
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(REPO_ROOT, "web", "index.html")


class TestIndexEta(unittest.TestCase):
    def test_next_draw_eta_targets_window_end_plus_6_confirmations(self):
        with open(INDEX) as f:
            html = f.read()
        self.assertRegex(html, r"nextId \* 144 \+ 143 \+ 6")


if __name__ == "__main__":
    unittest.main()
