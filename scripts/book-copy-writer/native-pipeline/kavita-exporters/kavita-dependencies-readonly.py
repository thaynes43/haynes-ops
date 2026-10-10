#!/usr/bin/env python3
"""Export/compare complete Kavita user dependencies from copied read-only DBs; never service writes."""
import argparse,datetime as dt,hashlib,json,pathlib,sqlite3
import os
import stat
from pathlib import Path


def write_private_text(path, text):
    """Publish one fresh private report; never repair or overwrite prior evidence."""
    path = Path(path).absolute()
    if not path.name or '..' in path.parts:
        raise ValueError('fresh output path without parent traversal required')

    def open_parent(create):
        directory = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for component in path.parts[1:-1]:
                try:
                    child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                    dir_fd=directory)
                except FileNotFoundError:
                    if not create:
                        raise
                    os.mkdir(component, 0o700, dir_fd=directory)
                    child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                    dir_fd=directory)
                os.close(directory)
                directory = child
            return directory
        except BaseException:
            os.close(directory)
            raise

    directory = open_parent(True)
    try:
        parent = os.fstat(directory)
        if parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) not in (0o700, 0o2700):
            raise ValueError('output parent must be owned and mode 0700')
        fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        with os.fdopen(fd, 'wb') as output:
            before = os.fstat(output.fileno())
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
                    or stat.S_IMODE(before.st_mode) != 0o600 or before.st_nlink != 1):
                raise ValueError('fresh output must be owned single-link mode 0600')
            raw = text.encode('utf-8')
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
            after = os.fstat(output.fileno())
            current = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
            fields = ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid', 'st_nlink')
            if (after.st_size != len(raw) or any(getattr(before, k) != getattr(after, k)
                    or getattr(after, k) != getattr(current, k) for k in fields)
                    or any(getattr(after, k) != getattr(current, k)
                           for k in ('st_size', 'st_mtime_ns', 'st_ctime_ns'))):
                raise ValueError('private output identity changed while publishing')
        check = open_parent(False)
        try:
            current_parent = os.fstat(check)
            if any(getattr(parent, k) != getattr(current_parent, k)
                   for k in ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid')):
                raise ValueError('private output parent changed while publishing')
        finally:
            os.close(check)
        os.fsync(directory)
    finally:
        os.close(directory)

DEPENDENCIES=['AppUserProgresses','AppUserBookmark','AppUserAnnotation','AppUserReadingSession',
 'AppUserReadingSessionActivityData','AppUserReadingHistory','AppUserTableOfContent','ReadingListItem',
 'ReadingListRemapRule','AppUserWantToRead','AppUserCollectionSeries','AppUserRating','AppUserChapterRating',
 'AppUserOnDeckRemoval','AppUserReadingProfiles','ScrobbleEvent','ScrobbleHold','ScrobbleError','SeriesRelation','SeriesBlacklist']
PARENTS=['ReadingList','AppUserCollection','ReadingListTag','ReadingListReadingListTag','CollectionTag','CollectionTagSeriesMetadata']
DIAGNOSTICS=['MediaError']
TABLES=DEPENDENCIES+PARENTS+DIAGNOSTICS
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
def instant(s):return dt.datetime.fromisoformat(s.replace('Z','+00:00'))
def read(path):return json.loads(pathlib.Path(path).read_text())
def dump(path,d):write_private_text(path, json.dumps(d,indent=2,ensure_ascii=False)+'\n')
def key(row):return row.get('Id') if 'Id' in row else digest(row)
def export(a):
 dbpath=a.db.resolve();copy=read(dbpath.parent/'copy-proof.json')
 assert copy['readOnlySource'] and copy['before']==copy['after'],'stable_source_copy_required'
 stamped=bool(copy.get('captureStartedAt'))
 assert stamped or a.allow_unstamped_exploration,'capture_started_at_required'
 if stamped:assert instant(copy['captureStartedAt'])<=instant(copy['capturedAt']),'capture_start_after_completion'
 c=sqlite3.connect('file:'+str(dbpath)+'?mode=ro',uri=True);c.execute('PRAGMA query_only=ON');c.execute('BEGIN');c.row_factory=sqlite3.Row
 schema={r['name'] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
 assert set(TABLES)<=schema,'required_dependency_table_absent'
 unknown=[]
 for t in sorted(schema):
  if t in TABLES or not t.startswith(('AppUser','ReadingList','Collection','Scrobble','SeriesRelation','SeriesBlacklist')):continue
  cols={r['name'] for r in c.execute('PRAGMA table_info("'+t.replace('"','""')+'")')}
  fks=list(c.execute('PRAGMA foreign_key_list("'+t.replace('"','""')+'")'))
  if cols&{'ChapterId','VolumeId','SeriesId','TargetSeriesId','ChapterIds','SeriesIds','ItemsId','SeriesMetadatasId'} or any(r['table'] in {'Series','Chapter','Volume','SeriesMetadata'} for r in fks):unknown.append(t)
 tables={}
 for t in TABLES:
  rows=[dict(r) for r in c.execute('SELECT * FROM "'+t+'" LIMIT 100001')];assert len(rows)<=100000,'dependency_row_bound'
  tables[t]=sorted(rows,key=lambda r:json.dumps(r,sort_keys=True,separators=(',',':')))
 files=[dict(r) for r in c.execute('SELECT f.Id fileId,f.FilePath,c.Id chapterId,v.Id volumeId,s.Id seriesId,s.LibraryId libraryId,s.Name seriesName FROM MangaFile f JOIN Chapter c ON c.Id=f.ChapterId JOIN Volume v ON v.Id=c.VolumeId JOIN Series s ON s.Id=v.SeriesId ORDER BY f.Id')]
 assert len(files)<=100000,'file_map_bound'
 ids={name:{r['Id'] for r in c.execute('SELECT Id FROM '+name)} for name in ['Chapter','Volume','Series']}
 meta={r['Id']:r['SeriesId'] for r in c.execute('SELECT Id,SeriesId FROM SeriesMetadata')}
 c.rollback();c.close()
 indexes={kind:{} for kind in ['Chapter','Volume','Series','Library']}
 for f in files:
  for kind,col in [('Chapter','chapterId'),('Volume','volumeId'),('Series','seriesId'),('Library','libraryId')]:indexes[kind].setdefault(f[col],[]).append(f)
 joined=[];unresolved=[]
 def link(table,row,field,kind,value,context=None):
  if value is None or value==0:return
  assert isinstance(value,int) and not isinstance(value,bool) and value>0,'invalid_book_reference_id'
  matches=indexes[kind].get(value,[])
  joined.append({'table':table,'rowKey':key(row),'field':field,'kind':kind,'id':value,'context':context,'files':matches})
  if not matches:unresolved.append({'table':table,'rowKey':key(row),'field':field,'kind':kind,'id':value,'idExists':value in ids.get(kind,set()),'qualification':'No current file association; never infer a work from title/path'})
 for table,rows in tables.items():
  for row in rows:
   for field,kind in [('ChapterId','Chapter'),('VolumeId','Volume'),('SeriesId','Series'),('TargetSeriesId','Series')]:
    if field in row:link(table,row,field,kind,row[field])
   if table=='AppUserCollectionSeries':link(table,row,'ItemsId','Series',row['ItemsId'],{'CollectionsId':row['CollectionsId']})
   if table=='CollectionTagSeriesMetadata':
    sid=meta.get(row['SeriesMetadatasId']);assert sid is not None,'collection_series_metadata_reference_unresolved'
    link(table,row,'SeriesMetadatasId','Series',sid,{'CollectionsId':row['CollectionTagsId'],'SeriesMetadataId':row['SeriesMetadatasId']})
   if table=='AppUserReadingProfiles':
    for field,kind in [('SeriesIds','Series'),('LibraryIds','Library')]:
     values=json.loads(row[field]);assert isinstance(values,list),'profile_id_list_shape'
     for v in values:link(table,row,field,kind,v)
   if table=='AppUserReadingHistory':
    data=json.loads(row['Data']);assert isinstance(data,dict),'history_data_shape'
    for field,kind in [('SeriesIds','Series'),('ChapterIds','Chapter')]:
     values=data.get(field);assert isinstance(values,list),'history_id_list_shape'
     for v in values:link(table,row,'Data.'+field,kind,v)
    activities=data.get('Activities');assert isinstance(activities,list),'history_activities_shape'
    for i,activity in enumerate(activities):
     assert isinstance(activity,dict),'history_activity_shape'
     for field,kind in [('ChapterId','Chapter'),('VolumeId','Volume'),('SeriesId','Series')]:link(table,row,'Data.Activities.'+str(i)+'.'+field,kind,activity.get(field),{'activityIndex':i,'qualification':'Own history activity identifiers only; no title or creator boundary inference'})
 sessions={r['Id']:r for r in tables['AppUserReadingSession']}
 for activity in tables['AppUserReadingSessionActivityData']:
  assert activity['AppUserReadingSessionId'] in sessions,'orphan_session_activity'
 result={'readOnly':True,'capturedAt':dt.datetime.now(dt.timezone.utc).isoformat(),'sourceDb':str(dbpath),'sourceCopyProof':copy,
  'sourceCopyProofSha256':hashlib.sha256((dbpath.parent/'copy-proof.json').read_bytes()).hexdigest(),'captureStartStamped':stamped,
  'exploratoryOnly':not stamped,'tables':tables,'tableCounts':{t:len(r) for t,r in tables.items()},'tableHashes':{t:digest(r) for t,r in tables.items()},
  'tableClasses':{t:'machine_generated_diagnostic' if t in DIAGNOSTICS else ('parent_metadata_mixed_app_owned_or_curated' if t in PARENTS else 'user_or_work_dependency') for t in TABLES},
  'schemaCompleteness':not unknown,'unknownBookDependencyTables':unknown,'pathJoins':joined,'unresolvedPathReferences':unresolved,
  'currentFileMap':files,'currentFileMapSha256':digest(files),'qualification':'Complete raw user dependency fields and own identifiers joined to current files. Unresolved legacy history references are reported; a separate guarded legacy absence/activity proof is needed before movement. No work equivalence, list repair, or keeper inference.'}
 dump(a.out,result)
 print(json.dumps({'artifact':str(a.out),'readOnly':True,'captureStartStamped':stamped,'tableCounts':result['tableCounts'],'unknownTables':unknown,'unresolvedReferences':len(unresolved)}))
 return 0 if not unknown else 2
def compare(a):
 before,after=read(a.before),read(a.after)
 assert before['readOnly'] and after['readOnly'] and set(before['tables'])==set(after['tables'])==set(TABLES),'complete_dependency_snapshots_required'
 results={}
 for t in TABLES:
  left,right=before['tables'][t],after['tables'][t];old={key(r):r for r in left};new={key(r):r for r in right}
  results[t]={'beforeCount':len(left),'afterCount':len(right),'exactlyEqual':left==right,'changedRows':[{'key':k,'before':old[k],'after':new[k]} for k in old.keys()&new.keys() if old[k]!=new[k]],'removedRows':[old[k] for k in old.keys()-new.keys()],'addedRows':[new[k] for k in new.keys()-old.keys()]}
 same=all(r['exactlyEqual'] for t,r in results.items() if t not in DIAGNOSTICS)
 out={'readOnly':True,'before':str(a.before),'after':str(a.after),'beforeSha256':hashlib.sha256(a.before.read_bytes()).hexdigest(),'afterSha256':hashlib.sha256(a.after.read_bytes()).hexdigest(),'rawDependencyFieldsExactlyEqual':same,'allCapturedFieldsExactlyEqual':all(r['exactlyEqual'] for r in results.values()),'machineGeneratedDiagnosticsChanged':[t for t in DIAGNOSTICS if not results[t]['exactlyEqual']],'pathDependencyMappingsExactlyEqual':before['pathJoins']==after['pathJoins'],'tables':results,'qualification':'Differences are reported exactly; machine-generated diagnostics are classified separately. Owned list reference changes require source work and ownership classification. No automatic relink, row deletion, or repair. Old48 reading-state preservation remains a separate proof.'}
 dump(a.out,out);print(json.dumps({'artifact':str(a.out),'rawDependencyFieldsExactlyEqual':same,'pathDependencyMappingsExactlyEqual':out['pathDependencyMappingsExactlyEqual'],'changedTables':[t for t,r in results.items() if not r['exactlyEqual']],'machineGeneratedDiagnosticsChanged':out['machineGeneratedDiagnosticsChanged']}));return 0 if same else 1
def main():
 p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='mode',required=True)
 e=sub.add_parser('export');e.add_argument('--db',type=pathlib.Path,required=True);e.add_argument('--out',type=pathlib.Path,required=True);e.add_argument('--allow-unstamped-exploration',action='store_true')
 q=sub.add_parser('compare');q.add_argument('before',type=pathlib.Path);q.add_argument('after',type=pathlib.Path);q.add_argument('--out',type=pathlib.Path,required=True)
 a=p.parse_args();raise SystemExit((export if a.mode=='export' else compare)(a))
if __name__=='__main__':main()
