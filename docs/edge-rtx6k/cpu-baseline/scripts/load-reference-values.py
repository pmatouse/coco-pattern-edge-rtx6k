#!/usr/bin/env python3
"""Update only the firmware reference entry in an already initialized cluster Vault."""
import argparse
import base64
import json
from pathlib import Path
import subprocess
import time

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('reference_file', type=Path)
a = p.parse_args()
contents = a.reference_file.read_text()
d = json.loads(contents)
assert d.get('snp_launch_measurement'), 'No launch measurements supplied'
for k in ['snp_bootloader', 'snp_tee_svn', 'snp_snp_svn', 'snp_microcode']:
    assert k in d and isinstance(d[k], list), f'Missing TCB list: {k}'
s = json.loads(subprocess.check_output(['oc', 'get', 'secret', 'vaultkeys', '-n', 'imperative', '-o', 'json']))
keys = json.loads(base64.b64decode(s['data']['vault_data_json']))
payload = keys['root_token'] + '\n' + json.dumps({'json': contents})
subprocess.run(['oc', 'exec', '-i', '-n', 'vault', 'vault-0', '--', 'sh', '-c',
                'read -r VAULT_TOKEN; export VAULT_TOKEN; '
                'vault kv put -format=json secret/hub/firmwareReferenceValues - >/dev/null'],
               input=payload.encode(), check=True)
subprocess.run(['oc', 'annotate', 'externalsecret', 'firmware-refvals-eso', '-n',
                'trustee-operator-system', f'force-sync={time.time_ns()}', '--overwrite'], check=True)
print('Updated firmware references in Vault. No secret values or tokens printed.')

