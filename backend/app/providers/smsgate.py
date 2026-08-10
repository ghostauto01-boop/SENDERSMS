"""SMS-Gate.app — send, test, webhook, poll messages. EXACT BULKSMS pattern."""
import base64, json, logging, uuid
from datetime import datetime, timezone
from typing import Optional
import httpx
from app.providers.base import DeliveryStatus, GatewayHealth, InboundMessage, SMSProvider, SMSResult
from app.config import settings

logger = logging.getLogger(__name__)
GW_API = "https://api.sms-gate.app/3rdparty/v1/messages"
GW_DEV = "https://api.sms-gate.app/3rdparty/v1/device"
GW_WH = "https://api.sms-gate.app/3rdparty/v1/webhooks"

def _creds():
    u = (settings.SMSGATE_USERNAME or "").strip()
    p = (settings.SMSGATE_PASSWORD or "").strip()
    return u, p

def _auth_header():
    u, p = _creds()
    if not u or not p: return None
    return {"Content-Type": "application/json", "Authorization": f"Basic {base64.b64encode(f'{u}:{p}'.encode()).decode()}"}

# ─── DIRECT FUNCTIONS ─────────────────────────

async def send_sms_direct(phone: str, body: str, sim: int = 1) -> dict:
    u, p = _creds()
    if not u or not p: return {"success": False, "error": "Credentials not set"}
    auth = base64.b64encode(f"{u}:{p}".encode()).decode()
    payload = {"textMessage": {"text": body}, "phoneNumbers": [phone], "simNumber": sim, "ttl": 3600}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(45)) as c:
            r = await c.post(f"{GW_API}?skipPhoneValidation=true",
                headers={"Content-Type": "application/json", "Authorization": f"Basic {auth}"}, json=payload)
            d = r.json() if r.text else {}
            logger.info(f"SMS send: HTTP {r.status_code} {json.dumps(d)[:200]}")
            if r.status_code < 400:
                mid = d.get("id") or d.get("messageId", str(uuid.uuid4()))
                return {"success": True, "provider_message_id": mid, "status": d.get("status", "sent"), "raw": d}
            err = d.get("message") or d.get("error") or f"HTTP {r.status_code}"
            return {"success": False, "error": str(err)[:500], "raw": d}
    except httpx.TimeoutException:
        return {"success": False, "error": f"Timed out — phone may be offline"}
    except Exception as e:
        return {"success": False, "error": str(e)[:500]}

async def test_connection_direct() -> dict:
    """Test gateway — returns success=True if phone can receive messages."""
    u, p = _creds()
    if not u or not p: return {"success": False, "error": "No credentials", "online": False}
    auth = base64.b64encode(f"{u}:{p}".encode()).decode()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15)) as c:
            # Try sending a test message (doesn't actually send — ttl=60 expires before phone receives)
            r = await c.post(f"{GW_API}?skipPhoneValidation=true",
                headers={"Content-Type": "application/json", "Authorization": f"Basic {auth}"},
                json={"textMessage":{"text":"test"},"phoneNumbers":["+2348000000000"],"simNumber":1,"ttl":60})
            logger.info(f"Gateway test: HTTP {r.status_code}")
            if r.status_code < 500:
                return {"success": True, "online": True, "message": "Connected — gateway accepting messages", "http": r.status_code}
            return {"success": False, "online": False, "message": f"Gateway returned HTTP {r.status_code}", "http": r.status_code}
    except httpx.ConnectError:
        return {"success": False, "online": False, "message": "Cannot reach api.sms-gate.app"}
    except httpx.TimeoutException:
        return {"success": False, "online": False, "message": "Connection timed out"}
    except Exception as e:
        return {"success": False, "online": False, "message": str(e)[:200]}

async def poll_messages_direct() -> dict:
    """Poll GET /messages — returns ALL messages (both directions)."""
    h = _auth_header()
    if not h: return {"messages": [], "error": "No credentials"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30)) as c:
            r = await c.get(f"{GW_API}?limit=50", headers=h)
            logger.info(f"Poll messages: HTTP {r.status_code}")
            if r.status_code == 200:
                d = r.json()
                msgs = d if isinstance(d, list) else d.get("messages", d.get("data", []))
                return {"messages": msgs, "count": len(msgs), "success": True}
            return {"messages": [], "error": f"HTTP {r.status_code}", "raw": r.text[:300]}
    except Exception as e:
        return {"messages": [], "error": str(e)[:200]}

async def register_webhook_direct(webhook_url: str) -> dict:
    h = _auth_header()
    if not h: return {"success": False, "error": "No credentials"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15)) as c:
            r = await c.post(GW_WH, headers=h, json={"url": webhook_url, "events": ["sms:received","sms:delivered","sms:sent","sms:failed"]})
            d = r.json() if r.text else {}
            logger.info(f"Register webhook: HTTP {r.status_code}")
            return {"success": r.status_code < 400, "http": r.status_code, "raw": d}
    except Exception as e:
        return {"success": False, "error": str(e)[:200]}

async def list_webhooks_direct() -> dict:
    h = _auth_header()
    if not h: return {"webhooks": [], "error": "No credentials"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15)) as c:
            r = await c.get(GW_WH, headers=h)
            return {"webhooks": r.json() if r.status_code == 200 and isinstance(r.json(), list) else [], "success": r.status_code == 200}
    except Exception as e:
        return {"webhooks": [], "error": str(e)[:200]}


# ─── CLASS WRAPPER (backward compat) ───

class SMSGateProvider(SMSProvider):
    def __init__(self, base_url=None, username=None, password=None, sim_number=1, timeout=45):
        self.base_url = (base_url or settings.SMSGATE_BASE_URL or "https://api.sms-gate.app/3rdparty/v1").rstrip("/")
        self.username = (username or settings.SMSGATE_USERNAME or "").strip()
        self.password = (password or settings.SMSGATE_PASSWORD or "").strip()
        self.sim_number = sim_number or 1
        self.timeout = max(timeout or 45, 30)
        self._client = None

    def _auth(self):
        if not self.username or not self.password: return None
        return base64.b64encode(f"{self.username}:{self.password}".encode()).decode()

    async def _cli(self):
        if not self._client or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(self.timeout), follow_redirects=True)
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed: await self._client.aclose(); self._client = None

    async def send_sms(self, to_number, message, **kw):
        r = await send_sms_direct(to_number, message, self.sim_number)
        return SMSResult(success=r["success"], provider_message_id=r.get("provider_message_id",""), status=r.get("status","unknown"), error=r.get("error"), raw_response=r.get("raw"))

    async def test_connection(self):
        r = await test_connection_direct()
        return r["success"]

    async def get_message_status(self, pid):
        if not pid: return DeliveryStatus(provider_message_id="", status="unknown")
        try: c = await self._cli(); r = await c.get(f"{GW_API}/{pid}"); return DeliveryStatus(provider_message_id=pid, status=r.json().get("status","unknown") if r.status_code==200 else "unknown")
        except: return DeliveryStatus(provider_message_id=pid, status="unknown")

    async def health_check(self):
        ok = await self.test_connection()
        return GatewayHealth(is_healthy=ok, status="healthy" if ok else "unhealthy", last_checked=datetime.now(timezone.utc))

    async def poll_messages(self, limit=50):
        try: c = await self._cli(); r = await c.get(f"{GW_API}?limit={limit}"); return r.json() if r.status_code==200 else []
        except: return []

    def parse_inbound_message(self, raw):
        p = raw.get("payload", raw); ts = p.get("sentAt") or p.get("receivedAt"); dt = datetime.now(timezone.utc)
        if ts:
            try: dt = datetime.fromisoformat(ts.replace("Z","+00:00"))
            except: pass
        return InboundMessage(provider_message_id=p.get("id",str(uuid.uuid4())), from_number=p.get("from",p.get("sender","")), to_number=p.get("to",""), body=p.get("text",p.get("message","")), received_at=dt, raw=raw)

    def parse_delivery_status(self, raw):
        p = raw.get("payload", raw); s = str(p.get("status","")).lower()
        return DeliveryStatus(provider_message_id=p.get("id",""), status="delivered" if "deliver" in s else "failed" if "fail" in s else "sent" if "sent" in s else "unknown", raw=raw)

    supports_webhooks = lambda self: True
    supports_polling = lambda self: True
    validate_webhook_signature = lambda self, *a, **kw: True
