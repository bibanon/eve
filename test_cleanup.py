import unittest
import os
import shutil

import cleanup

class TestCleanup(unittest.TestCase):
    def test_cleanup(self):
        cleanupDir = "cleanup_test"
        os.mkdir(cleanupDir)
        for i in ["a", "b_tmp", "c_tmp", "_tmpd"]:
            open(os.path.join(cleanupDir, i), "a").close()
        cleanup.cleanup(cleanupDir)
        self.assertEqual(sorted(["a", "_tmpd"]), sorted(os.listdir(cleanupDir)))
        shutil.rmtree(cleanupDir)
