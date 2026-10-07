# Draft edge RTX6K engineering runbooks

> **DRAFT — NOT PRODUCTION READY OR VERIFIED AS A COMPLETE CLEAN-CLUSTER REBUILD.**
> These guides record a successful staged lab installation and tests on edge-rtx6k.
> They still need consolidation, independent review and an end-to-end clean-cluster replay.

Recorded 7 October 2026. The server is a Dell PowerEdge XE7745 with AMD EPYC 9555
processors and four RTX PRO 6000 Blackwell Server Edition GPUs, running OpenShift
4.22.15, OpenShift Sandboxed Containers 1.13.0 and Trustee 1.2.0.

## Reading and rebuild order

| Document | Scope and status |
| --- | --- |
| [CPU rebuild guide](cpu-baseline/REBUILD-RUNBOOK.md) | Historical CPU installation at `5f158c3`; detailed clean-cluster starting procedure, not replay-verified. |
| [CPU validation record](cpu-baseline/VALIDATION-RECORD.md) | Tests of the installed CPU baseline; 14 applications at that stage. |
| [GPU readiness assessment](GPU-READINESS.md) | Historical pre-install findings; its unknowns and pending tasks describe that earlier phase. |
| [GPU extension guide](REBUILD-RUNBOOK.md) | Historical GPU extension through `877791d`; follow after the CPU baseline. |
| [GPU validation record](VALIDATION-RECORD.md) | One-GPU CUDA and attestation tests; 16 applications at this stage. |
| [Model-key policy runbook](../model-key-policy.md) | Later implemented CPU-and-GPU requirement for `default/model-keys/edge-model-key`, staging, tests and recovery. |
| [Model-key validation record](../../tests/model-key-policy/validation-2026-10-07.json) | 68 policy cases and live positive/negative checks; this is a recorded result, not a fresh test on opening the file. |
| [Sample application results](SAMPLE-APPLICATIONS.md) | Exec-policy and KBS delivery tests; sealed-secret example remains a literal placeholder. |

The GPU guide stops before the model-key phase. Its statements that mandatory GPU
key authorization is still pending are historical; the linked model-key runbook
records its subsequent implementation. Complete all three phases for the documented
final state. The historical CPU validator expects 14 applications; after adding
GPUs use the GPU bundle's CPU validator with `--expected-apps 16`.

For a new rebuild, use a separate branch at the recorded baseline and preserve the
staging described in the guides. Never reset the active GitOps branch to an old
snapshot. In particular, do not deploy the older permissive resource policy while
model keys remain mounted in KBS.

## What has and has not been validated

The installed environment passed CPU confidential-container tests, a one-GPU CUDA
vector addition, combined CPU/GPU appraisal, model-key retrieval and denial tests,
and secure guest exec denial. CC readiness and allocation were checked for all four
cards; this is not a four-GPU-in-one-VM validation or an exhaustive per-card test.

The clean-cluster procedure has not been destructively rerun. No encrypted-model
loading or inference server has been deployed. Vendor support for the exact
Dell/Turin/RTX combination has not been established by these lab tests. Some
inherited dependency versions float, so the bundle is not a complete offline
mirror or a byte-for-byte lock of the stack.

## Security boundaries and outstanding work

- Trustee and Vault share the workload cluster. Cluster administrators can access
  backing secrets or modify authorization; there is no separate administrative
  trust boundary for production model keys.
- Image acceptance remains `insecureAcceptAnything`. Hardware attestation is
  enforced, but an approved inference application identity is not yet enforced.
- Debug/demo guest policies remain available. Their exec behavior must not be
  confused with the strict guest policy or CPU hardware debug state.
- The `kbs-access-curl` demo intentionally serves a demo secret over a Route and
  uses a privileged guest container. It is not a production secret-serving design;
  do not point it at the protected model-key resource.
- The sealed-secret sample is still a literal Kubernetes Secret placeholder.
- The fork's Super Linter workflow is disabled. Its branch assumption was fixed,
  but formatting/type-check and status-permission failures remain unresolved.
- Remaining handoff work: consolidate historical steps into one current procedure,
  review commands and dependency locks, implement real sealed-secret delivery,
  enforce approved images/workload policy through initdata, separate Trustee/Vault,
  and perform a controlled clean-cluster rebuild before claiming reproducibility.

## Using the accompanying files

The original GPU rebuild bundle is preserved here with its `cpu-baseline/`
subdirectory, helper scripts, public reference material, version snapshots,
recorded logs, source archives and four Helm chart archives. The source archives
are historical snapshots, not the current branch head. Only the guides have
received draft/status framing; bundled helper behavior has not been changed.

Set `GPU_KIT` to the absolute path of this directory and `KIT` to its
`cpu-baseline` subdirectory when following the corresponding guides. Paths,
disk identity, hardware identity and reference values are specific to this server;
review them before using another host. Importing this documentation does not run
its installation commands.

Original ZIP files outside Git remain historical exports. Checksums in this
repository copy have been regenerated after adding status notices. They detect
accidental changes; they are not signed provenance or an independent security audit.

Kubeconfigs, registry credentials, private deploy keys, Vault recovery material
and model keys must be obtained or restored separately. They are not part of this
documentation bundle. The included VCEK certificate is public attestation collateral.
