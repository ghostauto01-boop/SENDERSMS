import axios from "axios";
import { dbOutageFromError, reportDbOk, reportDbOutage } from "../utils/dbStatus";

const api = axios.create({
  baseURL: "/api/v1",
  withCredentials: true,
  headers: { "Content-Type": "application/json" },
});

/**
 * FastAPI returns a *string* detail for HTTPException but an *array of error
 * objects* for 422 request-validation failures. Pages all render
 * `err.response.data.detail` directly, which turns the array into
 * "[object Object]". Flatten it here so every caller gets a readable string.
 */
const normaliseDetail = (data: any) => {
  const d = data?.detail;
  if (!Array.isArray(d)) return;
  const msg = d
    .map((e: any) => {
      const field = Array.isArray(e?.loc)
        ? e.loc.filter((p: any) => p !== "body").join(".")
        : "";
      const text = e?.msg || "Invalid value";
      return field ? `${field}: ${text}` : text;
    })
    .join("; ");
  if (msg) data.detail = msg;
};

api.interceptors.response.use(
  (response) => {
    // Any real answer from the API means the database is back.
    if (!String(response.config?.url || "").startsWith("/health")) {
      reportDbOk();
    }
    return response;
  },
  (error) => {
    if (error.response?.data) {
      normaliseDetail(error.response.data);
    }
    // 503 + error_kind "database": Postgres itself is down (e.g. the Neon
    // free plan suspended the project). Tell the banner; pages still get
    // the rejection so their own loading states settle.
    const outage = dbOutageFromError(error);
    if (outage) {
      reportDbOutage(outage);
    } else if (
      error.response &&
      error.response.status < 500 &&
      error.response.status !== 401
    ) {
      // A 4xx (other than a cookie-less 401, which never reaches the
      // database) proves the API and its database answered.
      reportDbOk();
    }
    if (error.response?.status === 401) {
      const currentPath = window.location.pathname;
      if (currentPath !== "/login") {
        window.location.href = "/login";
      }
    }
    return Promise.reject(error);
  }
);

export default api;
