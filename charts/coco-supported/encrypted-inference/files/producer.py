"""Trusted producer: pin, verify, encrypt Qwen, then push ciphertext only."""
import hashlib
import io
import json
import os
import secrets
import tarfile
import urllib.request
from pathlib import Path

from artifact import CHUNK, FORMAT, Registry, aad, canonical, crypt_stream, digest_file

MODEL = 'Qwen/Qwen3-0.6B'
REVISION = 'c1899de289a04d12100db370d81485cdf75e47ca'
FILES = {
    'LICENSE': (11343, 'git:6634c8cc3133b3848ec74b9f275acaaa1ea618ab'),
    'config.json': (726, 'git:f5c3703b78ae2a478ae15b247e9f855e0ce2107b'),
    'generation_config.json': (239, 'git:20a8a9156fc8c3f25295ca067f61fdf120d517c5'),
    'merges.txt': (1671853, 'git:31349551d90c7606f325fe0f11bbb8bd5fa0d7c7'),
    'model.safetensors': (1503300328, 'sha256:f47f71177f32bcd101b7573ec9171e6a57f4f4d31148d38e382306f42996874b'),
    'tokenizer.json': (11422654, 'sha256:aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4'),
    'tokenizer_config.json': (9732, 'git:417d038a63fa3de29cfde265caedae14d1a58d92'),
    'vocab.json': (2776833, 'git:4783fe10ac3adce15ac8f358ef5462739852c569'),
}


def main():
    os.umask(0o077)
    plain, encrypted = Path('/plaintext'), Path('/encrypted')
    key = bytes.fromhex(Path('/model-key/qwen3-06b').read_text().strip())
    bundle = {'format': FORMAT, 'model': MODEL, 'revision': REVISION,
              'key_resource': 'default/model-keys/qwen3-06b', 'files': []}
    for index, (name, (expected_size, expected_hash)) in enumerate(FILES.items()):
        source = plain / name
        algorithm, expected = expected_hash.split(':', 1)
        check = hashlib.sha256() if algorithm == 'sha256' else hashlib.sha1()
        if algorithm == 'git':
            check.update(f'blob {expected_size}\0'.encode())
        total = 0
        with urllib.request.urlopen(f'https://huggingface.co/{MODEL}/resolve/{REVISION}/{name}', timeout=180) as response, source.open('wb') as output:
            for block in iter(lambda: response.read(CHUNK), b''):
                total += len(block)
                if total > expected_size:
                    raise ValueError('Source model exceeds pinned size')
                output.write(block)
                check.update(block)
        if total != expected_size or check.hexdigest() != expected:
            raise ValueError('Source model content does not match pinned revision')
        nonce = secrets.token_bytes(12)
        encrypted_name = f'{index:02d}.enc'
        with source.open('rb') as stream:
            info = crypt_stream(stream, encrypted/encrypted_name, key, nonce, aad(bundle, name))
        source.unlink()
        bundle['files'].append({'name': name, 'blob': encrypted_name, 'nonce': nonce.hex(), **info})
        print(f'ENCRYPTED_FILE_PASS {name} bytes={info["size"]}', flush=True)
    del key
    layer = encrypted/'layer.tar.gz'
    with tarfile.open(layer, 'w:gz', compresslevel=1) as archive:
        metadata = canonical(bundle)
        member = tarfile.TarInfo('bundle.json')
        member.size, member.mode = len(metadata), 0o600
        archive.addfile(member, io.BytesIO(metadata))
        for entry in bundle['files']:
            archive.add(encrypted/entry['blob'], arcname=entry['blob'], recursive=False)
    # Docker image schema is supported by OpenShift's integrated registry.
    # The layer contains ONLY encrypted files and public metadata, never a runtime.
    import gzip
    diff = hashlib.sha256()
    with gzip.open(layer, 'rb') as stream:
        for block in iter(lambda: stream.read(CHUNK), b''):
            diff.update(block)
    config = {'architecture': 'amd64', 'os': 'linux', 'config': {},
              'rootfs': {'type': 'layers', 'diff_ids': ['sha256:' + diff.hexdigest()]},
              'history': [{'created_by': 'encrypted-model-producer'}]}
    config_path = encrypted/'config.json'
    config_path.write_bytes(canonical(config))
    registry = Registry()
    layer_descriptor = registry.put_blob(layer, 'application/vnd.docker.image.rootfs.diff.tar.gzip')
    config_descriptor = registry.put_blob(config_path, 'application/vnd.docker.container.image.v1+json')
    manifest = {'schemaVersion': 2, 'mediaType': 'application/vnd.docker.distribution.manifest.v2+json',
                'config': config_descriptor, 'layers': [layer_descriptor]}
    raw = canonical(manifest)
    with registry.request('/v2/' + registry.repository + '/manifests/' + os.environ['ARTIFACT_TAG'], 'PUT', raw,
                          {'Content-Type': manifest['mediaType']}) as response:
        published_digest = response.headers['Docker-Content-Digest']
    if published_digest != 'sha256:' + hashlib.sha256(raw).hexdigest():
        raise ValueError('Published manifest digest mismatch')
    record = {'model': MODEL, 'revision': REVISION, 'artifact_digest': published_digest,
              'repository': registry.repository, 'layer': layer_descriptor, 'files': bundle['files'],
              'key_resource': bundle['key_resource']}
    print('ENCRYPTED_MODEL_PUBLISHED ' + json.dumps(record, sort_keys=True), flush=True)


if __name__ == '__main__':
    main()
