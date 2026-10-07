# Encrypted Qwen inference on edge-rtx6k

> Draft lab runbook. The model producer and Trustee currently share the workload
> cluster's administrative trust boundary. This is not a production isolation claim.

This example extends the [encrypted GPU input test](encrypted-gpu-test.md) to a
real language model. The OpenShift integrated registry stores encrypted Qwen
weights. A confidential GPU VM fetches the ciphertext, retrieves a dedicated key
through CDH after CPU and GPU attestation, authenticates and decrypts the files in
guest memory, and launches vLLM from that local model directory.

## Components and pinned inputs

| Component | Selection |
| --- | --- |
| Model | `Qwen/Qwen3-0.6B`, revision `c1899de289a04d12100db370d81485cdf75e47ca` |
| Weights | `model.safetensors`, 1,503,300,328 bytes |
| Weights SHA-256 | `f47f71177f32bcd101b7573ec9171e6a57f4f4d31148d38e382306f42996874b` |
| vLLM image | `nvcr.io/nvidia/vllm:25.09-py3`, pinned by digest in chart values |
| Runtime | `kata-cc-nvidia-gpu` / AMD SEV-SNP |
| GPU request | One `nvidia.com/pgpu`, RTX PRO 6000 Blackwell Server Edition |
| Guest sizing | 8 vCPUs, 64 GiB RAM; accommodates the ~23 GB unpacked vLLM image |
| Key resource | `default/model-keys/qwen3-06b` |
| Vault source | `secret/hub/modelKeys`, field `qwen3-06b` |
| Registry | `image-registry.openshift-image-registry.svc:5000` |
| Artifact repository | `gpu-workload/encrypted-qwen3-06b` |
| Registry storage | 50 GiB RWO PVC `encrypted-model-registry`, storage class `lvms-vg1` |
| Service | `encrypted-qwen.gpu-workload.svc:8000` |

Qwen's [official model card](https://huggingface.co/Qwen/Qwen3-0.6B) documents the
Apache-2.0 model and vLLM support. NVIDIA's [25.09 release notes](https://docs.nvidia.com/deeplearning/frameworks/vllm-release-notes/rel-25-09.html)
list RTX PRO 6000 Blackwell Server Edition functional support. Successful local
validation must still be established for this exact confidential runtime.

## What is encrypted and where it lives

The trusted producer downloads eight files from the pinned Hugging Face revision:
weights, configuration, tokenizer files, and license. It checks the published Git
blob IDs or LFS SHA-256 values before encryption. Each file receives a separate
random 96-bit nonce under a 256-bit AES-GCM key. Authenticated associated data binds
the format, model identity, revision, key resource and filename.

The registry image is a data artifact, not an executable image. Its single layer
contains `bundle.json` and eight `.enc` files. The JSON holds public metadata,
nonces, authentication tags, file hashes and the KBS resource name. It contains no
key or plaintext model files. The artifact uses Docker schema 2 for compatibility
with the integrated registry. TLS verification uses OpenShift's injected service
CA. Registry authentication uses the pod's service-account token; it is distinct
from the model decryption key.

The guest pins the registry manifest by SHA-256, verifies the layer digest, and
validates the expected model, revision and filenames. It retrieves the model key
from `http://127.0.0.1:8006/cdh/resource/default/model-keys/qwen3-06b`. CDH is the
CoCo component inside the VM; Trustee/KBS is the remote service making the release
decision. The existing [model-key policy](model-key-policy.md) covers every tag in
`default/model-keys`, including this new key. CPU-only guests cannot obtain it.

Plaintext is written only to a staging directory on `/private`, a `medium: Memory`
emptyDir inside the guest. Startup verifies that it is tmpfs. A file remains in
private staging until its GCM tag and plaintext hash pass; the complete directory
is renamed to `/private/model` only after all files pass. Authentication failures
remove staging files and prevent vLLM startup. Decryption happens on the guest CPU;
vLLM then loads the authenticated weights onto the confidential GPU.

Home, temporary files and caches also use `/private`. `/dev/shm` is guest tmpfs.
Hugging Face and Transformers are explicitly offline at inference time, and vLLM
receives a local model directory, so missing weights cannot trigger a public-model
download fallback. No model-key Secret is mounted into the inference pod.

## Recreate after installing the base pattern

First follow the [edge rebuild guides](edge-rtx6k/README.md), including GPU CC mode,
SNP reference values, secure initdata and the CPU+GPU model-key policy. One free GPU
and enough host storage for the approximately 23 GB unpacked vLLM runtime image are
required. The encrypted model artifact is approximately 1.52 GB.

On a fresh cluster, keep `inference.enabled: false` in
`overrides/values-encrypted-qwen.yaml` until a new artifact has been produced.
The recorded registry digest belongs to this cluster/key pair and does not create
an artifact or a key on another cluster.

1. Commit the AMD SNP GPU profile with the `internal-registry` and
   `encrypted-inference` applications. The registry chart enables the existing
   OpenShift registry operator using a persistent PVC, one replica, and Recreate
   rollout. It creates no external registry route. The inference chart initially
   supplies only the trusted producer's support resources and ImageStream.

   ```sh
   export KUBECONFIG=/protected/path/cluster.kubeconfig
   oc rollout status -n openshift-image-registry deployment/image-registry
   oc get pvc encrypted-model-registry -n openshift-image-registry
   ```

2. Provision the dedicated key without rotating existing entries:

   ```sh
   python3 scripts/provision-qwen-key.py
   ```

   This administrative operation uses the existing Vault bootstrap credentials
   without printing them. ESO materializes `qwen3-06b` in the existing
   `trustee-operator-system/model-keys` Secret, which KBS already mounts. Allow the
   ESO refresh to complete. Check only the field name, never print its value:

   ```sh
   oc get secret model-keys -n trustee-operator-system \
     -o go-template='{{range $k,$v := .data}}{{$k}}{{"\n"}}{{end}}'
   ```

   Trustee 1.2's `secret-converter` init container copies resource Secret entries
   into KBS's memory-backed repository at pod startup. After adding a new key,
   recreate the KBS pod **after** ESO has synchronized the Secret:

   ```sh
   oc delete pod -n trustee-operator-system -l app=kbs
   oc wait pod -n trustee-operator-system -l app=kbs --for=condition=Ready --timeout=180s
   ```

   This briefly interrupts KBS service. A Deployment `rollout restart` is not
   reliable here: the Trustee operator removes the restart annotation. Merely
   seeing the new field in the Kubernetes Secret does not establish that the
   running KBS repository contains it.

3. Render and start the one-time producer job. Run this from the repository root:

   ```sh
   helm template encrypted-inference charts/coco-supported/encrypted-inference \
     --namespace gpu-workload --set producer.enabled=true \
     --show-only templates/producer-job.yaml | oc apply -f -
   oc wait -n trustee-operator-system job/prepare-encrypted-qwen \
     --for=condition=complete --timeout=3600s
   oc logs -n trustee-operator-system job/prepare-encrypted-qwen
   ```

   The producer runs as a restricted ordinary pod, without a GPU, in the Trustee
   namespace. It mounts only the dedicated `qwen3-06b` key field. Public source
   files use a memory-backed volume and are deleted after encryption; disk-backed
   scratch contains ciphertext. `ENCRYPTED_MODEL_PUBLISHED` reports the registry
   manifest digest and nonsecret provenance. Preserve that record, then remove the
   producer and its key mount:

   ```sh
   oc delete job prepare-encrypted-qwen -n trustee-operator-system
   ```

4. Set `inference.artifactDigest` to the producer's new `sha256:...` result and
   `inference.enabled: true` and `inference.replicas: 1` in
   `overrides/values-encrypted-qwen.yaml`. Commit and
   push. Argo CD deploys the confidential workload. A new key requires a newly
   encrypted artifact; the old ciphertext will fail authentication with it.

   ```sh
   oc rollout status -n gpu-workload deployment/encrypted-qwen --timeout=1800s
   ```

5. Read the deliberately nonsecret status endpoint and run the live tests:

   ```sh
   oc get --raw \
     /api/v1/namespaces/gpu-workload/services/encrypted-qwen-status:8080/proxy/startup.log
   python3 scripts/test-encrypted-inference.py --output /tmp/qwen-validation.json
   ```

   Secure guest policy blocks `oc exec` and host log streaming. The results sidecar
   exposes startup diagnostics and hashes, not model files or keys. vLLM request
   logging is disabled. There is no external inference route.

## Inference request

Use the authenticated Kubernetes service proxy:

```sh
cat <<'JSON' | oc create --raw \
  /api/v1/namespaces/gpu-workload/services/encrypted-qwen:8000/proxy/v1/chat/completions \
  -f -
{
  "model": "qwen3-0.6b-encrypted",
  "messages": [{"role": "user", "content": "What is 2 + 2? Answer with only the digit."}],
  "temperature": 0,
  "max_tokens": 32,
  "chat_template_kwargs": {"enable_thinking": false}
}
JSON
```

The test expects answer `4`, checks the producer's weights hash and configured
artifact digest, confirms CUDA execution and secure exec denial, and requests the
same key from the CPU-only `hello-openshift/insecure-policy` guest. It requires a
fresh KBS `PolicyDeny` / HTTP 401 record for that CPU-only request. A CDH HTTP 500
alone is insufficient proof of a policy denial.

`tests/encrypted-inference/test_crypto.py` independently cross-checks the streaming
OpenSSL implementation against Python cryptography AESGCM, including chunk
boundaries, empty input, wrong keys, wrong associated data and modified tags.
Guest startup also exercises wrong-key and modified-ciphertext rejection using the actual
encrypted model's configuration file before decrypting the complete model.

During initial deployment, two idle GPUs had missing PCI BAR addresses and then
fatal PCIe errors when assigned to a VM. Restoring BARs recovered host access but
did not resolve guest initialization. Those two cards were quarantined from VFIO
allocation; see the [hardware incident record](edge-rtx6k/GPU-BAR-RECOVERY.md).
The earlier vector-add deployment is scaled to zero to release its previously
validated GPU for Qwen; the encrypted-array deployment stays running. No
attestation policy was relaxed.

## Boundaries and limitations

- Qwen is a public model used to demonstrate the workflow; this test does not make
  the original public weights secret.
- The trusted producer needs plaintext and key access. In this lab it runs on the
  same cluster as Trustee. A real model owner would encrypt in its own trusted
  environment and use separately administered Trustee/Vault infrastructure.
- The key is stored in Vault and the ESO-managed KBS backing Secret. Cluster
  administrators can access those; it is not a Vault-only or administrator-proof
  deployment.
- The current guest image policy is permissive and debug initdata remains approved.
  Key release is not yet bound to a narrowly approved vLLM application identity.
  Digest pinning detects artifact changes but does not replace workload attestation
  or protection against a malicious deployment administrator.
- Attestation gates key release, not VM boot. A running workload already holding
  its weights is not retroactively revoked by later changing Trustee policy.
- This example protects model delivery and confidential computation. The internal
  inference HTTP service does not protect prompts/responses from cluster network
  administrators. An external confidential API needs TLS termination and an
  authenticated identity inside the guest.
- Python/OpenSSL cleanup is best effort; the code does not claim complete key or
  plaintext zeroization. Guest teardown removes the memory-backed model volume.
- Registry PVC deletion is deliberately excluded from automatic Argo pruning and
  application deletion, to preserve encrypted artifacts. Back it up alongside the
  separately protected key if the exact artifact must survive cluster replacement.

The 32 GiB guest failed unpacking the vLLM image with `No space left on device` in the guest filesystem. The chart therefore allocates 64 GiB; host image caching does not avoid the separate guest image pull/unpack.
