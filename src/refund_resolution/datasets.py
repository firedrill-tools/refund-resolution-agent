"""Hash-bound bundled snapshots, kept outside normal target context."""
import hashlib
import json
import os
from pathlib import Path

from .state import validate_state


def data_root():
    bundled=Path(__file__).parent/'sample_data'
    return bundled if bundled.exists() else Path(__file__).parents[2]/'data'


def load_manifest(root=None):
    root=Path(root) if root is not None else data_root()
    raw=(root/'manifest.json').read_bytes();manifest=json.loads(raw)
    if (manifest.get('id'),manifest.get('version'),manifest.get('synthetic'))!=('synthetic-refund-cases',2,True):raise ValueError('unsupported manifest identity')
    cases=manifest.get('cases')
    if not isinstance(cases,list) or not cases:raise ValueError('manifest has no cases')
    ids=set();files=set()
    for case in cases:
        if not isinstance(case,dict) or not isinstance(case.get('id'),str) or not case['id'] or case['id'] in ids:raise ValueError('duplicate/invalid case identity')
        ids.add(case['id']);file=case.get('file')
        if not isinstance(file,str) or Path(file).is_absolute() or '..' in Path(file).parts or file in files:raise ValueError('invalid case path')
        files.add(file)
        if case.get('profile') not in {'starter','busy','ambiguous','boundary'}:raise ValueError('invalid profile')
        if not isinstance(case.get('ticket_id'),str) or not isinstance(case.get('brief'),str):raise ValueError('invalid case input')
        if case.get('approval_id') is not None and not isinstance(case['approval_id'],str):raise ValueError('invalid approval input')
        target=(root/file).resolve(strict=True)
        if not target.is_relative_to(root.resolve(strict=True)):raise ValueError('case path leaves dataset')
        content=target.read_bytes()
        if hashlib.sha256(content).hexdigest()!=case.get('sha256'):raise ValueError('case hash differs')
        state=validate_state(json.loads(content));identity=state.get('dataset',{})
        if identity!={'id':manifest['id'],'version':2,'profile':case['profile'],'case_id':case['id']} or state.get('synthetic') is not True:raise ValueError('case identity differs')
        if not any(t['id']==case['ticket_id'] for t in state['tickets']):raise ValueError('case ticket missing')
        if case.get('approval_id') and not any(a['id']==case['approval_id'] for a in state['approvals']):raise ValueError('case approval missing')
    return manifest,hashlib.sha256(raw).hexdigest(),root


def list_cases(root=None):
    manifest,sha,_=load_manifest(root)
    return {**manifest,'manifest_sha256':sha}


def initialize(case_id,path,root=None):
    manifest,manifest_sha,root=load_manifest(root)
    case=next((c for c in manifest['cases'] if c['id']==case_id),None)
    if not case:raise ValueError('unknown case')
    raw=(root/case['file']).read_bytes()
    # A second hash binds the exact bytes copied even if an author replaces a
    # file between listing and copying; no mutable checked file is reopened later.
    if hashlib.sha256(raw).hexdigest()!=case['sha256']:raise ValueError('case changed before initialization')
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    descriptor=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(descriptor,'wb') as file:file.write(raw);file.flush();os.fsync(file.fileno())
    except BaseException:
        path.unlink(missing_ok=True);raise
    return {'workspace':str(path.resolve()),'dataset':manifest['id'],'dataset_version':2,'case_id':case_id,
        'sha256':case['sha256'],'manifest_sha256':manifest_sha,'ticket_id':case['ticket_id'],'approval_id':case['approval_id']}
