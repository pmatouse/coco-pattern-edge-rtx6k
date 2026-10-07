#!/usr/bin/env python3
"""Add a dedicated Qwen model key to Vault; preserve all existing keys."""
import base64
import json
import secrets
import subprocess

oc = ['oc', '--request-timeout=30s']
secret = json.loads(subprocess.check_output(oc + ['get', 'secret', 'vaultkeys', '-n', 'imperative', '-o', 'json']))
token = json.loads(base64.b64decode(secret['data']['vault_data_json']))['root_token']
payload = token + '\n' + json.dumps({'qwen3-06b': secrets.token_hex(32)})
script = '''read -r VAULT_TOKEN; export VAULT_TOKEN
if vault kv get -field=qwen3-06b secret/hub/modelKeys >/dev/null 2>&1; then
  cat >/dev/null
  echo 'Existing Qwen key retained; no rotation performed.'
else
  vault kv patch secret/hub/modelKeys - >/dev/null || exit 1
  echo 'Created dedicated 256-bit Qwen key; value withheld.'
fi'''
subprocess.run(oc + ['exec', '-i', '-n', 'vault', 'vault-0', '--', 'sh', '-c', script], input=payload.encode(), check=True)
