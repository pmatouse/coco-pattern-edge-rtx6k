# Encrypted input confidential GPU test

> **Draft lab testcase.** Validated on edge-rtx6k; not a production-hardened
> deployment or proof of isolation from this cluster's administrators.

This test encrypts two arrays of 4,096 integers outside a confidential VM, obtains
the decryption key from Trustee inside the VM, and runs CUDA vector addition on
the decrypted arrays. It verifies every result and the producer's expected hash.

## Files

| File | Purpose |
| --- | --- |
| [bundle.json](bundle.json) | Already encrypted input committed to Git. Contains AES-256-GCM ciphertext and public metadata; no encryption key or plaintext arrays. |
| [guest.py](guest.py) | Requests the key through CDH, authenticates/decrypts the input in guest memory, checks rejection cases and invokes CUDA. |
| [vector_add.cu](vector_add.cu) | Reads the decrypted arrays through an anonymous pipe and computes `C[i] = A[i] + B[i]` on the assigned GPU. |

## Encryption and deployment

The [producer script](../../../../../scripts/prepare-encrypted-gpu-test.py) runs
on a trusted administration machine. In this lab it uses the administrator's
kubeconfig to read `trustee-operator-system/model-keys`, field `edge-model-key`.
External Secrets Operator populates that Secret from Vault. The producer decodes
the 64-character hex value into a 32-byte AES key and encrypts randomly generated
input using AES-256-GCM with a fresh 12-byte nonce.

Only the encrypted bundle is written to disk. Its readable metadata includes the
nonce, format, count, Trustee resource address and expected output SHA-256.
`ciphertext_b64` is the encrypted payload plus authentication tag, encoded as Base64.
Base64 is the storage encoding; AES-GCM supplies encryption and authentication.

```text
Trusted producer -> encrypted bundle.json -> Git -> ArgoCD/Helm
                                                   |
                                                   v
                                  ConfigMap mounted at /test in GPU CVM
                                                   |
                                 CDH requests key from Trustee
                                                   |
                                 CPU + GPU appraisal and key policy
                                                   |
                                 AES-GCM decryption in guest memory
                                                   |
                                 CUDA addition -> verified result hash
```

The [Helm template](../../templates/encrypted-file-test.yaml) creates the ConfigMap,
the `gpu-encrypted-file` Deployment and an internal results Service. The pod uses
`kata-cc-nvidia-gpu`, one `nvidia.com/pgpu` and the existing secure initdata.
This README is excluded from Helm's file bundle through `.helmignore`.

The consumer retrieves the key through **Confidential Data Hub (CDH)**, a guest
component inside the VM, using:

```text
http://127.0.0.1:8006/cdh/resource/default/model-keys/edge-model-key
```

Trustee authorizes release using the existing CPU-and-GPU attestation policy.
The consumer has no mount of the backing model-key Secret. Key and plaintext stay
in guest process memory; the plaintext reaches the CUDA program through a pipe.
AES decryption runs on the guest CPU, and vector addition runs on the GPU.
The result endpoint contains status, GPU identity and hashes, not the key or arrays.

## Regenerate and run

The CPU/GPU baseline, model-key policy and key must already be installed. Run from
the repository root with `oc` configured and Python's `cryptography` package available:

```bash
export KUBECONFIG=/protected/path/cluster.kubeconfig
python3 scripts/prepare-encrypted-gpu-test.py \
  --output charts/coco-supported/gpu-workload/files/encrypted-file-test/bundle.json
git add charts/coco-supported/gpu-workload/files/encrypted-file-test/bundle.json
git commit -m "test: refresh encrypted GPU input fixture"
git push origin codex/edge-rtx6k
```

Our AMD GPU profile enables `encryptedFileTest.enabled`; the chart's default is
false. ArgoCD deploys the committed input and rolls the test pod when its input or
consumer code changes. If a fresh cluster generates a different key, or the key is
rotated, regenerate the bundle. The committed ciphertext requires the original key.

Check the result without enabling guest exec or log streaming:

```bash
oc get pods -n gpu-workload -l app=gpu-encrypted-file
oc get --raw \
  /api/v1/namespaces/gpu-workload/services/gpu-encrypted-file:8080/proxy/status.txt
python3 scripts/test-encrypted-gpu.py
```

## Recorded result and limits

The test passed on 7 October 2026 with an RTX PRO 6000 Blackwell Server Edition,
driver 595.58.03 and VBIOS 98.02.8D.00.01:

```text
TRUSTEE_KEY_FETCH_PASS
WRONG_KEY_REJECTED_PASS
TAMPERED_CIPHERTEXT_REJECTED_PASS
AES_256_GCM_DECRYPT_PASS
GPU_VECTOR_ADD_MATCH_PASS count=4096
ENCRYPTED_GPU_TEST_PASS
```

The external validator also confirmed that a CPU-only SNP guest was denied the
same key by KBS with HTTP 401, and arbitrary exec into the GPU guest remained blocked.

Trustee and Vault are co-located with workloads in this lab, and image/workload
acceptance remains permissive. The producer's administrator access is deliberately
trusted. This proves the tested encrypted-data and attested key-delivery flow; it
does not establish secrecy from a malicious cluster administrator or restrict keys
to a production-approved application. No AI model or inference server is involved.

See the [full runbook](../../../../../docs/encrypted-gpu-test.md),
[recorded validation](../../../../../tests/encrypted-gpu/validation-2026-10-07.json)
and [model-key policy](../../../../../docs/model-key-policy.md) for details.
