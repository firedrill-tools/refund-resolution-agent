"""Validate imported synthetic records without repairing or coercing money."""
import re

KINDS = ('orders','payments','tickets','approvals','refunds')
MAX_INT = 2**53-1


def validate_state(data):
    if not isinstance(data,dict): raise ValueError('workspace must be an object')
    index = {}
    def text(record,key,limit=8000,optional=False):
        value=record.get(key)
        if optional and value is None: return
        if not isinstance(value,str) or not value.strip() or len(value)>limit:
            raise ValueError(f'invalid {key}')
    def money(record,key,positive=False):
        value=record.get(key)
        if type(value) is not int or value < (1 if positive else 0) or value > MAX_INT:
            raise ValueError(f'{key} must be safe integer cents')
    def currency(record,optional=False):
        if optional and record.get('currency') is None: return
        if not isinstance(record.get('currency'),str) or re.fullmatch(r'[A-Z]{3}',record['currency']) is None:
            raise ValueError('invalid currency')
    for kind in KINDS:
        rows=data.get(kind)
        if not isinstance(rows,list) or len(rows)>10000: raise ValueError(f'invalid {kind} collection')
        index[kind]={}
        for record in rows:
            if not isinstance(record,dict): raise ValueError(f'invalid {kind} record')
            text(record,'id',128)
            if record['id'] in index[kind]: raise ValueError(f'duplicate {kind} identity')
            index[kind][record['id']]=record
    for row in data['orders']:
        text(row,'customer_email',320);money(row,'total_cents');currency(row)
        if row.get('status') not in {'delivered','processing','cancelled'}: raise ValueError('invalid order status')
    for row in data['payments']:
        text(row,'order_id',128);money(row,'captured_cents');money(row,'refunded_cents');currency(row,optional=True)
        if row['refunded_cents']>row['captured_cents']: raise ValueError('refunded balance exceeds capture')
        if row['order_id'] not in index['orders']: raise ValueError('orphan payment order')
    for row in data['tickets']:
        text(row,'order_id',128);text(row,'customer_email',320);text(row,'message')
        if row.get('status') not in {'open','refunded','cancelled','closed'}: raise ValueError('invalid ticket status')
        if row['order_id'] not in index['orders']: raise ValueError('orphan ticket order')
        if row.get('refund_id') is not None:
            refund=index['refunds'].get(row['refund_id'])
            if not refund or refund.get('ticket_id')!=row['id']: raise ValueError('orphan ticket refund')
    for row in data['approvals']:
        text(row,'ticket_id',128);text(row,'order_id',128);money(row,'amount_cents');currency(row)
        if row.get('status') not in {'approved','revoked','pending','denied'}: raise ValueError('invalid approval status')
        if row['ticket_id'] not in index['tickets'] or row['order_id'] not in index['orders']: raise ValueError('orphan approval')
    refunded_tickets=set(); totals={}
    for row in data['refunds']:
        text(row,'ticket_id',128);text(row,'order_id',128);money(row,'amount_cents',positive=True)
        if row['ticket_id'] in refunded_tickets: raise ValueError('multiple refunds for one ticket')
        refunded_tickets.add(row['ticket_id'])
        if row['ticket_id'] not in index['tickets'] or row['order_id'] not in index['orders']: raise ValueError('orphan refund')
        # Legacy incomplete evidence is readable but cannot authorize recovery.
        text(row,'payment_id',128,optional=True);currency(row,optional=True)
        if row.get('payment_id') is not None:
            payment=index['payments'].get(row['payment_id'])
            if not payment or payment['order_id']!=row['order_id']: raise ValueError('orphan refund payment')
            totals[payment['id']]=totals.get(payment['id'],0)+row['amount_cents']
        if row.get('approval_id') is not None and row['approval_id'] not in index['approvals']: raise ValueError('orphan refund approval')
        if row.get('proposal_observation') is not None and not isinstance(row['proposal_observation'],dict): raise ValueError('invalid original observation')
    if any(total>index['payments'][key]['refunded_cents'] for key,total in totals.items()):
        raise ValueError('refund history exceeds recorded payment balance')
    dataset=data.get('dataset')
    if dataset is not None:
        if not isinstance(dataset,dict): raise ValueError('invalid dataset')
        text(dataset,'id',128);text(dataset,'profile',64)
        if type(dataset.get('version')) is not int or not 1<=dataset['version']<=MAX_INT: raise ValueError('invalid dataset version')
    return data
