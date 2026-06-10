from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase" / "migrations" / "0001_product_auth_schema.sql"
RUNS_MIGRATION = ROOT / "supabase" / "migrations" / "0002_product_discovery_runs.sql"


def _sql() -> str:
    return MIGRATION.read_text()


def _runs_sql() -> str:
    return RUNS_MIGRATION.read_text()


def test_supabase_product_auth_migration_exists_with_required_tables() -> None:
    text = _sql()
    expected_tables = [
        "product_profiles",
        "product_organizations",
        "product_memberships",
        "product_projects",
        "product_usage_events",
        "product_feedback",
    ]

    for table in expected_tables:
        assert f"create table if not exists public.{table}" in text
        assert f"alter table public.{table} enable row level security" in text
        assert f"alter table public.{table} force row level security" in text


def test_supabase_product_auth_constraints_and_triggers_are_present() -> None:
    text = _sql()

    assert "role in ('owner', 'admin', 'researcher', 'viewer')" in text
    assert "plan in ('free_internal', 'pilot', 'trial')" in text
    assert "status in ('active', 'suspended', 'archived')" in text
    assert (
        "constraint product_memberships_unique_user_per_org unique (organization_id, user_id)"
        in text
    )
    assert "create or replace function public.set_product_updated_at()" in text

    for table in [
        "product_profiles",
        "product_organizations",
        "product_memberships",
        "product_projects",
    ]:
        assert f"before update on public.{table}" in text


def test_supabase_product_auth_rls_policies_are_tenant_scoped() -> None:
    text = _sql()

    assert "create or replace function public.is_org_member" in text
    assert "create or replace function public.has_org_role" in text
    assert "create or replace function public.can_view_org_member_profile" in text
    assert "create or replace function public.prevent_product_membership_self_escalation" in text
    assert "create or replace function public.prevent_product_project_identity_changes" in text
    assert "auth.uid()" in text
    assert "to authenticated" in text

    for table in [
        "product_profiles",
        "product_organizations",
        "product_memberships",
        "product_projects",
        "product_usage_events",
        "product_feedback",
    ]:
        assert re.search(rf"create policy .+?\s+on public\.{table}", text, flags=re.S), table


def test_supabase_product_auth_policy_comments_explain_each_policy() -> None:
    text = _sql()
    policy_names = re.findall(r'create policy "([^"]+)"', text)

    assert policy_names
    for policy_name in policy_names:
        pattern = rf"-- .+\ncreate policy \"{re.escape(policy_name)}\""
        assert re.search(pattern, text), f"{policy_name} should have an explaining SQL comment"


def test_supabase_product_auth_access_model_is_encoded() -> None:
    text = _sql()

    assert "using (id = auth.uid() or public.can_view_org_member_profile(id))" in text
    assert "grant update (\n  display_name,\n  avatar_url," in text
    assert "grant update (name, slug, status) on public.product_organizations" in text
    assert "grant update (role, status) on public.product_memberships" in text
    assert "public.has_org_role(id, array['owner', 'admin'])" in text
    assert "and role = 'owner'\n      and public.is_org_owner(organization_id)" in text
    assert "users cannot escalate their own product membership role" in text
    assert "created_by_user_id = auth.uid()" in text
    assert "public.has_org_role(organization_id, array['owner', 'admin', 'researcher'])" in text
    assert "public.has_org_role(organization_id, array['owner', 'admin'])" in text
    assert "user_id = auth.uid() and public.is_org_member(organization_id)" in text
    assert "project organization_id and created_by_user_id are immutable" in text


def test_supabase_product_auth_rls_docs_exist() -> None:
    text = (ROOT / "docs" / "product" / "v0_2_rls_policies.md").read_text()

    assert "is_org_member(org_id uuid)" in text
    assert "tenant isolation" in text
    assert "Service role keys can bypass RLS" in text
    assert "must never be used in browser code" in text
    assert "Default CI uses offline mocked tests" in text
    assert "apps/web/tests/tenant-isolation.test.mjs" in text
    assert "Optional Local Supabase Policy Checks" in text
    assert "must not be required in default CI" in text


def test_supabase_product_auth_schema_avoids_forbidden_surfaces() -> None:
    executable_sql = re.sub(r"--.*", "", _sql()).lower()

    forbidden = [
        "stripe",
        "subscription",
        "invoice",
        "payment",
        "patient",
        "phi",
        "hipaa",
        "service_role",
        "api_key",
        "secret",
    ]

    for term in forbidden:
        assert term not in executable_sql


def test_supabase_product_runs_artifacts_migration_exists_with_rls() -> None:
    text = _runs_sql()

    for table in ["product_runs", "product_run_artifacts"]:
        assert f"create table if not exists public.{table}" in text
        assert f"alter table public.{table} enable row level security" in text
        assert f"alter table public.{table} force row level security" in text

    assert "run_type text not null default 'discovery'" in text
    assert "mode text not null default 'dry_run'" in text
    assert "status text not null default 'queued'" in text
    assert "disease_or_goal text not null" in text
    assert "options jsonb not null default '{}'::jsonb" in text
    assert "progress jsonb not null default '{}'::jsonb" in text
    assert "constraint product_runs_run_type_check check (run_type in ('discovery', 'dry_run_discovery', 'mocked_discovery'))" in text
    assert "constraint product_runs_mode_check check (mode in ('mocked', 'dry_run', 'read_only_live'))" in text
    assert (
        "constraint product_runs_status_check check (status in ('queued', 'running', 'succeeded', 'failed', "
        "'partially_succeeded', 'cancelled'))"
        in text
    )
    assert "constraint product_run_artifacts_storage_kind_check check (storage_kind in ('database', 'local_file', 'supabase_storage'))" in text
    assert "constraint product_run_artifacts_run_tenant_fk foreign key (run_id, organization_id, project_id)" in text
    assert "constraint product_run_artifacts_no_cache_storage_path" in text
    assert "constraint product_run_artifacts_no_raw_internal_artifact_types" in text
    assert "public.has_org_role(organization_id, array['owner', 'admin', 'researcher'])" in text
    assert "public.is_org_member(organization_id)" in text
    assert "created_by_user_id = auth.uid()" in text
    assert "run organization_id, project_id, and created_by_user_id are immutable" in text
    assert "admin_only = false\n      or public.has_org_role(organization_id, array['owner', 'admin'])" in text
    assert re.search(r"raw\s+engine\s+internals", text, flags=re.I)
