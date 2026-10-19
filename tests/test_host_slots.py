import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from sandbox import lease


class HostSlots(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name) / "host"
        self.directory.mkdir()
        patch = mock.patch.object(lease, "SLOT_ROOT", Path(self.tmp.name))
        patch.start()
        self.addCleanup(patch.stop)

    def test_no_limit_file_means_no_pooling(self):
        self.assertIsNone(lease.acquire_host_slot("host"))

    def test_second_holder_waits_for_a_free_slot(self):
        lease.publish_host_limit("host", 1)
        first = lease.acquire_host_slot("host")
        got = []
        real_sleep = time.sleep
        with mock.patch.object(lease.time, "sleep", lambda s: real_sleep(0.01)):
            thread = threading.Thread(target=lambda: got.append(lease.acquire_host_slot("host")))
            thread.start()
            real_sleep(0.1)
            self.assertEqual(got, [])
            os.close(first)
            thread.join(2)
        self.assertEqual(len(got), 1)
        os.close(got[0])

    def test_acquiring_never_creates_slot_files(self):
        (self.directory / "limit").write_text("2")
        with self.assertRaises(FileNotFoundError):
            lease.acquire_host_slot("host")
        self.assertEqual(sorted(p.name for p in self.directory.iterdir()), ["limit"])

    def test_publishing_creates_every_slot_and_the_limit(self):
        lease.publish_host_limit("host", 3)
        self.assertEqual(sorted(p.name for p in self.directory.iterdir()), ["limit", "slot-0", "slot-1", "slot-2"])
        os.close(lease.acquire_host_slot("host"))


if __name__ == "__main__":
    unittest.main()
