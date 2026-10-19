export type ApiError = {
  status: number; // 0 means the request never reached the server
  message: string;
  fieldErrors: Record<string, string>;
};

export type ApiResult<T> = { ok: true; data: T } | { ok: false; error: ApiError };

const NETWORK_MESSAGE = "Cannot reach the server. Check your connection and try again.";

async function readError(response: Response): Promise<ApiError> {
  const fieldErrors: Record<string, string> = {};
  let message = "Something went wrong. Please try again.";
  try {
    const body = (await response.json()) as {
      error?: { message?: string; details?: { location?: string[]; message?: string }[] };
    };
    if (body.error?.message) message = body.error.message;
    for (const detail of body.error?.details ?? []) {
      const field = detail.location?.[detail.location.length - 1];
      if (field && detail.message && !(field in fieldErrors)) fieldErrors[field] = detail.message;
    }
  } catch {
    // The body was not JSON; keep the generic message.
  }
  return { status: response.status, message, fieldErrors };
}

async function csrfToken(): Promise<string | null> {
  try {
    const response = await fetch("/api/auth/csrf", { credentials: "same-origin" });
    if (!response.ok) return null;
    return ((await response.json()) as { csrf_token: string }).csrf_token;
  } catch {
    return null;
  }
}

/**
 * Calls the backend through the same-origin /api proxy. State-changing calls first fetch the
 * CSRF token for the current session; login and register have no session yet, so they skip it.
 */
export async function apiRequest<T = void>(
  method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE",
  path: string,
  body?: unknown,
  options: { csrf?: boolean; headers?: Record<string, string> } = {},
): Promise<ApiResult<T>> {
  const headers: Record<string, string> = { ...options.headers };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET" && options.csrf !== false) {
    const token = await csrfToken();
    if (!token) {
      return {
        ok: false,
        error: {
          status: 401,
          message: "Your session has expired. Sign in again.",
          fieldErrors: {},
        },
      };
    }
    headers["X-CSRF-Token"] = token;
  }
  try {
    const response = await fetch(`/api${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: "same-origin",
    });
    if (!response.ok) return { ok: false, error: await readError(response) };
    if (response.status === 204) return { ok: true, data: undefined as T };
    return { ok: true, data: (await response.json()) as T };
  } catch {
    return { ok: false, error: { status: 0, message: NETWORK_MESSAGE, fieldErrors: {} } };
  }
}
