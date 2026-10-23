{{/*
Common labels and names. Every workload gets <release>-<component> so the
in-cluster hostnames read like the compose service names.
*/}}
{{- define "demo.name" -}}
{{- printf "%s-%s" .Release.Name .component | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "demo.labels" -}}
app.kubernetes.io/name: intent-agent-demo
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: {{ .component }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{- define "demo.selector" -}}
app.kubernetes.io/name: intent-agent-demo
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "demo.image" -}}
{{- printf "%s:%s" .repository .tag -}}
{{- end -}}

{{- define "demo.secretEnv" -}}
- name: {{ .name }}
  valueFrom:
    secretKeyRef:
      name: {{ .secret }}
      key: {{ .key }}
{{- end -}}

{{- define "demo.securityContext" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: ["ALL"]
{{- end -}}
