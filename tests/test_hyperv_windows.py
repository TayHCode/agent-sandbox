from pathlib import Path
import base64
import json
import re
import subprocess

from sandbox.hyperv_windows import HyperVWindows
from sandbox.run import gc, run
from sandbox.settings import WINDOWS_BASE
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
            elif action == 'leftovers':
                stdout = json.dumps([VM]).encode()
            elif action == 'list':
                stdout = json.dumps([VM] if self.exists else []).encode()
        elif self.fail == 'scp':
            code, stderr = 1, b'Copy failed'
        elif argv[0] == 'scp' and ':' not in argv[-1]:
            Path(argv[-1]).write_text('x')
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
        assert req['image'] == WINDOWS_BASE
        assert req['cpus'] == '3' and req['memory_mb'] == '5120'
        assert len(req['password']) == 68
        assert [r['action'] for r in commands.requests] == ['create', 'ready', 'ready']
        assert commands.requests[-1]['password'] == req['password']
        b.destroy(VM)
        assert not b.guests
        assert b.list_owned() == []

    def test_create_survives_a_brief_stopped_state_during_setup_reboot(self):
        self.commands.ready = ['stopped', 'stopped', False, True]
        HyperVWindows().create(VM, None, 2, 4096)
        assert [r['action'] for r in self.commands.requests].count('ready') == 4

    def test_create_fails_when_guest_stays_stopped(self):
        clock = iter(range(0, 10000, 30))
        self.monkeypatch.setattr('sandbox.hyperv_windows.time.monotonic', lambda: next(clock))
        self.commands.ready = ['stopped'] * 50
        with self.assertRaisesRegex(RuntimeError, 'stopped before readiness'):
            HyperVWindows().create(VM, None, 2, 4096)

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
                (self.tmp_path / 'a b').write_text('x')
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
                (self.tmp_path / 'in').write_text('x')
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

    def test_leftover_directories_without_a_vm_are_listed(self):
        assert HyperVWindows().list_leftovers() == [VM]
        assert self.commands.requests[-1]['action'] == 'leftovers'

    def test_run_exit_and_gc_need_no_credentials(self):
        commands = self.commands
        b = HyperVWindows()
        result = run(b, lambda backend, vm: backend.exec(vm, ['cmd', '/c', 'exit 7']))
        assert result['returncode'] == 7 and result['cleaned']
        commands.exists = True
        leases = self.tmp_path / 'leases'
        leases.mkdir(exist_ok=True)
        (leases / VM).write_text('{}')
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


class TestBootOrder(Case):

    def test_guest_boots_from_its_disk_before_the_network(self):
        script = Path('sandbox/hyperv_windows.ps1').read_text()
        firmware = [line for line in script.splitlines() if 'Set-VMFirmware' in line]
        assert len(firmware) == 1
        assert '-FirstBootDevice (Get-VMHardDiskDrive -VMName $name)' in firmware[0]
        assert script.index('Add-VMHardDiskDrive') < script.index(firmware[0]) < script.index('Start-VM -Name')


class TestPostOobeBase(Case):

    def script(self):
        return Path('sandbox/hyperv_windows.ps1').read_text()

    def builder(self):
        return Path('scripts/make-windows-base.ps1').read_text()

    def test_default_base_is_the_post_oobe_one(self):
        assert WINDOWS_BASE == 'C:\\sandbox\\base\\windows11-eval-oobe-v2.vhdx'

    def test_default_create_requests_the_default_base(self):
        commands = Commands()
        self.monkeypatch.setattr(subprocess, 'run', commands)
        self.monkeypatch.setenv('SANDBOX_HYPERV_HOST', 'host')
        HyperVWindows().create(VM, None, 2, 4096)
        assert commands.requests[0]['image'] == WINDOWS_BASE

    def test_finished_base_gets_its_own_name_guid_and_password_without_specialize(self):
        script = self.script()
        assert 'IMAGE_STATE_COMPLETE' in script
        assert 'NV Hostname' in script and 'MachineGuid' in script
        assert '\\Control\\ComputerName\\ComputerName' in script
        assert "'sbx-init.json'" in script

    def test_sysprep_base_still_uses_the_unattend_path(self):
        script = self.script()
        assert 'IMAGE_STATE_GENERALIZE_RESEAL_TO_OOBE' in script and '<ComputerName>SBX-' in script

    def test_unknown_base_state_is_refused(self):
        assert 'Base must be generalized and awaiting OOBE, or finished with OOBE' in self.script()

    def test_password_file_is_removed_when_the_guest_is_ready(self):
        assert "Remove-Item -LiteralPath 'C:\\Windows\\Panther\\sbx-init.json'" in self.script()

    def test_builder_bakes_no_known_password(self):
        builder = self.builder()
        assert 'PostOobeFrom' in builder and 'windows11-eval-oobe-v2.vhdx' in builder
        assert builder.index('Set-LocalUser -Name sandboxadmin') < builder.index('IMAGE_STATE_COMPLETE')
        assert 'SandboxFirstBoot' in builder and 'Remove-Item -LiteralPath $file -Force' in builder


class TestReadyGate(Case):

    def ready_action(self):
        script = Path('sandbox/hyperv_windows.ps1').read_text()
        return script[script.index("if ($Request.action -eq 'ready') {"):script.index("'exec' {")]

    def test_no_session_is_attempted_before_the_guest_has_a_heartbeat_and_has_set_its_password(self):
        action = self.ready_action()
        gate = action.index('Heartbeat')
        assert gate < action.index('New-PSSession')
        assert 'Uptime.TotalSeconds -lt 15' in action[gate:action.index('New-PSSession')]

    def test_session_waits_for_the_guest_ready_value_or_falls_back_to_uptime(self):
        action = self.ready_action()
        session = action.index('New-PSSession')
        assert 'Msvm_KvpExchangeComponent' in action[:session] and "sbx_ready" in action[:session]
        assert 'Uptime.TotalSeconds -lt 15' in action[:session]
        assert '-not $signalled -and $vm.Uptime.TotalSeconds -lt 15' in action


class TestGuestReadySignal(Case):

    def firstboot(self):
        builder = Path('scripts/make-windows-base.ps1').read_text()
        return builder[builder.index("$firstBoot = @'"):builder.index("'@\n    try {")]

    def test_guest_writes_the_value_only_after_it_has_set_the_password(self):
        firstboot = self.firstboot()
        write = firstboot.index('sbx_ready')
        assert firstboot.index('Set-LocalUser') < write
        assert firstboot.index('Remove-Item -LiteralPath $file') < write
        assert 'HKLM:\\SOFTWARE\\Microsoft\\Virtual Machine\\Guest' in firstboot

    def test_value_carries_a_timestamp_and_no_password(self):
        write = next(line for line in self.firstboot().splitlines() if 'sbx_ready' in line)
        assert 'UtcNow' in write and '$password' not in write
