#!/usr/bin/env python3
"""Inspect or approve only the exact OSC, Trustee and GPU Operator initial installation plans."""
import argparse
import json
import subprocess

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--apply', action='store_true', help='Approve matching plans; default is inspection only')
a = p.parse_args()
expected = {'openshift-sandboxed-containers-operator': 'sandboxed-containers-operator.v1.13.0',
            'trustee-operator-system': 'trustee-operator.v1.2.0',
            'nvidia-gpu-operator': 'gpu-operator-certified.v26.3.0'}
plans = json.loads(subprocess.check_output(['oc', 'get', 'installplans.operators.coreos.com', '-A', '-o', 'json']))
for x in plans['items']:
    ns = x['metadata']['namespace']
    if ns not in expected or x['spec'].get('approved'):
        continue
    names = x['spec']['clusterServiceVersionNames']
    match = names == [expected[ns]] and x['status']['phase'] == 'RequiresApproval'
    print(ns, x['metadata']['name'], ','.join(names), 'MATCH' if match else 'NOT APPROVED')
    if a.apply and match:
        subprocess.run(['oc', 'patch', 'installplan', x['metadata']['name'], '-n', ns,
                        '--type=merge', '-p', '{"spec":{"approved":true}}'], check=True)

