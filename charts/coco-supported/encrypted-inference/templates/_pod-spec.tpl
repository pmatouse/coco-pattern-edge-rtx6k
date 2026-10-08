{{- define "encrypted-inference.podSpec" -}}
serviceAccountName: encrypted-inference
runtimeClassName: {{ .Values.inference.runtimeClassName }}
initContainers:
  - name: prepare-private-storage
    image: {{ .Values.resultsImage | quote }}
    command: [/bin/sh, -ec, 'chown 1001:0 /private /results /dev/shm; chmod 0700 /private /results; chmod 1777 /dev/shm']
    securityContext:
      runAsUser: 0
    volumeMounts:
      - {name: private, mountPath: /private}
      - {name: results, mountPath: /results}
      - {name: shm, mountPath: /dev/shm}
containers:
  - name: {{ .containerName }}
    image: {{ .Values.inference.image | quote }}
    command: [/bin/bash, -ec]
    args:
      - 'exec > /results/startup.log 2>&1; exec python3 /scripts/guest.py'
    securityContext:
      runAsUser: 1001
      allowPrivilegeEscalation: false
    env:
      # Use the injected guest driver, not the image's older compatibility driver.
      - {name: LD_LIBRARY_PATH, value: /usr/lib64:/usr/local/lib/python3.12/dist-packages/torch/lib:/usr/local/lib/python3.12/dist-packages/torch_tensorrt/lib:/usr/local/nvidia/lib:/usr/local/nvidia/lib64}
      - {name: PYTHONDONTWRITEBYTECODE, value: "1"}
      - {name: HOME, value: /private/home}
      - {name: TMPDIR, value: /private/tmp}
      - {name: XDG_CACHE_HOME, value: /private/cache}
      - {name: HF_HOME, value: /private/cache/huggingface}
      - {name: HF_HUB_OFFLINE, value: "1"}
      - {name: TRANSFORMERS_OFFLINE, value: "1"}
      - {name: VLLM_NO_USAGE_STATS, value: "1"}
      - {name: DO_NOT_TRACK, value: "1"}
      - {name: OMP_NUM_THREADS, value: "4"}
      - name: REGISTRY_HOST
        value: {{ .Values.registry.host | quote }}
      - name: REGISTRY_REPOSITORY
        value: {{ .Values.registry.repository | quote }}
      - {name: REGISTRY_CA, value: /registry-ca/service-ca.crt}
      - name: ARTIFACT_DIGEST
        value: {{ required "inference.artifactDigest must pin the encrypted artifact" .Values.inference.artifactDigest | quote }}
      - name: SERVED_MODEL_NAME
        value: {{ .Values.inference.servedModelName | quote }}
    resources:
      requests:
        cpu: {{ .Values.inference.cpus | quote }}
        memory: {{ .Values.inference.memory | quote }}
      limits:
        cpu: {{ .Values.inference.cpus | quote }}
        memory: {{ .Values.inference.memory | quote }}
        nvidia.com/pgpu: 1
    ports:
      - {name: inference, containerPort: 8000}
    readinessProbe:
      httpGet: {path: /health, port: 8000}
      periodSeconds: 10
      timeoutSeconds: 3
    volumeMounts:
      - {name: scripts, mountPath: /scripts, readOnly: true}
      - {name: registry-ca, mountPath: /registry-ca, readOnly: true}
      - {name: private, mountPath: /private}
      - {name: results, mountPath: /results}
      - {name: shm, mountPath: /dev/shm}
      - {name: initdata, mountPath: /opt/confidential-containers/initdata, readOnly: true}
  - name: results
    # Secure agent policy denies stdout streaming; avoid filling its unread pipe.
    command: [/bin/bash, -ec]
    args: ['exec container-entrypoint /usr/bin/run-httpd > /dev/null 2>&1']
    image: {{ .Values.resultsImage | quote }}
    securityContext:
      runAsUser: 1001
    ports:
      - {name: results, containerPort: 8080}
    volumeMounts:
      - {name: results, mountPath: /var/www/html, readOnly: true}
volumes:
  - name: scripts
    configMap: {name: encrypted-inference-scripts}
  - name: registry-ca
    configMap: {name: inference-registry-ca}
  - name: initdata
    configMap: {name: initdata}
  - name: private
    emptyDir: {medium: Memory, sizeLimit: 12Gi}
  - name: shm
    emptyDir: {medium: Memory, sizeLimit: 4Gi}
  - name: results
    emptyDir: {}
{{- end -}}
