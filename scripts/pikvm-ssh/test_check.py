"""Offline argument and fail-closed checks; never connects or uses a real credential."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parent


class Check(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir='/dev/shm')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.tool = self.root / 'tool'
        shutil.copytree(SOURCE, self.tool)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        ssh = self.bin / 'ssh'
        ssh.write_text('#!/usr/bin/env python3\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n')
        ssh.chmod(0o700)
        self.env = dict(os.environ, XDG_RUNTIME_DIR=str(self.root),
                        PATH=str(self.bin) + ':' + os.environ['PATH'])
        self.key = self.root / 'dev-env-hwssh/grant-offline/key'
        self.key.parent.mkdir(parents=True)
        self.key.touch()  # empty fixture; no credential material

    def run_check(self, *args):
        return subprocess.run([str(self.tool / 'check.sh'), *args], env=self.env,
                              text=True, capture_output=True)

    def test_fixed_command_and_effective_ssh_settings(self):
        result = self.run_check('grant-offline')
        self.assertEqual(result.returncode, 0, result.stderr)
        args = json.loads(result.stdout)
        for option in ('KnownHostsCommand=none', 'ControlPath=none'):
            self.assertIn(option, args)
        self.assertEqual(args[-2:], ['root@192.168.40.33', 'hostname; id; date -u'])
        real = shutil.which('ssh')
        config = subprocess.check_output([real, '-G', *args[:-1]], text=True)
        for setting in ('stricthostkeychecking true', 'hostkeyalgorithms ssh-ed25519',
                        'hostkeyalias pikvm.haynesnetwork', 'identityagent none',
                        'identitiesonly yes', 'updatehostkeys false', 'verifyhostkeydns false',
                        'globalknownhostsfile /dev/null',
                        'passwordauthentication no', 'kbdinteractiveauthentication no',
                        'clearallforwardings yes', 'forwardagent no'):
            self.assertIn(setting + '\n', config)
        self.assertIn('userknownhostsfile ' + str(self.tool / 'known_hosts'), config)
        self.assertIn('identityfile ' + str(self.key), config)

    def test_missing_or_symlink_credential_refuses_before_ssh(self):
        self.key.unlink()
        self.assertNotEqual(self.run_check('grant-offline').returncode, 0)
        self.key.symlink_to('/dev/null')
        self.assertNotEqual(self.run_check('grant-offline').returncode, 0)

    def test_changed_or_extra_public_trust_refuses_before_ssh(self):
        trust = self.tool / 'known_hosts'
        original = trust.read_text()
        trust.write_text(original.replace('pikvm.haynesnetwork', 'other'))
        self.assertNotEqual(self.run_check('grant-offline').returncode, 0)
        trust.write_text(original + original)
        self.assertNotEqual(self.run_check('grant-offline').returncode, 0)

    def test_no_command_or_path_injection(self):
        for args in ((), ('grant-../x',), ('grant-offline', 'reboot'), ('/tmp/key',)):
            self.assertEqual(self.run_check(*args).returncode, 2)


if __name__ == '__main__':
    unittest.main()
