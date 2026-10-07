# Draft sample application validation record

> **DRAFT LAB RECORD — NOT PRODUCTION SECURITY CERTIFICATION.**
> Recorded 7 October 2026 against the running edge-rtx6k deployment. These checks
> exercised existing samples; no sample configuration was changed.

## Observed results

| Sample | Runtime | Result |
| --- | --- | --- |
| `hello-openshift/standard` | Default container runtime | HTTP responded; `/bin/true` through `oc exec` succeeded. |
| `hello-openshift/secure` | `kata-cc` | HTTP responded; exec was denied with `ExecProcessRequest is blocked by policy`. |
| `hello-openshift/insecure-policy` | `kata-cc` | HTTP responded; exec succeeded as expected for the debug policy. |
| `kbs-access/kbs-access-curl` | Confidential sample deployment | HTTP result exactly matched Trustee demo `key3`, compared in memory. |
| `kbs-access/kbs-access-sealed` | Confidential sample deployment | HTTP served the mounted value; it was the upstream literal placeholder, not a sealed secret. |

The invoking credentials passed `oc auth can-i '*' '*' --all-namespaces`.
A fresh CDH request from `insecure-policy` also returned the exact configured
`default/kbsres1/key3` value. Secret values were withheld from test output.

Selected output from the checks:

```text
PASS standard exec allowed (exit 0)
PASS secure exec denied: ExecProcessRequest is blocked by policy
PASS insecure-policy exec allowed (exit 0)
PASS kbs-access-curl: served value exactly matches Trustee demo key3; value withheld
PASS kbs-access-sealed HTTP serves mounted value
Live value is upstream literal placeholder: True
Live value uses sealed-secret encoding: False
PASS fresh in-guest CDH request returned exact Trustee demo key3; value withheld
Administrator wildcard access: yes
```

## Interpretation and limits

The secure sample demonstrates guest-side exec denial even for the tested
administrator. The curl sample demonstrates actual KBS/CDH demo-secret delivery.
The sealed sample demonstrates only serving a normal mounted Kubernetes Secret;
it does not demonstrate cryptographic sealing or attested unsealing. See its
[placeholder template](../../charts/coco-supported/kbs-access-sealed/templates/sealed-secret.yaml).

These are CPU sample tests, not the GPU-required model-key tests and not image
signature-enforcement tests. The curl demo intentionally exposes a demo secret
through a web service; do not use its endpoint for confidential model-key material.
For model-key enforcement see the [separate runbook](../model-key-policy.md).
