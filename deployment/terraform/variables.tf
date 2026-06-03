variable "namespace" {
  description = "Kubernetes namespace for molecule-ranker."
  type        = string
  default     = "molecule-ranker"
}

variable "storage_class_name" {
  description = "StorageClass used for artifact, project, and platform PVCs."
  type        = string
  default     = "standard"
}

variable "artifact_storage_size" {
  description = "Artifact/object storage PVC size."
  type        = string
  default     = "100Gi"
}

variable "platform_storage_size" {
  description = "Platform metadata and worker scratch PVC size."
  type        = string
  default     = "50Gi"
}

variable "project_storage_size" {
  description = "Project workspace PVC size."
  type        = string
  default     = "100Gi"
}

variable "codex_worker_storage_size" {
  description = "Optional isolated Codex worker scratch PVC size."
  type        = string
  default     = "20Gi"
}

variable "secret_manager_auth_secret_ref" {
  description = "External secret-manager reference for the auth-secret key."
  type        = string
}

variable "secret_manager_database_url_ref" {
  description = "External secret-manager reference for the database-url key."
  type        = string
}
