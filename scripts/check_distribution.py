"""Fresh locked dependencies plus wheel and sdist outside the source checkout."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory
import tomllib
from zipfile import ZipFile

ROOT=Path(__file__).resolve().parents[1]
UV=os.environ.get('UV') or shutil.which('uv')
if not UV:raise RuntimeError('uv is required for distribution verification')
VERSION=tomllib.loads((ROOT/'pyproject.toml').read_text())['project']['version']
wheel=ROOT/'dist'/f'refund_resolution_agent-{VERSION}-py3-none-any.whl'
sdist=ROOT/'dist'/f'refund_resolution_agent-{VERSION}.tar.gz'
env={**os.environ}
for key in ('ANTHROPIC_API_KEY','VIRTUAL_ENV','PYTHONPATH','PYTHONHOME'):env.pop(key,None)


def run(command,cwd,check=True):
    return subprocess.run(command,cwd=cwd,env=env,check=check,text=True,capture_output=True,timeout=180)


def exercise(python,base):
    cli=[str(python),'-I','-m','refund_resolution.cli']
    run([*cli,'--help'],base)
    manifest=json.loads(run([*cli,'--list-cases'],base).stdout)
    assert len(manifest['cases'])==8 and manifest['version']==2
    for case in manifest['cases']:
        file=base/f'{case["id"]}.json'
        initialized=json.loads(run([*cli,'--init-case',case['id'],'--workspace',str(file)],base).stdout)
        assert initialized['sha256']==case['sha256'] and file.stat().st_mode&0o777==0o600
    recovery=json.loads(run([*cli,'t-100','--approval-id','approval-100','--workspace',str(base/'partial-recovery.json')],base).stdout)
    assert recovery['status']=='already_refunded' and recovery['model_usage'] is None
    failure=run([*cli,'t-100','--workspace',str(base/'starter.json')],base,False)
    assert failure.returncode==1
    receipt=json.loads(Path(json.loads(failure.stderr)['receipt_path']).read_text())
    assert receipt['status']=='failed' and receipt['provider_observations']==[] and receipt['initial_state']['sha256']==receipt['final_state']['sha256']
    program='''
from pathlib import Path
import sys
import refund_resolution
from refund_resolution.agent import Proposal
from refund_resolution.service import resolve
from refund_resolution.workspace import Workspace
assert Path(refund_resolution.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
store=Workspace(Path(sys.argv[1]))
result=resolve('t-104','approval-104',store,'anthropic:package-check',lambda *_:(Proposal(action='refund',amount_cents=2200,reason='Fictional damaged accessory'),{}))
assert result['status']=='refunded' and result['refund']['payment_id']=='pay-104' and result['refund']['id']=='refund-015'
assert len(store.data['orders'])==20 and len(store.data['refunds'])==8
'''
    run([str(python),'-I','-c',program,str(base/'busy.json')],base)


with TemporaryDirectory(prefix='refund-distribution-') as directory:
    temp=Path(directory)
    with ZipFile(wheel) as archive:
        assert 'refund_resolution/sample_data/manifest.json' in archive.namelist()
        assert len([n for n in archive.namelist() if n.startswith('refund_resolution/sample_data/') and n.endswith('.json')])==9
    requirements=temp/'requirements.txt'
    requirements.write_text(run([UV,'export','--locked','--no-dev','--no-emit-project','--format','requirements-txt'],ROOT).stdout)
    wheel_env=temp/'wheel-env';run([UV,'venv','--python',sys.executable,str(wheel_env)],temp)
    wheel_python=wheel_env/'bin/python'
    run([UV,'pip','sync','--python',str(wheel_python),str(requirements)],temp)
    run([UV,'pip','install','--python',str(wheel_python),'--no-deps',str(wheel)],temp)
    wheel_case=temp/'wheel-case';wheel_case.mkdir();exercise(wheel_python,wheel_case)
    source_root=temp/'source';source_root.mkdir()
    with tarfile.open(sdist) as archive:archive.extractall(source_root,filter='data')
    source,=source_root.iterdir();assert (source/'uv.lock').exists()
    run([UV,'sync','--locked','--no-dev','--no-editable','--project',str(source)],temp)
    source_case=temp/'source-case';source_case.mkdir();exercise(source/'.venv/bin/python',source_case)
print('Wheel and sdist passed fresh locked dependency installs, model-free CLI/help/eight private cases, payment recovery, failed-key receipt and real busy-case state operations outside checkout.')
