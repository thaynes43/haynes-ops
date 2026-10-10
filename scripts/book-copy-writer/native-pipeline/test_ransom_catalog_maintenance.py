"""Small SQLite/fake-fence controls only; serial nice19, no load or APIs."""
import ast
import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "kubernetes/main/apps/downloads/lazylibrarian/app/epub-convert"))
import ransom_catalog_maintenance as catalog
import ransom_maintenance_job as maintenance


class CatalogMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "EBooks"
        self.root.mkdir()
        self.retention = self.base / ".epub-convert" / "ransom-catalog"
        self.retention.mkdir(parents=True)
        self.path = self.base / "kavita.sqlite"
        self.db = sqlite3.connect(self.path, isolation_level=None)
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript('''
          CREATE TABLE Series(Id INTEGER PRIMARY KEY,LibraryId INTEGER,Format INTEGER,
            Name TEXT,NormalizedName TEXT,OriginalName TEXT,SortName TEXT,
            SortNameLocked INTEGER,UnchangedNullable TEXT,UnchangedBlob BLOB);
          CREATE TABLE SeriesMetadata(Id INTEGER PRIMARY KEY REFERENCES Series(Id),MetadataLocked INTEGER);
          CREATE TABLE Volume(Id INTEGER PRIMARY KEY,SeriesId INTEGER REFERENCES Series(Id),
            LookupName TEXT,Name TEXT,MinNumber REAL,MaxNumber REAL,CoverImageLocked INTEGER);
          CREATE TABLE Chapter(Id INTEGER PRIMARY KEY,VolumeId INTEGER REFERENCES Volume(Id),
            Count INTEGER,CoverImageLocked INTEGER);
          CREATE TABLE MangaFile(Id INTEGER PRIMARY KEY,ChapterId INTEGER REFERENCES Chapter(Id),FilePath TEXT);
          CREATE TABLE AppUserProgresses(Id INTEGER PRIMARY KEY,LibraryId INTEGER,
            SeriesId INTEGER REFERENCES Series(Id),VolumeId INTEGER REFERENCES Volume(Id),
            ChapterId INTEGER REFERENCES Chapter(Id),LastModifiedUtc TEXT,BookScrollId TEXT);
          CREATE TABLE AppUserReadingSession(Id INTEGER PRIMARY KEY,IsActive INTEGER);
          CREATE TABLE AppUserReadingSessionActivityData(Id INTEGER PRIMARY KEY,
            AppUserReadingSessionId INTEGER REFERENCES AppUserReadingSession(Id),LibraryId INTEGER,
            SeriesId INTEGER REFERENCES Series(Id),VolumeId INTEGER REFERENCES Volume(Id),
            ChapterId INTEGER REFERENCES Chapter(Id),StartTimeUtc TEXT,EndTimeUtc TEXT);
          CREATE TABLE AppUserReadingHistory(Id INTEGER PRIMARY KEY,DateUtc TEXT,Data TEXT);
          CREATE TABLE CuratedList(Id INTEGER PRIMARY KEY,Opaque BLOB);
        ''')
        self.db.execute("INSERT INTO Series VALUES(1650,1,3,'Gabriel Allon','gabrielallon',"
                        "'Gabriel Allon',NULL,0,NULL,?)", (b"unchanged\x00bytes",))
        self.db.execute("INSERT INTO SeriesMetadata VALUES(1650,0)")
        self.db.execute("INSERT INTO Volume VALUES(1800,1650,'26','26',26.0,26.0,0)")
        self.db.execute("INSERT INTO Chapter VALUES(3358,1800,26,0)")
        self.db.execute("INSERT INTO MangaFile VALUES(3570,3358,?)", (catalog.FILE,))
        self.db.execute("INSERT INTO AppUserProgresses VALUES(1,1,1650,1800,3358,"
                        "'2026-07-27 03:37:35.1','private-fixture-location')")
        self.db.execute("INSERT INTO AppUserReadingSession VALUES(1,0)")
        self.db.execute("INSERT INTO AppUserReadingSessionActivityData VALUES(1,1,1,1650,1800,3358,"
                        "'2026-07-27 03:30:00','2026-07-27 03:37:35.2')")
        self.db.execute("INSERT INTO AppUserReadingHistory VALUES(1,'2026-07-27',?)",
                        (json.dumps({"SeriesIds": [1650], "Activities": []}),))
        self.db.execute("INSERT INTO CuratedList VALUES(7,?)", (b"curation\x00",))
        self.now = 1791648000.0  # 2026-10-10; no wall-clock test coupling.
        self.before = catalog.snapshot(self.db)
        info = self.path.stat()
        self.contract = {"owner_approved": True, "root_runtime_go": True, "prepared_only": False,
                         "target_ids": catalog.TARGET, "before_sha256": catalog.digest(self.before),
                         "schema_sha256": catalog.digest(self.before["schema"]),
                         "original_abort_epoch": self.now + 10, "database_path": str(self.path),
                         "database_device_inode": [info.st_dev, info.st_ino],
                         "library_root": str(self.root), "retention_directory": str(self.retention)}

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def apply(self, guard=lambda: None, **kwargs):
        return catalog.apply(self.db, self.before, self.contract, guard, self.retention,
                             clock=lambda: self.now, **kwargs)

    def test_exact_seven_forward_inverse_preserves_typed_saved_curation_and_full_backup(self):
        calls = []
        result = self.apply(lambda: calls.append(True))
        self.assertEqual((result["changed_cells"], result["row_updates"]), (7, 2))
        self.assertEqual(catalog.snapshot(self.db), catalog.expected_after(self.before))
        with sqlite3.connect(self.retention / "catalog-before.sqlite") as retained:
            self.assertEqual(catalog.snapshot(retained), self.before)
        self.assertEqual((self.retention / "catalog-before.sqlite").stat().st_mode & 0o777, 0o600)
        self.assertEqual(len(calls), 5)
        self.apply(inverse=True)
        self.assertEqual(catalog.snapshot(self.db), self.before)

    def test_full_nullable_row_cas_and_guard_loss_rollback_first_update(self):
        for failure in ("nullable", "fence"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory(dir=self.base) as directory:
                self.retention = Path(directory)
                self.contract["retention_directory"] = directory
                calls = 0
                def guard():
                    nonlocal calls
                    calls += 1
                    if calls == 3 and failure == "nullable":
                        self.db.execute("UPDATE Series SET UnchangedNullable='drift' WHERE Id=1650")
                    if calls == 4 and failure == "fence":
                        raise catalog.Refused("existing maintenance fence lost")
                with self.assertRaises(catalog.Refused):
                    self.apply(guard)
                self.assertEqual(catalog.snapshot(self.db), self.before)

    def test_authority_deadline_schema_lock_alias_and_activity_refuse_before_catalog_write(self):
        for failure in ("authority", "deadline", "schema", "lock", "alias", "reading"):
            with self.subTest(failure=failure):
                before_contract = copy.deepcopy(self.contract)
                self.db.execute("BEGIN")
                if failure == "authority": self.contract["owner_approved"] = False
                if failure == "deadline": self.contract["original_abort_epoch"] = self.now
                if failure == "schema": self.db.execute("ALTER TABLE Series ADD COLUMN Unknown TEXT")
                if failure == "lock": self.db.execute("UPDATE Series SET SortNameLocked=1")
                if failure == "alias":
                    self.db.execute("INSERT INTO Series(Id,LibraryId,Format,SortName) VALUES(9,1,3,'Ransom')")
                if failure == "reading":
                    self.db.execute("UPDATE AppUserReadingSession SET IsActive=1")
                modified = catalog.snapshot(self.db)
                # Commit this separate fixture change so apply can check actual preconditions.
                self.db.commit()
                if failure in ("lock", "alias", "reading"):
                    saved = self.before
                    self.before = modified
                    self.contract.update(before_sha256=catalog.digest(modified),
                                         schema_sha256=catalog.digest(modified["schema"]))
                with self.assertRaises(catalog.Refused): self.apply()
                self.assertEqual(catalog.snapshot(self.db), modified)
                if failure in ("lock", "alias", "reading"): self.before = saved
                self.contract = before_contract
                if failure == "schema": self.db.execute("ALTER TABLE Series DROP COLUMN Unknown")
                if failure == "lock": self.db.execute("UPDATE Series SET SortNameLocked=0")
                if failure == "alias": self.db.execute("DELETE FROM Series WHERE Id=9")
                if failure == "reading": self.db.execute("UPDATE AppUserReadingSession SET IsActive=0")

    def test_native_scan_delta_refuses_catalog_only_inverse_without_overwrite(self):
        self.apply()
        self.db.execute("UPDATE Chapter SET Count=0 WHERE Id=3358")
        actual = catalog.snapshot(self.db)
        with self.assertRaises(catalog.Refused): self.apply(inverse=True)
        self.assertEqual(catalog.snapshot(self.db), actual)

    def scan_fixture(self):
        for table, (_, fields) in catalog.SCAN_FIELDS.items():
            existing = {r[1] for r in self.db.execute('PRAGMA table_info(' + catalog.quote(table) + ')')}
            for field in fields:
                if field not in existing:
                    kind = 'INTEGER' if field in {'Count', 'IsSpecial', 'TotalCount', 'Bytes', 'PublicationStatus', 'RowVersion'} else 'TEXT'
                    self.db.execute('ALTER TABLE ' + catalog.quote(table) + ' ADD COLUMN ' + catalog.quote(field) + ' ' + kind)
        self.db.execute('UPDATE SeriesMetadata SET RowVersion=100')
        self.before = catalog.snapshot(self.db)
        self.contract.update(before_sha256=catalog.digest(self.before), schema_sha256=catalog.digest(self.before['schema']))
        self.apply()
        explicit = {"Series:SortName": catalog.cell("text", "Ransom"), "Chapter:Count": catalog.cell("integer", 0),
                    "Chapter:IsSpecial": catalog.cell("integer", 1), "Chapter:Range": catalog.cell("text", "Ransom"),
                    "Chapter:Title": catalog.cell("text", "Ransom"), "Chapter:TotalCount": catalog.cell("integer", 1),
                    "MangaFile:Bytes": catalog.cell("integer", 1081344), "MangaFile:KoreaderHash": catalog.cell("text", "a" * 32),
                    "SeriesMetadata:TotalCount": catalog.cell("integer", 1), "SeriesMetadata:PublicationStatus": catalog.cell("integer", 2),
                    "SeriesMetadata:RowVersion": catalog.cell("integer", 102)}
        import datetime as dt
        from zoneinfo import ZoneInfo
        for table, (identity, fields) in catalog.SCAN_FIELDS.items():
            for field in fields:
                if field.startswith('Last'):
                    value = dt.datetime.fromtimestamp(self.now + 1, dt.timezone.utc if field.endswith('Utc') else ZoneInfo('America/New_York')).replace(tzinfo=None).isoformat(' ')
                elif field.endswith('Color'):
                    value = '#AABBCC'
                else:
                    value = catalog.decode(explicit[table + ':' + field])
                self.db.execute('UPDATE ' + catalog.quote(table) + ' SET ' + catalog.quote(field) + '=? WHERE Id=?', (value, identity))
        after = catalog.snapshot(self.db)
        self.contract.update(post_scan_after_sha256=catalog.digest(after), native_explicit_values=explicit,
                             scan_started_epoch=self.now, scan_finished_epoch=self.now + 2)
        return after

    def test_fresh_scan_inverse_exact32_and_current_reading_drift_refusal(self):
        after = self.scan_fixture()
        self.db.execute("UPDATE AppUserProgresses SET BookScrollId='new-legitimate-location'")
        drifted = catalog.snapshot(self.db)
        with self.assertRaises(catalog.Refused):
            catalog.invert_after_scan(self.db, self.before, after, self.contract, lambda: None, self.retention, clock=lambda: self.now)
        self.assertEqual(catalog.snapshot(self.db), drifted)
        self.db.execute("UPDATE AppUserProgresses SET BookScrollId='private-fixture-location'")
        result = catalog.invert_after_scan(self.db, self.before, after, self.contract, lambda: None, self.retention, clock=lambda: self.now)
        self.assertEqual((result['changed_cells'], result['row_updates']), (32, 5))
        self.assertEqual(catalog.snapshot(self.db), self.before)

    def test_postscan_guard_loss_rolls_back_partial_inverse(self):
        after = self.scan_fixture()
        calls = 0
        def guard():
            nonlocal calls
            calls += 1
            if calls == 4:raise catalog.Refused('real maintenance service guard lost')
        with self.assertRaises(catalog.Refused):
            catalog.invert_after_scan(self.db, self.before, after, self.contract, guard, self.retention, clock=lambda: self.now)
        self.assertEqual(catalog.snapshot(self.db), after)

    def test_publication_guard_refuses_escape_before_io_and_checks_atomic_rename(self):
        folder = self.root / 'Daniel Silva' / 'Ransom'
        folder.mkdir(parents=True)
        path = folder / 'Ransom - Daniel Silva.epub'
        path.write_bytes(b'original')
        state = self.base / '.epub-convert'
        calls = []
        with mock.patch.object(catalog, 'FILE', str(path)), mock.patch.object(maintenance, 'LIBRARY', str(self.root)), mock.patch.object(maintenance, 'STATE', str(state)):
            with maintenance.publication_guard(lambda: calls.append(True), self.retention,
                                               catalog.metadata.sha256(b'original'), catalog.metadata.sha256(b'candidate')):
                with catalog.metadata.safe_directory(str(folder)) as fd:
                    with self.assertRaises(catalog.Refused):catalog.metadata._write_file(fd, 'unrelated.epub', b'candidate')
                    self.assertFalse((folder / 'unrelated.epub').exists())
                    _, info = catalog.metadata.read_regular(fd, path.name)
                    catalog.metadata._replace(fd, str(folder), path.name, b'original', info, b'candidate')
        self.assertEqual(path.read_bytes(), b'candidate')
        self.assertEqual(len(calls), 2)  # Before candidate write and immediately before atomic rename.

    def test_source_drift_during_host_guard_does_not_overwrite_the_new_source(self):
        folder = self.root / 'Daniel Silva' / 'Ransom'
        folder.mkdir(parents=True)
        path = folder / 'Ransom - Daniel Silva.epub'
        path.write_bytes(b'original')
        calls = 0
        def guard():
            nonlocal calls
            calls += 1
            if calls == 2:path.write_bytes(b'legitimate-new-source')
        with mock.patch.object(catalog, 'FILE', str(path)), mock.patch.object(maintenance, 'LIBRARY', str(self.root)), mock.patch.object(maintenance, 'STATE', str(self.base / '.epub-convert')):
            with maintenance.publication_guard(guard, self.retention, catalog.metadata.sha256(b'original'), catalog.metadata.sha256(b'candidate')):
                with catalog.metadata.safe_directory(str(folder)) as fd:
                    _, info = catalog.metadata.read_regular(fd, path.name)
                    with self.assertRaises(catalog.Refused):
                        catalog.metadata._replace(fd, str(folder), path.name, b'original', info, b'candidate')
        self.assertEqual(path.read_bytes(), b'legitimate-new-source')
        self.assertEqual(list(folder.glob('*.partial')), [])

    def test_distinct_one_job_admission_refuses_extra_intent_or_changed_manifest(self):
        manifest = maintenance.job_manifest('a' * 32, 'ransom-maintenance-a', 'worker-fixture', 'kavita-fixture',
                                            {'server': 'fixture-nas', 'path': '/fixture-books'})
        phase = {'phase_token': 'a' * 32, 'owned_jobs': [{'writer': True, 'gate_env': None, 'mutable_env': [],
                 'ready_manifest': manifest, 'namespace': 'media', 'name': 'ransom-maintenance-a', 'uid': None}]}
        lease = {'phase_token': 'a' * 32, 'application_name': maintenance.PG_NAME, 'backend_pid': None, 'job_uid': None, 'pod_uid': None}
        config = {'phase_state': str(self.base / 'phase.json'), 'pg_owner': str(self.base / 'owner.json'),
                  'restore_pr': '1', 'phase_token': 'a' * 32, 'job_manifest': manifest, 'job_name': 'ransom-maintenance-a', 'claim_name': 'kavita-fixture'}
        core = mock.Mock()
        def put(path, value):
            Path(path).write_text(json.dumps(value));Path(path).chmod(0o600)
        put(config['phase_state'], phase);put(config['pg_owner'], lease)
        self.assertEqual(len(maintenance.one_job_phase(config, core)['pg_leases']), 1)
        core.validate_state.assert_called_once_with(phase, '1')
        for failure in ('extra', 'manifest'):
            modified = copy.deepcopy(phase)
            if failure == 'extra':modified['owned_jobs'].append(copy.deepcopy(modified['owned_jobs'][0]))
            else:modified['owned_jobs'][0]['ready_manifest'] = {'exact': 'different'}
            put(config['phase_state'], modified)
            with self.assertRaises(catalog.Refused):maintenance.one_job_phase(config, core)

    def test_actual_host_admission_checks_original_clock_custody_storage_and_stopped_services(self):
        # Reuse the unchanged real methods; mock external API/storage calls only.
        directory = HERE.parent / 'copy-window'
        def existing_method(file, name):
            tree = ast.parse((directory / file).read_bytes())
            method = next(node for cls in tree.body if isinstance(cls, ast.ClassDef)
                          for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name)
            method = copy.deepcopy(method);method.decorator_list = []
            scope = {'Refused': catalog.Refused, 'NotStopped': catalog.Refused, 'importlib': importlib,
                     'time': time, 'now': lambda: 'fixture', 'CRONS': [('downloads', 'lazylibrarian-epub-convert')]}
            exec(compile(ast.Module(body=[method], type_ignores=[]), file, 'exec'), scope)
            return scope[name]
        owned = existing_method('copy-recovery-watch.py', 'owned_job_pods')
        service = existing_method('copy-phase-supervisor.py', 'service_fence')
        phase, uid, pod_uid = 'a' * 32, '1' * 8 + '-1111-1111-1111-' + '1' * 12, '2' * 8 + '-2222-2222-2222-' + '2' * 12
        def put(name, value):
            path = self.base / name;path.write_bytes(catalog.canonical(value));path.chmod(0o600)
            return str(path)
        state = {'cached_stop_actuation_complete_at': 'fixture', 'complete': False, 'armed_ready': True,
                 'actuation_budget_started_at': dt.datetime.fromtimestamp(self.now, dt.timezone.utc).isoformat(),
                 'armed_at': dt.datetime.fromtimestamp(self.now - 10, dt.timezone.utc).isoformat(),
                 'cached_stop_actuation_binding': {'phase': phase}}
        state_path = put('watch.json', state)
        operation = {'phase_token': phase, 'owner_approved': True, 'root_runtime_go': True,
                     'prepared_only': False, 'original_abort_epoch': self.now + 170}
        operation_path = put('operation.json', operation)
        row = {'namespace': 'media', 'name': 'ransom-maintenance-a', 'phase_token': phase, 'uid': uid}
        pod = {'metadata': {'uid': pod_uid, 'name': 'owned-pod', 'labels': {maintenance.LABEL: phase},
                           'ownerReferences': [{'kind': 'Job', 'name': row['name'], 'uid': uid}]}}
        containers = [{'name': 'app', 'image': 'fixture-image', 'env': [{'name': 'LAZYLIBRARIAN_URL', 'value': ''}]}]
        libretto = {'metadata': {'uid': 'libretto-uid', 'generation': 4, 'annotations': {'deployment.kubernetes.io/revision': '2'}},
                    'spec': {'replicas': 1, 'selector': {'matchLabels': {'app': 'libretto'}}, 'template': {'spec': {'containers': containers}}},
                    'status': {'observedGeneration': 4, 'updatedReplicas': 1, 'readyReplicas': 1}}
        libretto_pod = {'metadata': {'labels': {'app': 'libretto'}, 'ownerReferences': [{'controller': True, 'kind': 'ReplicaSet', 'name': 'libretto-rs', 'uid': 'rs-uid'}]},
                        'spec': {'containers': containers}, 'status': {'phase': 'Running', 'conditions': [{'type': 'Ready', 'status': 'True'}]}}
        rs = {'metadata': {'uid': 'rs-uid', 'annotations': {'deployment.kubernetes.io/revision': '2'},
                          'ownerReferences': [{'controller': True, 'kind': 'Deployment', 'name': 'libretto', 'uid': 'libretto-uid'}]}}
        stopped = {'spec': {'replicas': 0, 'selector': {'matchLabels': {'app': 'stopped'}}}, 'status': {'replicas': 0}}
        claim = {'metadata': {'uid': 'claim-uid'}, 'spec': {'volumeName': 'bound-pv'}, 'status': {'phase': 'Bound'}}
        pv = {'metadata': {'uid': 'pv-uid'}, 'spec': {'claimRef': {'uid': 'claim-uid'}}}
        collector = self.base / 'collector.py';collector.write_text('def storage_inventory(pods,claims,volumes):return {"current":True}\n')
        config = {'phase_token': phase, 'operation': {'path': operation_path, 'sha256': catalog.digest(operation)},
                  'publisher_proof': {'path': put('publisher.json', {}), 'sha256': catalog.digest({})},
                  'watcher_state': state_path, 'watcher_stop': str(self.base / 'stop'), 'watcher_pid': 123,
                  'watcher_pgid': 123, 'watcher_birth': 456, 'pod_uid': pod_uid,
                  'cached_source_receipt': {'path': put('cache.json', {}), 'sha256': catalog.digest({})},
                  'manifest_contract': {'path': put('contract.json', {}), 'sha256': catalog.digest({})},
                  'claim_name': 'kavita', 'claim_uid': 'claim-uid', 'claim_spec': copy.deepcopy(claim['spec']),
                  'pv_name': 'bound-pv', 'pv_uid': 'pv-uid', 'pv_spec': copy.deepcopy(pv['spec']),
                  'publisher_scope_hook': {'script': str(collector)}, 'publisher_scope_sha256': catalog.digest({})}
        watch = mock.Mock();watch.args.arm_deadline = 1800;watch.owned_job_pods = owned
        publisher, cache, pod_guard = mock.Mock(), mock.Mock(), mock.Mock()
        supervisor = SimpleNamespace(epoch=lambda text: dt.datetime.fromisoformat(text).timestamp(),
                                     cache=cache, Supervisor=SimpleNamespace(service_fence=service))
        host = maintenance.HostAdmission(config, watch, mock.Mock(), pod_guard, supervisor, publisher)
        objects = {('job', row['name'], 'media'): {'metadata': {'uid': uid}}, ('pvc', 'kavita', 'media'): claim,
                   ('pv', 'bound-pv', ''): pv, ('deployment', 'lazylibrarian', 'downloads'): copy.deepcopy(stopped),
                   ('deployment', 'kavita', 'media'): copy.deepcopy(stopped), ('deployment', 'libretto', 'media'): libretto,
                   ('cronjob', 'lazylibrarian-epub-convert', 'downloads'): {'spec': {'suspend': True}},
                   ('replicaset', 'libretto-rs', 'media'): rs}
        host.get = lambda kind, name, namespace: objects[(kind, name, namespace)]
        host.list = lambda kind, namespace=None: [pod, libretto_pod] if kind == 'pods' and namespace in (None, 'media') else []
        real_read_text = Path.read_text
        kernel = '123 (fixture) S 1 123 ' + '0 ' * 16 + '456 0'
        def read_text(path, *args, **kwargs):
            return kernel if str(path) == '/proc/123/stat' else real_read_text(path, *args, **kwargs)
        with mock.patch.object(maintenance.time, 'time', return_value=self.now + 1), mock.patch.object(Path, 'read_text', read_text), \
             mock.patch.object(maintenance, 'one_job_phase', return_value={'phase_token': phase, 'owned_jobs': [row]}):
            host.guard()
            pod_guard.verify_owned_pod.assert_called_once_with(row, objects[('job', row['name'], 'media')], pod, pod_uid)
            self.assertEqual(cache.check_live.call_args.kwargs['deadline'], self.now + 170)
            publisher.verify.assert_called_once()
            for failure in ('clock', 'kernel', 'owner', 'claim', 'service', 'holds'):
                with self.subTest(failure=failure):
                    original_objects = copy.deepcopy(objects);original_pod = copy.deepcopy(pod)
                    if failure == 'clock':host.operation['original_abort_epoch'] += 1
                    if failure == 'kernel':host.c['watcher_birth'] += 1
                    if failure == 'owner':pod['metadata']['ownerReferences'][0]['uid'] = 'foreign'
                    if failure == 'claim':objects[('pv', 'bound-pv', '')]['spec']['claimRef']['uid'] = 'foreign'
                    if failure == 'service':objects[('deployment', 'kavita', 'media')]['status']['replicas'] = 1
                    if failure == 'holds':watch.verify_cached_stop_holds.side_effect = catalog.Refused('owned hold lost')
                    with self.assertRaises((catalog.Refused, RuntimeError)):host.guard()
                    objects.clear();objects.update(original_objects);pod.clear();pod.update(original_pod)
                    host.operation['original_abort_epoch'] = self.now + 170;host.c['watcher_birth'] = 456
                    watch.verify_cached_stop_holds.side_effect = None


if __name__ == "__main__":
    unittest.main()
