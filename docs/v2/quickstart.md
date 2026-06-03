# V2.0 Quickstart

This quickstart is for internal research teams validating a synthetic or
non-sensitive enterprise deployment. Do not use patient data, real secrets, lab
protocols, synthesis instructions, dosing information, or treatment guidance.

## Enterprise Quickstart

1. Install from the approved internal package or repository checkout.
2. Confirm version and contracts:

   ```bash
   molecule-ranker v2 validate-contracts
   molecule-ranker v2 release-gate
   ```

3. Start a local enterprise environment with synthetic data only.
4. Create an organization, team, admin user, and project.
5. Configure RBAC before importing artifacts or enabling integrations.
6. Run the mocked enterprise golden workflow:

   ```bash
   molecule-ranker validate enterprise-golden
   ```

7. Generate a validation evidence package:

   ```bash
   molecule-ranker validate v2-package --output validation_package/
   ```

## Deployment Quickstart

Use `deployment/docker-compose.enterprise.yml` for a single-node evaluation or
the split server/worker compose file for operational testing. Use Kubernetes or
the Helm-like templates when testing production-like scheduling, resource
limits, secret references, and separated workers.

External integration writes are disabled by default. Codex workers are optional
and must be isolated from unauthorized artifacts.

## Validation Boundaries

The quickstart validates platform behavior, contracts, controls, and synthetic
workflows. It does not validate clinical use and does not prove any molecule is
safe, active, effective, binding, or synthesizable.
