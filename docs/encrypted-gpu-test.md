# Draft encrypted input and confidential GPU test

> **DRAFT ENGINEERING TEST — NOT PRODUCTION HARDENING.**
> This example demonstrates pre-encryption, attested key delivery, authenticated
> decryption inside a confidential VM and CUDA processing of the decrypted input.
> Trustee and Vault remain on the workload cluster, and image/workload identity
> restrictions remain permissive. The full clean-cluster rebuild is not replay-verified.

## What this test does

A trusted producer generates two random arrays of 4,096 unsigned 32-bit integers
and encrypts their binary representation with AES-256-GCM. Only the encrypted
bundle is deployed as a ConfigMap. The consumer runs with `kata-cc-nvidia-gpu`,
one `nvidia.com/pgpu` and the existing secure initdata.

```text
Trusted producer                         Confidential GPU VM
random arrays + lab key                  encrypted bundle only
        |                                        |
        v                                        v
AES-256-GCM encryption                   CDH requests key from Trustee
        |                                        |
        v                                 CPU + GPU appraisal required
ciphertext + nonce + metadata                     |
        |                                        v
        +------------------------------> AES-GCM authentication and decryption
                                                 |
                                          plaintext in guest memory
                                                 |
                                                 v
                                          CUDA vector addition
                                                 |
                                          CPU comparison + producer digest
                                                 |
                                          PASS report, no plaintext/key
```

AES decryption runs on the **guest CPU**. CUDA actually receives the decrypted
arrays, launches a kernel computing `C[i] = A[i] + B[i]`, and returns the result.
The guest compares every result with a CPU calculation and checks the result's
SHA-256 against the producer's expected digest. This ties GPU execution to the
pre-encrypted data; it does not merely run an unrelated CUDA sample afterward.

The key is the existing 256-bit lab key at
`default/model-keys/edge-model-key`. KBS stores it as 64 hex characters, which the
guest decodes to 32 bytes. The VM obtains it only through the in-guest CDH endpoint:

```text
http://127.0.0.1:8006/cdh/resource/default/model-keys/edge-model-key
```

The loopback request is inside the VM. CDH uses the existing attested KBS exchange
and configured KBS TLS certificate. There is no model-key Kubernetes Secret mount,
key command-line argument or key environment variable in the test pod.

## Files and encrypted format

| File | Purpose |
| --- | --- |
| [Producer](../scripts/prepare-encrypted-gpu-test.py) | Generate random input and write only an encrypted JSON bundle. |
| [Guest consumer](../charts/coco-supported/gpu-workload/files/encrypted-file-test/guest.py) | Fetch the key, use OpenSSL EVP AES-GCM, run rejection checks and invoke CUDA. |
| [CUDA source](../charts/coco-supported/gpu-workload/files/encrypted-file-test/vector_add.cu) | Read plaintext arrays from an anonymous pipe and compute on the assigned GPU. |
| [Encrypted fixture](../charts/coco-supported/gpu-workload/files/encrypted-file-test/bundle.json) | Ciphertext, nonce, format, count, resource address and expected output digest. |
| [Helm resources](../charts/coco-supported/gpu-workload/templates/encrypted-file-test.yaml) | ConfigMap, separate Deployment and internal results Service. |
| [Live validator](../scripts/test-encrypted-gpu.py) | Match results to the deployed bundle, check exec denial and confirm CPU-only key denial. |

Plaintext encoding is little-endian: one `uint32` element count, then array A and
array B, each containing that many `uint32` values. AES-GCM uses a fresh random
12-byte nonce for each encryption and a 16-byte authentication tag appended to
the ciphertext. Associated data is the UTF-8 string
`edge-encrypted-gpu-v1|4096` for the default count, binding the format and count.

Only the ciphertext, nonce, metadata and output digest are saved by the producer.
Inputs are random demo data. Never reuse a nonce with the same AES-GCM key.
The helper generates fresh nonces and fresh input on every invocation.

The guest verifies the GCM tag before making plaintext available to CUDA. It first
checks that a changed key and a changed ciphertext tag are rejected. The successful
plaintext then travels from guest memory through an anonymous pipe to the CUDA
process; no plaintext file is created. Only status, device identity and hashes go
into the results volume. Python memory is not claimed to be securely zeroized;
the OpenSSL temporary output buffer is cleared after use.

## Prerequisites

- The documented CPU/GPU baseline and [model-key policy](model-key-policy.md) must
  already work. `model-keys-eso` must be Ready and the lab key must exist.
- A free confidential GPU allocation and enough guest memory for another 32 GiB
  VM. The existing vectorAdd test is separate and remains deployed.
- A trusted producer workstation with Python 3, `cryptography`, `oc`, an authorized
  kubeconfig and write access to the private GitOps branch.
- The pinned verifier image supplies Python, OpenSSL and CUDA 13.1/NVCC. No packages
  are downloaded or installed inside the guest by this test.

The producer deliberately reads the existing lab key using administrator access
to the backing Kubernetes Secret and retains it in process memory. This is a
trusted-producer convenience, **not a claim of key secrecy from cluster admins**.
In a production design the data owner encrypts using its own trusted key store
and provisions the key to a separately administered Trustee.

## Prepare and deploy a new encrypted input

Run from a checkout of this branch on the trusted producer machine. Set
`KUBECONFIG` to an existing protected file; do not copy it into the repository.

```bash
python3 -m venv /path/to/encryption-test-venv
/path/to/encryption-test-venv/bin/pip install cryptography
export KUBECONFIG=/protected/path/cluster.kubeconfig
/path/to/encryption-test-venv/bin/python scripts/prepare-encrypted-gpu-test.py \
  --output charts/coco-supported/gpu-workload/files/encrypted-file-test/bundle.json
```

The command prints the count and ciphertext hash only. It does not create or
rotate the lab key. A ciphertext fixture encrypted under the old key cannot be
used after key rotation or a fresh cluster generating a different key: regenerate
the fixture with the appropriate producer key before testing.

The chart defaults `encryptedFileTest.enabled` to false. Our AMD GPU profile enables
it through the `gpu-workload` application's Helm override. Review that profile
before deploying in another environment.

```bash
helm lint charts/coco-supported/gpu-workload --set encryptedFileTest.enabled=true
helm template gpu-workload charts/coco-supported/gpu-workload \
  --namespace gpu-workload --set encryptedFileTest.enabled=true \
  | oc apply --dry-run=server -n gpu-workload -f -
git add charts/coco-supported/gpu-workload/files/encrypted-file-test/bundle.json
git commit -m "test: refresh encrypted GPU input fixture"
git push origin codex/edge-rtx6k
```

ArgoCD deploys the committed chart. A ConfigMap-content checksum on the pod template
causes new ciphertext or consumer code to roll the test VM. The root application's
normal Vault hook may wait 120 seconds. Guest image pulling can take additional time.

## Validate

```bash
oc get pods -n gpu-workload -l app=gpu-encrypted-file
oc get --raw \
  /api/v1/namespaces/gpu-workload/services/gpu-encrypted-file:8080/proxy/status.txt
python3 scripts/test-encrypted-gpu.py --output /path/to/nonsecret-validation.json
```

Require 2/2 Ready and `ENCRYPTED_GPU_TEST_PASS`. The HTTP sidecar becomes Ready only
after the consumer writes the success marker. The internal Service has no Route;
`publishNotReadyAddresses` allows inspection during compilation and failures.
Guest exec and stream restrictions remain intact, so use this report instead of
weakening guest policy to obtain logs.

The validator checks:

1. The report matches the currently deployed ciphertext and producer result digest.
2. Both wrong-key and tampered-ciphertext authentication checks passed.
3. The test uses the confidential GPU runtime, requests one GPU and is Ready.
4. Arbitrary exec into the GPU guest is rejected.
5. A CPU-only SNP guest cannot retrieve the same decryption key. This requires a
   fresh KBS `PolicyDeny` and HTTP 401; the CDH HTTP 500 wrapper alone is insufficient.

For a repeat with the same encrypted fixture, delete only the dedicated test pod:

```bash
oc delete pod -n gpu-workload -l app=gpu-encrypted-file
```

To release its GPU and guest memory, set this demo's Helm override to false, commit
and push it, then allow ArgoCD to remove its resources. This does not require key
rotation, GPU mode changes or modifications to the existing vectorAdd test.

## Recorded live result

Validated on 7 October 2026 using implementation commit
`7e9f6ec38d550d96e0590db9e2178b91c437b46f`. The [machine-readable record](../tests/encrypted-gpu/validation-2026-10-07.json)
contains the ciphertext/result hashes, exact pod and device identity, and denial outcomes.
The GPU was an RTX PRO 6000 Blackwell Server Edition, driver 595.58.03,
VBIOS 98.02.8D.00.01. Both test containers became Ready with zero restarts.

Actual guest result output:

```text
ENCRYPTED_GPU_BUILD_START
ENCRYPTED_GPU_BUILD_PASS
ENCRYPTED_GPU_TEST_START
TRUSTEE_KEY_FETCH_PASS
WRONG_KEY_REJECTED_PASS
TAMPERED_CIPHERTEXT_REJECTED_PASS
AES_256_GCM_DECRYPT_PASS
GPU_VECTOR_ADD_MATCH_PASS count=4096
ENCRYPTED_GPU_TEST_PASS
```

The external validator also confirmed secure exec denial and CPU-only key denial
with a fresh KBS `PolicyDeny` / HTTP 401 (CDH returned its HTTP 500 wrapper).
The guest wrong-key test and tampered-tag test failed authentication as intended.
Local OpenSSL-versus-cryptography interoperability checks covered four plaintext
sizes, including the deployed payload size, and twelve rejection cases across
wrong keys, changed tags and changed associated data. Run them with:

```bash
python3 tests/encrypted-gpu/test_crypto.py
```

The initial pod setup had a transient CDH Secure Mount failure before the init
container started; kubelet retried and the same pod subsequently passed. No guest
policy, trusted measurement, GPU mode or key-release requirement was relaxed.

## Interpretation and remaining boundaries

This example demonstrates authenticated encrypted-data consumption after the
existing CPU-and-GPU key-release gate. VM startup still happens before attestation.
The negative corruption checks run inside the guest; they do not damage hardware
or alter trusted measurements. The CPU-only denial test checks authorization for
the actual decryption key, not just whether a resource is missing.

The test is not a model server, an encrypted container image, a benchmark or a
production workload-identity policy. The test code and expected digest arrive via
a lab ConfigMap; they are not yet bound to an approved workload identity against
a malicious cluster administrator. A malicious authorized workload could exfiltrate
the key or plaintext. See the [draft documentation index](edge-rtx6k/README.md) for
the co-located Trustee/Vault and permissive image-policy limitations.

References: [CoCo secret retrieval](https://confidentialcontainers.org/docs/features/get-resource/)
and [cryptography AESGCM](https://cryptography.io/en/latest/hazmat/primitives/aead/#cryptography.hazmat.primitives.ciphers.aead.AESGCM).
