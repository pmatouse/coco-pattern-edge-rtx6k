"""Fetch ciphertext, obtain an attested key, authenticate weights, start vLLM."""
import io
import json
import os
import shutil
import subprocess
import tarfile
import urllib.request
from pathlib import Path

from artifact import FORMAT, Registry, aad, crypt_stream

EXPECTED_FILES = {'LICENSE', 'config.json', 'generation_config.json', 'merges.txt',
                  'model.safetensors', 'tokenizer.json', 'tokenizer_config.json', 'vocab.json'}
REVISION = 'c1899de289a04d12100db370d81485cdf75e47ca'
PRIVATE = Path('/private')
RESULTS = Path('/results')


def stage(name):
    print(name, flush=True)


def main():
    os.umask(0o077)
    # Fail before key retrieval if the intended plaintext volume is not tmpfs.
    filesystem = subprocess.check_output(['stat', '-f', '-c', '%T', str(PRIVATE)], text=True).strip()
    if filesystem != 'tmpfs':
        raise ValueError('Plaintext/cache volume is not guest tmpfs')
    stage('GUEST_PRIVATE_TMPFS_PASS')
    for stale in ('staging', 'model', 'home', 'cache', 'tmp'):
        shutil.rmtree(PRIVATE/stale, ignore_errors=True)
    for directory in ('staging', 'home', 'cache', 'tmp'):
        (PRIVATE/directory).mkdir(mode=0o700)
    registry = Registry()
    digest = os.environ['ARTIFACT_DIGEST']
    manifest = registry.get_manifest(digest)
    layer = PRIVATE/'encrypted-model.tar.gz'
    registry.download_layer(manifest['layers'][0], layer)
    stage('REGISTRY_DIGEST_VERIFY_PASS')
    with tarfile.open(layer, 'r:gz') as archive:
        members = archive.getmembers()
        if len(members) != 9 or any(not m.isfile() for m in members):
            raise ValueError('Unexpected encrypted artifact members')
        metadata_member = archive.getmember('bundle.json')
        if metadata_member.size > 65536:
            raise ValueError('Oversized metadata')
        bundle = json.load(archive.extractfile(metadata_member))
        if (bundle['format'] != FORMAT or bundle['model'] != 'Qwen/Qwen3-0.6B'
                or bundle['revision'] != REVISION or bundle['key_resource'] != 'default/model-keys/qwen3-06b'):
            raise ValueError('Model or key identity does not match deployment')
        entries = bundle['files']
        if len(entries) != 8 or {f['name'] for f in entries} != EXPECTED_FILES:
            raise ValueError('Unexpected model filenames')
        expected_members = {'bundle.json'} | {f'{i:02d}.enc' for i in range(8)}
        if {m.name for m in members} != expected_members or {f['blob'] for f in entries} != expected_members - {'bundle.json'}:
            raise ValueError('Unexpected ciphertext filenames')
        if sum(f['size'] for f in entries) > 2 * 1024**3:
            raise ValueError('Oversized model')
        stage('TRUSTEE_KEY_FETCH_START')
        with urllib.request.urlopen('http://127.0.0.1:8006/cdh/resource/' + bundle['key_resource'], timeout=120) as response:
            key_text = response.read(65).strip()
        if len(key_text) != 64:
            raise ValueError('Unexpected Trustee key encoding')
        key = bytes.fromhex(key_text.decode('ascii'))
        del key_text
        stage('TRUSTEE_KEY_FETCH_PASS')
        # Exercise the actual artifact with wrong key and tampered ciphertext before loading.
        sample = next(f for f in entries if f['name'] == 'config.json')
        nonce, tag = bytes.fromhex(sample['nonce']), bytes.fromhex(sample['tag'])
        ciphertext = archive.extractfile(sample['blob']).read()
        for label, trial_key, trial_ciphertext in (
            ('WRONG_KEY_REJECTED_PASS', bytes([key[0] ^ 1]) + key[1:], ciphertext),
            ('TAMPERED_CIPHERTEXT_REJECTED_PASS', key, bytes([ciphertext[0] ^ 1]) + ciphertext[1:]),
        ):
            try:
                crypt_stream(io.BytesIO(trial_ciphertext), PRIVATE/'negative.partial', trial_key, nonce,
                             aad(bundle, sample['name']), tag)
            except ValueError:
                if (PRIVATE/'negative.partial').exists():
                    raise ValueError('Failed authentication left plaintext staging output')
                stage(label)
            else:
                raise ValueError('Unauthenticated data accepted')
        hashes = {}
        for entry in entries:
            member = archive.getmember(entry['blob'])
            if member.size != entry['size']:
                raise ValueError('Ciphertext size mismatch')
            destination = PRIVATE/'staging'/entry['name']
            info = crypt_stream(archive.extractfile(member), destination, key, bytes.fromhex(entry['nonce']),
                                aad(bundle, entry['name']), bytes.fromhex(entry['tag']))
            if info['sha256'] != entry['sha256'] or info['size'] != entry['size']:
                raise ValueError('Decrypted model file hash mismatch')
            hashes[entry['name']] = info['sha256']
        del key
    (PRIVATE/'staging').rename(PRIVATE/'model')
    layer.unlink()
    stage('MODEL_AUTHENTICATED_DECRYPT_PASS')
    # Nonsecret driver diagnostics remain available even when CUDA initialization fails.
    diagnostics = subprocess.run(['nvidia-smi', '--query-gpu=name,driver_version,uuid', '--format=csv,noheader'],
                                 capture_output=True, text=True, timeout=30)
    print('GPU_DRIVER_QUERY:', diagnostics.returncode, diagnostics.stdout.strip(), diagnostics.stderr.strip(), flush=True)
    import ctypes
    driver = ctypes.CDLL('libcuda.so.1')
    result = driver.cuInit(0)
    print('CUDA_DRIVER_INIT:', result, flush=True)
    if result != 0:
        raise RuntimeError('CUDA driver initialization failed with code ' + str(result))
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError('CUDA GPU unavailable; CPU fallback is forbidden')
    probe = torch.tensor([1, 2, 3], device='cuda')
    if (probe + probe).cpu().tolist() != [2, 4, 6]:
        raise RuntimeError('CUDA computation failed')
    device = torch.cuda.get_device_properties(0)
    record = {'model': bundle['model'], 'revision': REVISION, 'artifact_digest': digest,
              'key_resource': bundle['key_resource'], 'plaintext_storage': filesystem,
              'files_sha256': hashes, 'wrong_key_rejected': True, 'tampered_ciphertext_rejected': True,
              'cuda_device': device.name, 'cuda_capability': list(torch.cuda.get_device_capability(0)),
              'torch': torch.__version__, 'cuda': torch.version.cuda}
    (RESULTS/'model-validation.json').write_text(json.dumps(record, indent=2) + '\n')
    stage('CUDA_GPU_PROBE_PASS')
    stage('VLLM_START')
    os.execvp('vllm', ['vllm', 'serve', str(PRIVATE/'model'), '--served-model-name', os.environ['SERVED_MODEL_NAME'],
                      '--host', '0.0.0.0', '--port', '8000', '--dtype', 'bfloat16',
                      '--max-model-len', '2048', '--max-num-seqs', '4', '--gpu-memory-utilization', '0.25',
                      '--enforce-eager', '--disable-log-requests', '--generation-config', 'vllm'])


if __name__ == '__main__':
    try:
        main()
    except Exception:
        # Never leave a partially decrypted model available to another startup.
        shutil.rmtree(PRIVATE/'staging', ignore_errors=True)
        shutil.rmtree(PRIVATE/'model', ignore_errors=True)
        raise
