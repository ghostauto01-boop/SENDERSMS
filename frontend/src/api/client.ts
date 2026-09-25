import axios from "axios";

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
  (response) => response,
  (error) => {
    if (error.response?.data) {
      normaliseDetail(error.response.data);
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
