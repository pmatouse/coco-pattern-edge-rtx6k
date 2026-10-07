"""Decrypt authenticated input in the CVM, then compute on its assigned GPU."""
import base64
import ctypes as C
import ctypes.util
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import urllib.request


class AuthenticationFailed(Exception):
    pass


def decrypt_gcm(key, nonce, ciphertext, aad):
    """Use OpenSSL EVP; never return plaintext unless the GCM tag verifies."""
    if len(key) != 32 or len(nonce) != 12 or len(ciphertext) < 16:
        raise ValueError("Invalid AES-256-GCM input lengths")
    lib = C.CDLL(ctypes.util.find_library("crypto") or "libcrypto.so.3")
    signatures = {
        "EVP_CIPHER_CTX_new": (C.c_void_p, []),
        "EVP_CIPHER_CTX_free": (None, [C.c_void_p]),
        "EVP_aes_256_gcm": (C.c_void_p, []),
        "EVP_DecryptInit_ex": (C.c_int, [C.c_void_p] * 5),
        "EVP_CIPHER_CTX_ctrl": (C.c_int, [C.c_void_p, C.c_int, C.c_int, C.c_void_p]),
        "EVP_DecryptUpdate": (C.c_int, [C.c_void_p, C.c_void_p, C.POINTER(C.c_int), C.c_void_p, C.c_int]),
        "EVP_DecryptFinal_ex": (C.c_int, [C.c_void_p, C.c_void_p, C.POINTER(C.c_int)]),
    }
    for name, (restype, argtypes) in signatures.items():
        fn = getattr(lib, name)
        fn.restype, fn.argtypes = restype, argtypes

    def checked(value):
        if value != 1:
            raise RuntimeError("OpenSSL EVP operation failed")

    ctx = lib.EVP_CIPHER_CTX_new()
    if not ctx:
        raise RuntimeError("OpenSSL context allocation failed")
    out = C.create_string_buffer(len(ciphertext))
    length = C.c_int()
    try:
        checked(lib.EVP_DecryptInit_ex(ctx, lib.EVP_aes_256_gcm(), None, None, None))
        checked(lib.EVP_CIPHER_CTX_ctrl(ctx, 0x9, len(nonce), None))  # SET_IVLEN
        checked(lib.EVP_DecryptInit_ex(ctx, None, None, key, nonce))
        checked(lib.EVP_DecryptUpdate(ctx, None, C.byref(length), aad, len(aad)))
        data, tag = ciphertext[:-16], ciphertext[-16:]
        checked(lib.EVP_DecryptUpdate(ctx, out, C.byref(length), data, len(data)))
        size = length.value
        checked(lib.EVP_CIPHER_CTX_ctrl(ctx, 0x11, len(tag), tag))  # SET_TAG
        if lib.EVP_DecryptFinal_ex(ctx, C.byref(out, size), C.byref(length)) != 1:
            raise AuthenticationFailed("AES-GCM authentication rejected")
        return bytes(out.raw[:size + length.value])
    finally:
        C.memset(out, 0, len(out))
        lib.EVP_CIPHER_CTX_free(ctx)


def main():
    bundle = json.loads(Path("/test/bundle.json").read_text())
    if bundle["format"] != "edge-encrypted-gpu-v1":
        raise ValueError("Unsupported bundle format")
    if bundle["resource"] != "default/model-keys/edge-model-key":
        raise ValueError("Unexpected KBS resource")
    count = bundle["count"]
    if not isinstance(count, int) or not 1 <= count <= 16384:
        raise ValueError("Invalid element count")
    nonce = base64.b64decode(bundle["nonce_b64"], validate=True)
    ciphertext = base64.b64decode(bundle["ciphertext_b64"], validate=True)
    aad = ("edge-encrypted-gpu-v1|" + str(count)).encode()
    if len(ciphertext) != 4 + count * 8 + 16:
        raise ValueError("Unexpected ciphertext size")
    print("ENCRYPTED_GPU_TEST_START", flush=True)
    # The loopback HTTP endpoint is inside this pod's confidential VM.
    # CDH performs the attested KBS exchange using the configured trusted KBS TLS certificate.
    with urllib.request.urlopen("http://127.0.0.1:8006/cdh/resource/" + bundle["resource"], timeout=90) as response:
        encoded_key = response.read(65)
    if len(encoded_key) != 64:
        raise ValueError("Expected a 256-bit key encoded as 64 hex characters")
    key = bytes.fromhex(encoded_key.decode("ascii"))
    print("TRUSTEE_KEY_FETCH_PASS", flush=True)
    checks = {}
    wrong_key = bytes([key[0] ^ 1]) + key[1:]
    tampered = ciphertext[:-1] + bytes([ciphertext[-1] ^ 1])
    for name, candidate_key, candidate_ciphertext in [
        ("wrong_key_rejected", wrong_key, ciphertext),
        ("tampered_ciphertext_rejected", key, tampered),
    ]:
        try:
            decrypt_gcm(candidate_key, nonce, candidate_ciphertext, aad)
        except AuthenticationFailed:
            checks[name] = True
            print(name.upper() + "_PASS", flush=True)
        else:
            raise RuntimeError("Negative authentication test unexpectedly succeeded")
    plaintext = decrypt_gcm(key, nonce, ciphertext, aad)
    del key, encoded_key, wrong_key
    if len(plaintext) != 4 + count * 8 or struct.unpack_from("<I", plaintext)[0] != count:
        raise ValueError("Authenticated payload format mismatch")
    print("AES_256_GCM_DECRYPT_PASS", flush=True)
    # No plaintext file: anonymous pipe from Python memory to the CUDA process.
    result = subprocess.run(["/work/encrypted-vector-add"], input=plaintext,
                            capture_output=True, timeout=120)
    if result.returncode or len(result.stdout) != count * 4:
        raise RuntimeError("CUDA process failed; data and process output withheld")
    a = struct.unpack_from("<" + "I" * count, plaintext, 4)
    b = struct.unpack_from("<" + "I" * count, plaintext, 4 + count * 4)
    expected = struct.pack("<" + "I" * count, *[(x + y) & 0xffffffff for x, y in zip(a, b)])
    if result.stdout != expected:
        raise RuntimeError("GPU result differs from independent CPU calculation")
    digest = hashlib.sha256(result.stdout).hexdigest()
    if digest != bundle["expected_output_sha256"]:
        raise RuntimeError("GPU result differs from producer's expected digest")
    identity = subprocess.check_output([
        "nvidia-smi", "--query-gpu=name,uuid,vbios_version,driver_version", "--format=csv,noheader"
    ], text=True, timeout=30).strip()
    del plaintext, a, b, expected, result
    report = {
        "status": "PASS", "format": bundle["format"], "count": count,
        "ciphertext_sha256": hashlib.sha256(ciphertext).hexdigest(),
        "output_sha256": digest, "gpu": identity, "negative_tests": checks,
        "plaintext_transport": "memory and anonymous pipe; no plaintext file",
    }
    Path("/results/result.json").write_text(json.dumps(report, indent=2) + "\n")
    print("GPU_VECTOR_ADD_MATCH_PASS count=" + str(count), flush=True)
    Path("/results/passed").write_text("PASS\n")
    print("ENCRYPTED_GPU_TEST_PASS", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Neither HTTP response bodies nor decrypted input belong in the report.
        print("ENCRYPTED_GPU_TEST_FAIL type=" + type(error).__name__, flush=True)
        raise SystemExit(1)
