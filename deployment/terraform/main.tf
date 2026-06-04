terraform {
  required_version = ">= 1.5.0"
  required_providers {
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = ">= 2.25.0"
    }
  }
}

resource "kubernetes_namespace_v1" "molecule_ranker" {
  metadata {
    name = var.namespace
    labels = {
      "app.kubernetes.io/name"    = "molecule-ranker"
      "app.kubernetes.io/version" = "2.3.0"
    }
  }
}

resource "kubernetes_persistent_volume_claim_v1" "artifacts" {
  metadata {
    name      = "molecule-ranker-artifacts"
    namespace = kubernetes_namespace_v1.molecule_ranker.metadata[0].name
  }
  spec {
    access_modes       = ["ReadWriteMany"]
    storage_class_name = var.storage_class_name
    resources {
      requests = {
        storage = var.artifact_storage_size
      }
    }
  }
}

resource "kubernetes_persistent_volume_claim_v1" "storage" {
  metadata {
    name      = "molecule-ranker-storage"
    namespace = kubernetes_namespace_v1.molecule_ranker.metadata[0].name
  }
  spec {
    access_modes       = ["ReadWriteMany"]
    storage_class_name = var.storage_class_name
    resources {
      requests = {
        storage = var.platform_storage_size
      }
    }
  }
}

resource "kubernetes_persistent_volume_claim_v1" "projects" {
  metadata {
    name      = "molecule-ranker-projects"
    namespace = kubernetes_namespace_v1.molecule_ranker.metadata[0].name
  }
  spec {
    access_modes       = ["ReadWriteMany"]
    storage_class_name = var.storage_class_name
    resources {
      requests = {
        storage = var.project_storage_size
      }
    }
  }
}

resource "kubernetes_persistent_volume_claim_v1" "codex_worker_storage" {
  metadata {
    name      = "molecule-ranker-codex-worker-storage"
    namespace = kubernetes_namespace_v1.molecule_ranker.metadata[0].name
  }
  spec {
    access_modes       = ["ReadWriteOnce"]
    storage_class_name = var.storage_class_name
    resources {
      requests = {
        storage = var.codex_worker_storage_size
      }
    }
  }
}

resource "kubernetes_config_map_v1" "secret_reference_contract" {
  metadata {
    name      = "molecule-ranker-secret-reference-contract"
    namespace = kubernetes_namespace_v1.molecule_ranker.metadata[0].name
  }
  data = {
    auth_secret_ref  = var.secret_manager_auth_secret_ref
    database_url_ref = var.secret_manager_database_url_ref
    note             = "Populate molecule-ranker-secrets through an ExternalSecret or approved secret manager controller before applying workloads."
  }
}
