"""Small, dependency-free registry and streaming AES-256-GCM helpers."""
import ctypes
import ctypes.util
import hashlib
import json
import os
import ssl
import urllib.parse
import urllib.request
from pathlib import Path

CHUNK = 1024 * 1024
MAX_LAYER = 4 * 1024**3
FORMAT = 'coco-encrypted-model-v1'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def digest_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(CHUNK), b''):
            h.update(chunk)
    return 'sha256:' + h.hexdigest()


def aad(bundle, filename):
    return canonical({k: bundle[k] for k in ('format', 'model', 'revision', 'key_resource')} | {'file': filename})


def crypt_stream(source, destination, key, nonce, associated, tag=None):
    """Decrypt to a PRIVATE staging path only; erase it if authentication fails."""
    if len(key) != 32 or len(nonce) != 12 or (tag is not None and len(tag) != 16):
        raise ValueError('Invalid AES-256-GCM parameters')
    lib = ctypes.CDLL(os.environ.get('TEST_LIBCRYPTO') or ctypes.util.find_library('crypto'))
    ptr, integer = ctypes.c_void_p, ctypes.c_int
    signatures = {
        'EVP_CIPHER_CTX_new': ([], ptr),
        'EVP_CIPHER_CTX_free': ([ptr], None),
        'EVP_aes_256_gcm': ([], ptr),
        'EVP_CipherInit_ex': ([ptr, ptr, ptr, ptr, ptr, integer], integer),
        'EVP_CIPHER_CTX_ctrl': ([ptr, integer, integer, ptr], integer),
        'EVP_CipherUpdate': ([ptr, ptr, ptr, ptr, integer], integer),
        'EVP_CipherFinal_ex': ([ptr, ptr, ptr], integer),
    }
    for name, (args, result) in signatures.items():
        fn = getattr(lib, name)
        fn.argtypes, fn.restype = args, result
    ctx = lib.EVP_CIPHER_CTX_new()
    if not ctx:
        raise RuntimeError('Cannot allocate cipher context')
    encrypting = int(tag is None)
    count = integer()
    output = ctypes.create_string_buffer(CHUNK + 16)
    sha = hashlib.sha256()
    size = 0

    def check(result):
        if result != 1:
            raise ValueError('AES-GCM authentication or cipher operation failed')

    try:
        check(lib.EVP_CipherInit_ex(ctx, lib.EVP_aes_256_gcm(), None, None, None, encrypting))
        check(lib.EVP_CIPHER_CTX_ctrl(ctx, 0x9, 12, None))
        check(lib.EVP_CipherInit_ex(ctx, None, None, key, nonce, encrypting))
        check(lib.EVP_CipherUpdate(ctx, None, ctypes.byref(count), associated, len(associated)))
        with open(destination, 'wb') as out:
            os.chmod(destination, 0o600)
            for block in iter(lambda: source.read(CHUNK), b''):
                check(lib.EVP_CipherUpdate(ctx, output, ctypes.byref(count), block, len(block)))
                data = output.raw[:count.value]
                out.write(data)
                plaintext = block if encrypting else data
                sha.update(plaintext)
                size += len(plaintext)
            if tag is not None:
                check(lib.EVP_CIPHER_CTX_ctrl(ctx, 0x11, 16, tag))
            check(lib.EVP_CipherFinal_ex(ctx, output, ctypes.byref(count)))
            if count.value:
                out.write(output.raw[:count.value])
            result_tag = ctypes.create_string_buffer(16)
            if encrypting:
                check(lib.EVP_CIPHER_CTX_ctrl(ctx, 0x10, 16, result_tag))
        return {'sha256': sha.hexdigest(), 'size': size, 'tag': result_tag.raw.hex() if encrypting else tag.hex()}
    except Exception:
        Path(destination).unlink(missing_ok=True)
        raise
    finally:
        ctypes.memset(output, 0, len(output))
        lib.EVP_CIPHER_CTX_free(ctx)


class Registry:
    def __init__(self):
        self.origin = 'https://' + os.environ['REGISTRY_HOST']
        self.repository = os.environ['REGISTRY_REPOSITORY']
        self.context = ssl.create_default_context(cafile=os.environ['REGISTRY_CA'])
        self.token_path = os.environ.get('REGISTRY_TOKEN', '/var/run/secrets/kubernetes.io/serviceaccount/token')
        origin = self.origin

        class SameOriginRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                parsed = urllib.parse.urlsplit(newurl)
                if parsed.scheme != 'https' or parsed.netloc != urllib.parse.urlsplit(origin).netloc:
                    raise ValueError('Refusing to forward registry credentials to another origin')
                return super().redirect_request(req, fp, code, msg, headers, newurl)

        self.opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=self.context), SameOriginRedirect())

    def request(self, path, method='GET', data=None, headers=None):
        url = urllib.parse.urljoin(self.origin, path)
        if urllib.parse.urlsplit(url).netloc != urllib.parse.urlsplit(self.origin).netloc:
            raise ValueError('Registry redirect crossed trust boundary')
        token = Path(self.token_path).read_text().strip()
        request = urllib.request.Request(url, data=data, method=method, headers={
            'Authorization': 'Bearer ' + token,
            **(headers or {}),
        })
        return self.opener.open(request, timeout=180)

    def put_blob(self, path, media_type):
        digest = digest_file(path)
        with self.request('/v2/' + self.repository + '/blobs/uploads/', 'POST', b'') as response:
            location = response.headers['Location']
        with open(path, 'rb') as stream:
            while block := stream.read(8 * CHUNK):
                with self.request(location, 'PATCH', block, {'Content-Type': 'application/octet-stream'}) as response:
                    location = response.headers['Location']
        separator = '&' if '?' in location else '?'
        with self.request(location + separator + 'digest=' + digest, 'PUT', b'', {'Content-Type': 'application/octet-stream'}):
            pass
        return {'mediaType': media_type, 'size': Path(path).stat().st_size, 'digest': digest}

    def get_manifest(self, digest):
        if not digest.startswith('sha256:') or len(digest) != 71:
            raise ValueError('An immutable SHA-256 manifest digest is required')
        with self.request('/v2/' + self.repository + '/manifests/' + digest, headers={
            'Accept': 'application/vnd.docker.distribution.manifest.v2+json'
        }) as response:
            raw = response.read(CHUNK)
        if 'sha256:' + hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError('Registry manifest digest mismatch')
        manifest = json.loads(raw)
        if len(manifest['layers']) != 1 or manifest['layers'][0]['size'] > MAX_LAYER:
            raise ValueError('Unexpected model layer layout or size')
        return manifest

    def download_layer(self, descriptor, path):
        sha, total = hashlib.sha256(), 0
        with self.request('/v2/' + self.repository + '/blobs/' + descriptor['digest']) as response, open(path, 'wb') as output:
            for block in iter(lambda: response.read(CHUNK), b''):
                total += len(block)
                if total > descriptor['size'] or total > MAX_LAYER:
                    raise ValueError('Oversized artifact')
                sha.update(block)
                output.write(block)
        if total != descriptor['size'] or 'sha256:' + sha.hexdigest() != descriptor['digest']:
            raise ValueError('Registry layer digest mismatch')
