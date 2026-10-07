#!/usr/bin/env python3
"""Create a 256-bit lab model key in existing Vault without printing or rotating it."""
import base64,json,secrets,subprocess
oc=['oc','--request-timeout=30s']
s=json.loads(subprocess.check_output(oc+['get','secret','vaultkeys','-n','imperative','-o','json']))
root=json.loads(base64.b64decode(s['data']['vault_data_json']))['root_token']
# Store as hex text. Model consumers must hex-decode it to obtain 32 key bytes.
payload=root+'\n'+json.dumps({'edge-model-key':secrets.token_hex(32)})
script='''read -r VAULT_TOKEN; export VAULT_TOKEN
if vault kv get secret/hub/modelKeys >/dev/null 2>&1; then
  cat >/dev/null
  echo 'Existing model-key entry retained; no rotation performed.'
else
  vault kv put -cas=0 -format=json secret/hub/modelKeys - >/dev/null || exit 1
  echo 'Created 256-bit model key; value withheld.'
fi'''
subprocess.run(oc+['exec','-i','-n','vault','vault-0','--','sh','-c',script],input=payload.encode(),check=True)
