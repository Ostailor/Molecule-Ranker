export const productApiErrorCodes = {
  UNAUTHENTICATED: "UNAUTHENTICATED",
  ONBOARDING_REQUIRED: "ONBOARDING_REQUIRED",
  ORGANIZATION_REQUIRED: "ORGANIZATION_REQUIRED",
  FORBIDDEN: "FORBIDDEN",
  PLAN_LIMIT_EXCEEDED: "PLAN_LIMIT_EXCEEDED",
  NOT_FOUND: "NOT_FOUND",
  VALIDATION_ERROR: "VALIDATION_ERROR",
} as const;

export type ProductApiErrorCode = keyof typeof productApiErrorCodes;

export const defaultProductApiErrorStatus: Record<ProductApiErrorCode, number> = {
  UNAUTHENTICATED: 401,
  ONBOARDING_REQUIRED: 403,
  ORGANIZATION_REQUIRED: 403,
  FORBIDDEN: 403,
  PLAN_LIMIT_EXCEEDED: 429,
  NOT_FOUND: 404,
  VALIDATION_ERROR: 400,
};

export const defaultProductApiErrorMessage: Record<ProductApiErrorCode, string> = {
  UNAUTHENTICATED: "Sign in before using this product API.",
  ONBOARDING_REQUIRED: "Complete onboarding before using this product API.",
  ORGANIZATION_REQUIRED: "An active organization membership is required.",
  FORBIDDEN: "You do not have access to this product API.",
  PLAN_LIMIT_EXCEEDED: "This action exceeds the current plan limits.",
  NOT_FOUND: "The requested product resource was not found.",
  VALIDATION_ERROR: "Review the request and try again.",
};

export class ProductApiError extends Error {
  readonly code: ProductApiErrorCode;
  readonly status: number;
  readonly publicMessage: string;

  constructor(code: ProductApiErrorCode, message = defaultProductApiErrorMessage[code], status = defaultProductApiErrorStatus[code]) {
    super(message);
    this.name = "ProductApiError";
    this.code = code;
    this.status = status;
    this.publicMessage = message;
  }
}

export function productApiError(code: ProductApiErrorCode, message?: string, status?: number) {
  return new ProductApiError(code, message, status);
}

export function toProductApiError(error: unknown, fallbackCode: ProductApiErrorCode = "FORBIDDEN") {
  if (error instanceof ProductApiError) return error;

  return productApiError(fallbackCode);
}
