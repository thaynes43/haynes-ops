#!/usr/bin/env python3
"""Offline native Job/Pod binding and pre-pause baseline absence verifier.

Inputs are full privately captured native objects. This command performs no API,
exec, PG or library operation. Capturing them requires a separate Root runtime GO.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re

import bound_census_collectors as collectors

LABEL = 'issue825.haynesnetwork/phase'
MOUNT_ROOT = '/data/cephfs-hdd'
NFS_SERVER = 'gasha01.haynesnetwork'
NFS_EXPORT = '/hdd-nfs-repl'


class Refused(ValueError):
    pass


def load_json(path):
    if os.stat(path).st_size > 8 * 1024 * 1024:
        raise Refused('native inventory exceeds cap')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise Refused('duplicate native field')
            result[key] = value
        return result
    with open(path, 'rb') as source:
        return json.load(source, object_pairs_hook=unique)


def bind(manifest, job, pod, phase, modules, observed_at, helper, job_uid, pod_uid):
    meta = manifest['metadata']
    jm, pm = job['metadata'], pod['metadata']
    if (not re.fullmatch(r'[0-9a-f]{32}', phase) or meta.get('labels', {}).get(LABEL) != phase
            or not collectors.UUID.fullmatch(job_uid) or not collectors.UUID.fullmatch(pod_uid)
            or jm.get('uid') != job_uid or pm.get('uid') != pod_uid
            or not isinstance(modules, dict) or set(modules) != {'epub_copies.py', 'epub_metadata.py'}
            or any(not isinstance(value, str) or not collectors.SHA.fullmatch(value) for value in modules.values())):
        raise Refused('phase or reviewed module contract differs')
    row = {'ready_manifest': manifest, 'uid': jm.get('uid'), 'namespace': meta['namespace'],
           'name': meta['name'], 'phase_token': phase}
    helper.verify_owned_pod(row, job, pod, pm.get('uid'))
    spec = pod['spec']
    container = spec['containers'][0]
    security = spec.get('securityContext', {})
    csecurity = container.get('securityContext', {})
    volumes = {volume['name']: volume for volume in spec.get('volumes', [])}
    mounts = container.get('volumeMounts', [])
    books = [mount for mount in mounts if mount.get('name') == 'books']
    if (spec.get('nodeName') != 'talosw01' or spec.get('automountServiceAccountToken') is not False
            or security.get('runAsUser') != 1000 or security.get('runAsGroup') != 1000
            or csecurity.get('readOnlyRootFilesystem') is not True
            or csecurity.get('allowPrivilegeEscalation') is not False
            or csecurity.get('capabilities', {}).get('drop') != ['ALL']
            or len(books) != 1 or books[0] != {'name': 'books', 'mountPath': MOUNT_ROOT, 'readOnly': True}
            or volumes.get('books', {}).get('nfs') != {'server': NFS_SERVER, 'path': NFS_EXPORT}
            or set(volumes) != {'books', 'tmp'} or volumes['tmp'].get('emptyDir') != {'sizeLimit': '64Mi'}
            or len(mounts) != 2 or any(mount.get('name') not in volumes for mount in mounts)):
        raise Refused('exact W01 RW-backed NFS and read-only container profile differs')
    status = pod['status']['containerStatuses'][0]
    image_id = status['imageID'].removeprefix('docker-pullable://')
    binding = {'namespace': pm['namespace'], 'pod_name': pm['name'], 'pod_uid': pm['uid'],
               'job_uid': jm['uid'], 'node': spec['nodeName'], 'image': container['image'], 'image_id': image_id,
               'pod_spec_sha256': hashlib.sha256(collectors.canonical(spec)).hexdigest(),
               'restarts': status['restartCount'], 'mount_root': MOUNT_ROOT,
               'nfs_server': NFS_SERVER, 'nfs_export': NFS_EXPORT}
    return {'schema': 1, 'phase_token': phase, 'observed_at': observed_at,
            'module_sha256': modules, 'source_binding': binding}


def baseline_absent(jobs, pods, namespace, name, job_uid, phase):
    """All matching Jobs and Pods must be authoritatively absent before pause."""
    if (jobs.get('kind') != 'JobList' or pods.get('kind') != 'PodList'
            or not isinstance(jobs.get('items'), list) or not isinstance(pods.get('items'), list)
            or not collectors.UUID.fullmatch(job_uid) or not re.fullmatch(r'[0-9a-f]{32}', phase)):
        raise Refused('complete native cleanup inventory required')
    for job in jobs['items']:
        meta = job.get('metadata', {})
        if meta.get('namespace') == namespace and (meta.get('name') == name or meta.get('uid') == job_uid):
            raise Refused('baseline Job still present or name reused')
    for pod in pods['items']:
        meta = pod.get('metadata', {})
        if meta.get('namespace') != namespace:
            continue
        labels = meta.get('labels', {})
        owners = meta.get('ownerReferences', [])
        if (labels.get(LABEL) == phase or labels.get('batch.kubernetes.io/controller-uid') == job_uid
                or any(owner.get('kind') == 'Job' and (owner.get('name') == name or owner.get('uid') == job_uid)
                       for owner in owners)):
            raise Refused('baseline owned or same-name Pod still present')
    return {'schema': 1, 'namespace': namespace, 'job_name': name, 'job_uid': job_uid,
            'phase_token': phase, 'job_and_all_owned_pods_absent': True, 'operating_authorization': False}


def private(path, value):
    raw = collectors.canonical(value) + b'\n'
    if len(raw) > 65536:
        raise Refused('native contract exceeds cap')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as output:
        output.write(raw)
        output.flush()
        os.fsync(output.fileno())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=('bind', 'absence'), default='bind')
    parser.add_argument('--manifest')
    parser.add_argument('--job')
    parser.add_argument('--pod')
    parser.add_argument('--jobs')
    parser.add_argument('--pods')
    parser.add_argument('--namespace', default='frontend')
    parser.add_argument('--job-name')
    parser.add_argument('--phase', required=True)
    parser.add_argument('--job-uid', required=True)
    parser.add_argument('--pod-uid')
    parser.add_argument('--module-hashes')
    parser.add_argument('--observed-at')
    parser.add_argument('--helper')
    parser.add_argument('--helper-sha256')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if args.mode == 'absence':
        if not all((args.jobs, args.pods, args.job_name)):
            parser.error('absence needs full --jobs/--pods and --job-name')
        private(args.output, baseline_absent(load_json(args.jobs), load_json(args.pods), args.namespace,
                                             args.job_name, args.job_uid, args.phase))
        return
    if not all((args.manifest, args.job, args.pod, args.pod_uid, args.module_hashes, args.observed_at,
                args.helper, args.helper_sha256)):
        parser.error('bind needs the complete native Job/Pod/module and helper inputs')
    if hashlib.sha256(Path(args.helper).read_bytes()).hexdigest() != args.helper_sha256:
        raise Refused('approved helper pin differs')
    spec = importlib.util.spec_from_file_location('baseline_checkpoint', args.helper)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    value = bind(load_json(args.manifest), load_json(args.job), load_json(args.pod), args.phase,
                 load_json(args.module_hashes), args.observed_at, helper, args.job_uid, args.pod_uid)
    private(args.output, value)


if __name__ == '__main__':
    main()
