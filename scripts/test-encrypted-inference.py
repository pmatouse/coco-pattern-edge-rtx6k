#!/usr/bin/env python3
"""Verify actual Qwen inference, artifact identity, secure exec and CPU denial."""
import argparse
import datetime
import json
from pathlib import Path
import re
import subprocess
import select
import urllib.request
import xml.etree.ElementTree as ET

OC = ['oc', '--request-timeout=90s']


def run(args, *, data=None, check=True):
    result = subprocess.run(OC + args, input=data, capture_output=True, timeout=120)
    if check and result.returncode:
        raise RuntimeError('Cluster command failed: ' + result.stderr.decode()[:1000])
    return result


def get(*args):
    return json.loads(run(['get', *args, '-o', 'json']).stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--model-selector', default='app=encrypted-qwen')
    parser.add_argument('--model-container', default='vllm')
    args = parser.parse_args()
    ns = 'gpu-workload'
    pods = get('pods', '-n', ns, '-l', args.model_selector)['items']
    pods = [p for p in pods if not p['metadata'].get('deletionTimestamp')]
    assert len(pods) == 1
    pod = pods[0]
    assert pod['spec']['runtimeClassName'] == 'kata-cc-nvidia-gpu'
    assert any(c['type'] == 'Ready' and c['status'] == 'True' for c in pod['status']['conditions'])
    container = next(c for c in pod['spec']['containers'] if c['name'] == args.model_container)
    assert container['resources']['limits']['nvidia.com/pgpu'] == '1'
    assert not any('secret' in v or 'hostPath' in v or 'persistentVolumeClaim' in v for v in pod['spec']['volumes'])
    env = {e['name']: e.get('value') for e in container['env']}
    assert env['HF_HUB_OFFLINE'] == env['TRANSFORMERS_OFFLINE'] == '1'
    base = '/api/v1/namespaces/gpu-workload/services/'
    report = json.loads(run(['get', '--raw', base + 'encrypted-qwen-status:8080/proxy/model-validation.json']).stdout)
    assert report['artifact_digest'] == env['ARTIFACT_DIGEST']
    assert report['plaintext_storage'] == 'tmpfs'
    assert report['gpu_dram_encryption'] == 'Enabled'
    security_before = json.loads(run(['get', '--raw', base + 'encrypted-qwen-status:8080/proxy/gpu-security-before-model.json']).stdout)
    security_loaded = json.loads(run(['get', '--raw', base + 'encrypted-qwen-status:8080/proxy/gpu-security-model-loaded.json']).stdout)
    for evidence in (security_before, security_loaded):
        assert evidence['dram_encryption_current'] == ['Enabled']
        assert evidence['gpu_uuid'] == [report['gpu_uuid']]
    for evidence in (security_before, security_loaded):
        cc = evidence['confidential_compute']
        assert cc['returncode'] == 0
        assert re.search(r'CC State\s*:\s*ON\b', cc['stdout'])
        assert re.search(r'CC GPUs Ready State\s*:\s*Ready\b', cc['stdout'])
    root = ET.fromstring(security_loaded['xml']['stdout'])
    protected_used_mib = int(root.findtext('.//gpu/cc_protected_memory_usage/used').split()[0])
    assert protected_used_mib > 0
    engine_pids = {p.findtext('pid') for p in root.findall('.//process_info')
                   if 'VLLM' in (p.findtext('process_name') or '')}
    rows = [line.split() for line in security_loaded['process_memory']['stdout'].splitlines()
            if line.strip() and not line.startswith('#')]
    engine_rows = [row for row in rows if len(row) >= 6 and row[1] in engine_pids]
    assert engine_rows, 'No vLLM process protected-memory evidence'
    for row in engine_rows:
        assert int(row[3]) > 0 and int(row[3]) == int(row[4]), 'vLLM FB and CC protected memory differ'
    print('PASS: GPU DRAM encryption Enabled; CC ON/Ready; vLLM allocation entirely in CC protected memory')
    assert report['wrong_key_rejected'] and report['tampered_ciphertext_rejected']
    assert report['files_sha256']['model.safetensors'] == 'f47f71177f32bcd101b7573ec9171e6a57f4f4d31148d38e382306f42996874b'
    request = {'model': env['SERVED_MODEL_NAME'], 'messages': [
        {'role': 'user', 'content': 'Calculate 2 + 2.'}],
        'temperature': 0, 'max_tokens': 32, 'chat_template_kwargs': {'enable_thinking': False}}
    # oc create --raw sends a content type rejected by this vLLM build.
    # A loopback-only oc proxy retains authenticated/TLS access to the API server.
    proxy = subprocess.Popen(OC + ['proxy', '--address=127.0.0.1', '--port=0'],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        if not select.select([proxy.stdout], [], [], 15)[0]:
            raise RuntimeError('Local API proxy did not start')
        match = re.search(r'127\.0\.0\.1:(\d+)', proxy.stdout.readline())
        if not match:
            raise RuntimeError('Cannot determine local API proxy port')
        url = 'http://127.0.0.1:' + match[1] + base + 'encrypted-qwen:8000/proxy/v1/chat/completions'
        http = urllib.request.Request(url, data=json.dumps(request).encode(),
                                      headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(http, timeout=90) as response:
            completion = json.load(response)
    finally:
        proxy.terminate()
        proxy.wait(timeout=10)
    answer = completion['choices'][0]['message']['content'].strip()
    assert re.search(r'2\s*\+\s*2\s*=\s*4\b', answer), 'Unexpected calculation answer: ' + repr(answer)
    print('PASS: encrypted registry weights decrypted in guest; Qwen CUDA inference answered: ' + answer)
    denied = run(['exec', '-n', ns, pod['metadata']['name'], '-c', args.model_container, '--', '/bin/true'], check=False)
    assert denied.returncode and b'ExecProcessRequest is blocked by policy' in denied.stderr
    print('PASS: arbitrary exec remains blocked')
    cpu = get('deployment', 'insecure-policy', '-n', 'hello-openshift')
    assert cpu['spec']['template']['spec']['runtimeClassName'] == 'kata-cc'
    started = datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z')
    denied = run(['exec', '-n', 'hello-openshift', 'deployment/insecure-policy', '--', 'curl',
                  '--silent', '--show-error', '--max-time', '45', '--write-out', '\n%{http_code}',
                  'http://127.0.0.1:8006/cdh/resource/default/model-keys/qwen3-06b'])
    status = denied.stdout.rsplit(b'\n', 1)[1]
    assert status in (b'401', b'403', b'500'), 'CPU-only guest received protected key'
    logs = run(['logs', '-n', 'trustee-operator-system', 'deployment/trustee-deployment', '-c', 'kbs',
                '--since-time=' + started]).stdout.decode()
    logs = re.sub(r'\x1b\[[0-9;]*m', '', logs)
    assert 'PolicyDeny' in logs
    assert re.search(r'GET /kbs/v0/resource/default/model-keys/qwen3-06b HTTP/1.1" 401', logs)
    print('PASS: CPU-only SNP guest denied Qwen key by KBS policy (401)')
    record = {'checked_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'pod': pod['metadata']['name'], 'pod_uid': pod['metadata']['uid'],
              'runtime': pod['spec']['runtimeClassName'], 'image': container['image'],
              'model_validation': report, 'request': request, 'completion': completion,
              'gpu_security_before_model': security_before, 'gpu_security_model_loaded': security_loaded,
              'exec_denied': True, 'cpu_only_key_denied': True, 'kbs_denial_http_status': 401,
              'cdh_denial_http_status': int(status)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, indent=2) + '\n')


if __name__ == '__main__':
    main()
