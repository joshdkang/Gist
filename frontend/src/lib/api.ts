import { createClient } from "@/lib/supabase/client";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** An API failure. `status` is 0 when the API couldn't be reached at all. */
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

type ErrorBody = { detail?: string | { msg: string }[] };

function errorMessage(body: ErrorBody, fallback: string): string {
  if (typeof body.detail === "string") return body.detail;
  if (Array.isArray(body.detail)) return body.detail.map((problem) => problem.msg).join("; ");
  return fallback;
}

/** Sends a request to the Gist API as the signed-in user, and turns failures into ApiErrors. */
async function authorizedFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const { data } = await createClient().auth.getSession();
  const token = data.session?.access_token;
  if (!token) {
    window.location.assign("/login");
    throw new ApiError(401, "Please sign in.");
  }

  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");

  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, { ...init, headers });
  } catch {
    throw new ApiError(0, "Can't reach the Gist API. Check that it is running.");
  }

  if (response.status === 401) {
    window.location.assign("/login");
    throw new ApiError(401, "Your session has ended. Please sign in again.");
  }
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as ErrorBody;
    throw new ApiError(response.status, errorMessage(body, response.statusText || "Something went wrong."));
  }
  return response;
}

/** Calls the Gist API as the signed-in user. The API's error messages are written for people. */
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await authorizedFetch(path, init);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/**
 * Downloads a file the API generates, such as a CSV export. A plain link can't send the sign-in
 * token, so this fetches the file and hands it to the browser as a download, named as the API names
 * it (Content-Disposition).
 */
export async function downloadFile(path: string, fallbackName = "export.csv"): Promise<void> {
  const response = await authorizedFetch(path);
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const name = /filename="([^"]+)"/.exec(disposition)?.[1] ?? fallbackName;
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.append(link);
  link.click();
  link.remove();
  // Revoking at once can cancel the download in some browsers; a moment later is safe.
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}
