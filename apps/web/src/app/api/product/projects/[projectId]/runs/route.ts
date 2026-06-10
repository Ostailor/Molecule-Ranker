import { failure, success } from "@/lib/product/api-response";
import { productApiError } from "@/lib/product/api-errors";
import { requireProductPermission, sanitizeProductApiError } from "@/lib/product/auth-context";
import { roleHasPermission } from "@/lib/product/permissions";
import { executeProductRunWorker } from "@/lib/product/run-worker";
import { readJsonObject, readSafeRunOptions, safeOptionsToJson, textField } from "@/lib/product/run-safety";
import { checkUsageAllowed, recordUsageEvent } from "@/lib/product/usage";
import { createClient } from "@/lib/supabase/server";
import type { ProductRunSafeOptions } from "@/lib/product/run-safety";
import type { ProductRun, Project } from "@/lib/supabase/types";

type RunsRouteContext = {
  params: Promise<{
    projectId: string;
  }>;
};

const runSelect =
  "id, organization_id, project_id, created_by_user_id, run_type, mode, status, disease_or_goal, target_focus, options, progress, result_summary, error_summary, started_at, completed_at, created_at, updated_at";

function isUuid(value: string) {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
}

function booleanFromEnv(value: string | undefined) {
  return value === "1" || value === "true" || value === "TRUE";
}

function codexTaskUsageEnabled() {
  return (
    booleanFromEnv(process.env.PRODUCT_CODEX_TASK_USAGE_ENABLED) ||
    booleanFromEnv(process.env.PRODUCT_CODEX_RUNTIME_ENABLED) ||
    booleanFromEnv(process.env.NEXT_PUBLIC_PRODUCT_CODEX_RUNTIME_ENABLED)
  );
}

function requestedGeneratedHypothesisCount(options: ProductRunSafeOptions) {
  return options.includeGeneratedHypotheses ? Math.max(0, Math.floor(options.maxGeneratedHypotheses)) : 0;
}

function shouldRecordCodexTask(options: ProductRunSafeOptions) {
  return options.prepareResultBundle && codexTaskUsageEnabled();
}

async function checkRunUsageLimits({
  context,
  supabase,
  options,
}: {
  context: Awaited<ReturnType<typeof requireProductPermission>>;
  supabase: Awaited<ReturnType<typeof createClient>>;
  options: ProductRunSafeOptions;
}) {
  await checkUsageAllowed("run_discovery", 1, { context, supabaseClient: supabase });

  const generatedHypothesisCount = requestedGeneratedHypothesisCount(options);
  if (generatedHypothesisCount > 0) {
    await checkUsageAllowed("generated_hypotheses", generatedHypothesisCount, { context, supabaseClient: supabase });
  }

  if (shouldRecordCodexTask(options)) {
    await checkUsageAllowed("codex_task", 1, { context, supabaseClient: supabase });
  }
}

async function recordRunUsageEvents({
  context,
  supabase,
  projectId,
  run,
  options,
}: {
  context: Awaited<ReturnType<typeof requireProductPermission>>;
  supabase: Awaited<ReturnType<typeof createClient>>;
  projectId: string;
  run: ProductRun;
  options: ProductRunSafeOptions;
}) {
  await recordUsageEvent(
    "run_discovery",
    1,
    { project_id: projectId, run_id: run.id, mode: options.mode },
    { context, supabaseClient: supabase },
  );

  const generatedHypothesisCount = requestedGeneratedHypothesisCount(options);
  if (generatedHypothesisCount > 0) {
    await recordUsageEvent(
      "generated_hypotheses",
      generatedHypothesisCount,
      { project_id: projectId, run_id: run.id, mode: options.mode },
      { context, supabaseClient: supabase },
    );
  }

  if (shouldRecordCodexTask(options)) {
    await recordUsageEvent(
      "codex_task",
      1,
      { project_id: projectId, run_id: run.id, mode: options.mode, summary: "result_bundle" },
      { context, supabaseClient: supabase },
    );
  }
}

async function getProjectForContext(supabase: Awaited<ReturnType<typeof createClient>>, projectId: string, organizationId: string) {
  const { data, error } = await supabase
    .from("product_projects")
    .select("id, organization_id, created_by_user_id, name, research_goal, disease_focus, target_focus, status, created_at, updated_at")
    .eq("id", projectId)
    .eq("organization_id", organizationId)
    .eq("status", "active")
    .maybeSingle();

  if (error || !data) {
    throw productApiError("NOT_FOUND");
  }

  return data as Project;
}

export async function GET(_request: Request, { params }: RunsRouteContext) {
  try {
    const { projectId } = await params;

    if (!isUuid(projectId)) {
      throw productApiError("NOT_FOUND");
    }

    const supabase = await createClient();
    const context = await requireProductPermission("run:read", supabase);
    await getProjectForContext(supabase, projectId, context.organization.id);

    const { data, error } = await supabase
      .from("product_runs")
      .select(runSelect)
      .eq("organization_id", context.organization.id)
      .eq("project_id", projectId)
      .order("created_at", { ascending: false })
      .limit(50);

    if (error) {
      throw productApiError("FORBIDDEN");
    }

    return success({ runs: data ?? [] });
  } catch (error) {
    const apiError = sanitizeProductApiError(error);
    return failure(apiError.code, apiError.publicMessage, apiError.status);
  }
}

export async function POST(request: Request, { params }: RunsRouteContext) {
  try {
    const { projectId } = await params;

    if (!isUuid(projectId)) {
      throw productApiError("NOT_FOUND");
    }

    const supabase = await createClient();
    const context = await requireProductPermission("run:create", supabase);
    if (!roleHasPermission(context.role, "project:read")) {
      throw productApiError("FORBIDDEN");
    }
    const project = await getProjectForContext(supabase, projectId, context.organization.id);
    const input = await readJsonObject(request);
    const options = readSafeRunOptions(input);
    const objective = textField(input.disease_or_goal ?? input.disease_project_objective);
    const targetFocus = textField(input.target_focus);
    const diseaseOrGoal = objective || project.disease_focus || project.research_goal || project.name;

    if (objective.length > 1000) {
      throw productApiError("VALIDATION_ERROR", "Disease or project objective must be 1000 characters or less.");
    }

    if (targetFocus.length > 160) {
      throw productApiError("VALIDATION_ERROR", "Target focus must be 160 characters or less.");
    }

    await checkRunUsageLimits({ context, supabase, options });

    const { data: runData, error: runError } = await supabase
      .from("product_runs")
      .insert({
        organization_id: context.organization.id,
        project_id: projectId,
        created_by_user_id: context.user.id,
        run_type: options.mode === "mocked" ? "mocked_discovery" : "dry_run_discovery",
        mode: options.mode,
        status: "queued",
        disease_or_goal: diseaseOrGoal,
        target_focus: targetFocus || project.target_focus,
        options: safeOptionsToJson({
          ...options,
          diseaseOrGoal: objective || null,
          targetFocus: targetFocus || null,
        }),
        progress: { step: "queued" },
        result_summary: {},
      })
      .select(runSelect)
      .single();

    if (runError || !runData) {
      throw productApiError("FORBIDDEN", "Could not create the discovery run.");
    }

    const run = runData as ProductRun;

    await recordRunUsageEvents({ context, supabase, projectId, run, options });

    const workerResult = await executeProductRunWorker({ context, supabase, project, run, options });

    return success({ run: workerResult.run, artifact: workerResult.artifact, worker: { executed: workerResult.executed } }, 201);
  } catch (error) {
    const apiError = sanitizeProductApiError(error);
    return failure(apiError.code, apiError.publicMessage, apiError.status);
  }
}
