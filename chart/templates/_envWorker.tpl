# SPDX-License-Identifier: Apache-2.0
# Copyright 2022 The HuggingFace Authors.

{{- define "envWorker" -}}
- name: WORKER_CONTENT_MAX_BYTES
  value: {{ .Values.worker.contentMaxBytes | quote}}
- name: WORKER_HEARTBEAT_INTERVAL_SECONDS
  value: {{ .Values.worker.heartbeatIntervalSeconds | quote}}
- name: WORKER_KILL_ZOMBIES_INTERVAL_SECONDS
  value: {{ .Values.worker.killZombiesIntervalSeconds | quote}}
- name: WORKER_MAX_DISK_USAGE_PCT
  value: {{ .Values.worker.maxDiskUsagePct | quote }}
- name: WORKER_MAX_JOB_DURATION_SECONDS
  value: {{ .Values.worker.maxJobDurationSeconds | quote }}
- name: WORKER_MAX_LOAD_PCT
  value: {{ .Values.worker.maxLoadPct | quote }}
- name: WORKER_MAX_MEMORY_PCT
  value: {{ .Values.worker.maxMemoryPct | quote }}
- name: WORKER_MAX_MISSING_HEARTBEATS
  value: {{ .Values.worker.maxMissingHeartbeats | quote }}
- name: WORKER_SLEEP_SECONDS
  value: {{ .Values.worker.sleepSeconds | quote }}
- name: TMPDIR
  value: "/tmp"
  # ^ensure the temporary files are created in /tmp, which is writable
- name: WORKER_STORAGE_PATHS
  value: {{ .Values.assets.storageDirectory | quote }}
# specific to the /first-rows job runner
- name: FIRST_ROWS_MAX_BYTES
  value: {{ .Values.firstRows.maxBytes | quote }}
- name: FIRST_ROWS_MAX_NUMBER
  value: {{ .Values.firstRows.maxNumber | quote }}
- name: FIRST_ROWS_MIN_CELL_BYTES
  value: {{ .Values.firstRows.minCellBytes | quote }}
- name: FIRST_ROWS_MIN_NUMBER
  value: {{ .Values.firstRows.minNumber| quote }}
- name: FIRST_ROWS_COLUMNS_MAX_NUMBER
  value: {{ .Values.firstRows.columnsMaxNumber| quote }}
# specific to 'config-parquet-and-info' job runner
- name: PARQUET_AND_INFO_BLOCKED_DATASETS
  value: {{ .Values.parquetAndInfo.blockedDatasets | quote }}
- name: PARQUET_AND_INFO_COMMIT_MESSAGE
  value: {{ .Values.parquetAndInfo.commitMessage | quote }}
- name: PARQUET_AND_INFO_COMMITTER_HF_TOKEN
  {{- if .Values.secrets.appParquetConverterHfToken.fromSecret }}
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.appParquetConverterHfToken.secretName | quote }}
      key: HF_TOKEN
      optional: false
  {{- else }}
  value: {{ .Values.secrets.appParquetConverterHfToken.value }}
  {{- end }}
- name: PARQUET_AND_INFO_MAX_DATASET_SIZE
  value: {{ .Values.parquetAndInfo.maxDatasetSize | quote }}
- name: PARQUET_AND_INFO_SOURCE_REVISION
  value: {{ .Values.parquetAndInfo.sourceRevision | quote }}
- name: PARQUET_AND_INFO_SUPPORTED_DATASETS
  value: {{ .Values.parquetAndInfo.supportedDatasets | quote }}
- name: PARQUET_AND_INFO_TARGET_REVISION
  value: {{ .Values.parquetAndInfo.targetRevision | quote }}
- name: PARQUET_AND_INFO_URL_TEMPLATE
  value: {{ .Values.parquetAndInfo.urlTemplate | quote }}
# specific to the split-opt-in-out-urls-scan job runner
- name: OPT_IN_OUT_URLS_SCAN_COLUMNS_MAX_NUMBER
  value: {{ .Values.optInOutUrlsScan.columnsMaxNumber | quote }}
- name: OPT_IN_OUT_URLS_SCAN_MAX_CONCURRENT_REQUESTS_NUMBER
  value: {{ .Values.optInOutUrlsScan.maxConcurrentRequestsNumber | quote }}
- name: OPT_IN_OUT_URLS_SCAN_MAX_REQUESTS_PER_SECOND
  value: {{ .Values.optInOutUrlsScan.maxRequestsPerSecond | quote }}
- name: OPT_IN_OUT_URLS_SCAN_ROWS_MAX_NUMBER
  value: {{ .Values.optInOutUrlsScan.rowsMaxNumber | quote }}
- name: OPT_IN_OUT_URLS_SCAN_SPAWNING_TOKEN
  {{- if .Values.secrets.spawningToken.fromSecret }}
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.spawningToken.secretName | quote }}
      key: SPAWNING_TOKEN
      optional: false
  {{- else }}
  value: {{ .Values.secrets.spawningToken.value }}
  {{- end }}
- name: OPT_IN_OUT_URLS_SCAN_URLS_NUMBER_PER_BATCH
  value: {{ .Values.optInOutUrlsScan.urlsNumberPerBatch | quote }}
- name: OPT_IN_OUT_URLS_SCAN_SPAWNING_URL
  value: {{ .Values.optInOutUrlsScan.spawningUrl | quote }}
- name: CONFIG_NAMES_MAX_NUMBER
  value: {{ .Values.configNames.maxNumber | quote }}

{{- end -}}
