// The API error envelope (API_SPECIFICATION.md §1.1), typed for the UI.

export interface ErrorDetail {
  field: string;
  code: string;
  message: string;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: ErrorDetail[];
  readonly requestId: string | null;

  constructor(
    status: number,
    code: string,
    message: string,
    details: ErrorDetail[],
    requestId: string | null,
  ) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
    this.requestId = requestId;
  }
}

interface Envelope {
  error?: { code?: string; message?: string; details?: ErrorDetail[]; request_id?: string | null };
}

export function toApiError(response: Response, body: unknown): ApiError {
  const e = (body as Envelope | undefined)?.error;
  return new ApiError(
    response.status,
    e?.code ?? "internal_error",
    e?.message ?? "Something went wrong. Try again.",
    e?.details ?? [],
    e?.request_id ?? null,
  );
}

/** Unwrap an openapi-fetch result: return data, or throw the typed error. */
export async function unwrap<T>(
  promise: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  let result: { data?: T; error?: unknown; response: Response };
  try {
    result = await promise;
  } catch {
    throw new ApiError(
      0,
      "network_error",
      "The server could not be reached. Check your connection.",
      [],
      null,
    );
  }
  if (!result.response.ok) throw toApiError(result.response, result.error);
  return result.data as T;
}

export const SESSION_CODES = new Set(["unauthenticated", "session_expired"]);
