"""Noninteractive local invocation and model-free dataset initialization."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import signal
import sys

from .datasets import initialize, list_cases
from .service import resolve_async
from .workspace import Workspace


async def run(args):
    loop=asyncio.get_running_loop();task=asyncio.current_task()
    previous=signal.getsignal(signal.SIGTERM)
    loop.add_signal_handler(signal.SIGTERM,task.cancel)
    try:
        return await resolve_async(args.ticket_id,args.approval_id,Workspace(args.workspace),args.model,output_root=args.output_root)
    finally:
        loop.remove_signal_handler(signal.SIGTERM);signal.signal(signal.SIGTERM,previous)


def main(argv=None):
    parser=argparse.ArgumentParser(description='Resolve a synthetic refund ticket',allow_abbrev=False)
    parser.add_argument('ticket_id',nargs='?')
    parser.add_argument('--approval-id')
    parser.add_argument('--workspace',type=Path,default=Path('workspace.json'))
    parser.add_argument('--model',default=os.getenv('ANTHROPIC_MODEL','anthropic:claude-sonnet-4-6'))
    parser.add_argument('--output-root',type=Path)
    group=parser.add_mutually_exclusive_group()
    group.add_argument('--list-cases',action='store_true')
    group.add_argument('--init-case')
    args=parser.parse_args(argv)
    if (args.list_cases or args.init_case) and (args.ticket_id or args.approval_id or args.output_root):
        parser.error('list/init cannot execute a ticket')
    try:
        if args.list_cases:result=list_cases()
        elif args.init_case:result=initialize(args.init_case,args.workspace)
        else:
            if not args.ticket_id:parser.error('ticket_id is required for a run')
            if not args.workspace.exists():parser.error('workspace missing; initialize a fresh case first')
            result=asyncio.run(run(args))
    except (Exception,asyncio.CancelledError,KeyboardInterrupt) as error:
        print(json.dumps({'error':str(error) or type(error).__name__,'type':type(error).__name__,
            'receipt_path':getattr(error,'receipt_path',None),'evidence_error':getattr(error,'evidence',{}).get('evidence_error') if getattr(error,'evidence',None) else None}),file=sys.stderr)
        return 130 if isinstance(error,(asyncio.CancelledError,KeyboardInterrupt)) else 1
    print(json.dumps(result,indent=2))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
