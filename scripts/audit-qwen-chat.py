#!/usr/bin/env python3
"""Trace the public chat to the live SNP/GPU workload and record a fresh request."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import re
import secrets
import subprocess
import urllib.request

OC = ['oc', '--request-timeout=30s']
ROOT = Path(__file__).resolve().parents[1]


def run(*args, data=None):
    return subprocess.check_output(OC + list(args), input=data, timeout=60)


def get(*args):
    return json.loads(run('get', *args, '-o', 'json'))


def ready_pod(label):
    pods = get('pods', '-n', 'gpu-workload', '-l', label)['items']
    pods = [p for p in pods if not p['metadata'].get('deletionTimestamp')]
    assert len(pods) == 1
    p = pods[0]
    assert any(c['type'] == 'Ready' and c['status'] == 'True' for c in p['status']['conditions'])
    return p


def endpoints(service, pod):
    slices = get('endpointslices', '-n', 'gpu-workload', '-l', 'kubernetes.io/service-name=' + service)['items']
    ready = [ep for s in slices for ep in s.get('endpoints', []) if ep.get('conditions', {}).get('ready')]
    assert len(ready) == 1
    assert ready[0]['targetRef']['uid'] == pod['metadata']['uid']
    assert pod['status']['podIP'] in ready[0]['addresses']
    return ready


def counter():
    raw = run('get', '--raw', '/api/v1/namespaces/gpu-workload/services/encrypted-qwen:8000/proxy/metrics').decode()
    lines = [line for line in raw.splitlines() if line.startswith('vllm:request_success_total{')]
    assert lines, 'vLLM request metric missing'
    return sum(float(line.rsplit(' ', 1)[1]) for line in lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://10.14.202.14:30080')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    chat, model = ready_pod('app=qwen-chat'), ready_pod('app=encrypted-qwen')
    service = get('service', 'qwen-chat', '-n', 'gpu-workload')
    assert service['spec']['type'] == 'NodePort'
    assert service['spec']['ports'][0]['nodePort'] == 30080
    chat_env = {e['name']: e.get('value') for e in chat['spec']['containers'][0]['env']}
    assert chat_env['QWEN_BACKEND'] == 'http://encrypted-qwen.gpu-workload.svc:8000'
    chain = {'public_url': args.url, 'chat_pod': chat['metadata']['name'], 'chat_uid': chat['metadata']['uid'],
             'chat_endpoints': endpoints('qwen-chat', chat), 'model_endpoints': endpoints('encrypted-qwen', model),
             'backend': chat_env['QWEN_BACKEND'], 'model_pod': model['metadata']['name'], 'model_uid': model['metadata']['uid']}
    cm = get('configmap', 'qwen-chat', '-n', 'gpu-workload')
    deployed = cm['data']['qwen-chat.py']
    expected = (ROOT/'charts/coco-supported/qwen-chat/files/qwen-chat.py').read_text()
    assert deployed == expected, 'Deployed chat server differs from audited source'
    chain['chat_source_sha256'] = hashlib.sha256(deployed.encode()).hexdigest()
    assert model['spec']['runtimeClassName'] == 'kata-cc-nvidia-gpu'
    runtime = get('runtimeclass', model['spec']['runtimeClassName'])
    assert runtime['handler'] == 'kata-snp-nvidia-gpu'
    node = model['spec']['nodeName']
    mcd = get('pods', '-n', 'openshift-machine-config-operator', '-l', 'k8s-app=machine-config-daemon',
              '--field-selector', 'spec.nodeName=' + node)['items'][0]['metadata']['name']
    sandboxes = json.loads(run('exec', '-n', 'openshift-machine-config-operator', mcd, '-c', 'machine-config-daemon',
                              '--', 'chroot', '/rootfs', 'crictl', 'pods', '--name', model['metadata']['name'], '-o', 'json'))['items']
    sandbox = next(s for s in sandboxes if s['metadata']['uid'] == model['metadata']['uid'] and s['state'] == 'SANDBOX_READY')
    host_script = r'''import pathlib,json,sys
sid=sys.argv[1]
for p in pathlib.Path('/proc').glob('[0-9]*/cmdline'):
 try:
  a=p.read_bytes().decode().split('\x00')
  if a[0]!='/usr/libexec/qemu-kvm':continue
  name=a[a.index('-name')+1]
  if name.split(',')[0]!='sandbox-'+sid:continue
  print(json.dumps({'pid':p.parts[2],'sandbox':sid,'machine':a[a.index('-machine')+1],
   'objects':[a[i+1] for i,v in enumerate(a[:-1]) if v=='-object'],
   'gpu':[s for s in a if s.startswith('vfio-pci,host=')]}))
 except (OSError,ValueError):pass
'''
    vm = json.loads(run('exec', '-i', '-n', 'openshift-machine-config-operator', mcd, '-c', 'machine-config-daemon',
                        '--', 'python3', '-', sandbox['id'], data=host_script.encode()))
    assert 'confidential-guest-support=snp' in vm['machine']
    assert any(o.startswith('sev-snp-guest,') for o in vm['objects'])
    assert len(vm['gpu']) == 1
    print('PASS: NodePort -> chat -> model Service -> exact pod UID -> SEV-SNP QEMU -> passed-through GPU')
    base = args.url.rstrip('/')
    page = urllib.request.urlopen(base, timeout=20).read().decode()
    token = re.search("const token='([^']+)'", page)[1]
    marker = 'CC-AUDIT-' + secrets.token_hex(6)
    before = counter()
    request = {'messages': [{'role': 'user', 'content': 'Reply with exactly this text and nothing else: ' + marker}]}
    http = urllib.request.Request(base + '/api/chat', data=json.dumps(request).encode(),
                                  headers={'Content-Type': 'application/json', 'X-Chat-Token': token, 'Origin': base})
    with urllib.request.urlopen(http, timeout=120) as response:
        reply = json.load(response)
    after = counter()
    assert marker in reply['message'], 'Unexpected fresh inference reply'
    assert after >= before + 1, 'No corresponding vLLM request-counter increase'
    print('PASS: unique prompt returned through shared IP; live vLLM success counter increased')
    record = {'checked_at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'chain': chain,
              'runtime': runtime['metadata']['name'], 'runtime_handler': runtime['handler'], 'vm': vm,
              'request': request, 'reply': reply, 'vllm_success_before': before, 'vllm_success_after': after,
              'note': 'Host process mapping plus separate guest/attestation validation; secure guest policy was not relaxed.'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, indent=2) + '\n')


if __name__ == '__main__':
    main()
