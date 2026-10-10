#!/usr/bin/env python3
"""Private, read-only verifier for the normal write scopes of active publishers.

This does not assert that a process is incapable of arbitrary filesystem writes.
It accepts only root-reviewed, complete configuration captures, bound to actual
pod/spec identities and checked real paths. The collector must refresh the proof
throughout the copy window. Unknown, changed, enabled-hook or overlapping scopes
refuse; no additional service outages are implied.
"""
import datetime as dt
import hashlib
import importlib.util
import json
import posixpath
from pathlib import Path, PurePosixPath

SERVER = 'gasha01.haynesnetwork'
EXPORT = '/hdd-nfs-repl'
EBOOKS = 'data/media/books/EBooks'
PHASE_LABEL = 'issue825.haynesnetwork/phase'
KINDS = {'radarr', 'sonarr', 'lidarr', 'qbittorrent', 'sabnzbd', 'sabnzbd-fast', 'kapowarr', 'ytdrivarr'}
class Refused(RuntimeError): pass
CAP=64*1024*1024
def read_bounded(file,cap=CAP):
    with Path(file).open('rb') as source:raw=source.read(cap+1)
    if len(raw)>cap:raise Refused('publisher proof exceeds byte cap')
    return raw
def nfs_separation(value,current):
    if value.get('schema')!=1 or value.get('read_only') is not True:raise Refused('alternate NFS physical proof is missing')
    checked_at(value['capture_started_at'],current,65);checked_at(value['captured_at'],current,35)
    raw=read_bounded(value['host_inventory'],4*1024*1024)
    if hashlib.sha256(raw).hexdigest()!=value['host_inventory_sha256']:raise Refused('host mount inventory bytes changed')
    inventory=json.loads(raw);rows=[]
    def walk(values):
        if not isinstance(values,list):raise Refused('complete host mount inventory is missing')
        for row in values:
            if len(rows)>=10000:raise Refused('host mount inventory exceeds bound')
            rows.append(row);walk(row.get('children',[]))
    walk(inventory['filesystems'])
    roots=[r for r in rows if r.get('target')=='/mnt/user']
    if len(roots)!=1 or roots[0].get('fstype')!='fuse.shfs' or roots[0].get('source')!='shfs':raise Refused('Unraid user share backing is unknown')
    if any(r.get('target','').startswith('/mnt/user/data/') or r.get('target')=='/mnt/user/data' for r in rows):raise Refused('nested Unraid export mount can alias EBooks')
    # A remote filesystem under an array/pool data share can enter shfs. The
    # observed CIFS mount under /mnt/remotes is outside every contributing root.
    remote=('nfs','nfs4','cifs','fuse.sshfs','ceph','cephfs')
    classified_remote=('/mnt/remotes/NAS01.HAYNESNETWORK_cephfs-hdd','//NAS01.HAYNESNETWORK/cephfs-hdd','cifs')
    if any(r.get('fstype') in remote and r.get('target','').startswith('/mnt/')
           and (r.get('target'),r.get('source'),r.get('fstype'))!=classified_remote for r in rows):raise Refused('remote filesystem can enter array/cache-pool exported share')
    addresses=value['host_addresses']
    if (not isinstance(addresses,list) or '192.168.40.39' not in addresses or '192.168.40.23' in addresses
            or value.get('nfs_server')!='haynestower.haynesnetwork' or value.get('nfs_export')!='/mnt/user/data'):raise Refused('NFS server address separation is unproved')
    stable={k:value[k] for k in ('nfs_server','nfs_export','host_inventory_sha256','host_addresses')}
    return {('NFS','haynestower.haynesnetwork','/mnt/user/data'):{'server_address':'192.168.40.39','separation_sha256':digest(stable)}}

def digest(value): return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
def pod_scope(pod):
    spec = pod['spec']
    return digest({key: spec.get(key, []) for key in ('containers', 'initContainers', 'ephemeralContainers', 'volumes')})
def path(value, absolute=True):
    if (not isinstance(value, str) or not value or len(value)>4096
            or any(ord(c)<32 or ord(c)==127 for c in value) or '\\' in value):
        raise Refused('invalid publisher path')
    p = PurePosixPath(value)
    if p.is_absolute() != absolute or any(part in ('.', '..') for part in value.split('/')):
        raise Refused('publisher path must be canonical and confined')
    return str(p)
def overlaps(left, right):
    return left == right or left.startswith(right + '/') or right.startswith(left + '/')
def normal_paths(kind, source):
    """Derive the whole path scope from captured API rows, never filtered paths."""
    result = []
    if source.get('complete') is not True or source.get('custom_hooks_disabled') is not True:
        raise Refused('source API capture is incomplete or has enabled custom hooks')
    if kind in ('radarr', 'sonarr', 'lidarr'):
        items = source['items']
        if len(items) != source['item_count']: raise Refused('incomplete arr item inventory')
        result = [row['path'] for row in source['root_folders']] + [row['path'] for row in items]
        if any(row['enabled'] is not False for row in source.get('custom_hooks', [])):
            raise Refused('arr custom script is enabled or unknown')
    elif kind == 'qbittorrent':
        preferences = source['preferences']; torrents = source['torrents']
        if len(torrents) != source['torrent_count']: raise Refused('incomplete torrent inventory')
        if preferences.get('autorun_enabled') is not False or preferences.get('autorun_on_torrent_added_enabled') is not False:
            raise Refused('qBittorrent custom hooks are enabled')
        if not isinstance(preferences.get('scan_dirs'), dict) or preferences['scan_dirs']:
            raise Refused('qBittorrent watch-directory writer scope is unclassified')
        result = [preferences['save_path']]
        for field in ('temp_path', 'export_dir', 'export_dir_fin'):
            if preferences.get(field): result.append(preferences[field])
        result += [row['savePath'] or preferences['save_path'] for row in source['categories'].values()]
        for row in torrents:
            result.append(row['save_path'])
            if row.get('content_path'): result.append(row['content_path'])
        rules=source['rss_rules']
        if not isinstance(rules,dict) or len(rules)!=source['rss_rule_count']:
            raise Refused('incomplete qBittorrent RSS save-path inventory')
        for row in rules.values():
            if type(row['enabled']) is not bool or not isinstance(row.get('savePath'),str) or not isinstance(row.get('assignedCategory'),str):
                raise Refused('qBittorrent RSS save path/category is unknown')
            # Even disabled rules retain their saved destination; do not silently
            # omit a configured path that can become active without new identity.
            if row['savePath']:result.append(row['savePath'])
            elif row['assignedCategory']:
                if row['assignedCategory'] not in source['categories']:raise Refused('qBittorrent RSS category is unresolved')
                result.append(source['categories'][row['assignedCategory']]['savePath'] or preferences['save_path'])
    elif kind in ('sabnzbd', 'sabnzbd-fast'):
        misc = source['misc'];bases=source['path_bases'];home=path(bases['home']);local=path(bases['local_data'])
        complete = misc['complete_dir']
        complete=complete if complete.startswith('/') else posixpath.join(home,complete)
        for field, value in misc.items():
            if field.endswith('_dir') and value:
                base=bases['interfaces'] if field=='web_dir' else local if field in ('admin_dir','log_dir','nzb_backup_dir') else home
                result.append(value if value.startswith('/') else posixpath.join(base, value))
        if len(source['categories']) != source['category_count']: raise Refused('incomplete SAB category inventory')
        disabled = ('', 'Default', '*None', 'None', None)
        if any(misc.get(field) not in disabled for field in ('default_script', 'dirscan_script')):
            raise Refused('SAB custom scripts are enabled')
        for row in source['categories']:
            if row.get('script') not in disabled: raise Refused('SAB category script is enabled')
            if row.get('dir'): result.append(row['dir'] if row['dir'].startswith('/') else posixpath.join(complete, row['dir']))
    elif kind == 'kapowarr':
        if len(source['volumes']) != source['volume_count']: raise Refused('incomplete Kapowarr volume inventory')
        if source.get('native_code_profile') != 'kapowarr-v1.3.2-reviewed':raise Refused('Kapowarr hook capability is unproved')
        naming=source['naming_settings']
        if set(naming)!=set(['volume_folder_naming','file_naming','file_naming_empty','file_naming_special_version','file_naming_vai']):raise Refused('Kapowarr complete naming formats are unknown')
        if any(not isinstance(v,str) or not v or v.startswith('/') or '..' in v.split('/')
               or '\\' in v or any(ord(c)<32 or ord(c)==127 for c in v) for v in naming.values()):raise Refused('Kapowarr naming can escape its captured roots')
        result = [row['folder'] for row in source['root_folders']] + [row['folder'] for row in source['volumes']]
        result += [source['download_folder'],source['db_backup_folder']]
        spec=importlib.util.spec_from_file_location('kapowarr_native_paths',Path(__file__).with_name('kapowarr-native-files-capture.py'))
        native_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(native_module)
        native=source.get('native_file_inventory',{})
        if (native.get('complete') is not True or native.get('read_only') is not True
                or native.get('native_profile')!=native_module.PROFILE
                or native.get('connection_uri_mode')!='ro' or native.get('query_only') is not True
                or native.get('database',{}).get('path')!=native_module.DB
                or native.get('installed_sha256')!=native_module.PINS):raise Refused('Kapowarr native file inventory is unproved')
        try:native_module.validate_inventory(native)
        except (native_module.Refused,KeyError,TypeError,ValueError) as error:raise Refused('Kapowarr native file inventory is incomplete or invalid') from error
        if (set(native.get('schemas',{}))!=set(native_module.SCHEMAS)
                or any([c.get('name') for c in native['schemas'][name]]!=columns for name,columns in native_module.SCHEMAS.items())
                or digest(native['schemas'])!=native['schema_sha256']):raise Refused('Kapowarr native file schema is unproved')
        tables=native['tables']
        roots=sorted(tables['root_folders'],key=lambda r:r['id'])
        volumes=sorted([{'id':r['id'],'folder':r['folder']} for r in tables['volumes']],key=lambda r:r['id'])
        if roots!=sorted(source['root_folders'],key=lambda r:r['id']) or volumes!=sorted(source['volumes'],key=lambda r:r['id']):raise Refused('Kapowarr API and native path inventory disagree')
        processes=native.get('processes')
        if (not isinstance(processes,list) or not processes
                or any(type(p.get('pid')) is not int or p['pid']<=0 or p.get('entrypoint')!='/app/Kapowarr.py'
                       or p.get('database_folder')!='/app/db' or not isinstance(p.get('normal_write_paths'),list)
                       or not p['normal_write_paths'] for p in processes)):raise Refused('Kapowarr actual process paths are unproved')
        result += [row['filepath'] for row in tables['files']] + [native['database']['path']]
        result += [value for process in processes for value in process['normal_write_paths']]
    else:
        if source.get('native_code_profile') != 'ytdrivarr-v0.9.2-reviewed':raise Refused('YT core hook capability is unproved')
        libraries = source['libraries']
        if len(libraries) != source['library_count']: raise Refused('incomplete ytdrivarr library inventory')
        for row in libraries:
            projection = row['projectionPath']
            credential = row['credentialPath'] if row['credentialPath'] is not None else row['mediaRoot']
            result += [row['mediaRoot'], row['workingDirectory'],
                       projection if projection.startswith('/') else posixpath.join(source['projection_root'],projection),
                       credential if credential.startswith('/') else posixpath.join(source['credential_root'] or source['cwd'],credential)]
    return sorted({path(value) for value in result})
def scope_digest(rows):
    fields = ('kind', 'namespace', 'pod_name', 'container_name', 'pod_uid', 'pod_scope_sha256', 'api_binding',
              'configuration_sha256_before', 'configuration_sha256_after', 'normal_write_paths', 'resolved_paths','mountinfo_capture_sha256')
    return digest(sorted([{key: row[key] for key in fields} for row in rows],
                         key=lambda row: (row['namespace'], row['pod_name'], row['container_name'])))
def checked_at(value, current, age=65):
    try:
        at = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        if at.tzinfo is None: raise ValueError()
        delta = current - at.timestamp()
    except (ValueError, TypeError, AttributeError): raise Refused('publisher timestamp is invalid')
    if not -5 <= delta <= age: raise Refused('publisher scope proof is stale or future')
    return at.timestamp()

def mount_volume(pod, mount):
    return next((v for v in pod['spec'].get('volumes', []) if v['name'] == mount['name']), {})
def storage_source(pod, mount, storage):
    volume = mount_volume(pod, mount)
    claim = volume.get('persistentVolumeClaim', {}).get('claimName')
    if claim:
        key = (pod['metadata']['namespace'], claim)
        item = storage.get(key)
        if item is None or not item.get('claim_uid') or not item.get('pv_uid'):
            raise Refused('publisher PVC physical source is unknown')
        return item['source']
    return volume
def verify_client_nfs(pod,container,mounted,storage):
    if not isinstance(mounted,list):raise Refused('actual NFS client inventory is missing')
    for mount in container.get('volumeMounts',[]):
        source=storage_source(pod,mount,storage);nfs=source.get('nfs')
        if nfs is None:continue
        if mount.get('subPathExpr'):raise Refused('dynamic NFS mount root is unknown')
        sub=path(mount['subPath'],False) if mount.get('subPath') else ''
        expected_root=path(posixpath.join(path(nfs['path']),sub))
        expected_address='192.168.40.23' if (nfs['server'],nfs['path'])==(SERVER,EXPORT) else storage.get(('NFS',nfs['server'],nfs['path']),{}).get('server_address')
        rows=[v for v in mounted if v['mount_path']==path(mount['mountPath'])]
        if len(rows)!=1 or expected_address is None:raise Refused('actual NFS mount/source is unknown')
        row=rows[0];server,separator,export=row['source'].partition(':')
        actual_root=path(posixpath.join(path(export),path(row['kernel_root']).lstrip('/'))) if separator else None
        if server!=nfs['server'] or actual_root!=expected_root or row['server_address']!=expected_address:raise Refused('actual NFS server/root differs from export plus subPath')
NORMAL_PROFILE_SHA256='21765c9982ecaf04d8e440c84ee37e8548c2e292b73179d7671d5fa7b2f9a5d3'
def normal_profiles():
    raw=read_bounded(Path(__file__).with_name('approved-normal-write-profiles.json'),8*1024*1024)
    if hashlib.sha256(raw).hexdigest()!=NORMAL_PROFILE_SHA256:raise Refused('normal-write approved profile bytes changed')
    value=json.loads(raw)
    if (value.get('schema')!=1 or value.get('normal_configured_writes_only') is not True
            or value.get('kernel_mount_alias_absence_proven') is not False
            or value.get('arbitrary_privileged_write_incapability_proven') is not False):raise Refused('normal-write profile contract changed')
    rows=value['profiles'];result={r['pod_uid']:r for r in rows}
    if len(result)!=145 or len(rows)!=len(result):raise Refused('normal-write profile identities are incomplete or duplicated')
    return result
def matches_normal_profile(pod,container_name,mount,storage,profiles):
    row=(profiles or {}).get(pod['metadata']['uid'])
    if row is None:return False
    meta=pod['metadata']
    if (any(meta.get(k)!=row[v] for k,v in [('namespace','namespace'),('name','pod_name'),('uid','pod_uid')])
            or pod['spec'].get('nodeName')!=row['node'] or digest(pod['spec'])!=row['full_spec_sha256']
            or meta.get('ownerReferences',[])!=row['direct_owner_references']):raise Refused('normal-write profile Pod, owner or complete spec changed')
    if row.get('unstarted_snapshot') is True:
        status=pod.get('status',{})
        if (status.get('phase')!='Pending' or any(not isinstance(status.get(kind,[]),list) or status.get(kind,[])
                for kind in ('containerStatuses','initContainerStatuses','ephemeralContainerStatuses'))):
            raise Refused('normal-write unstarted snapshot phase or status changed')
    for kind,expected in row['captured_image_ids'].items():
        actual={v['name']:v.get('imageID') for v in pod.get('status',{}).get(kind,[])}
        if any(actual.get(name)!=image for name,image in expected.items()):raise Refused('normal-write profile actual imageID changed or missing')
    entries=[r for r in row['source_mounts'] if r['container']==container_name and r['mount']==mount]
    if len(entries)!=1:raise Refused('normal-write profile full mount/container changed')
    expected=entries[0];source={k:v for k,v in storage_source(pod,mount,storage).items() if k!='name'}
    if source!=expected['source']:raise Refused('normal-write profile physical source changed')
    binding=expected['claim_binding']
    if binding:
        actual=storage.get((meta['namespace'],binding['claim_name']),{})
        fields=('claim_uid','pv_uid','node_affinity','volume_mode') if 'local' in source else ('claim_uid','pv_uid')
        if any(actual.get(k)!=binding[k] for k in fields):raise Refused('normal-write profile claim/PV identity or affinity changed')
    return True
def possible_ebooks_mount(pod, mount, storage, verified_local=None,profiles=None,container_name=None):
    volume = mount_volume(pod, mount)
    source = storage_source(pod, mount, storage)
    nfs = source.get('nfs', {})
    if not nfs:
        claim = volume.get('persistentVolumeClaim', {}).get('claimName')
        backing = (verified_local or {}).get((pod['metadata']['uid'], claim))
        if backing is not None:
            profile, mounts = backing['profile'], backing['filesystem_mounts']
            if (pod['metadata']['namespace'] != profile['namespace'] or pod['metadata']['uid'] != profile['pod_uid']
                    or pod['spec'].get('nodeName') != profile['node'] or claim != profile['claim_name']
                    or source != {'local': {'fsType': '', 'path': profile['local_path']}}
                    or mount.get('mountPath') != profile['mount_path'] or mount.get('subPath') or mount.get('subPathExpr')
                    or any(row['mount_path'].startswith(profile['mount_path'].rstrip('/') + '/') for row in mounts)):
                raise Refused('local_backing: final scan mount differs from the exact verified local profile')
            return False
        if ('emptyDir' in source or 'configMap' in source or 'secret' in source
                or 'projected' in source or 'downwardAPI' in source): return False
        csi = source.get('csi', {})
        if csi.get('driver', '').endswith('.rbd.csi.ceph.com') and csi.get('volumeHandle'): return False
        if matches_normal_profile(pod,container_name,mount,storage,profiles):return False
        raise Refused('publisher writable physical storage is unclassified')
    if nfs.get('server') != SERVER or nfs.get('path') != EXPORT:
        if ('NFS',nfs.get('server'),nfs.get('path')) not in storage:raise Refused('alternate NFS export separation is unknown')
        return False
    if mount.get('subPathExpr'):
        raise Refused('dynamic publisher subPath requires an explicit physical mapping')
    relative = path(mount['subPath'], False) if mount.get('subPath') else ''
    return not relative or overlaps(relative, EBOOKS)

def verify(proof_path, expected_scope_sha256, pods, owned_job_uids, current, storage=None):
    raw = read_bounded(proof_path)
    proof = json.loads(raw)
    if proof.get('schema') != 1 or proof.get('read_only') is not True or proof.get('complete') is not True:
        raise Refused('complete read-only publisher scope proof is required')
    start = checked_at(proof.get('capture_started_at'), current)
    end = checked_at(proof.get('captured_at'), current,35)
    if start > end: raise Refused('publisher capture completion precedes start')
    separation=nfs_separation(proof['storage_separation'],current) if proof.get('storage_separation') else {}
    storage={**(storage or {}),**separation}
    rows = proof.get('publishers')
    if not isinstance(rows, list): raise Refused('full publisher records are required')
    if scope_digest(rows) != expected_scope_sha256:
        raise Refused('publisher scope differs from the reviewed pre-window capture')
    records = {}
    verified_local = {}
    approved_profiles=normal_profiles()
    for row in rows:
        identity = (row.get('namespace'), row.get('pod_name'), row.get('container_name'))
        if identity in records or row.get('kind') not in KINDS:
            raise Refused('duplicate or unknown publisher identity')
        binding=row.get('api_binding',{})
        if (binding.get('target_pod_uid')!=row.get('pod_uid') or binding.get('transport') not in ('exec_loopback','cluster_service')):
            raise Refused('source API is not bound to the actual publisher pod')
        if (row.get('complete_configuration') is not True or row.get('custom_hooks_disabled') is not True
                or row.get('configuration_sha256_before') != row.get('configuration_sha256_after')
                or not isinstance(row.get('configuration_sha256_before'), str)
                or len(row['configuration_sha256_before']) != 64):
            raise Refused('publisher configuration is incomplete, changed or has custom hooks')
        source = Path(row['source_capture'])
        source_raw=read_bounded(source)
        if hashlib.sha256(source_raw).hexdigest() != row['source_capture_sha256']:
            raise Refused('publisher source capture bytes changed')
        source_rows = json.loads(source_raw)
        after_source = Path(row['source_capture_after'])
        after_raw=read_bounded(after_source)
        if hashlib.sha256(after_raw).hexdigest() != row['source_capture_after_sha256']:
            raise Refused('publisher after-capture bytes changed')
        after_rows = json.loads(after_raw)
        if (digest(source_rows) != row['configuration_sha256_before']
                or digest(after_rows) != row['configuration_sha256_after']
                or source_rows != after_rows):
            raise Refused('publisher before/after configuration is unproved or changed')
        if normal_paths(row['kind'], source_rows) != sorted({path(value) for value in row['normal_write_paths']}):
            raise Refused('publisher proof filtered or invented a source write path')
        if not isinstance(row.get('normal_write_paths'), list) or not row['normal_write_paths']:
            raise Refused('all configured and current normal write paths are required')
        checked = row.get('resolved_paths')
        if not isinstance(checked, list): raise Refused('physical real-path evidence is required')
        normal = {path(value) for value in row['normal_write_paths']}
        if len(checked) != len(normal) or {path(r['requested_path']) for r in checked} != normal:
            raise Refused('publisher real-path evidence omitted a normal write path')
        if not row.get('mountinfo_capture') or not row.get('mountinfo_capture_sha256'):raise Refused('actual complete NFS client evidence is required')
        if row.get('mountinfo_capture'):
            mounted_raw=read_bounded(row['mountinfo_capture'],4*1024*1024)
            if hashlib.sha256(mounted_raw).hexdigest()!=row['mountinfo_capture_sha256']:raise Refused('actual NFS mount capture changed')
            mounted=json.loads(mounted_raw)
            if mounted.get('complete') is not True or mounted.get('pod_uid')!=row['pod_uid']:raise Refused('NFS client capture PodUID is unproved')
            actual_pods=[p for p in pods if p['metadata'].get('uid')==row['pod_uid']]
            if len(actual_pods)!=1:raise Refused('NFS client PodUID is absent or duplicated')
            actual_containers=[c for c in actual_pods[0]['spec'].get('containers',[]) if c['name']==row['container_name']]
            if len(actual_containers)!=1:raise Refused('NFS client container is absent')
            verify_client_nfs(actual_pods[0],actual_containers[0],mounted['nfs_mounts'],storage)
            local_backings=mounted.get('local_backing_proofs',[])
            if local_backings:
                filename=Path(__file__).with_name('publisher-local-backing.py')
                if hashlib.sha256(filename.read_bytes()).hexdigest()!='6f5cd923ca353229f85fa2b095fb0102ca5e4d8c6c51609f78b8ec75dd5c34e8':raise Refused('local_backing: reviewed verifier pin changed')
                spec=importlib.util.spec_from_file_location('local_backing',filename);local=importlib.util.module_from_spec(spec);spec.loader.exec_module(local)
                if len(local_backings)!=1:raise Refused('local_backing: exact single SAB source proof required')
                backing=local_backings[0]
                try:
                    expected=local.verify(backing['profile'],actual_pods[0],storage,mounted['filesystem_mounts'],backing['node'])
                except local.Refused as error:raise Refused(str(error)) from None
                if expected!=backing:raise Refused('local_backing: complete source proof changed')
                key=(row['pod_uid'],backing['profile']['claim_name'])
                if key in verified_local:raise Refused('local_backing: duplicate verified local profile')
                verified_local[key]={'profile':backing['profile'],'filesystem_mounts':mounted['filesystem_mounts']}
        for resolved in checked:
            path(resolved['resolved_path'])
            if resolved.get('complete') is not True or resolved.get('unknown_or_symlink_escape') is not False:
                raise Refused('publisher physical path resolution is incomplete')
            nfs = resolved.get('physical_nfs')
            if nfs is not None:
                if row.get('mountinfo_capture'):
                    prefix=nfs['server']+':'+nfs['export']
                    matching=[v for v in mounted['nfs_mounts'] if v['source']==prefix or v['source'].startswith(prefix+'/')]
                    expected_address='192.168.40.23' if (nfs['server'],nfs['export'])==(SERVER,EXPORT) else separation.get(('NFS',nfs['server'],nfs['export']),{}).get('server_address')
                    if not matching or any(v['server_address']!=expected_address for v in matching):raise Refused('actual NFS server identity differs')
                if nfs.get('server') == SERVER and nfs.get('export') == EXPORT:
                    if overlaps(path(nfs['relative_path'], False), EBOOKS):
                        raise Refused('publisher normal write path overlaps EBooks')
                else:
                    bound=separation.get(('NFS',nfs.get('server'),nfs.get('export')))
                    if bound is None or nfs.get('server_address')!=bound['server_address'] or resolved.get('storage_separation_sha256')!=bound['separation_sha256']:raise Refused('alternate physical NFS mapping is unproved')
            elif resolved.get('disjoint_volume_verified') is not True:
                raise Refused('publisher physical volume is unknown')
            elif resolved.get('physical_volume',{}).get('kind')=='reviewed_sab_local_xfs':
                if not local_backings:raise Refused('local_backing: actual proof missing')
                try:expected=local.mapping(backing,resolved,mounted['filesystem_mounts'])
                except local.Refused as error:raise Refused(str(error)) from None
                if expected!=resolved['physical_volume']:raise Refused('local_backing: physical path proof changed')
        records[identity] = row
    encountered = set()
    for pod in pods:
        if pod.get('status', {}).get('phase') in ('Succeeded', 'Failed'): continue
        meta = pod['metadata']; spec = pod['spec']
        owners = {row.get('uid') for row in meta.get('ownerReferences', []) if row.get('kind') == 'Job'}
        if owners & owned_job_uids: continue
        for container in spec.get('containers', []) + spec.get('initContainers', []) + spec.get('ephemeralContainers', []):
            identity = (meta['namespace'], meta['name'], container['name'])
            for mount in container.get('volumeMounts', []):
                volume = mount_volume(pod, mount)
                protected_pvc = (meta['namespace'], volume.get('persistentVolumeClaim', {}).get('claimName')) in (
                    ('downloads', 'lazylibrarian'), ('media', 'kavita'))
                if (mount.get('readOnly') is True or volume.get('nfs', {}).get('readOnly') is True
                        or volume.get('persistentVolumeClaim', {}).get('readOnly') is True):
                    continue
                if protected_pvc: raise Refused('unowned writer can reach a fenced LL/Kavita PVC')
                if not possible_ebooks_mount(pod, mount, storage or {}, verified_local,approved_profiles,container['name']): continue
                row = records.get(identity)
                if row is None or row.get('pod_uid') != meta['uid'] or row.get('pod_scope_sha256') != pod_scope(pod):
                    raise Refused('unproved EBooks-capable publisher or changed pod/mount identity')
                encountered.add(identity)
    if set(records) != encountered:
        raise Refused('publisher capture is not the exact set of active overlapping RW mounts')
    return {'publisher_count': len(records), 'capture_started_at': proof['capture_started_at'],
            'captured_at': proof['captured_at'], 'read_only': True}
