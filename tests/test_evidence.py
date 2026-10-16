import asyncio
from copy import deepcopy
import json
from pathlib import Path

import pytest

from refund_resolution import evidence as evidence_module
from refund_resolution.agent import Proposal
from refund_resolution.service import resolve, resolve_async
from refund_resolution.workspace import Workspace
from test_native import Script, BODY, MODEL, workspace


def test_native_receipt_tracks_private_hashes_reads_and_payment(tmp_path,monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY','synthetic-not-real')
    reads=deepcopy(BODY);reads['content']=[{'type':'tool_use','id':'read-1','name':'read_ticket','input':{'requested_ticket_id':'t-100'}}]
    script=Script([reads,BODY]);store=workspace(tmp_path);before=store.path.read_bytes()
    result=resolve('t-100','approval-100',store,MODEL,transport=script.transport)
    receipt=Path(result['receipt_path']); data=json.loads(receipt.read_text())
    import hashlib
    assert data['status']=='completed' and data['result']['status']=='refunded' and data['schema_version']==1
    assert data['initial_state']['sha256']==hashlib.sha256(before).hexdigest()
    assert data['final_state']['sha256']==hashlib.sha256(store.path.read_bytes()).hexdigest()
    assert data['reported_usage']['total_tokens']==38
    assert [a['name'] for a in data['actions']]==['read_ticket','issue_refund','close_ticket']
    assert data['actions'][0]['tool_call_id']=='read-1' and data['actions'][0]['model_attempt']==1
    assert data['actions'][1]['state']['counts']['refunds']==1 and data['actions'][1]['result']['currency']=='USD'
    assert receipt.stat().st_mode&0o777==0o600 and receipt.parent.stat().st_mode&0o777==0o700
    assert 'synthetic-not-real' not in receipt.read_text()
    original=result['refund']['proposal_observation']
    replay=resolve('t-100','approval-100',Workspace(store.path),MODEL,lambda *_:pytest.fail('no model on replay'))
    replay_data=json.loads(Path(replay['receipt_path']).read_text())
    assert replay['status']=='already_refunded' and replay['model_usage'] is None
    assert replay['refund']['proposal_observation']==original
    assert replay_data['provider_observations']==[] and replay_data['reported_usage']['total_tokens'] is None
    assert replay_data['initial_state']['sha256']==replay_data['final_state']['sha256']


def test_payment_survives_ticket_publication_failure_and_recovery_rechecks_original_intent(tmp_path,monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY','synthetic-not-real')
    store=workspace(tmp_path);script=Script([BODY])
    def fail(*_): raise OSError('synthetic ticket unavailable')
    store.close_ticket=fail
    with pytest.raises(OSError) as error: resolve('t-100','approval-100',store,MODEL,transport=script.transport)
    data=json.loads(Path(error.value.receipt_path).read_text())
    assert data['status']=='failed' and data['final_state']['counts']['refunds']==1 and data['reported_usage']['total_tokens']==19
    saved=Workspace(store.path)
    assert saved.data['tickets'][0]['status']=='open' and saved.data['payments'][0]['refunded_cents']==4999
    saved.data['tickets'][0]['message']='Correction: keep the order open; do not close my case.';saved.save()
    review=resolve('t-100','approval-100',saved,MODEL,lambda *_:pytest.fail('no model for recorded payment'))
    assert review['status']=='review' and Workspace(store.path).data['tickets'][0]['status']=='open'
    assert len(saved.data['refunds'])==1


@pytest.mark.parametrize('persistent',[False,True])
def test_receipt_failure_after_payment_stops_ticket_close_and_retains_actual_effect(tmp_path,monkeypatch,persistent):
    store=workspace(tmp_path);publish=evidence_module.publish;failed=[]
    def broken(path,value):
        if value['actions'] and value['actions'][-1]['name']=='issue_refund' and (persistent or not failed):
            failed.append(True);raise OSError('synthetic recording failure')
        return publish(path,value)
    monkeypatch.setattr(evidence_module,'publish',broken)
    with pytest.raises(OSError) as error:
        resolve('t-100','approval-100',store,MODEL,lambda *_:(Proposal(action='refund',amount_cents=4999,reason='Damaged part requested'),{}))
    saved=Workspace(store.path)
    assert len(saved.data['refunds'])==1 and saved.data['tickets'][0]['status']=='open'
    assert error.value.evidence['status']=='failed' and error.value.evidence['final_state']['counts']['refunds']==1
    if persistent:
        assert 'recording failure' in error.value.evidence['evidence_error']
        assert json.loads(Path(error.value.receipt_path).read_text())['status']=='running'
    else:
        assert json.loads(Path(error.value.receipt_path).read_text())['status']=='failed'


def test_same_canonical_workspace_cannot_run_concurrently_and_claim_is_released(tmp_path,monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY','synthetic-not-real')
    async def exercise():
        store=workspace(tmp_path);entered=asyncio.Event();blocked=asyncio.Event()
        async def pending(*_): entered.set();await blocked.wait()
        first=asyncio.create_task(resolve_async('t-100','approval-100',store,MODEL,pending))
        await entered.wait();alias=tmp_path/'alias.json';alias.symlink_to(store.path)
        with pytest.raises(RuntimeError,match='active refund'):
            await resolve_async('t-100','approval-100',Workspace(alias),MODEL,pending)
        first.cancel()
        with pytest.raises(asyncio.CancelledError) as error: await first
        assert error.value.evidence['status']=='failed'
        result=await resolve_async('t-100',None,Workspace(store.path),MODEL,lambda *_:(Proposal(action='review',amount_cents=0,reason='Need customer clarification'),{}))
        assert result['status']=='review' and store.observer is None
    asyncio.run(exercise())


def test_duplicate_or_orphan_imports_fail_before_model_and_id_gaps_do_not_collide(tmp_path):
    store=workspace(tmp_path);before=store.path.read_bytes()
    store.data['orders'].append(deepcopy(store.data['orders'][0]))
    with pytest.raises(ValueError,match='duplicate'): store.save()
    assert store.path.read_bytes()==before
    store.reload();store.data['tickets'][0]['order_id']='missing'
    with pytest.raises(ValueError,match='orphan'):store.save()
    store.reload();store.issue_refund('t-100','ord-100',4999,'approval-100')
    store.data['refunds'][0]['id']='refund-007';store.save()
    second=store.issue_refund('t-101','ord-101',500,'approval-100')
    assert second['id']=='refund-008' and len({r['id'] for r in Workspace(store.path).data['refunds']})==2


def test_receipt_failure_at_native_response_cannot_release_proposal_or_payment(tmp_path,monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY','synthetic-not-real')
    store=workspace(tmp_path);before=store.path.read_bytes();publish=evidence_module.publish
    def broken(path,value):
        if value['provider_observations'] and value['provider_observations'][-1]['body'] and value['status']=='running':raise OSError('synthetic response receipt failure')
        return publish(path,value)
    monkeypatch.setattr(evidence_module,'publish',broken);script=Script([BODY])
    with pytest.raises(Exception) as error:resolve('t-100','approval-100',store,MODEL,transport=script.transport)
    assert store.path.read_bytes()==before and script.closed and len(script.requests)==1
    assert error.value.evidence['status']=='failed' and error.value.evidence['provider_observations'][0]['body']==BODY


def test_alias_run_publishes_to_canonical_state_and_preserves_alias(tmp_path):
    store=workspace(tmp_path);alias=tmp_path/'alias.json';alias.symlink_to(store.path)
    result=resolve('t-100','approval-100',Workspace(alias),MODEL,lambda *_:(Proposal(action='refund',amount_cents=4999,reason='Damaged accessory'),{}))
    assert result['status']=='refunded' and alias.is_symlink() and len(Workspace(store.path).data['refunds'])==1
