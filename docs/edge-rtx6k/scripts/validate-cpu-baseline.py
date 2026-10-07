#!/usr/bin/env python3
"""Read-only checks plus harmless /bin/true exec tests for the installed CPU baseline."""
import argparse
import base64
import json
import subprocess

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--expected-apps', type=int, default=16)
p.add_argument('--ingress-ip', help='Override route DNS with this IP')
a = p.parse_args()
def get(*args):
    return json.loads(subprocess.check_output(['oc', '--request-timeout=30s', 'get', *args, '-o', 'json']))
apps = get('applications.argoproj.io', '-n', 'vp-gitops')['items']
assert len(apps) == a.expected_apps, f'Expected {a.expected_apps} applications, found {len(apps)}'
bad = [(x['metadata']['name'], x['status']['sync']['status'], x['status']['health']['status'])
       for x in apps if x['status']['sync']['status'] != 'Synced' or x['status']['health']['status'] != 'Healthy']
if bad:
    print(f'FAIL: Applications not ready: {bad}', flush=True)
else:
    print(f'PASS: {len(apps)} applications Synced and Healthy', flush=True)
for x in get('co')['items']:
    for c in x['status']['conditions']:
        assert not (c['type'] == 'Degraded' and c['status'] == 'True'), x['metadata']['name']
        assert not (c['type'] == 'Available' and c['status'] != 'True'), x['metadata']['name']
print('PASS: cluster operators Available and not Degraded')
r = get('runtimeclass', 'kata-cc')
assert r['handler'] == 'kata-snp', r['handler']
k = get('kataconfig', 'default-kata-config')['status']['kataNodes']
assert k['readyNodeCount'] == k['nodeCount'] == 1, k
print('PASS: Kata ready on one node with AMD SNP runtime')
for name, allow in [('insecure-policy', True), ('secure', False)]:
    r = subprocess.run(['oc', 'exec', '-n', 'hello-openshift', 'deployment/' + name, '--', '/bin/true'], capture_output=True, text=True)
    if allow:
        assert r.returncode == 0, r.stderr
    else:
        assert r.returncode != 0 and 'ExecProcessRequest is blocked by policy' in r.stderr, r.stderr
print('PASS: debug exec allowed; secure exec denied by guest policy')
s = get('secret', 'kbsres1', '-n', 'trustee-operator-system')
expected = base64.b64decode(s['data']['key3'])
host = get('route', 'kbs-access-curl', '-n', 'kbs-access')['spec']['host']
cmd = ['curl', '--fail', '--silent', '--show-error', '--max-time', '30', '--noproxy', '*']
if a.ingress_ip:
    cmd += ['--resolve', f'{host}:80:{a.ingress_ip}']
actual = subprocess.check_output(cmd + [f'http://{host}/secret.txt'])
assert actual == expected, 'KBS response does not match the configured test secret'
print('PASS: KBS secret retrieved through the confidential workload; value withheld')
if bad:
    raise SystemExit(1)
