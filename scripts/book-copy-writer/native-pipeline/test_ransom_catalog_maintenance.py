"""Small SQLite/fake-fence controls only; serial nice19, no load or APIs."""
import ast
import copy
import datetime as dt
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import shutil
import subprocess
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
    def test_host_inventory_uses_real_core_api_and_namespace_validation(self):
        spec = importlib.util.spec_from_file_location('ransom_inventory_window', HERE.parent / 'copy-window/window_contract.py')
        window = importlib.util.module_from_spec(spec)
        # This pure inventory route never uses the YAML parser; the writer test
        # image lacks that optional import, so any accidental parser call fails.
        with mock.patch.dict(sys.modules, {'yaml': __import__('types').ModuleType('yaml')}):
            spec.loader.exec_module(window)
        host = maintenance.HostAdmission.__new__(maintenance.HostAdmission)
        host.supervisor = SimpleNamespace(window=window)
        routes = [('pods', 'downloads', 'Pod', '/api/v1/namespaces/downloads/pods'),
                  ('pods', None, 'Pod', '/api/v1/pods'),
                  ('pvc', 'media', 'PersistentVolumeClaim', '/api/v1/namespaces/media/persistentvolumeclaims'),
                  ('pvc', None, 'PersistentVolumeClaim', '/api/v1/persistentvolumeclaims'),
                  ('pv', None, 'PersistentVolume', '/api/v1/persistentvolumes')]
        for kind, namespace, typed, endpoint in routes:
            with self.subTest(endpoint=endpoint):
                item = {'metadata': {'name': 'native', 'uid': 'native-uid'}}
                if kind != 'pv':item['metadata']['namespace'] = namespace or 'downloads'
                envelope = {'kind': typed + 'List', 'apiVersion': 'v1',
                            'metadata': {'resourceVersion': '7'}, 'items': [item]}
                host.run = mock.Mock(return_value=json.dumps(envelope))
                self.assertEqual(host.list(kind, namespace), [dict(item, kind=typed, apiVersion='v1')])
                host.run.assert_called_once_with(['kubectl', 'get', '--raw', endpoint])
        envelope = {'kind': 'PodList', 'apiVersion': 'v1', 'metadata': {'resourceVersion': '7'},
                    'items': [{'metadata': {'name': 'native', 'uid': 'native-uid', 'namespace': 'downloads'}}]}
        wrong_api = dict(envelope, apiVersion='batch/v1')
        wrong_namespace = copy.deepcopy(envelope)
        wrong_namespace['items'][0]['metadata']['namespace'] = 'media'
        generic_list = dict(envelope, kind='List')  # Actual kubectl get -o json boundary.
        for value in (wrong_api, wrong_namespace, generic_list, dict(envelope, kind='JobList')):
            with self.subTest(envelope=value['kind'], api=value['apiVersion']):
                host.run = mock.Mock(return_value=json.dumps(value))
                with self.assertRaises(ValueError):host.list('pods', 'downloads')
                host.run.assert_called_once_with(['kubectl', 'get', '--raw', '/api/v1/namespaces/downloads/pods'])
        for kind, namespace in [('pv', 'media'), ('jobs', None), ('pods', ''), ('pods', '../media')]:
            host.run = mock.Mock()
            with self.assertRaises(catalog.Refused):host.list(kind, namespace)
            host.run.assert_not_called()

    def test_cleanup_uses_only_original_watchers_accepted_private_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cache.json"
            receipt = {"phase_token": "a" * 32, "exact": "accepted"}
            path.write_bytes(catalog.canonical(receipt));path.chmod(0o600)
            phase = {"phase_token": receipt["phase_token"]}
            config = {"phase_token": phase["phase_token"],
                      "watcher_arguments": {"cached_source_receipt": str(path)},
                      "cached_source_receipt": None}  # Prearm field is never authority.
            watch = SimpleNamespace(args=SimpleNamespace(cached_source_receipt=str(path)),
                                    state={"cached_source_owner": phase.copy()})
            with self.assertRaises(catalog.Refused):maintenance.accepted_watcher_cache(config, watch, phase)
            watch.state["cached_source_receipt_sha256"] = "0" * 64
            with self.assertRaises(catalog.Refused):maintenance.accepted_watcher_cache(config, watch, phase)
            watch.state["cached_source_receipt_sha256"] = catalog.digest(receipt)
            self.assertEqual(maintenance.accepted_watcher_cache(config, watch, phase), receipt)
            config["watcher_arguments"]["cached_source_receipt"] = str(path.with_name("foreign.json"))
            with self.assertRaises(catalog.Refused):maintenance.accepted_watcher_cache(config, watch, phase)
            config["watcher_arguments"]["cached_source_receipt"] = str(path)
            watch.state["cached_source_owner"]["phase_token"] = "b" * 32
            with self.assertRaises(catalog.Refused):maintenance.accepted_watcher_cache(config, watch, phase)

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

    def test_qualified_native_koreader_hash_preserves_exact_case(self):
        self.scan_fixture()
        actual = "B78F996A630E8AB6BEF038351232C1AE"
        self.db.execute("UPDATE MangaFile SET KoreaderHash=? WHERE Id=3570", (actual,))
        after = catalog.snapshot(self.db)
        explicit = self.contract["native_explicit_values"]
        explicit["MangaFile:KoreaderHash"] = catalog.cell("text", actual)
        before = catalog.expected_after(self.before)
        catalog.require_scan_delta(before, after, explicit, self.now, self.now + 2)
        self.assertEqual(catalog.row(after, "MangaFile", 3570)["KoreaderHash"],
                         catalog.cell("text", actual))
        for invalid in (actual.lower(), "G" + actual[1:], actual[:-1], actual + "0"):
            with self.subTest(invalid=invalid):
                explicit["MangaFile:KoreaderHash"] = catalog.cell("text", invalid)
                with self.assertRaises(catalog.Refused):
                    catalog.require_scan_delta(before, after, explicit, self.now, self.now + 2)
        self.assertEqual(catalog.snapshot(self.db), after)

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

    def lidarr_checkpoint_fixture(self):
        directory = self.base / 'checkpoint';directory.mkdir()
        for name in ('checkpoint-owned-job.core.py', 'ransom_lidarr_checkpoint.py'):
            path = directory / name;path.write_bytes((HERE / name).read_bytes());path.chmod(0o600)
        shim = directory / 'ransom_lidarr_checkpoint.py'
        core = maintenance.module('fixture_checkpoint', {'path': str(shim), 'sha256': catalog.metadata.sha256(shim.read_bytes())})
        path = self.base / 'phase.json'
        core.process(SimpleNamespace(command='init', restore_pr='1'), path)
        phase, _ = core.read_json(path)
        token = phase['phase_token']
        helper = {'apiVersion': 'batch/v1', 'kind': 'Job', 'metadata': {'namespace': 'media', 'name': 'ransom-lidarr-a'},
                  'spec': {'activeDeadlineSeconds': 120, 'backoffLimit': 0, 'template': {'metadata': {'annotations': {'k8tz.io/inject': 'false'}},
                           'spec': {'automountServiceAccountToken': False, 'nodeSelector': {'kubernetes.io/hostname': 'worker-fixture'},
                                    'restartPolicy': 'Never', 'containers': [{'name': 'reader', 'image': maintenance.IMAGE,
                                      'command': ['python', '-c', 'standby'], 'env': [
                                      {'name': 'LIDARR_CAPTURE_PHASE_READY', 'value': '0'}, {'name': 'LIDARR_CAPTURE_DEADLINE_EPOCH', 'value': '0'},
                                      {'name': 'LIDARR_SOURCE_POD_UID', 'value': '3ab85d05-efdb-4299-88af-b7ec8fb407ec'},
                                      {'name': 'LIDARR_CONFIG_SHA256', 'value': '1' * 64}],
                                      'volumeMounts': [{'name': 'config', 'mountPath': '/source', 'readOnly': True}, {'name': 'tmp', 'mountPath': '/tmp'}]}],
                                    'volumes': [{'name': 'config', 'persistentVolumeClaim': {'claimName': 'lidarr', 'readOnly': True}},
                                                {'name': 'tmp', 'emptyDir': {'sizeLimit': '32Mi'}}]}}}}
        def put(path, value):
            raw = core.encoded(value);Path(path).write_bytes(raw);Path(path).chmod(0o600)
            return {'path': str(path), 'sha256': core.digest(raw)}
        helper_ref = put(self.base / 'lidarr.json', helper)
        manifest = maintenance.job_manifest(token, 'ransom-maintenance-a', 'worker-fixture', 'kavita-fixture', maintenance.BOOK_NFS)
        for name, ref, writer in (('helper', helper_ref, False), ('writer', put(self.base / 'writer.json', manifest), True)):
            core.process(SimpleNamespace(command='register', restore_pr='1', manifest=ref['path'], source_sha256=ref['sha256'],
                                         output=str(self.base / (name + '-registered.json')), writer=writer), path)
        lease = {'phase_token': token, 'application_name': maintenance.PG_NAME, 'backend_pid': None, 'job_uid': None, 'pod_uid': None}
        put(self.base / 'owner.json', lease)
        config = {'phase_state': str(path), 'pg_owner': str(self.base / 'owner.json'), 'restore_pr': '1', 'phase_token': token,
                  'job_manifest': manifest, 'job_name': 'ransom-maintenance-a', 'claim_name': 'kavita-fixture',
                  'lidarr_helper': {'name': helper['metadata']['name'], 'source_template': helper_ref}}
        return core, config

    def test_distinct_two_row_admission_refuses_extra_writer_or_changed_manifest(self):
        core, config = self.lidarr_checkpoint_fixture()
        phase, _ = core.read_json(Path(config['phase_state']))
        self.assertEqual(len(maintenance.one_job_phase(config, core)['pg_leases']), 1)
        self.assertEqual(maintenance.writer_row(phase, config)['name'], config['job_name'])
        self.assertFalse(hasattr(core, 'INTENTS'))
        for failure in ('extra', 'writer', 'manifest'):
            modified = copy.deepcopy(phase)
            if failure == 'extra':modified['owned_jobs'].append(copy.deepcopy(modified['owned_jobs'][0]))
            elif failure == 'writer':modified['owned_jobs'][0]['writer'] = True
            else:modified['owned_jobs'][1]['ready_manifest'] = {'exact': 'different'}
            core.save(Path(config['phase_state']), modified)
            with self.assertRaises((catalog.Refused, core.Refused)):maintenance.one_job_phase(config, core)

    def test_actual_independent_lidarr_bridge_imports_exact_readonly_checkpoint_profile(self):
        core, config = self.lidarr_checkpoint_fixture()
        path = Path(config['phase_state'])
        phase, _ = core.read_json(path);row = maintenance.helper_row(phase, config)
        values = {'LIDARR_CAPTURE_PHASE_READY': '1', 'LIDARR_CAPTURE_DEADLINE_EPOCH': str(time.time() + 115)}
        env = self.base / 'helper-env.json';env.write_bytes(catalog.canonical(values));env.chmod(0o600)
        args = SimpleNamespace(command='bind', restore_pr='1', namespace='media', name=row['name'],
                               initial_manifest_sha256=row['initial_manifest_sha256'], env_file=str(env), output=str(self.base / 'helper-ready.json'))
        with mock.patch.dict(core.process.__globals__, lookup_job=lambda *args: None):
            env.write_bytes(catalog.canonical(dict(values, UNREVIEWED='1')))
            with self.assertRaises(core.Refused):core.process(args, path)
            env.write_bytes(catalog.canonical(values));core.process(args, path)
        phase, _ = core.read_json(path);row = maintenance.helper_row(phase, config)
        core.ensure_env_only(row['initial_manifest'], row['ready_manifest'], set(values))
        modified = copy.deepcopy(row['ready_manifest']);modified['spec']['template']['spec']['containers'][0]['command'].append('foreign')
        with self.assertRaises(core.Refused):core.ensure_env_only(row['initial_manifest'], modified, set(values))
        helper_uid, pod_uid = '11111111-1111-1111-1111-111111111111', '22222222-2222-2222-2222-222222222222'
        actual = copy.deepcopy(row['ready_manifest']);actual['metadata']['uid'] = helper_uid
        actual['spec']['template']['metadata']['labels'].update({
            'batch.kubernetes.io/controller-uid': helper_uid, 'controller-uid': helper_uid,
            'batch.kubernetes.io/job-name': row['name'], 'job-name': row['name']})
        actual['spec']['selector'] = {'matchLabels': {'batch.kubernetes.io/controller-uid': helper_uid}}
        with mock.patch.dict(core.process.__globals__, lookup_job=lambda *args: actual):
            core.process(SimpleNamespace(command='observe', restore_pr='1', namespace='media', name=row['name']), path)
        source = actual['spec']['template']['spec']
        pod = {'metadata': {'name': 'helper-pod', 'uid': pod_uid, 'annotations': {'k8tz.io/inject': 'false'},
                           'labels': {maintenance.LABEL: config['phase_token'], 'batch.kubernetes.io/controller-uid': helper_uid},
                           'ownerReferences': [{'apiVersion': 'batch/v1', 'kind': 'Job', 'name': row['name'], 'uid': helper_uid,
                                                'controller': True, 'blockOwnerDeletion': True}]},
               'spec': dict(copy.deepcopy(source), nodeName='worker-fixture'),
               'status': {'phase': 'Running', 'containerStatuses': [{'imageID': maintenance.IMAGE, 'restartCount': 0, 'ready': True, 'state': {'running': {}}}]}}
        program = self.base / 'native.py';program.write_text('# retained SELECT-only fixture\n');program.chmod(0o600)
        shim = self.base / 'checkpoint/ransom_lidarr_checkpoint.py'
        profile = {'checkpoint_helper': str(shim), 'checkpoint_sha256': catalog.metadata.sha256(shim.read_bytes()),
                   'phase_state': str(path), 'restore_pr': '1', 'namespace': 'media', 'job_name': row['name'], 'claim_uid': 'claim-uid',
                   'source_template': config['lidarr_helper']['source_template']['path'],
                   'source_template_sha256': config['lidarr_helper']['source_template']['sha256'], 'node': 'worker-fixture',
                   'config_sha256': '1' * 64, 'capture_program': str(program), 'capture_sha256': catalog.metadata.sha256(program.read_bytes())}
        inventory = {'counts_before': {'Artists': 0, 'RootFolders': 0, 'Notifications': 0},
                     'counts_after': {'Artists': 0, 'RootFolders': 0, 'Notifications': 0},
                     'tables': {'Artists': [], 'RootFolders': [], 'Notifications': []}}
        native = {'complete': True, 'read_only': True, 'source_writes': 0, 'kind': 'lidarr', 'inventory': inventory,
                  'inventory_before_sha256': catalog.digest(inventory), 'inventory_after_sha256': catalog.digest(inventory),
                  'source_files_before': {}, 'source_files_after': {}, 'source': {'items': [], 'root_folders': [], 'custom_hooks': [], 'custom_hooks_disabled': True},
                  'helper_identity': {'COPY_PHASE_TOKEN': config['phase_token'], 'COPY_JOB_UID': helper_uid, 'COPY_POD_UID': pod_uid},
                  'source_pod_uid': '3ab85d05-efdb-4299-88af-b7ec8fb407ec'}
        small = {'native_artist_projection_required': True, 'complete': False, 'vendor_status':
                 {'version': '3.1.6.5078', 'appData': '/config', 'startupPath': '/app/bin', 'databaseType': 'sqLite'},
                 'root_folders': [], 'custom_hooks': [], 'notification_count': 0, 'custom_hooks_disabled': True}
        calls = []
        def run(argv):
            calls.append(argv)
            if argv[1] == 'exec':return native
            return {'pvc': {'metadata': {'uid': 'claim-uid'}}, 'job': actual, 'pods': {'items': [pod]}}[argv[2]]
        spec = importlib.util.spec_from_file_location('actual_lidarr_bridge', HERE / 'lidarr-native-source-bridge.py')
        bridge = importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)
        value, _ = bridge._capture_bounded(run, profile, {'pod_uid': native['source_pod_uid']}, small, 10)
        self.assertEqual(value['items'], [])
        self.assertEqual(len([argv for argv in calls if argv[1] == 'exec']), 1)
        self.assertFalse(hasattr(core, 'INTENTS'))  # No fabricated COPY SOURCE or PG owner.

    def test_readonly_prelude_union_and_two_row_writer_first_cleanup(self):
        core, config = self.lidarr_checkpoint_fixture()
        phase, _ = core.read_json(Path(config['phase_state']))
        helper = maintenance.helper_row(phase, config);helper['uid'] = 'helper-uid'
        writer = maintenance.writer_row(phase, config)
        # Generic validation/binding is exercised separately above; this uses
        # the actual inherited cleanup method with only external API fakes.
        helper['ready_manifest'] = copy.deepcopy(helper['initial_manifest'])
        for row in helper['ready_manifest']['spec']['template']['spec']['containers'][0]['env']:
            if row['name'] == 'LIDARR_CAPTURE_PHASE_READY':row['value'] = '1'
            if row['name'] == 'LIDARR_CAPTURE_DEADLINE_EPOCH':row['value'] = str(time.time() + 115)
        helper['ready_manifest_sha256'] = core.digest(core.encoded(helper['ready_manifest']))
        core.save(Path(config['phase_state']), phase)
        jobs = {helper['name']: {'metadata': {'name': helper['name'], 'uid': helper['uid'], 'labels': {maintenance.LABEL: config['phase_token']}}}}
        pod = {'metadata': {'name': 'helper-pod', 'uid': 'helper-pod-uid', 'labels': {maintenance.LABEL: config['phase_token']},
                           'ownerReferences': [{'apiVersion': 'batch/v1', 'kind': 'Job', 'name': helper['name'], 'uid': helper['uid'],
                                                'controller': True, 'blockOwnerDeletion': True}]}}
        events = []
        class Present(RuntimeError):pass
        class Base:
            def inventory(self, kind, namespace):
                return list(jobs.values()) if kind == 'Job' and namespace == 'media' else [pod] if kind == 'Pod' and namespace == 'media' and pod else []
            def verify_phase_absent(self, received):
                if received['owned_jobs'] and jobs:raise Present('owned union not empty')
                events.append('original-pg-absence')
            @staticmethod
            def owned_job_pods(pods, row, uid):return []
            def run(self, argv, input_text):
                body = json.loads(input_text);name = argv[3].rsplit('/', 1)[-1]
                self.assert_uid = body['preconditions']['uid'] == jobs[name]['metadata']['uid']
                if not self.assert_uid:raise AssertionError('UID deletion differs')
                events.append('delete-' + name);del jobs[name]
        tree = ast.parse((HERE.parent / 'copy-window/copy-recovery-watch.py').read_bytes())
        cleanup = next(method for cls in tree.body if isinstance(cls, ast.ClassDef) for method in cls.body
                       if isinstance(method, ast.FunctionDef) and method.name == 'cleanup_phase_jobs')
        scope = {'PhaseResourcesPresent': Present, 'json': json};exec(compile(ast.Module(body=[cleanup], type_ignores=[]), 'existing-cleanup', 'exec'), scope)
        Base.cleanup_phase_jobs = scope['cleanup_phase_jobs']
        fake = SimpleNamespace(Watchdog=Base, PhaseResourcesPresent=Present)
        klass = maintenance.maintenance_watchdog(fake, core, config, mock.Mock(), mock.Mock())
        watch = klass();watch.stop = self.base / 'stop';watch.kube = mock.Mock()
        watch.verify_only_helper(maintenance.one_job_phase(config, core), pod['metadata']['uid'])
        self.assertEqual(events, ['original-pg-absence'])
        pod['metadata']['uid'] = 'foreign'
        with self.assertRaises(catalog.Refused):watch.verify_only_helper(maintenance.one_job_phase(config, core), 'helper-pod-uid')
        pod.clear();writer['uid'] = 'writer-uid';core.save(Path(config['phase_state']), phase)
        jobs[writer['name']] = {'metadata': {'name': writer['name'], 'uid': writer['uid'], 'labels': {maintenance.LABEL: config['phase_token']}}}
        access = {'fixture': 'exact original'};config['converter_lock_access'] = access
        admission = {'phase_token': config['phase_token'], 'access_sha256': catalog.digest(access), 'state_inode': 10}
        target = self.base / 'lock-admission.json';target.write_bytes(catalog.canonical(admission));target.chmod(0o600)
        config['converter_lock_admission'] = {'path': str(target), 'sha256': catalog.digest(admission)}
        with mock.patch.object(maintenance, 'sonarr_lock', return_value={'state_inode': 10, 'absent': True}):watch.cleanup_phase_jobs()
        deletes = [event for event in events if event.startswith('delete-')]
        self.assertEqual(deletes, ['delete-' + writer['name'], 'delete-' + helper['name']])
        retained, _ = core.read_json(Path(config['phase_state']))
        self.assertEqual([row['uid'] for row in retained['owned_jobs']], ['helper-uid', 'writer-uid'])

    def test_complete_prelude_binds_real_checkpoint_and_requires_gc_before_writer(self):
        original_base = self.base
        for foreign_after_gc in (False, True):
            with self.subTest(foreign_after_gc=foreign_after_gc):
                self.base = original_base / str(foreign_after_gc);self.base.mkdir()
                core, config = self.lidarr_checkpoint_fixture()
                helper = config['lidarr_helper']
                helper.update(env_output=str(self.base / 'env.json'), ready_output=str(self.base / 'ready.json'))
                publisher = self.base / 'publisher';publisher.mkdir()
                names = {'kapowarr-native-files-capture.py', 'lidarr-native-paths-capture.py', 'lidarr-native-source-bridge.py',
                         'publisher-config-capture.js', 'publisher-local-backing.py', 'publisher-path-capture.js',
                         'publisher-path-capture.py', 'publisher-path-capture.sh', 'publisher-scope-capture.py',
                         'publisher-scope-guard.py', 'sab-local-config-capture.py', 'approved-normal-write-profiles.json'}
                refs = {}
                for name in names:
                    path = publisher / name;path.write_bytes(b'# external capture fixture\n');path.chmod(0o600)
                    refs[name] = {'path': str(path), 'sha256': catalog.metadata.sha256(path.read_bytes())}
                shim = self.base / 'checkpoint/ransom_lidarr_checkpoint.py'
                config.update(sources={'checkpoint': {'path': str(shim), 'sha256': catalog.metadata.sha256(shim.read_bytes())},
                                       'publisher_guard': refs['publisher-scope-guard.py']}, publisher_sources=refs,
                              publisher_scope_hook={'script': refs['publisher-scope-capture.py']['path'],
                                                    'sha256': refs['publisher-scope-capture.py']['sha256']},
                              publisher_scope_sha256='a' * 64, publisher_proof=None,
                              publisher_proof_output=str(self.base / 'proof.json'),
                              publisher_proof_binding_output=str(self.base / 'proof-binding.json'))
                profile = dict(checkpoint_helper=str(shim), checkpoint_sha256=config['sources']['checkpoint']['sha256'],
                               phase_state=config['phase_state'], restore_pr=config['restore_pr'], namespace='media',
                               job_name=helper['name'], source_template=helper['source_template']['path'],
                               source_template_sha256=helper['source_template']['sha256'],
                               capture_program=refs['lidarr-native-paths-capture.py']['path'],
                               capture_sha256=refs['lidarr-native-paths-capture.py']['sha256'])
                path = self.base / 'routes.json';path.write_bytes(catalog.canonical({'lidarr_native_helper': profile}));path.chmod(0o600)
                config['publisher_config'] = {'path': str(path), 'sha256': catalog.metadata.sha256(path.read_bytes())}
                trace, jobs = [], {}
                pod = {'metadata': {'uid': 'helper-pod'}, 'status': {'phase': 'Running'}}
                def create(argv, **kw):
                    self.assertEqual(argv, ['kubectl', 'create', '-f', '-', '-o', 'json'])
                    actual = json.loads(kw['input']);uid = 'helper-job';actual['metadata']['uid'] = uid
                    actual['spec']['template']['metadata']['labels'].update({
                        'batch.kubernetes.io/controller-uid': uid, 'controller-uid': uid,
                        'batch.kubernetes.io/job-name': helper['name'], 'job-name': helper['name']})
                    actual['spec']['selector'] = {'matchLabels': {'batch.kubernetes.io/controller-uid': uid}}
                    jobs[helper['name']] = actual;trace.append('helper-create')
                    return SimpleNamespace(returncode=0, stdout=catalog.canonical(actual))
                def delete(argv, **kw):
                    self.assertEqual(json.loads(kw['input_text'])['preconditions']['uid'], 'helper-job')
                    self.assertEqual(json.loads(kw['input_text'])['propagationPolicy'], 'Foreground')
                    trace.append('helper-foreground-delete');jobs.clear()
                def absent(phase):
                    self.assertIsNone(maintenance.writer_row(phase, config)['uid'])
                    if jobs or foreign_after_gc:raise catalog.Refused('full phase union still present')
                    trace.append('full-union-pg-absent')
                class Capture:
                    def __init__(self, received, output, budget):
                        self.asserted = received == {'lidarr_native_helper': profile} and budget == 60
                        self.output = Path(output)
                    def run(self, *_):trace.append('complete-native-route')
                    def execute(self):
                        if not self.asserted:raise AssertionError('collector config differs')
                        self.run(['external-read']);self.output.write_bytes(b'{}');self.output.chmod(0o600)
                host = maintenance.HostAdmission.__new__(maintenance.HostAdmission)
                host.c, host.core, host.publisher_pending = config, core, True
                host.operation, host.status = {'original_abort_epoch': time.time() + 170}, {}
                host.guard_lease = lambda *a: None;host.guard = lambda **kw: trace.append('writer-admission' if not kw.get('read_only_prelude') else 'readonly-admission')
                host.list = lambda *a: [pod] if jobs else []
                host.watch = SimpleNamespace(run=delete, owned_job_pods=lambda *a: [pod], verify_phase_absent=absent,
                                             phase_resources_present=RuntimeError)
                host.supervisor = SimpleNamespace(Supervisor=SimpleNamespace(service_fence=lambda *_: trace.append('full-proof-gate')))
                with mock.patch.dict(core.process.__globals__, lookup_job=lambda *a: jobs.get(helper['name'])), \
                     mock.patch.object(maintenance.subprocess, 'run', side_effect=create), \
                     mock.patch.object(maintenance, 'module', return_value=SimpleNamespace(Capture=Capture)):
                    if foreign_after_gc:
                        with self.assertRaises(catalog.Refused):host.publisher_prelude()
                        self.assertTrue(host.publisher_pending);self.assertNotIn('writer-admission', trace)
                    else:
                        host.publisher_prelude();self.assertFalse(host.publisher_pending)
                        self.assertLess(trace.index('full-proof-gate'), trace.index('helper-foreground-delete'))
                        self.assertLess(trace.index('full-union-pg-absent'), trace.index('writer-admission'))
                self.assertEqual(maintenance.current_publisher_ref(config)['path'], config['publisher_proof_output'])
                phase, _ = core.read_json(Path(config['phase_state']))
                self.assertEqual(maintenance.helper_row(phase, config)['uid'], 'helper-job')
                self.assertIsNone(maintenance.writer_row(phase, config)['uid'])
        self.base = original_base

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
        operation = {'phase_token': phase, 'action': 'forward', 'owner_approved': True, 'root_runtime_go': True,
                     'prepared_only': False, 'original_abort_epoch': self.now + 170}
        operation_path = put('operation.json', operation)
        row = {'namespace': 'media', 'name': 'ransom-maintenance-a', 'phase_token': phase, 'uid': uid, 'writer': True}
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
        collector = self.base / 'collector.py';collector.write_text('def storage_inventory(pods,claims,volumes):return {"current":True}\n');collector.chmod(0o600)
        collector_bytes = collector.read_bytes()
        config = {'phase_token': phase, 'job_name': row['name'], 'operation': {'path': operation_path, 'sha256': catalog.digest(operation)},
                  'publisher_proof': {'path': put('publisher.json', {}), 'sha256': catalog.digest({})},
                  'watcher_state': state_path, 'watcher_stop': str(self.base / 'stop'), 'watcher_pid': 123,
                  'watcher_pgid': 123, 'watcher_birth': 456, 'pod_uid': pod_uid,
                  'cached_source_receipt': {'path': put('cache.json', {}), 'sha256': catalog.digest({})},
                  'manifest_contract': {'path': put('contract.json', {}), 'sha256': catalog.digest({})},
                  'claim_name': 'kavita', 'claim_uid': 'claim-uid', 'claim_spec': copy.deepcopy(claim['spec']),
                  'pv_name': 'bound-pv', 'pv_uid': 'pv-uid', 'pv_spec': copy.deepcopy(pv['spec']),
                  'publisher_scope_hook': {'script': str(collector), 'sha256': catalog.metadata.sha256(collector_bytes)},
                  'publisher_scope_sha256': catalog.digest({})}
        config['converter_lock_access'] = {'fixture': 'exact pre-admitted access'}
        admission = {'phase_token': phase, 'lock_absent': True, 'admitted_epoch': self.now - 1, 'state_inode': 10,
                     'access_sha256': catalog.digest(config['converter_lock_access'])}
        config['converter_lock_admission'] = {'path': put('lock-admission.json', admission), 'sha256': catalog.digest(admission)}
        config['bootstrap_sources'] = []
        for name in ('ransom_catalog_maintenance.py', 'ransom_maintenance_job.py'):
            path = self.base / name;raw = (HERE / name).read_bytes();path.write_bytes(raw);path.chmod(0o600)
            config['bootstrap_sources'].append({'name': name, 'path': str(path), 'sha256': catalog.metadata.sha256(raw)})
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
             mock.patch.object(maintenance, 'sonarr_lock', return_value={'state_inode': 10, 'absent': True}), \
             mock.patch.object(maintenance, 'one_job_phase', return_value={'phase_token': phase, 'owned_jobs': [row]}):
            host.guard()
            pod_guard.verify_owned_pod.assert_called_once_with(row, objects[('job', row['name'], 'media')], pod, pod_uid)
            self.assertEqual(cache.check_live.call_args.kwargs['deadline'], self.now + 170)
            publisher.verify.assert_called_once()
            for failure in ('clock', 'kernel', 'owner', 'claim', 'service', 'holds', 'hook'):
                with self.subTest(failure=failure):
                    original_objects = copy.deepcopy(objects);original_pod = copy.deepcopy(pod)
                    if failure == 'clock':host.operation['original_abort_epoch'] += 1
                    if failure == 'kernel':host.c['watcher_birth'] += 1
                    if failure == 'owner':pod['metadata']['ownerReferences'][0]['uid'] = 'foreign'
                    if failure == 'claim':objects[('pv', 'bound-pv', '')]['spec']['claimRef']['uid'] = 'foreign'
                    if failure == 'service':objects[('deployment', 'kavita', 'media')]['status']['replicas'] = 1
                    if failure == 'holds':watch.verify_cached_stop_holds.side_effect = catalog.Refused('owned hold lost')
                    if failure == 'hook':collector.write_bytes(b'raise AssertionError("unbound hook must never execute")\n')
                    with self.assertRaises((catalog.Refused, RuntimeError)):host.guard()
                    objects.clear();objects.update(original_objects);pod.clear();pod.update(original_pod)
                    host.operation['original_abort_epoch'] = self.now + 170;host.c['watcher_birth'] = 456
                    watch.verify_cached_stop_holds.side_effect = None
                    collector.write_bytes(collector_bytes)

    def test_pre_stop_activation_reuses_original_clock_and_complete_stop_gate(self):
        # Execute the existing publication/schema/Stop gate with external APIs
        # faked; neither a clock reset nor a partial Stop admits the helper.
        directory = HERE.parent / 'copy-window'
        def methods(file, names, scope):
            tree = ast.parse((directory / file).read_bytes())
            found = [copy.deepcopy(node) for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name in names]
            self.assertEqual({node.name for node in found}, set(names))
            exec(compile(ast.Module(body=found, type_ignores=[]), file, 'exec'), scope)
            return SimpleNamespace(**{name: scope[name] for name in names})
        cache = methods('cached_source.py', {'write_private', 'activation', 'wall_guard'},
                        {'wc': SimpleNamespace(require=catalog.require), 'os': maintenance.os, 'uuid': __import__('uuid'),
                         'ctypes': __import__('ctypes'), 'dt': dt, 'read_private': maintenance.private,
                         'contextmanager': maintenance.contextlib.contextmanager, 'signal': maintenance.signal, 'time': time})
        epoch = lambda text: dt.datetime.fromisoformat(text).timestamp()
        original = methods('copy-phase-supervisor.py', {'cached_guard', 'cached_stop_gate'},
                           {'cache': cache, 'time': time, 'epoch': epoch, 'json': json, 'hashlib': maintenance.hashlib,
                            'Refused': catalog.Refused, 'window': SimpleNamespace(sha=catalog.metadata.sha256),
                            'pinned_artifact': lambda ref, cap: maintenance.private(ref['path'], ref['sha256'])})
        phase = {'phase_token': 'a' * 32, 'owned_jobs': [{'uid': None}, {'uid': None}],
                 'pg_leases': [{'backend_pid': None, 'job_uid': None, 'pod_uid': None}]}
        scopes = [('downloads', 'lazylibrarian'), ('media', 'kavita'), ('media', 'libretto'), ('frontend', 'haynesnetwork')]
        for failure in (None, 'watcher', 'kernel', 'late-kernel', 'review', 'union', 'lock', 'service', 'stop-binding'):
            with self.subTest(failure=failure):
                folder = self.base / ('activation-' + str(failure));folder.mkdir(mode=0o700)
                def put(name, value):
                    path = folder / name;path.write_bytes(catalog.canonical(value));path.chmod(0o600)
                    return {'path': str(path), 'sha256': catalog.metadata.sha256(path.read_bytes())}
                holds = {ns + '/' + name: {'before': {'metadata': {'uid': ns + '-' + name}, 'spec': {'suspend': False}}} for ns, name in scopes}
                parents = {name: {'before': {'metadata': {'uid': name}, 'spec': {'suspend': False}}} for name in ('cluster', 'cluster-apps')}
                receipt = dict(holds=holds, parents=parents, stop_main_sha='b' * 40)
                cache_ref = put('cache.json', receipt);contract = put('contract.json', {})
                state = {'armed_at': dt.datetime.fromtimestamp(self.now - 20, dt.timezone.utc).isoformat(),
                         'armed_ready': True, 'recover_ks': True, 'complete': failure == 'watcher', 'restore_pr': '1',
                         'normal_inverse_merge_sha': 'c' * 40, 'cached_source_ready_at': 'reviewed',
                         'cached_source_receipt_sha256': cache_ref['sha256'], 'cached_ks_owners': {
                          ns + '/' + name: {'uid': proof['before']['metadata']['uid'], 'spec': proof['before']['spec'], 'phase_token': phase['phase_token']}
                          for ns, name, proof in [(ns, name, holds[ns + '/' + name]) for ns, name in scopes]
                            + [('flux-system', name, proof) for name, proof in parents.items()]}}
                state_ref = put('watch.json', state);trace = []
                config = {'phase_token': phase['phase_token'], 'watcher_state': state_ref['path'], 'restore_pr': '1',
                          'watcher_stop': str(folder / 'stop'), 'watcher_pid': 123, 'watcher_pgid': 123,
                          'watcher_birth': 999 if failure == 'kernel' else 456, 'normal_merge_sha': 'c' * 40,
                          'cached_source_receipt': cache_ref, 'manifest_contract': contract, 'restore_reserve_seconds': 130,
                          'cached_source_activation': str(folder / 'activation.json'),
                          'watcher_arguments': {'cached_source_activation': str(folder / 'activation.json')},
                          'converter_lock_admission': put('lock.json', {'admitted_epoch': self.now - (301 if failure == 'lock' else 1), 'state_inode': 10})}
                def review(host):
                    trace.append('exact-reviewed-inverse')
                    if failure == 'review':raise catalog.Refused('current inverse review differs')
                    host.status['normal_inverse_merge_sha'] = 'c' * 40
                def absent(_):
                    trace.append('full-union-pg-absent')
                    if failure == 'union':raise catalog.Refused('original writer union present')
                cache.check_live = lambda *args, **kw: trace.append('cache-holds' if kw.get('holds') else 'cache-source-parents')
                cache.read_private = maintenance.private;cache.PARENTS = tuple(parents)
                host = maintenance.HostAdmission.__new__(maintenance.HostAdmission)
                host.c, host.core, host.operation = config, mock.Mock(), {'original_abort_epoch': None}
                host.status, host.deadline, host.stop, host.execution = {'phase_token': phase['phase_token']}, None, False, None
                host.watch = SimpleNamespace(args=SimpleNamespace(arm_deadline=1800), verify_phase_absent=absent)
                host.supervisor = SimpleNamespace(epoch=epoch, cache=cache,
                    window=SimpleNamespace(SCOPES=tuple(scopes)), Supervisor=SimpleNamespace(
                        cached_guard=original.cached_guard, cached_stop_gate=original.cached_stop_gate, merged_inverse_ready=review))
                host.get = lambda *a: {'spec': {'replicas': 0 if failure == 'service' else 1, 'selector': {'matchLabels': {}}},
                                      'status': {'readyReplicas': 1}}
                host.list = lambda *a: [{'metadata': {}}]
                host.guard = lambda **kw: trace.append('complete-stopped-fence')
                def finish_stop(_):
                    trace.append('watcher-stop')
                    active = json.loads(maintenance.private(config['watcher_arguments']['cached_source_activation']))
                    state.update(actuation_budget_started_at=active['actuation_budget_started_at'], cached_stop_actuation_complete_at='complete',
                                 cached_stop_holds=holds, cached_stop_actuation_binding={
                                 'phase_token': phase['phase_token'], 'cached_source_receipt_sha256': cache_ref['sha256'],
                                 'activation_sha256': catalog.digest(active), 'stop_main_sha': 'd' * 40 if failure == 'stop-binding' else 'b' * 40})
                    Path(state_ref['path']).write_bytes(catalog.canonical(state))
                class Clock(dt.datetime):
                    @classmethod
                    def now(cls, tz=None):return dt.datetime.fromtimestamp(self.now, dt.timezone.utc)
                kernel = '123 (fixture) S 1 123 ' + '0 ' * 16 + '456 0'
                with mock.patch.object(maintenance, 'dt', SimpleNamespace(datetime=Clock, timezone=dt.timezone)), \
                     mock.patch.object(maintenance.time, 'time', return_value=self.now), \
                     mock.patch.object(maintenance.time, 'sleep', side_effect=finish_stop), \
                     mock.patch.object(Path, 'read_text', side_effect=[kernel, kernel.replace('456 0', '457 0')] if failure == 'late-kernel' else lambda *a, **kw: kernel), \
                     mock.patch.object(maintenance, 'one_job_phase', return_value=phase), \
                     mock.patch.object(maintenance, 'sonarr_lock', return_value={'state_inode': 10, 'absent': True}):
                    if failure:
                        with self.assertRaises(catalog.Refused):host.activate_and_wait()
                        self.assertNotIn('complete-stopped-fence', trace)
                    else:
                        host.activate_and_wait()
                        self.assertEqual(host.operation['original_abort_epoch'], self.now + 170)
                        self.assertEqual(host.deadline, self.now + 300)
                        self.assertLess(trace.index('exact-reviewed-inverse'), trace.index('watcher-stop'))
                        self.assertLess(trace.index('watcher-stop'), trace.index('complete-stopped-fence'))
                target = Path(config['watcher_arguments']['cached_source_activation'])
                self.assertEqual(target.exists(), failure in (None, 'stop-binding'))
                if target.exists():self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_automatic_handoff_binds_fresh_rows_once_without_replacing_inverse_baseline(self):
        host = maintenance.HostAdmission.__new__(maintenance.HostAdmission)
        state = self.base / 'automatic-watch.json'
        state.write_bytes(catalog.canonical({'actuation_budget_started_at': dt.datetime.fromtimestamp(self.now, dt.timezone.utc).isoformat()}));state.chmod(0o600)
        current_path = self.base / 'fresh.json'
        def current_rows(value):
            raw = catalog.canonical(value);current_path.write_bytes(raw);current_path.chmod(0o600)
            return {'retained_before_path': str(current_path), 'before_sha256': catalog.metadata.sha256(raw),
                    'database_device_inode': self.contract['database_device_inode']}
        host.c = {'approved_schema_sha256': self.contract['schema_sha256'], 'watcher_state': str(state),
                  'bound_operation_output': str(self.base / 'bound-operation.json')}
        host.operation = dict(self.contract, action='forward', original_abort_epoch=None, database_device_inode=None,
                              before=None, before_sha256=None)
        template = copy.deepcopy(host.operation)
        host.supervisor = SimpleNamespace(epoch=lambda text: dt.datetime.fromisoformat(text).timestamp())
        calls = []
        def activate():
            calls.append('activation-stop');host.operation['original_abort_epoch'] = self.now + 170
        host.activate_and_wait = activate
        host.create = lambda: calls.append('create')
        host.publisher_prelude = lambda: calls.append('publisher-prelude')
        proof = current_rows(self.before)
        host.inspect = lambda: (calls.append('inspect') or proof)
        host.deliver = lambda name, raw: calls.append(name)
        host.execute = lambda dsn: (calls.append('execute') or {'admitted': True})
        self.assertEqual(host.run_automatic('private-fixture'), {'admitted': True})
        self.assertEqual(calls, ['activation-stop', 'publisher-prelude', 'create', 'inspect', 'before.json', 'operation.json', 'execute'])
        self.assertEqual(host.operation['original_abort_epoch'], self.now + 170)
        self.assertEqual(host.operation['before_sha256'], catalog.digest(self.before))
        neutral = copy.deepcopy(host.operation)
        for key in ('original_abort_epoch', 'database_device_inode', 'before', 'before_sha256'):neutral[key] = template[key]
        self.assertEqual(neutral, template)
        self.assertEqual((self.base / 'bound-operation.json').stat().st_mode & 0o777, 0o600)

        after = self.scan_fixture()
        original_raw, after_raw = catalog.canonical(self.before), catalog.canonical(after)
        original_path, after_path = self.base / 'original.json', self.base / 'after.json'
        for path, raw in ((original_path, original_raw), (after_path, after_raw)):
            path.write_bytes(raw);path.chmod(0o600)
        host.c.update(approved_schema_sha256=self.contract['schema_sha256'], bound_operation_output=str(self.base / 'inverse-operation.json'),
                      original_rows={'path': str(original_path), 'sha256': catalog.metadata.sha256(original_raw)},
                      post_scan_rows={'path': str(after_path), 'sha256': catalog.metadata.sha256(after_raw)})
        inverse_template = dict(self.contract, action='inverse-after-scan', original_abort_epoch=None, database_device_inode=None,
                                before={'path': '/maintenance/before.json', 'sha256': catalog.metadata.sha256(original_raw)},
                                post_scan={'path': '/maintenance/post-scan.json', 'sha256': catalog.metadata.sha256(after_raw)})
        drift = copy.deepcopy(after);drift['tables']['AppUserProgresses'][0]['BookScrollId'] = catalog.cell('text', 'new-reading')
        proof = current_rows(drift);host.operation = copy.deepcopy(inverse_template);calls.clear()
        with self.assertRaisesRegex(catalog.Refused, 'after-state drifted'):host.run_automatic('private-fixture')
        self.assertEqual(calls, ['activation-stop', 'publisher-prelude', 'create', 'inspect'])
        self.assertFalse((self.base / 'inverse-operation.json').exists())
        proof = current_rows(after);host.operation = copy.deepcopy(inverse_template);calls.clear()
        host.run_automatic('private-fixture')
        self.assertEqual(calls, ['activation-stop', 'publisher-prelude', 'create', 'inspect', 'post-scan.json', 'before.json', 'operation.json', 'execute'])
        self.assertEqual(host.operation['before'], inverse_template['before'])
        self.assertEqual(host.operation['before_sha256'], catalog.digest(self.before))

    def test_reused_native_reader_raw_files_are_private_and_umask_restored(self):
        directory = self.base / 'native-private'
        def capture(pod, output):
            output.mkdir()
            (output / 'kavita.db').write_bytes(b'private-fixture')
            (output / 'copy-proof.json').write_text('{}')
            return output / 'kavita.db', {'readOnlySource': True}
        previous = maintenance.os.umask(0o022)
        try:
            maintenance.private_native_capture(capture, 'fixture-pod', directory)
            self.assertEqual(maintenance.os.umask(0o022), 0o022)
        finally:maintenance.os.umask(previous)
        self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        self.assertTrue(all(path.stat().st_mode & 0o777 == 0o600 for path in directory.iterdir()))
        with self.assertRaises(catalog.Refused):maintenance.private_native_capture(capture, 'fixture-pod', directory)
        directory = self.base / 'native-consumed'
        db = mock.Mock()
        def connect(*args, **kwargs):
            self.assertEqual(maintenance.os.umask(0o077), 0o077)
            (directory / 'kavita.db-shm').write_bytes(b'local-wal-index')
            return db
        previous = maintenance.os.umask(0o022)
        try:
            with mock.patch.object(maintenance.sqlite3, 'connect', side_effect=connect), \
                 mock.patch.object(catalog, 'snapshot', return_value=self.before):
                state, _ = maintenance.private_native_snapshot(capture, 'fixture-pod', directory)
            self.assertEqual(state, self.before)
            self.assertEqual(maintenance.os.umask(0o022), 0o022)
        finally:maintenance.os.umask(previous)
        self.assertEqual((directory / 'kavita.db-shm').stat().st_mode & 0o777, 0o600)
        db.close.assert_called_once()

    def test_exact_operation_and_bootstrap_are_bound_before_worker_guard_or_api(self):
        refs = []
        for name in ('ransom_catalog_maintenance.py', 'ransom_maintenance_job.py'):
            path = self.base / name;raw = (HERE / name).read_bytes();path.write_bytes(raw);path.chmod(0o600)
            refs.append({'name': name, 'path': str(path), 'sha256': catalog.metadata.sha256(raw)})
        maintenance.validate_bootstrap({'bootstrap_sources': refs})
        with self.assertRaises(catalog.Refused):maintenance.validate_bootstrap({'bootstrap_sources': [refs[0], refs[0]]})
        changed = copy.deepcopy(refs);Path(changed[0]['path']).write_bytes(b'unbound catalog')
        changed[0]['sha256'] = catalog.metadata.sha256(b'unbound catalog')
        with self.assertRaises(catalog.Refused):maintenance.validate_bootstrap({'bootstrap_sources': changed})
        host = maintenance.HostAdmission.__new__(maintenance.HostAdmission)
        host.operation = dict(self.contract, phase_token='a' * 32)
        host.guard = mock.Mock()
        with self.assertRaisesRegex(catalog.Refused, 'delivered operation differs'):
            host.deliver('operation.json', catalog.canonical(dict(host.operation, root_runtime_go=False)))
        host.guard.assert_not_called()
        path = self.base / 'worker-operation.json';raw = catalog.canonical(host.operation)
        path.write_bytes(raw);path.chmod(0o600);sha = catalog.metadata.sha256(raw)
        real_private = maintenance.private
        def redirected(source, expected=None, cap=32 * 1024 * 1024):
            return real_private(path if source == '/maintenance/operation.json' else source, expected, cap)
        def fake_worker(operation, dsn, ask, writer):
            self.assertEqual(operation, host.operation);ask(41);return {'changed_cells': 7}
        for failure in (None, 'file', 'ack'):
            with self.subTest(failure=failure):
                path.write_bytes(raw if failure != 'file' else b'{}')
                ack = {'event': 'guard-ok', 'phase_token': 'a' * 32, 'operation_sha256': sha if failure != 'ack' else '0' * 64, 'sequence': 1}
                stream = io.StringIO(json.dumps({'dsn': 'private-fixture'}) + '\n' + json.dumps(ack) + '\n')
                output = io.StringIO()
                with mock.patch.dict(sys.modules, {'book_copy_writer': SimpleNamespace(DeadlineExpired=catalog.Refused)}), \
                     mock.patch.object(maintenance, 'private', side_effect=redirected), mock.patch.object(maintenance, 'worker', side_effect=fake_worker) as worker, \
                     mock.patch.object(maintenance.time, 'time', return_value=self.now), mock.patch.object(maintenance.signal, 'signal'), \
                     mock.patch.object(maintenance.signal, 'setitimer'), mock.patch.object(sys, 'argv', ['-c', sha]), \
                     mock.patch.object(sys, 'stdin', stream), mock.patch.object(sys, 'stdout', output):
                    if failure:
                        with self.assertRaises(catalog.Refused):maintenance.worker_main()
                        if failure == 'file':worker.assert_not_called()
                    else:
                        maintenance.worker_main()
                        events = [json.loads(line) for line in output.getvalue().splitlines()]
                        self.assertEqual([event['event'] for event in events], ['guard', 'complete'])
                        self.assertTrue(all(event['operation_sha256'] == sha for event in events))

    def test_epub_and_inverse_backup_preflight_refuses_before_catalog_mutation(self):
        folder = self.root / 'Daniel Silva' / 'Ransom';folder.mkdir(parents=True)
        path = folder / 'Ransom.epub';path.write_bytes(b'original')
        source, candidate = catalog.metadata.sha256(b'original'), catalog.metadata.sha256(b'candidate')
        operation = {'action': 'forward', 'original_epub_sha256': source, 'candidate_epub_sha256': candidate}
        guard = mock.Mock()
        with mock.patch.object(catalog, 'FILE', str(path)), mock.patch.object(maintenance, 'LIBRARY', str(self.root)), \
             mock.patch.object(maintenance, 'STATE', str(self.base / '.epub-convert')), \
             mock.patch.object(catalog.metadata.time, 'time', return_value=time.time() + 901) as clock, \
             mock.patch.object(catalog.metadata, 'sanitized_epub', return_value=(b'candidate', {})):
            maintenance.preflight_epub(operation, guard)
            self.assertEqual(guard.call_count, 2)
            with self.assertRaises(catalog.Refused):maintenance.preflight_epub(dict(operation, candidate_epub_sha256='0' * 64), guard)
            clock.return_value = path.stat().st_ctime + 899
            with self.assertRaises(catalog.Refused):maintenance.preflight_epub(operation, guard)
            clock.return_value += 2
            with mock.patch.object(catalog.metadata, 'sanitized_epub', return_value=(b'original', {})):
                with self.assertRaises(catalog.Refused):maintenance.preflight_epub(dict(operation, candidate_epub_sha256=source), guard)
            path.write_bytes(b'candidate')
            inverse = dict(operation, action='inverse-before-scan', epub_backup_manifest='/wrong/backup.json')
            with self.assertRaises(catalog.Refused):maintenance.preflight_epub(inverse, guard)
            relative = str(path.relative_to(self.root))
            inverse['epub_backup_manifest'] = str(self.base / '.epub-convert' / 'backup' / (catalog.metadata.sha256(relative.encode()) + '-' + source + '.json'))
            with mock.patch.object(catalog.metadata, 'restore_backup', return_value={'path': relative, 'original_sha256': source}) as restore:
                maintenance.preflight_epub(inverse, guard)
                restore.assert_called_once_with(inverse['epub_backup_manifest'], str(self.root), str(self.base / '.epub-convert'), dry_run=True)
        self.assertEqual(catalog.snapshot(self.db), self.before)

    def test_interrupted_worker_lock_release_requires_original_absence_and_exact_empty_custody(self):
        state = self.base / 'converter-state';state.mkdir()
        lock = state / 'lock';lock.mkdir()
        phase = {'phase_token': 'a' * 32, 'owned_jobs': [{'uid': 'job-uid', 'writer': True, 'namespace': 'media', 'name': 'ransom-a'}]}
        observed = maintenance.lock_io(str(state))
        custody = {k: observed[k] for k in ('state_inode', 'lock_inode', 'lock_uid', 'lock_mode')}
        custody['lock_uid'] = 1000  # Production Job always runs as UID1000.
        def put(name, value):
            path = self.base / name;path.write_bytes(catalog.canonical(value));path.chmod(0o600)
            return str(path)
        access = {'fixture': 'exact pre-admitted access'}
        admission = {'state_inode': observed['state_inode'], 'phase_token': phase['phase_token'], 'access_sha256': catalog.digest(access)}
        config = {'phase_token': phase['phase_token'], 'job_name': 'ransom-a', 'converter_lock_custody_output': str(self.base / 'lock-custody.json'),
                  'converter_lock_access': access,
                  'converter_lock_admission': {'path': put('lock-admit.json', admission), 'sha256': catalog.digest(admission)},
                  'pod_uid': 'pod-uid', 'pg_owner': put('lock-lease.json', {'backend_pid': 41, 'pod_uid': 'pod-uid'}),
                  'bound_operation_output': put('lock-operation.json', {'phase_token': phase['phase_token']}),
                  'publisher_proof': {'path': put('lock-publishers.json', {}), 'sha256': catalog.digest({})},
                  'publisher_scope_hook': {'script': put('lock-hook.json', {}), 'sha256': catalog.digest({})},
                  'cached_source_receipt': {'path': put('lock-cache.json', {}), 'sha256': catalog.digest({})},
                  'manifest_contract': {'path': put('lock-contract.json', {}), 'sha256': catalog.digest({})}}
        event = {'lock_custody': dict(custody, created_after_absent=True, nas=maintenance.BOOK_NFS,
                                    logical_path=maintenance.BOOK_NFS['path'] + '/.epub-convert/lock'),
                 'phase_token': phase['phase_token'], 'operation_sha256': catalog.digest({'phase_token': phase['phase_token']}), 'backend_pid': 41}
        lease = {'phase_token': phase['phase_token'], 'backend_pid': 41, 'job_uid': 'job-uid', 'pod_uid': 'pod-uid'}
        maintenance.record_lock_custody(config, event, lease)
        self.assertEqual(Path(config['converter_lock_custody_output']).stat().st_mode & 0o777, 0o600)
        with self.assertRaises(catalog.Refused):maintenance.record_lock_custody(config, dict(event, lock_custody=dict(event['lock_custody'], lock_inode=999)), lease)
        trace = []
        class Base:
            def inventory(self, *_):return []
            def verify_phase_absent(self, _):
                trace.append('original-union-and-pg-absence')
                if self.present:raise catalog.Refused('original writer still present')
            def verify_cached_stop_holds(self, _):
                trace.append('owned-held-drained')
                if self.hold_lost:raise catalog.Refused('converter hold lost')
        def service(observer):trace.append('current-full-service-publisher-fence');observer.guard_lease()
        supervisor = SimpleNamespace(epoch=lambda value: value, cache=SimpleNamespace(check_live=lambda *a, **kw: trace.append('all-seven-held')),
                                     Supervisor=SimpleNamespace(service_fence=service))
        cls = maintenance.maintenance_watchdog(SimpleNamespace(Watchdog=Base, PhaseResourcesPresent=RuntimeError), mock.Mock(), config, supervisor, mock.Mock())
        watch = cls();watch.state = {'actuation_budget_started_at': self.now, 'cached_stop_actuation_binding': {}}
        watch.present = watch.hold_lost = False;watch.kube = mock.Mock();watch.run = mock.Mock()
        def route(c, get, run, expected=None, release=False):
            trace.append('rmdir' if release else 'lock-observation')
            # Same NAS inode; fixture process UID differs from production UID1000.
            if expected:expected = dict(expected, lock_uid=lock.stat().st_uid)
            actual = maintenance.lock_io(str(state), expected, release)
            if not actual['absent']:actual['lock_uid'] = 1000
            return actual
        with mock.patch.object(maintenance.time, 'time', return_value=self.now + 200), mock.patch.object(maintenance, 'sonarr_lock', side_effect=route):
            for failure in ('writer', 'hold', 'foreign', 'nonempty'):
                with self.subTest(failure=failure):
                    watch.present = failure == 'writer';watch.hold_lost = failure == 'hold'
                    proof = json.loads(Path(config['converter_lock_custody_output']).read_bytes())
                    if failure == 'foreign':
                        changed = dict(proof, lock_inode=proof['lock_inode'] + 1)
                        Path(config['converter_lock_custody_output']).write_bytes(catalog.canonical(changed))
                    if failure == 'nonempty':(lock / 'foreign-member').write_bytes(b'preserve')
                    trace.clear()
                    with self.assertRaises((catalog.Refused, ValueError)):watch.verify_phase_absent(phase)
                    self.assertTrue(lock.exists());self.assertNotIn('rmdir', trace)
                    Path(config['converter_lock_custody_output']).write_bytes(catalog.canonical(proof))
                    if failure == 'nonempty':(lock / 'foreign-member').unlink()
            watch.present = watch.hold_lost = False;trace.clear()
            watch.verify_phase_absent(phase)
            self.assertFalse(lock.exists())
            self.assertLess(trace.index('original-union-and-pg-absence'), trace.index('rmdir'))
            self.assertLess(trace.index('current-full-service-publisher-fence'), trace.index('rmdir'))
            self.assertEqual(trace[-1], 'lock-observation')
            # An empty replacement state directory cannot conceal the original inode/lock.
            original = state.rename(self.base / 'original-converter-state');state.mkdir()
            with self.assertRaises(catalog.Refused):watch.verify_phase_absent(phase)
            state.rmdir();original.rename(state)
        # Low-level CAS rejects a foreign inode and nonempty directory without deleting it.
        lock.mkdir();identity = maintenance.lock_io(str(state));identity = {k: identity[k] for k in custody}
        with self.assertRaises(ValueError):maintenance.lock_io(str(state), dict(identity, lock_inode=identity['lock_inode'] + 1), True)
        (lock / 'foreign').write_bytes(b'keep')
        with self.assertRaises(ValueError):maintenance.lock_io(str(state), identity, True)
        self.assertTrue(lock.exists())

    def test_existing_sonarr_lock_route_admits_exact_mount_tool_uid_and_refuses_drift(self):
        state = self.base / 'mapped-nas-state';state.mkdir()
        access = {'namespace': 'media', 'container': 'app', 'pod_name': 'sonarr-fixture', 'pod_uid': 'original-sonarr-uid',
                  'node_name': 'worker-fixture', 'image_id': 'immutable-sonarr-image', 'restart_count': 0,
                  'nfs': {'server': maintenance.BOOK_NFS['server'], 'path': '/hdd-nfs-repl'},
                  'volume_name': 'data-proxmox', 'mount_path': '/data/cephfs-hdd',
                  'runtime_uid': maintenance.os.getuid(),
                  'tools': {name: {'path': shutil.which(name), 'sha256': catalog.metadata.sha256(Path(shutil.which(name)).read_bytes())}
                            for name in ('sh', 'stat', 'sha256sum', 'id', 'rmdir', 'find')}}
        pod = {'metadata': {'uid': access['pod_uid'], 'labels': {'app.kubernetes.io/name': 'sonarr'}},
               'spec': {'nodeName': access['node_name'], 'volumes': [{'name': access['volume_name'], 'nfs': access['nfs']}],
                        'containers': [{'name': 'app', 'volumeMounts': [{'name': access['volume_name'], 'mountPath': access['mount_path']}]},
                                       {'name': 'exportarr', 'volumeMounts': []}]},
               'status': {'phase': 'Running', 'containerStatuses': [{'name': 'exportarr', 'imageID': 'sidecar', 'ready': True, 'restartCount': 0},
                                                                  {'name': 'app', 'imageID': access['image_id'], 'ready': True, 'restartCount': 0}]}}
        access['pod_spec'] = copy.deepcopy(pod['spec'])
        config = {'converter_lock_access': access, 'phase_token': 'a' * 32, 'owner_approved': True, 'root_lock_admission_go': True}
        get = mock.Mock(return_value=pod)
        calls = []
        def run(argv, timeout):
            self.assertEqual(argv[:9], ['kubectl', 'exec', '-n', 'media', access['pod_name'], '-c', 'app', '--', access['tools']['sh']['path']])
            # Model only the already-proved NFS mount mapping; execute the actual fixed shell program.
            self.assertIn('state=' + maintenance.STATE, argv[-1]);calls.append(True)
            program = argv[-1].replace('state=' + maintenance.STATE, 'state=' + str(state))
            result = subprocess.run([argv[8], '-c', program], capture_output=True, text=True, timeout=timeout)
            catalog.require(result.returncode == 0, 'fixture exact Sonarr shell refused')
            return result.stdout
        receipt = maintenance.lock_admission(config, get, run)
        self.assertEqual(receipt['state_inode'], state.stat().st_ino);self.assertTrue(receipt['lock_absent'])
        for failure in ('pod', 'mount', 'uid', 'tool', 'existing-lock', 'duplicate-app', 'missing-app'):
            with self.subTest(failure=failure):
                old = copy.deepcopy(pod);old_access = copy.deepcopy(access)
                if failure == 'pod':pod['metadata']['uid'] = 'replacement'
                if failure == 'mount':pod['spec']['volumes'][0]['nfs']['path'] = '/different-export'
                if failure == 'uid':access['runtime_uid'] += 1
                if failure == 'tool':access['tools']['stat']['sha256'] = '0' * 64
                if failure == 'existing-lock':(state / 'lock').mkdir()
                if failure == 'duplicate-app':pod['status']['containerStatuses'].append(copy.deepcopy(pod['status']['containerStatuses'][1]))
                if failure == 'missing-app':pod['status']['containerStatuses'].pop()
                with self.assertRaises((catalog.Refused, AssertionError)):maintenance.lock_admission(config, get, run)
                pod.clear();pod.update(old);access.clear();access.update(old_access)
                if failure == 'existing-lock':(state / 'lock').rmdir()
        # The actual shell's CAS must refuse foreign/nonempty/replaced-state locks,
        # and remove only the exact empty phase-created directory.
        lock = state / 'lock';lock.mkdir()
        identity = maintenance.sonarr_lock(config, get, run)
        expected = {key: identity[key] for key in ('state_inode', 'lock_inode', 'lock_uid', 'lock_mode')}
        with self.assertRaises(catalog.Refused):
            maintenance.sonarr_lock(config, get, run, dict(expected, lock_inode=expected['lock_inode'] + 1), True)
        (lock / 'foreign').write_bytes(b'keep')
        with self.assertRaises(catalog.Refused):maintenance.sonarr_lock(config, get, run, expected, True)
        self.assertEqual((lock / 'foreign').read_bytes(), b'keep');(lock / 'foreign').unlink()
        original = state.rename(self.base / 'original-shell-state');state.mkdir()
        with self.assertRaises(catalog.Refused):maintenance.sonarr_lock(config, get, run, expected, True)
        state.rmdir();original.rename(state)
        released = maintenance.sonarr_lock(config, get, run, expected, True)
        self.assertTrue(released['absent']);self.assertFalse(lock.exists())
        self.assertEqual(released['state_inode'], expected['state_inode'])


if __name__ == "__main__":
    unittest.main()
