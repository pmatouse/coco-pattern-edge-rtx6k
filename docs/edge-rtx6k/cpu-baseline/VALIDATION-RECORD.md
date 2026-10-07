# Documentation validation record

> **DRAFT ENGINEERING RUNBOOK / LAB RECORD — NOT PRODUCTION READY.**
> Historical CPU-only snapshot at `5f158c3`, recorded 7 October 2026. The staged installation was tested; the complete clean-cluster rebuild procedure has not been rerun. These guides have not completed a handoff review. Read the [documentation status and sequence](../README.md) first. The later [model-key policy phase](../../model-key-policy.md) supersedes statements here that GPU-required key release remains future work. Do not roll back the policy on an existing cluster while protected model keys remain mounted.

Checked on 2026-10-07 09:22 UTC. These checks inspected the existing cluster; they did not reinstall it or change its configuration.

| Check | Result |
| --- | --- |
| Six bundled Python scripts | Syntax passed |
| All 38 Bash blocks in the runbook | `bash -n` passed |
| Documented clustergroup Helm render | Passed; 86 objects |
| Documented storage Helm render | Passed; 5 objects; one approved disk path |
| OSC/Trustee rendered subscriptions | Manual approval; exact 1.13.0 / 1.2.0 starting CSVs |
| Approval helper without `--apply` | Newer 1.13.1 / 1.2.1 plans correctly reported NOT APPROVED |
| Argo applications, final check | All 14 Synced and Healthy |
| Cluster operators | Available and not Degraded |
| Kata | One node ready; `kata-cc` handler `kata-snp` |
| Debug workload `/bin/true` exec | Allowed |
| Secure workload `/bin/true` exec | Denied by guest policy |
| Confidential workload secret retrieval | Exact byte match with configured KBS test secret; value not printed |
| Bundled reference JSON | 32 unique, correctly sized launch measurements |
| Document local links and fenced code blocks | Passed |
| Credential-pattern scan | No private-key blocks, GitHub token patterns, or Slack token patterns found in 868 files/archive members |

An earlier check found the parent Argo application `coco-pattern-baremetal` Healthy but OutOfSync. The final check passed without a refresh or configuration mutation during documentation validation. This intermittent status and its inspection procedure are covered in the runbook. The earlier version snapshot retains its original observation.

The complete clean-cluster rebuild has **not** been destructively retested. Mutating helper scripts were syntax checked and reviewed against the successful installation procedure; they were not rerun merely to validate documentation. Installer-container commands were not rerun after the local Podman failure. The bundle does not include a full image/catalog mirror or a retained digest for the original mutable utility image.
