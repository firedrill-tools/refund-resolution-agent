"""Controlled HTTP through the normal Pydantic AI / Anthropic SDK path."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path

import httpx2
import pytest

from refund_resolution.agent import propose, propose_async
from refund_resolution.provider import reported_usage
from refund_resolution.service import resolve
from refund_resolution.workspace import Workspace

MODEL = 'anthropic:claude-sonnet-4-6'
BODY = {'id': 'msg-synthetic', 'type': 'message', 'role': 'assistant', 'model': 'claude-sonnet-4-6-synthetic',
    'content': [{'type': 'tool_use', 'id': 'proposal-1', 'name': 'refund_proposal', 'input': {
        'action': 'refund', 'amount_cents': 4999, 'reason': 'The damaged part has an exact refund request.'}}],
    'stop_reason': 'tool_use', 'stop_sequence': None,
    'usage': {'input_tokens': 10, 'output_tokens': 2, 'cache_creation_input_tokens': 3, 'cache_read_input_tokens': 4},
    'unknown_marker': {'original': True}}


@pytest.fixture(autouse=True)
def synthetic_key(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'synthetic-not-a-provider-key')
    monkeypatch.setenv('PYDANTIC_AI_NO_BANNER', '1')


def workspace(tmp_path):
    path = tmp_path/'state.json'
    path.write_bytes((Path(__file__).parents[1]/'fixtures/starter.json').read_bytes())
    return Workspace(path)


class Script:
    def __init__(self, bodies):
        self.bodies = iter(bodies); self.requests = []; self.closed = False
        owner = self
        class Transport(httpx2.AsyncBaseTransport):
            async def handle_async_request(self, request):
                owner.requests.append(json.loads(request.content))
                item = next(owner.bodies)
                if isinstance(item, BaseException):
                    raise item
                if isinstance(item, int):
                    return httpx2.Response(item, json={'error': {'type': 'api_error', 'message': 'Synthetic unavailable'}, 'usage': {'input_tokens': 1, 'output_tokens': 2, 'cache_creation_input_tokens': 0, 'cache_read_input_tokens': 0}}, headers={'request-id':'req-failed'})
                return httpx2.Response(200, json=item, headers={'request-id': 'req-synthetic'})
            async def aclose(self):
                owner.closed = True
        self.transport = Transport()


def test_native_proposal_preserves_raw_usage_model_and_real_limits(tmp_path):
    script = Script([BODY]); store = workspace(tmp_path)
    output, observation = propose('t-100', store, MODEL, transport=script.transport)
    assert output.amount_cents == 4999 and store.data['refunds'] == []
    assert script.closed and len(script.requests) == 1
    request = script.requests[0]
    assert request['model'] == 'claude-sonnet-4-6' and request['max_tokens'] == 4096
    assert request.get('stream', False) is False
    assert {t['name'] for t in request['tools']} == {'read_ticket','read_order','read_payment','refund_proposal'}
    assert observation['input_tokens'] == 17 and observation['output_tokens'] == 2 and observation['total_tokens'] == 19
    assert observation['response_model'] == BODY['model'] and observation['cost_usd'] is None
    raw = observation['provider_observations'][0]
    assert raw['body'] == BODY and raw['request_id'] == 'req-synthetic' and raw['finish_reason'] == 'tool_use'
    assert len(raw['input_sha256']) == len(raw['body_sha256']) == 64
    assert 'synthetic-not-a-provider-key' not in json.dumps(observation)


@pytest.mark.parametrize('finish', ['max_tokens','end_turn','pause_turn','refusal',None])
def test_native_unfinished_or_inconsistent_proposal_never_pays(tmp_path, finish):
    body = deepcopy(BODY); body['stop_reason'] = finish
    script = Script([body]); store = workspace(tmp_path); before = store.path.read_bytes()
    with pytest.raises(Exception) as error:
        resolve('t-100','approval-100',store,MODEL,transport=script.transport)
    assert len(script.requests) == 1 and script.closed and store.path.read_bytes() == before
    assert error.value.observations[0]['finish_reason'] == finish
    assert error.value.observations[0]['status'] == 'rejected'
    assert error.value.reported_usage['total_tokens'] == 19


@pytest.mark.parametrize('kind', ['missing','partial','zero','invalid'])
def test_native_usage_does_not_infer_sdk_defaults(tmp_path, kind):
    body = deepcopy(BODY)
    if kind == 'missing': body.pop('usage')
    elif kind == 'partial': body['usage'] = {'input_tokens':0,'output_tokens':0}
    elif kind == 'zero': body['usage'] = dict.fromkeys(BODY['usage'], 0)
    else: body['usage'] = {**BODY['usage'],'input_tokens':True}
    script=Script([body])
    try:
        _, observation = propose('t-100',workspace(tmp_path),MODEL,transport=script.transport)
        reported=observation['reported_usage']; raw=observation['provider_observations'][0]
    except Exception as error:
        reported=error.reported_usage; raw=error.observations[0]
    assert raw['body'] == body and script.closed and len(script.requests) == 1
    assert reported['complete'] is (kind == 'zero')
    assert reported['total_tokens'] == (0 if kind == 'zero' else None)


def test_native_http_error_has_one_attempt_and_failed_measurements(tmp_path):
    script=Script([503])
    with pytest.raises(Exception) as error:
        propose('t-100',workspace(tmp_path),MODEL,transport=script.transport)
    assert len(script.requests) == 1 and script.closed
    raw=error.value.observations[0]
    assert raw['http_status'] == 503 and raw['request_id'] == 'req-failed'
    assert error.value.reported_usage['total_tokens'] == 3


def test_native_read_tools_use_real_scoped_feedback(tmp_path):
    reads=deepcopy(BODY); reads['content']=[
        {'type':'tool_use','id':'ticket','name':'read_ticket','input':{'requested_ticket_id':'t-100'}},
        {'type':'tool_use','id':'foreign','name':'read_order','input':{'order_id':'ord-101'}}]
    script=Script([reads,BODY]);store=workspace(tmp_path); actions=[]
    store.observer=lambda *action: actions.append(action)
    output,obs=propose('t-100',store,MODEL,transport=script.transport)
    assert output.amount_cents == 4999 and len(script.requests) == 2
    assert actions[0][2]['id'] == 't-100' and 'error' in actions[1][2]
    text=json.dumps(script.requests[1]['messages'])
    assert 'The package arrived damaged' in text and 'outside the assigned case' in text
    assert 'leo@northstar.example' not in text
    assert obs['reported_usage']['attempts'] == 2


def test_native_call_budget_closes_transport_without_proposal(tmp_path):
    reads=deepcopy(BODY); reads['content']=[{'type':'tool_use','id':'ticket','name':'read_ticket','input':{'requested_ticket_id':'t-100'}}]
    script=Script([reads]*9)
    with pytest.raises(Exception,match='request_limit') as error:
        propose('t-100',workspace(tmp_path),MODEL,transport=script.transport)
    assert len(script.requests) == 8 and script.closed
    assert error.value.reported_usage['attempts'] == 8


@pytest.mark.parametrize('mode',['cancel','deadline'])
def test_native_cancel_and_deadline_close_owned_transport(tmp_path,mode):
    async def exercise():
        entered=asyncio.Event(); cancelled=[];closed=[]
        class Pending(httpx2.AsyncBaseTransport):
            async def handle_async_request(self,request):
                entered.set()
                try: await asyncio.Event().wait()
                finally: cancelled.append(True)
            async def aclose(self): closed.append(True)
        task=asyncio.create_task(propose_async('t-100',workspace(tmp_path),MODEL,transport=Pending(),timeout_seconds=0.04 if mode=='deadline' else 120))
        await entered.wait()
        if mode=='cancel': task.cancel()
        with pytest.raises(asyncio.CancelledError if mode=='cancel' else TimeoutError) as error: await task
        assert cancelled and closed and error.value.observations[0]['status'] == 'failed'
        assert error.value.reported_usage['total_tokens'] is None
    asyncio.run(exercise())


def test_raw_usage_invalid_and_overflow_are_unknown():
    assert reported_usage({'usage':{**BODY['usage'],'input_tokens':2**53}})['complete'] is False


def test_owned_client_closes_when_agent_construction_fails(tmp_path,monkeypatch):
    from refund_resolution import agent
    script=Script([])
    def broken(*_):raise RuntimeError('synthetic startup failure')
    monkeypatch.setattr(agent,'native_model',broken)
    with pytest.raises(RuntimeError,match='startup failure'):
        propose('t-100',workspace(tmp_path),MODEL,transport=script.transport)
    assert script.closed and script.requests==[]


@pytest.mark.parametrize('mode',['invalid-json','oversized'])
def test_native_rejected_body_is_retained_and_cannot_pay(tmp_path,mode):
    raw=b'{synthetic-invalid-json' if mode=='invalid-json' else (json.dumps(BODY)+' '*1_048_576).encode()
    class Transport(httpx2.AsyncBaseTransport):
        async def handle_async_request(self,request):return httpx2.Response(200,content=raw,headers={'content-type':'application/json','request-id':'bad-body'})
    store=workspace(tmp_path);before=store.path.read_bytes()
    with pytest.raises(Exception) as error:
        resolve('t-100','approval-100',store,MODEL,transport=Transport())
    observation=error.value.observations[0]
    assert observation['request_id']=='bad-body' and observation['status']=='rejected'
    assert observation['truncated'] is (mode=='oversized')
    assert observation['body'].startswith('{synthetic') if mode=='invalid-json' else observation['body']==BODY
    assert store.path.read_bytes()==before and error.value.evidence['status']=='failed'
