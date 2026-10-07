#!/usr/bin/env python3
"""Create the three repository credentials used by the tested private deployment."""
import argparse
import base64
import json
from pathlib import Path
import subprocess

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--repo', required=True)
p.add_argument('--private-key', type=Path, required=True)
a = p.parse_args()
key = a.private_key.read_bytes()
if b'PRIVATE KEY' not in key:
    p.error('The supplied file is not a private key')
for ns, name in [('patterns-operator', 'coco-deploy-git'),
                 ('vp-gitops', 'vp-private-repo-credentials'),
                 ('openshift-gitops', 'vp-private-repo-credentials')]:
    def apply(obj):
        subprocess.run(['oc', 'apply', '-f', '-'], input=json.dumps(obj).encode(), check=True)
    apply({'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': ns}})
    apply({'apiVersion': 'v1', 'kind': 'Secret', 'type': 'Opaque',
           'metadata': {'name': name, 'namespace': ns,
                        'labels': {'argocd.argoproj.io/secret-type': 'repository'}},
           'data': {k: base64.b64encode(v).decode() for k, v in {
               'type': b'git', 'url': a.repo.encode(), 'sshPrivateKey': key}.items()}})

