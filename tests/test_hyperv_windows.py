import base64
import json
import re
import subprocess

from sandbox.hyperv_windows import HyperVWindows
from sandbox.run import gc, run
from tests.support import Case


VM = 'sbx-1700000000-aabbcc'


class Commands:

    def __init__(self):
        self.calls = []
        self.requests = []
        self.exists = False
        self.ready = [False, True]
        self.fail = None

    def __call__(self, argv, input=None, capture_output=False):
        assert capture_output
        self.calls.append((argv, input))
        stdout, stderr, code = b'', b'', 0
        if 'powershell' in argv:
            script = input.decode()
            assert argv[-4:] == ['powershell', '-NoProfile', '-Command', '-']
            assert 'Console.InputEncoding' not in script
            encoded = re.search(r"FromBase64String\('([^']+)'\)", script)[1]
            req = json.loads(base64.b64decode(encoded))
            self.requests.append(req)
            action = req['action']
            if action == self.fail:
                code, stderr = 1, b'Windows failure'
            elif action == 'create':
                self.exists = True
            elif action == 'ready':
                stdout = json.dumps(self.ready.pop(0)).encode()
            elif action == 'exec':
                stdout = json.dumps(dict(returncode=7, stdout=base64.b64encode(b'output\x00\xff').decode(),
                                         stderr=base64.b64encode(b'error\r\n').decode())).encode()
            elif action == 'destroy':
                self.exists = False
            elif action == 'list':
                stdout = json.dumps([VM] if self.exists else []).encode()
        elif self.fail == 'scp':
            code, stderr = 1, b'Copy failed'
        return subprocess.CompletedProcess(argv, code, stdout, stderr)


class TestHypervWindows(Case):

    def setUp(self):
        super().setUp()
        commands = Commands()
        self.monkeypatch.setattr(subprocess, 'run', commands)
        self.monkeypatch.setattr('sandbox.hyperv_windows.time.sleep', lambda seconds: None)
        self.commands = commands

    def test_create_waits_and_uses_disposable_password(self):
        commands = self.commands
        b = HyperVWindows()
        b.create(VM, None, 3, 5120)
        req = commands.requests[0]
        assert req['image'] == r'C:\sandbox\base\windows11-eval.vhdx'
        assert req['cpus'] == '3' and req['memory_mb'] == '5120'
        assert len(req['password']) == 68
        assert [r['action'] for r in commands.requests] == ['create', 'ready', 'ready']
        assert commands.requests[-1]['password'] == req['password']
        b.destroy(VM)
        assert not b.guests
        assert b.list_owned() == []

    def test_exec_preserves_bytes_arguments_and_exit(self):
        commands = self.commands
        b = HyperVWindows()
        b.create(VM, r'D:\custom.vhdx', 2, 4096)
        argv = ['cmd', '/c', 'echo a & exit 7']
        result = b.exec(VM, argv, b'\x00\xffstdin')
        assert result.returncode == 7
        assert result.stdout == b'output\x00\xff'
        assert result.stderr == b'error\r\n'
        req = commands.requests[-1]
        assert req['executable'] == 'cmd'
        assert req['arguments'] == subprocess.list2cmdline(argv[1:])
        assert base64.b64decode(req['stdin']) == b'\x00\xffstdin'
        assert commands.requests[0]['image'] == r'D:\custom.vhdx'

    def test_file_transfers_stage_and_remove_files(self):
        commands = self.commands
        b = HyperVWindows()
        b.create(VM, None, 2, 4096)
        for method in ('put', 'get'):
            if method == 'put':
                b.put(VM, self.tmp_path / 'a b', 'a b')
            else:
                b.get(VM, 'a b', self.tmp_path / 'a b')
            reqs = commands.requests[-3:]
            assert [r['action'] for r in reqs] == ['stage', method, 'unstage']
            assert len({r['stage'] for r in reqs}) == 1
            assert reqs[1]['remote'] == 'a b'
            scp = next(a for a, _ in reversed(commands.calls) if a[0] == 'scp')
            assert scp[:5] == ['scp', '-r', '-o', 'BatchMode=yes', '--']
            assert any(f'hyperv-host:C:/sandbox/vms/{VM}/' in a for a in scp)

    def _test_transfer_failure_unstages(self, failure_):
        commands = self.commands
        failure = failure_
        b = HyperVWindows()
        b.create(VM, None, 2, 4096)
        commands.fail = failure
        with self.assertRaises(RuntimeError):
            if failure == 'get':
                b.get(VM, 'file', self.tmp_path / 'out')
            else:
                b.put(VM, self.tmp_path / 'in', 'file')
        assert commands.requests[-1]['action'] == 'unstage'

    def test_transfer_failure_unstages_scp(self):
        self._test_transfer_failure_unstages('scp')

    def test_transfer_failure_unstages_put(self):
        self._test_transfer_failure_unstages('put')

    def test_transfer_failure_unstages_get(self):
        self._test_transfer_failure_unstages('get')

    def test_create_failure_finally_destroys(self):
        commands = self.commands
        commands.fail = 'create'
        b = HyperVWindows()
        with self.assertRaisesRegex(RuntimeError, 'Windows failure'):
            run(b, lambda backend, vm: backend.exec(vm, ['cmd', '/c', 'ver']))
        assert [r['action'] for r in commands.requests] == ['create', 'destroy', 'list']
        assert not b.guests

    def test_run_exit_and_gc_need_no_credentials(self):
        commands = self.commands
        b = HyperVWindows()
        result = run(b, lambda backend, vm: backend.exec(vm, ['cmd', '/c', 'exit 7']))
        assert result['returncode'] == 7 and result['cleaned']
        commands.exists = True
        assert gc(HyperVWindows()) == [VM]
        assert commands.requests[-1] == dict(action='destroy', vm=VM)

    def test_destroy_failure_forgets_password(self):
        commands = self.commands
        b = HyperVWindows()
        b.create(VM, None, 2, 4096)
        commands.fail = 'destroy'
        with self.assertRaises(RuntimeError):
            b.destroy(VM)
        assert not b.guests

    def test_request_values_are_framed_data(self):
        commands = self.commands
        image = "C:\\a'; Stop-VM other-vm; '"
        b = HyperVWindows()
        b.create(VM, image, 2, 4096)
        assert commands.requests[0]['image'] == image
        assert image not in commands.calls[0][1].decode()
