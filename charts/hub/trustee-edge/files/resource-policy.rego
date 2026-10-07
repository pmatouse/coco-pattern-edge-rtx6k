package policy
import rego.v1

# Model keys are a separate resource class; all other resources retain CPU-only
# support. KBS supplies the canonical path as data, not caller-supplied claims.
default allow := false

valid_resource_request if {
    data.plugin == "resource"
    is_array(data["resource-path"])
    count(data["resource-path"]) == 3
    every part in data["resource-path"] { is_string(part); part != "" }
}

in_affirming_range(value) if {
    is_number(value)
    value >= 2
    value <= 31
}

submod_affirming(submod) if {
    submod["ear.status"] == "affirming"
    vector := submod["ear.trustworthiness-vector"]
    in_affirming_range(vector.hardware)
    in_affirming_range(vector.executables)
    in_affirming_range(vector.configuration)
}

all_submods_affirming if {
    is_object(input.submods)
    count(input.submods) > 0
    every _, submod in input.submods { submod_affirming(submod) }
}

# General CPU demos and bootstrap resources still work without a GPU.
allow if {
    valid_resource_request
    data["resource-path"][1] != "model-keys"
    all_submods_affirming
}

# Every tag in default/model-keys requires a verified SNP CPU and NVIDIA GPU.
# Missing fields are undefined and fail closed. AS has already checked SNP
# launch/initdata/TCB references and NVIDIA report signatures and RIMs.
allow if {
    valid_resource_request
    data["resource-path"][0] == "default"
    data["resource-path"][1] == "model-keys"
    all_submods_affirming
    cpu := input.submods.cpu0["ear.veraison.annotated-evidence"].snp
    cpu.policy_debug_allowed == false
    cpu.policy_migrate_ma == false
    is_string(cpu.measurement)
    cpu.measurement != ""
    gpu := input.submods.gpu0["ear.veraison.annotated-evidence"].nvidia
    gpu["x-nvidia-overall-att-result"] == true
    gpu.secboot == true
    gpu.dbgstat == "disabled"
    gpu.measres == "success"
}
