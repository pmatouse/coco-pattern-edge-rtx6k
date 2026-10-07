"""Read-only GPU security diagnostics; fail closed before fetching model keys."""
import datetime
import json
from pathlib import Path
import subprocess
import time
import urllib.request
import xml.etree.ElementTree as ET

RESULTS = Path('/results')


def query(args):
    result = subprocess.run(['nvidia-smi', *args], capture_output=True, text=True, timeout=30)
    return {'command': ['nvidia-smi', *args], 'returncode': result.returncode,
            'stdout': result.stdout, 'stderr': result.stderr}


def snapshot(phase):
    xml = query(['-q', '-x'])
    record = {'checked_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'phase': phase, 'xml': xml, 'confidential_compute': query(['conf-compute', '-q']),
              'process_memory': query(['pmon', '-s', 'm', '-c', '1'])}
    if xml['returncode'] == 0:
        root = ET.fromstring(xml['stdout'])
        gpus = root.findall('gpu')
        record['gpu_count'] = len(gpus)
        record['gpu_uuid'] = [g.findtext('uuid') for g in gpus]
        current = []
        for gpu in gpus:
            for node in gpu.iter():
                if 'dram' in node.tag.lower() and 'encryption' in node.tag.lower():
                    for child in node:
                        if 'current' in child.tag.lower():
                            current.append((child.text or '').strip())
        record['dram_encryption_current'] = current
    path = RESULTS / ('gpu-security-' + phase + '.json')
    partial = path.with_suffix('.partial')
    partial.write_text(json.dumps(record, indent=2) + '\n')
    partial.replace(path)
    return record


def require_encrypted_dram():
    record = snapshot('before-model')
    states = record.get('dram_encryption_current', [])
    if record.get('gpu_count') != 1 or states != ['Enabled']:
        raise RuntimeError('GPU DRAM encryption is not confirmed Enabled: ' + repr(states)
                           + '; model key retrieval and model loading stopped')
    print('GPU_DRAM_ENCRYPTION_ENABLED_PASS', flush=True)
    return record


def monitor_loaded_model():
    # This process survives the parent's exec into vLLM and captures its memory usage.
    for _ in range(240):
        try:
            with urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2) as response:
                if response.status == 200:
                    snapshot('model-loaded')
                    return
        except Exception:
            pass
        time.sleep(5)
    (RESULTS/'gpu-security-monitor-error.txt').write_text('Inference did not become healthy within 20 minutes.\n')


if __name__ == '__main__':
    monitor_loaded_model()
