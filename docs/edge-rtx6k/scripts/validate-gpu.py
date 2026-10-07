#!/usr/bin/env python3
"""Validate the single-GPU test without guest exec or printing secret values."""
import base64
import hashlib
import json
import subprocess

oc = ['oc', '--request-timeout=30s']


def get(*args):
    return json.loads(subprocess.check_output(oc + ['get', *args, '-o', 'json']))


nodes = get('nodes')['items']
assert len(nodes) == 1
node = nodes[0]
labels = node['metadata']['labels']
assert labels['nvidia.com/cc.mode.state'] == 'on'
assert labels['nvidia.com/cc.ready.state'] == 'true'
assert node['status']['allocatable']['nvidia.com/pgpu'] == '4'
assert get('runtimeclass', 'kata-cc-nvidia-gpu')['handler'] == 'kata-snp-nvidia-gpu'
print('PASS: GPU CC mode on, four allocatable cards, SNP GPU runtime')

pods = get('pods', '-n', 'gpu-workload', '-l', 'app=gpu-vectoradd')['items']
pods = [p for p in pods if not p['metadata'].get('deletionTimestamp')]
assert len(pods) == 1
pod = pods[0]
assert pod['spec']['runtimeClassName'] == 'kata-cc-nvidia-gpu'
assert pod['spec']['containers'][0]['resources']['limits']['nvidia.com/pgpu'] == '1'
assert any(c['type'] == 'Ready' and c['status'] == 'True' for c in pod['status']['conditions'])
report = subprocess.check_output(oc + ['get', '--raw',
    '/api/v1/namespaces/gpu-workload/services/gpu-validation:8080/proxy/status.txt'], text=True)
assert 'GPU_VECTORADD_PASS' in report and 'GPU_SECRET_FETCH_PASS' in report, report
secret = get('secret', 'kbsres1', '-n', 'trustee-operator-system')
expected = hashlib.sha256(base64.b64decode(secret['data']['key3'])).hexdigest()
assert expected + '  -' in report, 'Retrieved secret hash differs from configured value'
print('PASS: CUDA vectorAdd and matching KBS test-secret hash; secret withheld')

result = subprocess.run(oc + ['exec', '-n', 'gpu-workload', pod['metadata']['name'],
    '-c', 'gpu-cc-verifier', '--', '/bin/true'], capture_output=True, text=True)
assert result.returncode != 0 and 'ExecProcessRequest is blocked by policy' in result.stderr
print('PASS: GPU guest exec denied by secure policy')
print('Also inspect Trustee appraisal for both cpu0 and gpu0 Affirming; CUDA alone is insufficient.')
