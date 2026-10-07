#!/usr/bin/env python3
"""Verify actual Qwen inference, artifact identity, secure exec and CPU denial."""
import argparse
import datetime
import json
from pathlib import Path
import re
import subprocess

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
    args = parser.parse_args()
    ns = 'gpu-workload'
    pods = get('pods', '-n', ns, '-l', 'app=encrypted-qwen')['items']
    pods = [p for p in pods if not p['metadata'].get('deletionTimestamp')]
    assert len(pods) == 1
    pod = pods[0]
    assert pod['spec']['runtimeClassName'] == 'kata-cc-nvidia-gpu'
    assert any(c['type'] == 'Ready' and c['status'] == 'True' for c in pod['status']['conditions'])
    container = next(c for c in pod['spec']['containers'] if c['name'] == 'vllm')
    assert container['resources']['limits']['nvidia.com/pgpu'] == '1'
    assert not any('secret' in v or 'hostPath' in v or 'persistentVolumeClaim' in v for v in pod['spec']['volumes'])
    env = {e['name']: e.get('value') for e in container['env']}
    assert env['HF_HUB_OFFLINE'] == env['TRANSFORMERS_OFFLINE'] == '1'
    base = '/api/v1/namespaces/gpu-workload/services/'
    report = json.loads(run(['get', '--raw', base + 'encrypted-qwen-status:8080/proxy/model-validation.json']).stdout)
    assert report['artifact_digest'] == env['ARTIFACT_DIGEST']
    assert report['plaintext_storage'] == 'tmpfs'
    assert report['wrong_key_rejected'] and report['tampered_ciphertext_rejected']
    assert report['files_sha256']['model.safetensors'] == 'f47f71177f32bcd101b7573ec9171e6a57f4f4d31148d38e382306f42996874b'
    request = {'model': env['SERVED_MODEL_NAME'], 'messages': [
        {'role': 'user', 'content': 'What is 2 + 2? Answer with only the digit.'}],
        'temperature': 0, 'max_tokens': 32, 'chat_template_kwargs': {'enable_thinking': False}}
    result = run(['create', '--raw', base + 'encrypted-qwen:8000/proxy/v1/chat/completions', '-f', '-'],
                 data=json.dumps(request).encode())
    completion = json.loads(result.stdout)
    answer = completion['choices'][0]['message']['content'].strip()
    assert answer == '4', 'Unexpected deterministic test answer: ' + repr(answer)
    print('PASS: encrypted registry weights decrypted in guest; Qwen CUDA inference answered 4')
    denied = run(['exec', '-n', ns, pod['metadata']['name'], '-c', 'vllm', '--', '/bin/true'], check=False)
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
              'exec_denied': True, 'cpu_only_key_denied': True, 'kbs_denial_http_status': 401,
              'cdh_denial_http_status': int(status)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, indent=2) + '\n')


if __name__ == '__main__':
    main()
