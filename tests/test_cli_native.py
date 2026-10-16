"""Actual CLI and native HTTP over an owned loopback provider, no live key."""
from contextlib import contextmanager
from copy import deepcopy
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from threading import Event,Thread

import pytest

from refund_resolution.datasets import initialize
from refund_resolution.workspace import Workspace
from test_native import BODY


@contextmanager
def provider(mode):
    requests=[];entered=Event();release=Event()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def do_POST(self):
            request=json.loads(self.rfile.read(int(self.headers['Content-Length'])));requests.append(request);entered.set()
            if mode=='cancel':release.wait(12);return
            status=503 if mode=='failure' else 200
            if mode=='failure':body={'error':{'type':'api_error','message':'Synthetic provider unavailable'}}
            else:
                body=deepcopy(BODY)
                if len(requests)==1:
                    body['content']=[{'type':'tool_use','id':name,'name':name,'input':args} for name,args in [
                        ('read_ticket',{'requested_ticket_id':'t-104'}),('read_order',{'order_id':'ord-104'}),('read_payment',{'order_id':'ord-104'})]]
                else:body['content'][0]['input']['amount_cents']=2200
            raw=json.dumps(body).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.send_header('request-id','loopback-synthetic');self.end_headers();self.wfile.write(raw)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield f'http://127.0.0.1:{server.server_port}',requests,entered
    finally:release.set();server.shutdown();server.server_close();thread.join(timeout=3)


def launch(tmp_path,url,file):
    # Test-only composition supplies the documented client endpoint seam. Normal
    # CLI has no canned-mode/preload option and still constructs the real SDK.
    program='''
import sys
from refund_resolution import agent
from refund_resolution.cli import main
original=agent.create_client
agent.create_client=lambda key,evidence,**options: original(key,evidence,**{**options,'base_url':sys.argv[1]})
raise SystemExit(main(sys.argv[2:]))
'''
    env={**os.environ,'ANTHROPIC_API_KEY':'synthetic-not-real','PYDANTIC_AI_NO_BANNER':'1'}
    return subprocess.Popen([sys.executable,'-c',program,url,'t-104','--approval-id','approval-104','--workspace',str(file)],cwd=tmp_path,env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)


@pytest.mark.parametrize('mode',['success','failure','cancel'])
def test_actual_cli_native_wire_completion_error_and_signal(tmp_path,mode):
    file=tmp_path/'busy.json';initialize('busy',file);before=Workspace(file).data
    with provider(mode) as (url,requests,entered):
        process=launch(tmp_path,url,file)
        try:
            assert entered.wait(8),'CLI did not reach owned provider'
            if mode=='cancel':process.send_signal(signal.SIGTERM)
            stdout,stderr=process.communicate(timeout=10)
        finally:
            if process.poll() is None:process.kill();process.communicate()
    receipts=list((tmp_path/'runs').glob('*/receipt.json'));assert len(receipts)==1
    data=json.loads(receipts[0].read_text());after=Workspace(file).data
    if mode=='success':
        assert process.returncode==0,stderr
        result=json.loads(stdout);assert result['status']=='refunded' and result['refund']['amount_cents']==2200
        assert len(requests)==2 and data['reported_usage']['total_tokens']==38 and data['status']=='completed'
        assert {a['tool_call_id'] for a in data['actions'][:3]}=={'read_ticket','read_order','read_payment'}
        prompt=json.dumps(requests[1]['messages']);assert 'CAD' in prompt and 'mina@maple.example' in prompt and 'leo@northstar.example' not in prompt
        assert after['refunds'][:-1]==before['refunds'] and after['refunds'][-1]['id']=='refund-015'
    else:
        assert process.returncode==(130 if mode=='cancel' else 1),stderr
        failure=json.loads(stderr.splitlines()[-1]);assert failure['receipt_path']==str(receipts[0])
        assert after==before and len(requests)==1 and data['status']=='failed'
        assert data['reported_usage']['total_tokens'] is None
        if mode=='failure':assert data['provider_observations'][0]['http_status']==503
        else:assert data['provider_observations'][0]['status']=='failed'
