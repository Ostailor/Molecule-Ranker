-- Release V0.2 product auth, organization, and tenant data schema.
-- This migration intentionally does not create billing, PHI, patient, or secret tables.

create extension if not exists pgcrypto;

create or replace function public.set_product_updated_at()
returns trigger
language plpgsql
set search_path = public
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create table if not exists public.product_profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  email text not null,
  display_name text,
  avatar_url text,
  onboarding_completed boolean not null default false,
  research_use_acknowledged_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint product_profiles_email_not_blank check (length(btrim(email)) > 0)
);

create table if not exists public.product_organizations (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  slug text unique not null,
  owner_user_id uuid references auth.users(id),
  plan text not null default 'free_internal',
  status text not null default 'active',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint product_organizations_name_not_blank check (length(btrim(name)) > 0),
  constraint product_organizations_slug_format check (slug ~ '^[a-z0-9]([a-z0-9-]*[a-z0-9])?$'),
  constraint product_organizations_plan_check check (plan in ('free_internal', 'pilot', 'trial')),
  constraint product_organizations_status_check check (status in ('active', 'suspended', 'archived'))
);

create table if not exists public.product_memberships (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.product_organizations(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  role text not null,
  status text not null default 'active',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint product_memberships_role_check check (role in ('owner', 'admin', 'researcher', 'viewer')),
  constraint product_memberships_status_check check (status in ('active', 'invited', 'disabled', 'removed')),
  constraint product_memberships_unique_user_per_org unique (organization_id, user_id)
);

create table if not exists public.product_projects (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.product_organizations(id) on delete cascade,
  created_by_user_id uuid references auth.users(id),
  name text not null,
  research_goal text,
  disease_focus text,
  target_focus text,
  status text not null default 'active',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint product_projects_name_not_blank check (length(btrim(name)) > 0),
  constraint product_projects_status_check check (status in ('active', 'archived', 'deleted'))
);

create table if not exists public.product_usage_events (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.product_organizations(id) on delete cascade,
  user_id uuid references auth.users(id),
  event_type text not null,
  quantity integer not null default 1,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  constraint product_usage_events_event_type_not_blank check (length(btrim(event_type)) > 0),
  constraint product_usage_events_quantity_positive check (quantity > 0),
  constraint product_usage_events_metadata_object check (jsonb_typeof(metadata) = 'object')
);

create table if not exists public.product_feedback (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.product_organizations(id),
  user_id uuid references auth.users(id),
  category text not null,
  message text not null,
  status text not null default 'open',
  created_at timestamptz not null default now(),
  constraint product_feedback_category_not_blank check (length(btrim(category)) > 0),
  constraint product_feedback_message_not_blank check (length(btrim(message)) > 0),
  constraint product_feedback_status_check check (status in ('open', 'in_review', 'resolved', 'closed'))
);

create index if not exists product_profiles_email_idx
  on public.product_profiles (email);

create index if not exists product_organizations_owner_user_id_idx
  on public.product_organizations (owner_user_id);

create index if not exists product_organizations_status_idx
  on public.product_organizations (status);

create index if not exists product_memberships_user_id_idx
  on public.product_memberships (user_id);

create index if not exists product_memberships_organization_id_role_idx
  on public.product_memberships (organization_id, role);

create index if not exists product_memberships_active_lookup_idx
  on public.product_memberships (organization_id, user_id)
  where status = 'active';

create index if not exists product_projects_organization_id_status_idx
  on public.product_projects (organization_id, status);

create index if not exists product_projects_created_by_user_id_idx
  on public.product_projects (created_by_user_id);

create index if not exists product_usage_events_organization_id_created_at_idx
  on public.product_usage_events (organization_id, created_at desc);

create index if not exists product_usage_events_user_id_created_at_idx
  on public.product_usage_events (user_id, created_at desc);

create index if not exists product_usage_events_event_type_idx
  on public.product_usage_events (event_type);

create index if not exists product_feedback_organization_id_status_idx
  on public.product_feedback (organization_id, status);

create index if not exists product_feedback_user_id_created_at_idx
  on public.product_feedback (user_id, created_at desc);

drop trigger if exists set_product_profiles_updated_at on public.product_profiles;
create trigger set_product_profiles_updated_at
  before update on public.product_profiles
  for each row
  execute function public.set_product_updated_at();

drop trigger if exists set_product_organizations_updated_at on public.product_organizations;
create trigger set_product_organizations_updated_at
  before update on public.product_organizations
  for each row
  execute function public.set_product_updated_at();

drop trigger if exists set_product_memberships_updated_at on public.product_memberships;
create trigger set_product_memberships_updated_at
  before update on public.product_memberships
  for each row
  execute function public.set_product_updated_at();

drop trigger if exists set_product_projects_updated_at on public.product_projects;
create trigger set_product_projects_updated_at
  before update on public.product_projects
  for each row
  execute function public.set_product_updated_at();

create or replace function public.product_role_rank(target_role text)
returns integer
language sql
immutable
set search_path = public
as $$
  select case target_role
    when 'viewer' then 10
    when 'researcher' then 20
    when 'admin' then 30
    when 'owner' then 40
    else 0
  end;
$$;

create or replace function public.prevent_product_membership_self_escalation()
returns trigger
language plpgsql
set search_path = public, auth
as $$
begin
  if new.organization_id <> old.organization_id or new.user_id <> old.user_id then
    raise exception 'membership organization_id and user_id are immutable';
  end if;

  if auth.uid() = old.user_id
     and public.product_role_rank(new.role) > public.product_role_rank(old.role) then
    raise exception 'users cannot escalate their own product membership role';
  end if;

  return new;
end;
$$;

drop trigger if exists prevent_product_membership_self_escalation on public.product_memberships;
create trigger prevent_product_membership_self_escalation
  before update on public.product_memberships
  for each row
  execute function public.prevent_product_membership_self_escalation();

create or replace function public.prevent_product_project_identity_changes()
returns trigger
language plpgsql
set search_path = public
as $$
begin
  if new.organization_id <> old.organization_id
     or new.created_by_user_id is distinct from old.created_by_user_id then
    raise exception 'project organization_id and created_by_user_id are immutable';
  end if;

  return new;
end;
$$;

drop trigger if exists prevent_product_project_identity_changes on public.product_projects;
create trigger prevent_product_project_identity_changes
  before update on public.product_projects
  for each row
  execute function public.prevent_product_project_identity_changes();

create or replace function public.is_org_member(org_id uuid)
returns boolean
language sql
stable
security definer
set search_path = public, auth
as $$
  select exists (
    select 1
    from public.product_memberships membership
    where membership.organization_id = org_id
      and membership.user_id = auth.uid()
      and membership.status = 'active'
  );
$$;

create or replace function public.has_org_role(
  org_id uuid,
  allowed_roles text[]
)
returns boolean
language sql
stable
security definer
set search_path = public, auth
as $$
  select exists (
    select 1
    from public.product_memberships membership
    where membership.organization_id = org_id
      and membership.user_id = auth.uid()
      and membership.status = 'active'
      and membership.role = any(allowed_roles)
  );
$$;

create or replace function public.is_org_owner(org_id uuid)
returns boolean
language sql
stable
security definer
set search_path = public, auth
as $$
  select exists (
    select 1
    from public.product_organizations organization
    where organization.id = org_id
      and organization.owner_user_id = auth.uid()
  );
$$;

create or replace function public.can_view_org_member_profile(target_user_id uuid)
returns boolean
language sql
stable
security definer
set search_path = public, auth
as $$
  select exists (
    select 1
    from public.product_memberships viewer_membership
    join public.product_memberships target_membership
      on target_membership.organization_id = viewer_membership.organization_id
    where viewer_membership.user_id = auth.uid()
      and viewer_membership.status = 'active'
      and viewer_membership.role in ('owner', 'admin')
      and target_membership.user_id = target_user_id
      and target_membership.status = 'active'
  );
$$;

revoke all on function public.product_role_rank(text) from public;
revoke all on function public.is_org_member(uuid) from public;
revoke all on function public.has_org_role(uuid, text[]) from public;
revoke all on function public.is_org_owner(uuid) from public;
revoke all on function public.can_view_org_member_profile(uuid) from public;

grant execute on function public.is_org_member(uuid) to authenticated;
grant execute on function public.has_org_role(uuid, text[]) to authenticated;
grant execute on function public.is_org_owner(uuid) to authenticated;
grant execute on function public.can_view_org_member_profile(uuid) to authenticated;

alter table public.product_profiles enable row level security;
alter table public.product_organizations enable row level security;
alter table public.product_memberships enable row level security;
alter table public.product_projects enable row level security;
alter table public.product_usage_events enable row level security;
alter table public.product_feedback enable row level security;

alter table public.product_profiles force row level security;
alter table public.product_organizations force row level security;
alter table public.product_memberships force row level security;
alter table public.product_projects force row level security;
alter table public.product_usage_events force row level security;
alter table public.product_feedback force row level security;

revoke all on table public.product_profiles from anon, authenticated;
revoke all on table public.product_organizations from anon, authenticated;
revoke all on table public.product_memberships from anon, authenticated;
revoke all on table public.product_projects from anon, authenticated;
revoke all on table public.product_usage_events from anon, authenticated;
revoke all on table public.product_feedback from anon, authenticated;

grant select, insert on table public.product_profiles to authenticated;
grant update (
  display_name,
  avatar_url,
  onboarding_completed,
  research_use_acknowledged_at
) on public.product_profiles to authenticated;

grant select, insert on table public.product_organizations to authenticated;
grant update (name, slug, status) on public.product_organizations to authenticated;

grant select, insert on table public.product_memberships to authenticated;
grant update (role, status) on public.product_memberships to authenticated;

grant select, insert on table public.product_projects to authenticated;
grant update (
  name,
  research_goal,
  disease_focus,
  target_focus,
  status
) on public.product_projects to authenticated;

grant select, insert on table public.product_usage_events to authenticated;

grant select, insert on table public.product_feedback to authenticated;
grant update (status) on public.product_feedback to authenticated;

-- product_profiles select: users can read their own profile; org owners/admins can
-- read member profiles in active organizations for V0.2 admin surfaces.
create policy "profiles_select_own_or_org_peer"
  on public.product_profiles
  for select
  to authenticated
  using (id = auth.uid() or public.can_view_org_member_profile(id));

-- product_profiles insert: authenticated users can create only their own profile row.
create policy "profiles_insert_own"
  on public.product_profiles
  for insert
  to authenticated
  with check (id = auth.uid());

-- product_profiles update: authenticated users can update only their own profile row;
-- column grants above limit mutable fields to display/avatar/onboarding/acknowledgement.
create policy "profiles_update_own"
  on public.product_profiles
  for update
  to authenticated
  using (id = auth.uid())
  with check (id = auth.uid());

-- product_organizations select: active members and the owner can read their organization.
create policy "organizations_select_for_members"
  on public.product_organizations
  for select
  to authenticated
  using (owner_user_id = auth.uid() or public.is_org_member(id));

-- product_organizations insert: authenticated users can bootstrap organizations
-- only when they set themselves as owner.
create policy "organizations_insert_for_owner"
  on public.product_organizations
  for insert
  to authenticated
  with check (owner_user_id = auth.uid());

-- product_organizations update: only active owners/admins can update basic metadata;
-- column grants prevent plan and owner_user_id mutation through the public API.
create policy "organizations_update_for_owner_or_admin"
  on public.product_organizations
  for update
  to authenticated
  using (public.has_org_role(id, array['owner', 'admin']))
  with check (public.has_org_role(id, array['owner', 'admin']));

-- product_memberships select: active members can view memberships only inside
-- organizations where they are active members.
create policy "memberships_select_for_same_org_members"
  on public.product_memberships
  for select
  to authenticated
  using (public.is_org_member(organization_id));

-- product_memberships insert: active owners/admins can add members; an organization
-- owner can also create the first owner membership for their own organization.
create policy "memberships_insert_for_owner_or_admin"
  on public.product_memberships
  for insert
  to authenticated
  with check (
    public.has_org_role(organization_id, array['owner', 'admin'])
    or (
      user_id = auth.uid()
      and role = 'owner'
      and public.is_org_owner(organization_id)
    )
  );

-- product_memberships update: only active owners/admins can update role/status;
-- the trigger prevents users from escalating their own role.
create policy "memberships_update_for_owner_or_admin"
  on public.product_memberships
  for update
  to authenticated
  using (public.has_org_role(organization_id, array['owner', 'admin']))
  with check (public.has_org_role(organization_id, array['owner', 'admin']));

-- product_projects select: active organization members can read projects only
-- within their organization.
create policy "projects_select_for_members"
  on public.product_projects
  for select
  to authenticated
  using (public.is_org_member(organization_id));

-- product_projects insert: owners, admins, and researchers can create projects;
-- viewers cannot create projects and created_by_user_id must be the requester.
create policy "projects_insert_for_research_roles"
  on public.product_projects
  for insert
  to authenticated
  with check (
    created_by_user_id = auth.uid()
    and public.has_org_role(organization_id, array['owner', 'admin', 'researcher'])
  );

-- product_projects update: project creators can update their own projects, and
-- owners/admins can update any project in their organization; viewers cannot update.
create policy "projects_update_for_research_roles"
  on public.product_projects
  for update
  to authenticated
  using (
    public.has_org_role(organization_id, array['owner', 'admin'])
    or (
      created_by_user_id = auth.uid()
      and public.has_org_role(organization_id, array['researcher'])
    )
  )
  with check (
    public.has_org_role(organization_id, array['owner', 'admin'])
    or (
      created_by_user_id = auth.uid()
      and public.has_org_role(organization_id, array['researcher'])
    )
  );

-- product_usage_events select: users can read their own events; owners/admins can
-- read all usage events for their organization.
create policy "usage_events_select_for_org_members"
  on public.product_usage_events
  for select
  to authenticated
  using (
    (user_id = auth.uid() and public.is_org_member(organization_id))
    or public.has_org_role(organization_id, array['owner', 'admin'])
  );

-- product_usage_events insert: authenticated users can write their own usage
-- events only for organizations where they are active members.
create policy "usage_events_insert_for_org_members"
  on public.product_usage_events
  for insert
  to authenticated
  with check (
    user_id = auth.uid()
    and public.is_org_member(organization_id)
  );

-- product_feedback select: users can read their own feedback; owners/admins can
-- read all feedback in their organization for support triage.
create policy "feedback_select_for_org_members"
  on public.product_feedback
  for select
  to authenticated
  using (
    (user_id = auth.uid() and public.is_org_member(organization_id))
    or public.has_org_role(organization_id, array['owner', 'admin'])
  );

-- product_feedback insert: authenticated users can submit their own feedback only
-- for organizations where they are active members.
create policy "feedback_insert_for_org_members"
  on public.product_feedback
  for insert
  to authenticated
  with check (
    user_id = auth.uid()
    and public.is_org_member(organization_id)
  );

-- product_feedback update: owners/admins can update feedback status for their
-- organization; regular users cannot update feedback after submission.
create policy "feedback_update_for_owner_or_admin"
  on public.product_feedback
  for update
  to authenticated
  using (public.has_org_role(organization_id, array['owner', 'admin']))
  with check (public.has_org_role(organization_id, array['owner', 'admin']));
