#!/usr/bin/env python3
"""Finite synthetic SQLite/physical publication controls; no APIs or live corpus."""
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

HERE = Path(__file__).parent
EXPORTERS = HERE / 'kavita-exporters'
ORIGINALS = {
    'reading-state-readonly.py': '8c61b873374fdb147625afb8c19e46713a88963dd69dc97d50d7ae7a1b82cb32',
    'kavita-dependencies-readonly.py': 'e1b5b2ed160e619df7955dcb1ec11d457b9ac1ff4121885397758f99d16fb9b5',
    'kavita-locks-readonly.py': '474735f82763b9beae897d91f5e6c4806381f70c25cdb52d3a34875831a15c45',
}
WINDOW = HERE.parent / 'copy-window'
sys.path.insert(0, str(WINDOW))
spec = importlib.util.spec_from_file_location('finite_vendor_seal', WINDOW / 'copy-phase-supervisor.py')
supervisor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(supervisor)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def writer(name):
    # Load the real publication function without the scripts' CLI/database work.
    tree = ast.parse((EXPORTERS / name).read_text())
    nodes = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))
             or isinstance(node, ast.FunctionDef) and node.name == 'write_private_text']
    module = types.ModuleType('finite_writer')
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(EXPORTERS / name), 'exec'), module.__dict__)
    return module.write_private_text


class PrivateExports(unittest.TestCase):
    def test_only_publication_diff_reverses_to_exact_original_sources(self):
        writers = []
        for name, expected in ORIGINALS.items():
            raw = (EXPORTERS / name).read_text()
            node = next(n for n in ast.parse(raw).body
                        if isinstance(n, ast.FunctionDef) and n.name == 'write_private_text')
            writers.append(ast.dump(node, include_attributes=False))
            start = raw.index('\n\ndef write_private_text')
            end = sum(map(len, raw.splitlines(keepends=True)[:node.end_lineno]))
            restored = raw[:start] + raw[end:]
            restored = restored.replace('import os\nimport stat\n', '', 1)
            if name == 'reading-state-readonly.py':
                restored = restored.replace('write_private_text(args.out, ', 'Path(args.out).write_text(')
            elif name == 'kavita-dependencies-readonly.py':
                restored = restored.replace('from pathlib import Path\n', '', 1)
                restored = restored.replace('write_private_text(path, ', 'pathlib.Path(path).write_text(')
            else:
                restored = restored.replace('from pathlib import Path\n', '', 1)
                restored = restored.replace('write_private_text(a.out, ', 'a.out.write_text(')
            self.assertEqual(hashlib.sha256(restored.encode()).hexdigest(), expected, name)
        self.assertEqual(writers, [writers[0]] * 3)

    def fixture(self, root):
        db = root / 'kavita.db'
        fd = os.open(db, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        source = (EXPORTERS / 'kavita-dependencies-readonly.py').read_text()
        scope = {}
        for node in ast.parse(source).body:
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
                name = node.targets[0].id
                if name in ('DEPENDENCIES', 'PARENTS', 'DIAGNOSTICS'):
                    scope[name] = ast.literal_eval(node.value)
        tables = scope['DEPENDENCIES'] + scope['PARENTS'] + scope['DIAGNOSTICS']
        with sqlite3.connect(db) as con:
            for name in tables:
                extra = ', ChapterId INTEGER, AppUserId INTEGER, Value TEXT'
                if name == 'AppUserReadingSessionActivityData':
                    extra += ', AppUserReadingSessionId INTEGER'
                con.execute(f'CREATE TABLE "{name}" (Id INTEGER PRIMARY KEY{extra})')
            for name in ('AppUserProgresses', 'AppUserBookmark', 'AppUserAnnotation', 'AppUserReadingSession'):
                con.execute(f'INSERT INTO "{name}" VALUES(101,301,7,?)', ('synthetic user value',))
            con.execute('INSERT INTO AppUserReadingSessionActivityData VALUES(102,301,7,?,101)', ('synthetic activity',))
            con.execute('CREATE TABLE Series(Id INTEGER PRIMARY KEY,LibraryId INTEGER,Name TEXT,NameLocked INTEGER)')
            con.execute('INSERT INTO Series VALUES(201,1,?,1)', ('synthetic saved name',))
            con.execute('CREATE TABLE Volume(Id INTEGER PRIMARY KEY,SeriesId INTEGER)')
            con.execute('INSERT INTO Volume VALUES(251,201)')
            con.execute('CREATE TABLE Chapter(Id INTEGER PRIMARY KEY,VolumeId INTEGER)')
            con.execute('INSERT INTO Chapter VALUES(301,251)')
            con.execute('CREATE TABLE MangaFile(Id INTEGER PRIMARY KEY,ChapterId INTEGER,FilePath TEXT)')
            con.execute('INSERT INTO MangaFile VALUES(401,301,?)', ('/data/cephfs-hdd/data/media/books/EBooks/synthetic.epub',))
            con.execute('CREATE TABLE SeriesMetadata(Id INTEGER PRIMARY KEY,SeriesId INTEGER)')
            con.execute('INSERT INTO SeriesMetadata VALUES(211,201)')
            con.execute('CREATE TABLE Person(Id INTEGER PRIMARY KEY)')
        proof = {'schema': 1, 'kind': 'kavita', 'phase_token': 'synthetic-phase',
                 'job_uid': 'synthetic-job', 'pod_uid': 'synthetic-pod', 'readOnlySource': True,
                 'sourceWrites': 0, 'before': {'synthetic': 1}, 'after': {'synthetic': 1},
                 'captureStartedAt': '2026-10-10T00:00:00+00:00', 'capturedAt': '2026-10-10T00:00:01+00:00',
                 'files': [{'name': db.name, 'size': db.stat().st_size, 'sha256': digest(db)}]}
        supervisor.private_json(root / 'copy-proof.json', proof)
        supervisor.private_json(root / 'approval.json', {'approval_sha256': 'a' * 64,
                                                       'plan': {'targets': [{'path': 'different.epub'}]}})
        return db

    def run_cli(self, name, *args):
        result = subprocess.run([sys.executable, '-B', str(EXPORTERS / name), *map(str, args)],
                                capture_output=True, text=True, umask=0, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def private_artifact(self, path):
        info = path.stat(follow_symlinks=False)
        self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
        self.assertEqual(info.st_uid, os.getuid())
        self.assertEqual(info.st_nlink, 1)
        return {'path': str(path), 'sha256': digest(path)}

    def test_real_exports_and_compares_are_private_and_pass_existing_seal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = self.fixture(root)
            original_db = digest(db)
            reading = root / 'reading-state-all.json'
            dependencies = root / 'dependencies.json'
            locks = root / 'saved-metadata-locks.json'
            self.run_cli('reading-state-readonly.py', 'export', '--db', db, '--out', reading)
            self.run_cli('kavita-dependencies-readonly.py', 'export', '--db', db, '--out', dependencies)
            self.run_cli('kavita-locks-readonly.py', '--db', db, '--approval', root / 'approval.json', '--out', locks)
            result = {'proof': self.private_artifact(root / 'copy-proof.json'),
                      'files': {'kavita.db': self.private_artifact(db)},
                      'raw_exports': {p.name: self.private_artifact(p) for p in (reading, dependencies, locks)}}
            sealed = supervisor.seal_vendor_payload(('media', 'synthetic-reader'), result,
                      {'uid': 'synthetic-job'}, 'synthetic-pod', 'synthetic-phase', 'unused')
            self.assertTrue(sealed['complete_copied_payload'])
            self.assertEqual(digest(db), original_db)
            before = json.loads(reading.read_bytes())
            self.assertTrue(all(count == 1 for count in before['tableCounts'].values()))
            self.assertEqual(before['tables']['AppUserProgresses'][0]['Value'], 'synthetic user value')
            self.assertEqual(json.loads(locks.read_bytes())['tables']['Series']['rows'][0]
                             ['savedLockedValues'], {'Name': 'synthetic saved name'})
            for script, source, filename in (('reading-state-readonly.py', reading, 'reading-compare.json'),
                                            ('kavita-dependencies-readonly.py', dependencies, 'dependency-compare.json')):
                out = root / 'new' / filename
                self.run_cli(script, 'compare', source, source, '--out', out)
                self.private_artifact(out)
                self.assertEqual(out.parent.stat().st_mode & 0o777, 0o700)
                report = json.loads(out.read_bytes())
                self.assertTrue(report.get('exactlyEqual', report.get('rawDependencyFieldsExactlyEqual')))
            # A repeat real CLI cannot overwrite an admitted output or alter its bytes.
            original = reading.read_bytes()
            attempt = subprocess.run([sys.executable, '-B', str(EXPORTERS / 'reading-state-readonly.py'),
                                      'export', '--db', str(db), '--out', str(reading)],
                                     capture_output=True, timeout=5)
            self.assertNotEqual(attempt.returncode, 0)
            self.assertEqual(reading.read_bytes(), original)

    def test_existing_links_and_unsafe_parents_refuse_without_repair(self):
        for name in ORIGINALS:
            publish = writer(name)
            with self.subTest(exporter=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                victim = root / 'victim'; victim.write_bytes(b'prior evidence')
                linked = root / 'hardlink'; os.link(victim, linked)
                symbolic = root / 'symlink'; symbolic.symlink_to(victim)
                folder = root / 'directory'; folder.mkdir(mode=0o700)
                unsafe = root / 'unsafe'; unsafe.mkdir(mode=0o755)
                unsafe_mode = unsafe.stat().st_mode
                alias = root / 'parent-alias'; alias.symlink_to(root, target_is_directory=True)
                for path in (victim, linked, symbolic, folder, unsafe / 'out.json', alias / 'out.json'):
                    with self.subTest(path=path.name), self.assertRaises((OSError, ValueError)):
                        publish(path, 'new evidence\n')
                self.assertEqual(victim.read_bytes(), b'prior evidence')
                self.assertEqual(victim.stat().st_nlink, 2)
                self.assertEqual(unsafe.stat().st_mode, unsafe_mode)
                self.assertFalse((unsafe / 'out.json').exists())
                self.assertFalse((root / 'out.json').exists())

    def test_file_link_and_parent_replacement_during_publication_refuse(self):
        for name in ORIGINALS:
            for change in ('hardlink', 'parent replacement'):
                with self.subTest(exporter=name, change=change), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory); parent = root / 'owned'; parent.mkdir(mode=0o700)
                    out = parent / 'out.json'; real_fsync = os.fsync
                    changed = False

                    def interfere(fd):
                        nonlocal changed
                        real_fsync(fd)
                        if not changed and stat.S_ISREG(os.fstat(fd).st_mode):
                            changed = True
                            if change == 'hardlink':
                                os.link(out, parent / 'foreign-link')
                            else:
                                parent.rename(root / 'replaced')
                                parent.mkdir(mode=0o700)

                    with mock.patch('os.fsync', side_effect=interfere), self.assertRaisesRegex(ValueError, 'changed'):
                        writer(name)(out, 'fresh evidence\n')
                    self.assertTrue(changed)
                    if change == 'parent replacement':
                        self.assertFalse(out.exists())
                        self.assertTrue((root / 'replaced' / 'out.json').exists())


if __name__ == '__main__':
    unittest.main()
