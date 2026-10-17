import subprocess
from unittest import mock

from sandbox import backend
from tests.support import Case


class RunAsInvoker(Case):
    def calls(self, argv, user, euid):
        self.monkeypatch.setenv('SUDO_USER', user) if user else self.monkeypatch.delenv('SUDO_USER', raising=False)
        done = subprocess.CompletedProcess(argv, 0, b'', b'')
        with mock.patch('os.geteuid', return_value=euid), mock.patch('subprocess.run', return_value=done) as run:
            backend.sh(argv)
        return run.call_args.args[0]

    def test_root_remote_calls_drop_to_the_invoking_user(self):
        argv = self.calls(['ssh', 'example-host', 'true'], 'nobody', 0)
        self.assertEqual(argv[:5], ['runuser', '-u', 'nobody', '--', 'env'])
        self.assertEqual(argv[-3:], ['ssh', 'example-host', 'true'])

    def test_other_calls_and_non_root_are_untouched(self):
        self.assertEqual(self.calls(['tar', '-cf', '-'], 'nobody', 0), ['tar', '-cf', '-'])
        self.assertEqual(self.calls(['ssh', 'example-host'], 'nobody', 1000), ['ssh', 'example-host'])
