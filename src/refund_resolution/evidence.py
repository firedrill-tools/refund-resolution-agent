"""Application-owned private run receipts; state remains authoritative."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from importlib.metadata import version
from .provider import aggregate_usage, now
from .state import validate_state


def state_snapshot(path):
    result = {'path': str(path), 'sha256': None, 'dataset': None, 'counts': None, 'error': None}
    try:
        raw = path.read_bytes(); result['sha256'] = hashlib.sha256(raw).hexdigest()
        data = validate_state(json.loads(raw))
        result.update(dataset=data.get('dataset'), counts={kind: len(data[kind]) for kind in ('orders','payments','tickets','approvals','refunds')})
    except Exception as error:
        result['error'] = f'{type(error).__name__}: {error}'
    return result


def publish(path, value):
    staged = None
    try:
        with NamedTemporaryFile(mode='w',encoding='utf8',dir=path.parent,prefix='.receipt-',suffix='.tmp',delete=False) as file:
            staged = Path(file.name)
            json.dump(value, file, indent=2, allow_nan=False); file.write('\n'); file.flush(); os.fsync(file.fileno())
        os.replace(staged, path)
    finally:
        if staged is not None: staged.unlink(missing_ok=True)


class Receipt:
    def __init__(self, workspace, ticket_id, approval_id, model, output_root=None):
        root = Path(output_root) if output_root is not None else workspace.path.parent/'runs'
        root.mkdir(parents=True,exist_ok=True,mode=0o700)
        run_id = str(uuid4()); directory = root/run_id; directory.mkdir(mode=0o700)
        self.path = directory/'receipt.json'; self.workspace = workspace
        self._matched_calls = set()
        snapshot = state_snapshot(workspace.path)
        self.data = {'schema_version':1,'run_id':run_id,'receipt_path':str(self.path),'status':'running',
            'started_at':now(),'finished_at':None,'ticket_id':ticket_id,'approval_id':approval_id,'requested_model':model,
            'dependencies':{name:version(name) for name in ('pydantic-ai-slim','anthropic','httpx2')},
            'initial_state':snapshot,'current_state':snapshot,'final_state':None,
            'provider_observations':[],'reported_usage':aggregate_usage([]),'actions':[], 'result':None,'error':None}
        self.save()

    def save(self):
        self.data['current_state'] = state_snapshot(self.workspace.path)
        self.data['reported_usage'] = aggregate_usage(self.data['provider_observations'])
        publish(self.path, self.data)

    def observations(self, value):
        self.data['provider_observations'] = deepcopy(value)
        self.save()

    def action(self, name, args, result):
        observation = self.data['provider_observations'][-1] if self.data['provider_observations'] else None
        tool_id = None
        if observation and name.startswith('read_') and isinstance(observation.get('body'),dict):
            for call in observation['body'].get('content',[]):
                if isinstance(call,dict) and call.get('type')=='tool_use' and call.get('name')==name and call.get('input')==args and call.get('id') not in self._matched_calls:
                    tool_id=call.get('id'); self._matched_calls.add(tool_id); break
        self.data['actions'].append({'tool_call_id':tool_id,'number':len(self.data['actions'])+1,'name':name,'args':args,'result':result,
            'model_attempt':observation['attempt'] if observation and name.startswith('read_') else None,
            'at':now(),'state':state_snapshot(self.workspace.path)})
        self.save()

    def finish(self, result=None, error=None):
        causes=[];current=error
        while current is not None and len(causes)<5:
            causes.append({'name':type(current).__name__,'message':str(current),
                'http_status':getattr(current,'status_code',None)})
            current=current.__cause__

        self.data.update(status='failed' if error is not None else 'completed',finished_at=now(),result=result,
            error={'name':type(error).__name__,'message':str(error),'causes':causes} if error is not None else None,
            final_state=state_snapshot(self.workspace.path))
        self.save()
