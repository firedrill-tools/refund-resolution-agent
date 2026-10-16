from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from refund_resolution.datasets import data_root, initialize, list_cases
from refund_resolution.agent import Proposal
from refund_resolution.service import resolve
from refund_resolution.workspace import Workspace


def test_all_cases_are_private_independent_and_hash_bound(tmp_path):
    manifest=list_cases()
    assert manifest['version']==2 and len(manifest['cases'])==8
    assert {c['profile'] for c in manifest['cases']}=={'starter','busy','ambiguous','boundary'}
    for case in manifest['cases']:
        file=tmp_path/f'{case["id"]}.json'
        result=initialize(case['id'],file)
        assert hashlib.sha256(file.read_bytes()).hexdigest()==result['sha256']==case['sha256']
        assert file.stat().st_mode&0o777==0o600 and Workspace(file).data['dataset']['case_id']==case['id']
        before=file.read_bytes()
        with pytest.raises(FileExistsError):initialize(case['id'],file)
        assert file.read_bytes()==before


def test_busy_prior_work_is_coherent_and_new_refund_preserves_it(tmp_path):
    file=tmp_path/'busy.json';initialize('busy',file);store=Workspace(file);before=deepcopy(store.data)
    assert len(before['orders'])==20 and len(before['tickets'])==21 and len(before['refunds'])==7
    totals={p['id']:sum(r['amount_cents'] for r in before['refunds'] if r['payment_id']==p['id']) for p in before['payments']}
    assert all(p['refunded_cents']==totals[p['id']] for p in before['payments'])
    assert all(r['proposal_observation'] is None for r in before['refunds'])
    result=resolve('t-104','approval-104',store,'anthropic:test',lambda *_:(Proposal(action='refund',amount_cents=2200,reason='Damaged CAD accessory'),{}))
    assert result['refund']['payment_id']=='pay-104' and result['refund']['currency']=='CAD' and result['refund']['id']=='refund-015'
    assert store.data['refunds'][:-1]==before['refunds']
    for kind,key in [('orders',None),('payments','pay-104'),('tickets','t-104'),('approvals',None)]:
        assert [r for r in store.data[kind] if r['id']!=key]==[r for r in before[kind] if r['id']!=key]


@pytest.mark.parametrize('case_id',['partial-recovery','already-completed'])
def test_seeded_human_recovery_makes_no_model_call(tmp_path,case_id):
    file=tmp_path/'state.json';case=initialize(case_id,file);store=Workspace(file)
    result=resolve(case['ticket_id'],case['approval_id'],store,'anthropic:test',lambda *_:pytest.fail('seeded payment must not pay again'))
    assert result['status']=='already_refunded' and result['model_usage'] is None and len(store.data['refunds'])==1
    assert store.data['payments'][0]['refunded_cents']==4999 and store.data['tickets'][0]['status']=='refunded'


@pytest.mark.parametrize('mode',['hash','identity','path','duplicate'])
def test_dataset_drift_is_rejected_before_initialization(tmp_path,mode):
    root=tmp_path/'data';shutil.copytree(data_root(),root);file=root/'manifest.json';data=json.loads(file.read_text())
    if mode=='hash':
        target=root/data['cases'][0]['file'];target.write_bytes(target.read_bytes()+b'\n')
    elif mode=='identity':
        target=root/data['cases'][0]['file'];state=json.loads(target.read_text());state['dataset']['case_id']='other';raw=json.dumps(state).encode();target.write_bytes(raw);data['cases'][0]['sha256']=hashlib.sha256(raw).hexdigest()
    elif mode=='path':data['cases'][0]['file']='../escape.json'
    else:data['cases'][1]['id']=data['cases'][0]['id']
    file.write_text(json.dumps(data));destination=tmp_path/'state.json'
    with pytest.raises(ValueError):initialize('starter',destination,root)
    assert not destination.exists()


def test_actual_cli_lists_initializes_and_recovers_without_a_key(tmp_path):
    env={**os.environ};env.pop('ANTHROPIC_API_KEY',None)
    command=[sys.executable,'-m','refund_resolution.cli']
    def run(*args):return subprocess.run([*command,*args],cwd=tmp_path,env=env,text=True,capture_output=True,timeout=15)
    listed=run('--list-cases');assert listed.returncode==0 and len(json.loads(listed.stdout)['cases'])==8
    file=tmp_path/'state.json';selected=run('--init-case','partial-recovery','--workspace',str(file));assert selected.returncode==0
    result=run('t-100','--approval-id','approval-100','--workspace',str(file))
    assert result.returncode==0 and json.loads(result.stdout)['status']=='already_refunded'
    before=file.read_bytes();assert run('--init-case','starter','--workspace',str(file)).returncode==1 and file.read_bytes()==before
    fresh=tmp_path/'new.json';assert run('--init-case','starter','--workspace',str(fresh)).returncode==0
    failure=run('t-100','--workspace',str(fresh));assert failure.returncode==1
    receipt=json.loads(Path(json.loads(failure.stderr)['receipt_path']).read_text())
    assert receipt['status']=='failed' and receipt['provider_observations']==[]
