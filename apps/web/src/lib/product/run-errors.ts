export class ProductRunConfigurationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ProductRunConfigurationError";
  }
}

export class ProductRunExecutionError extends Error {
  readonly diagnostics: string;

  constructor(message: string, diagnostics = "") {
    super(message);
    this.name = "ProductRunExecutionError";
    this.diagnostics = diagnostics;
  }
}

export function redactEngineDiagnostics(rawText: string) {
  return rawText
    .replace(/(api[_-]?key|secret|token|password|credential)(\s*[:=]\s*)[^\s"']+/gi, "$1$2[redacted]")
    .replace(/(authorization:\s*bearer\s+)[^\s"']+/gi, "$1[redacted]")
    .replace(/(supabase[_-]?service[_-]?role[_-]?key\s*[:=]\s*)[^\s"']+/gi, "$1[redacted]")
    .replace(/-----BEGIN [^-]+-----[\s\S]*?-----END [^-]+-----/g, "[redacted private material]");
}

export function safeEngineError(error: unknown) {
  if (error instanceof ProductRunConfigurationError) {
    return {
      publicMessage: error.message,
      diagnostics: "",
    };
  }

  if (error instanceof ProductRunExecutionError) {
    return {
      publicMessage: "The bounded discovery workflow could not prepare a product-safe result bundle.",
      diagnostics: redactEngineDiagnostics(error.diagnostics),
    };
  }

  if (error instanceof Error) {
    return {
      publicMessage: "The bounded discovery workflow could not prepare a product-safe result bundle.",
      diagnostics: redactEngineDiagnostics(error.message),
    };
  }

  return {
    publicMessage: "The bounded discovery workflow could not prepare a product-safe result bundle.",
    diagnostics: "",
  };
}
