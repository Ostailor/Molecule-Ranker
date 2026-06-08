import { NextResponse } from "next/server";

import type { ProductApiErrorCode } from "./api-errors";
import { defaultProductApiErrorStatus } from "./api-errors";

export type ProductApiSuccess<T> = {
  ok: true;
  data: T;
};

export type ProductApiFailure = {
  ok: false;
  error: {
    code: ProductApiErrorCode;
    message: string;
  };
};

export function success<T>(data: T, status = 200) {
  return NextResponse.json<ProductApiSuccess<T>>(
    {
      ok: true,
      data,
    },
    { status },
  );
}

export function failure(errorCode: ProductApiErrorCode, message: string, status = defaultProductApiErrorStatus[errorCode]) {
  return NextResponse.json<ProductApiFailure>(
    {
      ok: false,
      error: {
        code: errorCode,
        message,
      },
    },
    { status },
  );
}
