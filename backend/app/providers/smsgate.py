"""SMS Gateway — send with full debug, poll inbox for incoming."""
import base64, json, logging, uuid
from datetime import datetime, timezone
from typing import Optional
import httpx
from app.providers.base import DeliveryStatus, GatewayHealth, InboundMessage, SMSProvider, SMSResult
from app.config import settings

logger = logging.getLogger(__name__)
GW_API = "https://api.sms-gate.app/3rdparty/v1/messages"
GW_DEV = "https://api.sms-gate.app/3rdparty/v1/device"

class SMSGateProvider(SMSProvider):
    def __init__(self, base_url=None, username=None, password=None, sim_number=1, timeout=30):
        self.base_url = (base_url or settings.SMSGATE_BASE_URL or "https://api.sms-gate.app/3rdparty/v1").rstrip("/")
        self.username = (username or settings.SMSGATE_USERNAME or "").strip()
        self.password = (password or settings.SMSGATE_PASSWORD or "").strip()
        self.sim_number = sim_number or 1
        self.timeout = max(timeout or 30, 30)
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

    async def send_sms(self, to_number, message, sender_id=None, idempotency_key=None):
        u = self.username; p = self.password
        if not u or not p: return SMSResult(success=False, error="Username and password not set")
        auth = base64.b64encode(f"{u}:{p}".encode()).decode()
        payload = {"textMessage":{"text":message},"phoneNumbers":[to_number],"simNumber":self.sim_number,"ttl":3600}
        logger.info(f"SEND to {to_number} via {GW_API} user={u[:4]}... sim={self.sim_number}")
        try:
            c = await self._cli()
            r = await c.post(f"{GW_API}?skipPhoneValidation=true",
                headers={"Content-Type":"application/json","Authorization":f"Basic {auth}"},
                json=payload, timeout=self.timeout)
            try: d = r.json()
            except: d = {"raw": r.text}
            logger.info(f"SMS RESP: HTTP={r.status_code} body={json.dumps(d)[:400]}")
            if r.status_code >= 400:
                err = d.get("message") or d.get("error") or f"HTTP {r.status_code}"
                return SMSResult(success=False, error=str(err)[:500], raw_response=d)
            msg_id = d.get("id") or d.get("messageId", "")
            if not msg_id:
                return SMSResult(success=False, error=f"No message ID in response: {json.dumps(d)[:200]}", raw_response=d)
            logger.info(f"SMS OK: id={msg_id}")
            return SMSResult(success=True, provider_message_id=msg_id, status="sent",
                           segments=1 if len(message)<=160 else (len(message)+152)//153, raw_response=d)
        except httpx.TimeoutException:
            return SMSResult(success=False, error=f"Timed out ({self.timeout}s)")
        except Exception as e:
            logger.error(f"SMS err: {e}")
            return SMSResult(success=False, error=str(e)[:500])

    async def test_connection(self) -> bool:
        u = self.username; p = self.password
        if not u or not p: return False
        auth = base64.b64encode(f"{u}:{p}".encode()).decode()
        try:
            c = await self._cli()
            r = await c.get(GW_DEV, headers={"Authorization":f"Basic {auth}"}, timeout=10)
            logger.info(f"TEST /device: HTTP={r.status_code}")
            if r.status_code < 400: return True
            r2 = await c.post(f"{GW_API}?skipPhoneValidation=true",
                headers={"Content-Type":"application/json","Authorization":f"Basic {auth}"},
                json={"textMessage":{"text":"test"},"phoneNumbers":["+2348000000000"],"simNumber":self.sim_number,"ttl":60}, timeout=10)
            logger.info(f"TEST fallback: HTTP={r2.status_code}")
            return r2.status_code < 500
        except Exception as e:
            logger.warning(f"TEST failed: {e}")
            return False

    async def get_message_status(self, pid):
        if not pid: return DeliveryStatus(provider_message_id="", status="unknown")
        try:
            c = await self._cli(); r = await c.get(f"https://api.sms-gate.app/3rdparty/v1/messages/{pid}")
            return DeliveryStatus(provider_message_id=pid, status=r.json().get("status","unknown") if r.status_code==200 else "unknown")
        except: return DeliveryStatus(provider_message_id=pid, status="unknown")

    async def health_check(self):
        ok = await self.test_connection()
        return GatewayHealth(is_healthy=ok, status="healthy" if ok else "unhealthy", last_checked=datetime.now(timezone.utc))

    async def poll_inbox(self):
        try:
            c = await self._cli()
            r = await c.get("https://api.sms-gate.app/3rdparty/v1/inbox")
            if r.status_code == 200:
                d = r.json()
                result = d if isinstance(d, list) else d.get("messages", d.get("data", []))
                logger.info(f"INBOX poll: got {len(result)} messages")
                return result
            logger.warning(f"INBOX poll: HTTP {r.status_code}")
            return []
        except Exception as e:
            logger.warning(f"INBOX poll error: {e}")
            return []

    async def poll_messages(self, limit=50):
        try: c = await self._cli(); r = await c.get(f"https://api.sms-gate.app/3rdparty/v1/messages?limit={limit}"); return r.json() if r.status_code==200 else []
        except: return []

    def parse_inbound_message(self, raw):
        p = raw.get("payload", raw); ts = p.get("sentAt") or p.get("receivedAt"); dt = datetime.now(timezone.utc)
        if ts:
            try: dt = datetime.fromisoformat(ts.replace("Z","+00:00"))
            except: pass
        return InboundMessage(provider_message_id=p.get("id",str(uuid.uuid4())),
            from_number=p.get("from",p.get("sender","")), to_number=p.get("to",""),
            body=p.get("text",p.get("message","")), received_at=dt, raw=raw)

    def parse_delivery_status(self, raw):
        p = raw.get("payload", raw); s = str(p.get("status","")).lower()
        return DeliveryStatus(provider_message_id=p.get("id",""),
            status="delivered" if "deliver" in s else "failed" if "fail" in s else "sent" if "sent" in s else "unknown", raw=raw)

    supports_webhooks = lambda self: True
    supports_polling = lambda self: True
    validate_webhook_signature = lambda self, *a, **kw: True
