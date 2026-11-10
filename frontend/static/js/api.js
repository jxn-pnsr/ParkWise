const BASE = "/api";
let csrfToken = null;
let meCache = null; // Promise of the current user (or null)

export const ROLE_HOME = {
  Customer: "/customer/dashboard.html",
  Staff: "/staff/dashboard.html",
  Admin: "/admin/dashboard.html",
};

export class ApiError extends Error {
  constructor(status, code, message, fields = null, extra = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;   // HTTP status (0 = network down)
    this.code = code; 
    this.fields = fields;   // {field: "reason"} for form errors
    this.extra = extra;     // anything else the backend sent (e.g. alternatives)
  }
}

function buildUrl(path, query) {
  const url = new URL(BASE + path, location.origin);
  if (query) {
    for (const [k, v] of Object.entries(query)) {
      if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, v);
    }
  }
  return url.pathname + url.search;
}

const NO_CSRF = new Set(["/auth/login", "/auth/register"]);

async function request(method, path, { body, query, redirectOn401 = true, _retried = false } = {}) {
  const headers = { Accept: "application/json" };
  const isWrite = method !== "GET";

  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (isWrite && !NO_CSRF.has(path)) {
    if (!csrfToken) await me({ force: true });
    if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
  }

  let res;
  try {
    res = await fetch(buildUrl(path, query), {
      method,
      headers,
      credentials: "same-origin",
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "NETWORK_ERROR", "Can't reach the server. Please try again.");
  }

  let json = null;
  try { json = await res.json(); } catch { /* not JSON */ }

  if (res.ok && json && json.ok) return json.data;

  const err = (json && json.error) || { code: `HTTP_${res.status}`, message: "Something went wrong. Please try again." };
  const { code, message, fields, ...extra } = err;

  if (res.status === 403 && code === "CSRF_INVALID" && !_retried) {
    csrfToken = null; // token rotated (e.g. after login elsewhere) -> fetch a fresh one once
    return request(method, path, { body, query, redirectOn401, _retried: true });
  }
  if (res.status === 401) {
    csrfToken = null;
    meCache = null;
    if (redirectOn401) redirectToLogin();
  }
  throw new ApiError(res.status, code, message, fields || null, extra);
}

export const api = {
  get: (path, query, opts) => request("GET", path, { ...opts, query }),
  post: (path, body = {}, opts) => request("POST", path, { ...opts, body }),
  put: (path, body = {}, opts) => request("PUT", path, { ...opts, body }),
  patch: (path, body = {}, opts) => request("PATCH", path, { ...opts, body }),
  del: (path, opts) => request("DELETE", path, opts),
};

// session

/** Current user, or null when not logged in. Cached for the page's lifetime. */
export function me({ force = false } = {}) {
  if (!meCache || force) {
    meCache = request("GET", "/auth/me", { redirectOn401: false })
      .then((data) => {
        csrfToken = data.csrf_token || csrfToken;
        return data;
      })
      .catch((e) => {
        if (e.status === 401) return null;
        meCache = null;
        throw e;
      });
  }
  return meCache;
}

export async function login(email, password) {
  const data = await request("POST", "/auth/login", { body: { email, password }, redirectOn401: false });
  csrfToken = data.csrf_token || null;
  meCache = Promise.resolve(data);
  return data;
}

export async function register(payload) {
  return request("POST", "/auth/register", { body: payload, redirectOn401: false });
}

export async function logout() {
  try { await api.post("/auth/logout", {}, { redirectOn401: false }); } catch { /* already logged out */ }
  csrfToken = null;
  meCache = null;
  location.href = "/login.html";
}

/** Only allow same-site paths as ?next= (blocks open redirects like //evil.com). */
export function safeNext(value, fallback = "/") {
  if (typeof value === "string" && value.startsWith("/") && !value.startsWith("//") && !value.startsWith("/\\")) {
    return value;
  }
  return fallback;
}

export function redirectToLogin() {
  if (location.pathname === "/login.html") return;
  const next = location.pathname + location.search;
  location.href = "/login.html?next=" + encodeURIComponent(next);
}

export function homeFor(user) {
  return (user && ROLE_HOME[user.role]) || "/login.html";
}
