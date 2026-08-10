"""
Render free-tier compatible worker: runs Celery + a tiny health HTTP server
in the same process so it qualifies as a "web service" on Render free tier.
"""

import os
import sys
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

# Add backend to path so Celery imports work
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.tasks.celery_app import celery_app  # noqa: E402


class HealthHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler — only serves /health for Render's health checks."""
    def do_GET(self):
        if self.path == "/health" or self.path == "/":
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok","service":"sendsms-worker"}')
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # silence HTTP logs


def run_health_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    print(f"[worker-web] health server listening on port {port}")
    server.serve_forever()


if __name__ == "__main__":
    # Start health check server in a daemon thread
    t = threading.Thread(target=run_health_server, daemon=True)
    t.start()

    # Start Celery worker (blocks forever)
    celery_app.worker_main(
        argv=["worker", "--loglevel=INFO", "-B"]
    )
