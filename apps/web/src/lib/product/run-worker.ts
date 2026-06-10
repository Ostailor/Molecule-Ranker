import { productApiError } from "./api-errors";
import { storeProductSafeResultArtifact } from "./artifact-storage";
import { runProductSafeDiscoveryWorkflow, resultSummaryToJson, type ProductSafeResultBundle } from "./engine-runner";
import { ProductRunExecutionError, safeEngineError } from "./run-errors";
import type { ProductRunSafeOptions } from "./run-safety";
import type { ProductAuthSupabaseClient, ProductRouteAuthContext } from "./auth-context";
import type { ProductRun, ProductRunArtifact, Project } from "@/lib/supabase/types";

const runSelect =
  "id, organization_id, project_id, created_by_user_id, run_type, mode, status, disease_or_goal, target_focus, options, progress, result_summary, error_summary, started_at, completed_at, created_at, updated_at";

export type ProductRunWorkerResult = {
  run: ProductRun;
  artifact: ProductRunArtifact | null;
  terminalStatus: "succeeded" | "failed" | "partially_succeeded" | "cancelled" | "running" | "queued";
  executed: boolean;
};

type ProductRunWorkerDependencies = {
  runner?: typeof runProductSafeDiscoveryWorkflow;
  artifactStore?: typeof storeProductSafeResultArtifact;
  now?: () => string;
  timeoutMs?: number;
};

export type ProductRunWorkerInput = {
  context: ProductRouteAuthContext;
  supabase: ProductAuthSupabaseClient;
  project: Project;
  run: ProductRun;
  options: ProductRunSafeOptions;
  dependencies?: ProductRunWorkerDependencies;
};

function booleanFromEnv(value: string | undefined) {
  return value === "1" || value === "true" || value === "TRUE";
}

function productRunWorkerTimeoutMs() {
  const seconds = Number.parseInt(process.env.PRODUCT_RUN_TIMEOUT_SECONDS ?? "", 10);
  return (Number.isFinite(seconds) && seconds > 0 ? seconds : 120) * 1000;
}

export function isProductRunWorkerEnabled() {
  return !booleanFromEnv(process.env.PRODUCT_RUN_WORKER_DISABLED);
}

function assertRunScope({ context, project, run }: Pick<ProductRunWorkerInput, "context" | "project" | "run">) {
  if (
    run.organization_id !== context.organization.id ||
    run.project_id !== project.id ||
    project.organization_id !== context.organization.id
  ) {
    throw productApiError("FORBIDDEN", "Queued run is outside the active organization and project scope.");
  }
}

async function updateRun(
  supabase: ProductAuthSupabaseClient,
  context: ProductRouteAuthContext,
  run: ProductRun,
  patch: Partial<ProductRun>,
) {
  const { data, error } = await supabase
    .from("product_runs")
    .update(patch)
    .eq("id", run.id)
    .eq("organization_id", context.organization.id)
    .select(runSelect)
    .single();

  if (error || !data) {
    throw productApiError("FORBIDDEN", "Could not update the discovery run.");
  }

  return data as ProductRun;
}

function withRunTimeout<T>(promise: Promise<T>, timeoutMs: number) {
  return new Promise<T>((resolve, reject) => {
    const timeout = setTimeout(() => {
      reject(new ProductRunExecutionError("Product run worker timed out."));
    }, timeoutMs);

    promise.then(
      (value) => {
        clearTimeout(timeout);
        resolve(value);
      },
      (error) => {
        clearTimeout(timeout);
        reject(error);
      },
    );
  });
}

export async function executeProductRunWorker({
  context,
  supabase,
  project,
  run,
  options,
  dependencies = {},
}: ProductRunWorkerInput): Promise<ProductRunWorkerResult> {
  assertRunScope({ context, project, run });

  if (!isProductRunWorkerEnabled()) {
    return {
      run,
      artifact: null,
      terminalStatus: run.status,
      executed: false,
    };
  }

  const now = dependencies.now ?? (() => new Date().toISOString());
  const runner = dependencies.runner ?? runProductSafeDiscoveryWorkflow;
  const artifactStore = dependencies.artifactStore ?? storeProductSafeResultArtifact;
  let currentRun = await updateRun(supabase, context, run, {
    status: "running",
    progress: { step: "running", message: "Preparing product-safe discovery result." },
    started_at: run.started_at ?? now(),
  });

  let result: ProductSafeResultBundle;
  try {
    result = await withRunTimeout(
      runner({
        project,
        run: currentRun,
        options,
      }),
      dependencies.timeoutMs ?? productRunWorkerTimeoutMs(),
    );
  } catch (error) {
    const safeError = safeEngineError(error);
    const failedRun = await updateRun(supabase, context, currentRun, {
      status: "failed",
      progress: { step: "failed" },
      error_summary: safeError.publicMessage,
      completed_at: now(),
    });

    return {
      run: failedRun,
      artifact: null,
      terminalStatus: "failed",
      executed: true,
    };
  }

  let artifact: ProductRunArtifact | null = null;
  try {
    artifact = await artifactStore({
      context,
      projectId: project.id,
      run: currentRun,
      result,
      supabase,
    });
  } catch {
    const partialRun = await updateRun(supabase, context, currentRun, {
      status: "partially_succeeded",
      progress: { step: "partially_succeeded", message: "Result summary prepared; artifact storage did not complete." },
      result_summary: resultSummaryToJson(result),
      error_summary: "The bounded discovery workflow prepared a result summary, but product artifact storage failed.",
      completed_at: now(),
    });

    return {
      run: partialRun,
      artifact: null,
      terminalStatus: "partially_succeeded",
      executed: true,
    };
  }

  currentRun = await updateRun(supabase, context, currentRun, {
    status: "succeeded",
    progress: { step: "completed" },
    result_summary: resultSummaryToJson(result),
    completed_at: now(),
  });

  return {
    run: currentRun,
    artifact,
    terminalStatus: "succeeded",
    executed: true,
  };
}
