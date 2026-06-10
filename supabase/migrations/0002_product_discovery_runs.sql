-- Product V0.3 discovery runs.
-- Policy intent: store only product-safe run state and redacted result
-- artifacts. Raw engine internals, raw transcripts, raw trace logs, secrets,
-- cache paths, clinical claims, lab protocols, synthesis instructions, dosing
-- guidance, and patient treatment guidance must not be stored here.

create table if not exists public.product_runs (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.product_organizations(id) on delete cascade,
  project_id uuid not null references public.product_projects(id) on delete cascade,
  created_by_user_id uuid references auth.users(id),
  run_type text not null default 'discovery',
  mode text not null default 'dry_run',
  status text not null default 'queued',
  disease_or_goal text not null,
  target_focus text,
  options jsonb not null default '{}'::jsonb,
  progress jsonb not null default '{}'::jsonb,
  result_summary jsonb not null default '{}'::jsonb,
  error_summary text,
  started_at timestamptz,
  completed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint product_runs_run_type_check check (run_type in ('discovery', 'dry_run_discovery', 'mocked_discovery')),
  constraint product_runs_mode_check check (mode in ('mocked', 'dry_run', 'read_only_live')),
  constraint product_runs_status_check check (status in ('queued', 'running', 'succeeded', 'failed', 'partially_succeeded', 'cancelled')),
  constraint product_runs_disease_or_goal_not_blank check (length(btrim(disease_or_goal)) > 0),
  constraint product_runs_options_object check (jsonb_typeof(options) = 'object'),
  constraint product_runs_progress_object check (jsonb_typeof(progress) = 'object'),
  constraint product_runs_result_summary_object check (jsonb_typeof(result_summary) = 'object'),
  constraint product_runs_tenant_identity_unique unique (id, organization_id, project_id)
);

create table if not exists public.product_run_artifacts (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.product_organizations(id) on delete cascade,
  project_id uuid not null references public.product_projects(id) on delete cascade,
  run_id uuid not null references public.product_runs(id) on delete cascade,
  artifact_type text not null,
  storage_kind text not null default 'database',
  storage_path text,
  content_json jsonb,
  content_text text,
  sha256 text,
  size_bytes integer,
  public_to_user boolean not null default true,
  admin_only boolean not null default false,
  created_at timestamptz not null default now(),
  metadata jsonb not null default '{}'::jsonb,
  constraint product_run_artifacts_type_not_blank check (length(btrim(artifact_type)) > 0),
  constraint product_run_artifacts_storage_kind_check check (storage_kind in ('database', 'local_file', 'supabase_storage')),
  constraint product_run_artifacts_content_json_object check (content_json is null or jsonb_typeof(content_json) = 'object'),
  constraint product_run_artifacts_metadata_object check (jsonb_typeof(metadata) = 'object'),
  constraint product_run_artifacts_size_bytes_non_negative check (size_bytes is null or size_bytes >= 0),
  constraint product_run_artifacts_sha256_hex check (sha256 is null or sha256 ~ '^[a-f0-9]{64}$'),
  constraint product_run_artifacts_no_cache_storage_path check (
    storage_path is null or storage_path !~* '(^|/)(\\.cache|cache)(/|$)'
  ),
  constraint product_run_artifacts_no_raw_internal_artifact_types check (
    artifact_type !~* '(raw|secret|transcript|log)'
  ),
  constraint product_run_artifacts_run_tenant_fk foreign key (run_id, organization_id, project_id)
    references public.product_runs(id, organization_id, project_id) on delete cascade
);

create index if not exists product_runs_organization_id_idx
  on public.product_runs (organization_id);

create index if not exists product_runs_project_id_idx
  on public.product_runs (project_id);

create index if not exists product_runs_status_idx
  on public.product_runs (status);

create index if not exists product_runs_created_at_idx
  on public.product_runs (created_at desc);

create index if not exists product_run_artifacts_organization_id_idx
  on public.product_run_artifacts (organization_id);

create index if not exists product_run_artifacts_project_id_idx
  on public.product_run_artifacts (project_id);

create index if not exists product_run_artifacts_run_id_idx
  on public.product_run_artifacts (run_id);

create index if not exists product_run_artifacts_created_at_idx
  on public.product_run_artifacts (created_at desc);

drop trigger if exists set_product_runs_updated_at on public.product_runs;
create trigger set_product_runs_updated_at
  before update on public.product_runs
  for each row execute function public.set_product_updated_at();

create or replace function public.prevent_product_run_identity_changes()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if new.organization_id <> old.organization_id
    or new.project_id <> old.project_id
    or new.created_by_user_id is distinct from old.created_by_user_id then
    raise exception 'run organization_id, project_id, and created_by_user_id are immutable';
  end if;

  return new;
end;
$$;

drop trigger if exists prevent_product_run_identity_changes on public.product_runs;
create trigger prevent_product_run_identity_changes
  before update on public.product_runs
  for each row execute function public.prevent_product_run_identity_changes();

alter table public.product_runs enable row level security;
alter table public.product_run_artifacts enable row level security;

alter table public.product_runs force row level security;
alter table public.product_run_artifacts force row level security;

revoke all on table public.product_runs from anon, authenticated;
revoke all on table public.product_run_artifacts from anon, authenticated;

grant select, insert on table public.product_runs to authenticated;
grant update (
  run_type,
  mode,
  status,
  options,
  progress,
  result_summary,
  error_summary,
  started_at,
  completed_at,
  updated_at
) on public.product_runs to authenticated;

grant select, insert on table public.product_run_artifacts to authenticated;
grant update (
  artifact_type,
  storage_kind,
  storage_path,
  content_json,
  content_text,
  sha256,
  size_bytes,
  public_to_user,
  admin_only,
  metadata
) on public.product_run_artifacts to authenticated;

-- product_runs select: active organization members can read run state only
-- inside their organization. The organization/project predicates prevent
-- cross-org run discovery through direct table access.
create policy "runs_select_for_org_members"
  on public.product_runs
  for select
  to authenticated
  using (public.is_org_member(organization_id));

-- product_runs insert: only researchers, admins, and owners can create a
-- bounded discovery run for a project in their organization. Viewers are
-- intentionally excluded from this role check.
create policy "runs_insert_for_research_roles"
  on public.product_runs
  for insert
  to authenticated
  with check (
    created_by_user_id = auth.uid()
    and public.has_org_role(organization_id, array['owner', 'admin', 'researcher'])
    and exists (
      select 1
      from public.product_projects project
      where project.id = product_runs.project_id
        and project.organization_id = product_runs.organization_id
    )
  );

-- product_runs update: normal users cannot mutate arbitrary run state.
-- Owners/admins can repair or finalize status. The synchronous V0.3 product
-- wrapper may also finalize the creator's own run after applying product
-- guardrails; trusted backend workers can execute the same changes through an
-- elevated database role outside normal authenticated RLS. Viewers cannot
-- update run status.
create policy "runs_update_for_org_admins_and_creator_wrapper"
  on public.product_runs
  for update
  to authenticated
  using (
    public.has_org_role(organization_id, array['owner', 'admin'])
    or (
      created_by_user_id = auth.uid()
      and public.has_org_role(organization_id, array['owner', 'admin', 'researcher'])
    )
  )
  with check (
    (
      public.has_org_role(organization_id, array['owner', 'admin'])
      or (
        created_by_user_id = auth.uid()
        and public.has_org_role(organization_id, array['owner', 'admin', 'researcher'])
      )
    )
    and exists (
      select 1
      from public.product_projects project
      where project.id = product_runs.project_id
        and project.organization_id = product_runs.organization_id
    )
  );

-- product_run_artifacts select: organization members can read public
-- artifacts, while admin-only artifacts require owner/admin role. Artifacts
-- remain scoped to their organization, project, and run.
create policy "run_artifacts_select_for_org_members"
  on public.product_run_artifacts
  for select
  to authenticated
  using (
    public.is_org_member(organization_id)
    and exists (
      select 1
      from public.product_runs run
      where run.id = run_id
        and run.organization_id = product_run_artifacts.organization_id
        and run.project_id = product_run_artifacts.project_id
    )
    and (
      admin_only = false
      or public.has_org_role(organization_id, array['owner', 'admin'])
    )
  );

-- product_run_artifacts insert: research roles can write product-safe public
-- result artifacts for runs in their organization. Admin-only artifacts are
-- limited to owners/admins so hidden diagnostics cannot be created by viewers
-- or researchers.
create policy "run_artifacts_insert_for_research_roles"
  on public.product_run_artifacts
  for insert
  to authenticated
  with check (
    public.has_org_role(organization_id, array['owner', 'admin', 'researcher'])
    and (
      admin_only = false
      or public.has_org_role(organization_id, array['owner', 'admin'])
    )
    and exists (
      select 1
      from public.product_runs run
      where run.id = run_id
        and run.organization_id = product_run_artifacts.organization_id
        and run.project_id = product_run_artifacts.project_id
    )
  );

-- product_run_artifacts update: only owners/admins can repair artifact
-- metadata or mark artifacts admin-only. Raw logs, raw transcripts, raw traces,
-- secrets, and cache-backed paths are blocked by table constraints.
create policy "run_artifacts_update_for_org_admins"
  on public.product_run_artifacts
  for update
  to authenticated
  using (public.has_org_role(organization_id, array['owner', 'admin']))
  with check (
    public.has_org_role(organization_id, array['owner', 'admin'])
    and exists (
      select 1
      from public.product_runs run
      where run.id = run_id
        and run.organization_id = product_run_artifacts.organization_id
        and run.project_id = product_run_artifacts.project_id
    )
  );
