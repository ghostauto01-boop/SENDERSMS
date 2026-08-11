"""SMS-Gate.app — send, test, webhook, status poll."""
import base64, json, logging, uuid
from datetime import datetime, timezone
from typing import Optional
import httpx
from app.providers.base import DeliveryStatus, GatewayHealth, InboundMessage, SMSProvider, SMSResult
from app.config import settings

logger = logging.getLogger(__name__)
GW_API = "https://api.sms-gate.app/3rdparty/v1/messages"
GW_WH = "https://api.sms-gate.app/3rdparty/v1/webhooks"

def _creds():
    u=(settings.SMSGATE_USERNAME or "").strip()
    p=(settings.SMSGATE_PASSWORD or "").strip()
    return u,p

async def send_sms_direct(phone,body,sim=1):
    u,p=_creds()
    if not u or not p: return {"success":False,"error":"No credentials"}
    auth=base64.b64encode(f"{u}:{p}".encode()).decode()
    payload={"textMessage":{"text":body},"phoneNumbers":[phone],"simNumber":sim,"ttl":3600}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(45)) as c:
            r=await c.post(f"{GW_API}?skipPhoneValidation=true",
                headers={"Content-Type":"application/json","Authorization":f"Basic {auth}"},json=payload)
            d=r.json() if r.text else {}
            logger.info(f"SMS: HTTP {r.status_code} {json.dumps(d)[:200]}")
            if r.status_code<400:
                mid=d.get("id") or d.get("messageId",str(uuid.uuid4()))
                return {"success":True,"provider_message_id":mid,"status":d.get("state","sent"),"raw":d}
            return {"success":False,"error":str(d.get("message") or d.get("error") or f"HTTP {r.status_code}")[:500],"raw":d}
    except httpx.TimeoutException: return {"success":False,"error":"Timed out"}
    except Exception as e: return {"success":False,"error":str(e)[:500]}

async def test_connection_direct():
    u,p=_creds()
    if not u or not p: return {"success":False,"online":False,"message":"No credentials"}
    auth=base64.b64encode(f"{u}:{p}".encode()).decode()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15)) as c:
            r=await c.post(f"{GW_API}?skipPhoneValidation=true",
                headers={"Content-Type":"application/json","Authorization":f"Basic {auth}"},
                json={"textMessage":{"text":"test"},"phoneNumbers":["+2348000000000"],"simNumber":1,"ttl":60})
            return {"success":r.status_code<500,"online":r.status_code<500,"http":r.status_code}
    except Exception: return {"success":False,"online":False,"message":"Cannot reach"}

async def poll_status_for_ids(ids:list):
    """Check delivery status for specific provider message IDs."""
    u,p=_creds()
    if not u or not p: return[]
    auth=base64.b64encode(f"{u}:{p}".encode()).decode()
    results=[]
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30)) as c:
            for mid in ids:
                if not mid: continue
                try:
                    r=await c.get(f"{GW_API}/{mid}",headers={"Content-Type":"application/json","Authorization":f"Basic {auth}"})
                    if r.status_code==200:
                        d=r.json()
                        state=(d.get("state")or"").lower()
                        if "deliver" in state: st="delivered"
                        elif "fail" in state: st="failed"
                        elif "sent" in state: st="sent"
                        elif "process" in state: st="sent"
                        else: st=state
                        results.append({"provider_message_id":mid,"status":st})
                except Exception: pass
        return results
    except Exception: return[]

# SMS-Gate registers ONE event per webhook. Sending {"events": [...]} is silently
# ignored by the API: the record is created with an empty event, so the device
# never matches it and no webhook is ever delivered. Each event needs its own POST
# with the singular {"event": "..."} field.
# https://docs.sms-gate.app/features/webhooks/
# Used to recognise our own (possibly stale) registrations on the gateway.
WEBHOOK_PATH_SUFFIX = "/api/v1/webhooks/smsgateway"

INBOUND_EVENTS = ("sms:received", "sms:data-received", "mms:received", "mms:downloaded")
STATUS_EVENTS = ("sms:sent", "sms:delivered", "sms:failed", "sms:cancelled")
DEFAULT_EVENTS = INBOUND_EVENTS + STATUS_EVENTS


async def register_webhook_direct(url, events=None):
    """Register `url` for each event, one registration per event.

    Idempotent: existing registrations for the same (url, event) pair are left
    alone, and stale registrations pointing at a different URL for the same path
    are deleted so the device stops retrying a dead endpoint.
    """
    u, p = _creds()
    if not u or not p:
        return {"success": False, "error": "No credentials"}
    if not url:
        return {"success": False, "error": "No webhook URL"}

    events = tuple(events or DEFAULT_EVENTS)
    auth = base64.b64encode(f"{u}:{p}".encode()).decode()
    headers = {"Content-Type": "application/json", "Authorization": f"Basic {auth}"}
    created, kept, deleted, errors = [], [], [], []

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20)) as c:
            existing = []
            try:
                r = await c.get(GW_WH, headers=headers)
                if r.status_code == 200:
                    data = r.json()
                    existing = data if isinstance(data, list) else data.get("webhooks", [])
            except Exception as e:
                logger.warning(f"register_webhook: list failed: {e}")

            have = set()
            for w in existing:
                if not isinstance(w, dict):
                    continue
                w_url, w_event, w_id = w.get("url", ""), w.get("event", ""), w.get("id")
                if w_url == url:
                    have.add(w_event)
                    kept.append(w_event)
                elif w_id and w_url.endswith(WEBHOOK_PATH_SUFFIX) and w_event in events:
                    # Same integration, old deployment URL -> remove it.
                    try:
                        d = await c.delete(f"{GW_WH}/{w_id}", headers=headers)
                        if d.status_code < 400:
                            deleted.append(w_url)
                    except Exception:
                        pass

            for event in events:
                if event in have:
                    continue
                try:
                    r = await c.post(GW_WH, headers=headers, json={"url": url, "event": event})
                    if r.status_code < 400:
                        created.append(event)
                    else:
                        errors.append(f"{event}: HTTP {r.status_code} {r.text[:120]}")
                except Exception as e:
                    errors.append(f"{event}: {str(e)[:120]}")

        registered = sorted(set(kept) | set(created))
        return {
            "success": not errors and bool(set(events) & set(registered)),
            "url": url,
            "registered": registered,
            "created": created,
            "kept": sorted(set(kept)),
            "deleted_stale": deleted,
            "errors": errors,
        }
    except Exception as e:
        logger.warning(f"register_webhook error: {e}")
        return {"success": False, "error": str(e)[:300]}


async def list_webhooks_direct():
    u,p=_creds()
    if not u or not p: return{"webhooks":[]}
    auth=base64.b64encode(f"{u}:{p}".encode()).decode()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15)) as c:
            r=await c.get(GW_WH,headers={"Content-Type":"application/json","Authorization":f"Basic {auth}"})
            if r.status_code!=200: return{"webhooks":[],"http":r.status_code,"error":r.text[:300]}
            data=r.json()
            return{"webhooks":data if isinstance(data,list) else data.get("webhooks",[]),"http":200}
    except Exception as e: return{"webhooks":[],"error":str(e)[:300]}


async def delete_webhook_direct(webhook_id:str):
    u,p=_creds()
    if not u or not p or not webhook_id: return{"success":False}
    auth=base64.b64encode(f"{u}:{p}".encode()).decode()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15)) as c:
            r=await c.delete(f"{GW_WH}/{webhook_id}",
                headers={"Content-Type":"application/json","Authorization":f"Basic {auth}"})
            return{"success":r.status_code<400,"http":r.status_code}
    except Exception as e: return{"success":False,"error":str(e)[:300]}

async def get_devices_direct():
    """Get list of connected devices. Returns [{"id":"...", "name":"...", ...}]."""
    u,p=_creds()
    if not u or not p: return[]
    auth=base64.b64encode(f"{u}:{p}".encode()).decode()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15)) as c:
            r=await c.get("https://api.sms-gate.app/3rdparty/v1/devices",
                headers={"Content-Type":"application/json","Authorization":f"Basic {auth}"})
            if r.status_code==200:
                data=r.json()
                return data if isinstance(data,list) else data.get("devices",data.get("data",[]))
            logger.warning(f"get_devices: HTTP {r.status_code} {r.text[:200]}")
            return[]
    except Exception as e:
        logger.warning(f"get_devices error: {e}")
        return[]

async def get_primary_device_id():
    """Best-effort device id for the account, or "" when nothing is connected."""
    devices = await get_devices_direct()
    for d in devices:
        if isinstance(d, dict):
            did = d.get("id") or d.get("deviceId") or ""
            if did:
                return did
    return ""


async def export_inbox_direct(device_id:str, since:str=None, until:str=None):
    """
    Trigger inbox export. Device will push messages as webhooks.
    This is THE way to receive historical SMS content from SMS-Gate.app.
    
    POST /3rdparty/v1/messages/inbox/export
    Body: {"deviceId": "...", "since": "2024-01-01T00:00:00Z", "until": "2024-12-31T23:59:59Z"}
    """
    u,p=_creds()
    if not u or not p: return{"success":False,"error":"No credentials"}
    auth=base64.b64encode(f"{u}:{p}".encode()).decode()
    payload={"deviceId":device_id}
    if since: payload["since"]=since
    if until: payload["until"]=until
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30)) as c:
            r=await c.post("https://api.sms-gate.app/3rdparty/v1/messages/inbox/export",
                headers={"Content-Type":"application/json","Authorization":f"Basic {auth}"},
                json=payload)
            logger.info(f"inbox_export: HTTP {r.status_code} {r.text[:300]}")
            if r.status_code<400:
                return{"success":True,"http":r.status_code,"body":r.text[:500]}
            return{"success":False,"http":r.status_code,"error":r.text[:500]}
    except Exception as e:
        logger.warning(f"inbox_export error: {e}")
        return{"success":False,"error":str(e)[:500]}

class SMSGateProvider(SMSProvider):
    def __init__(self,base_url=None,username=None,password=None,sim_number=1,timeout=45):
        self.base_url=(base_url or settings.SMSGATE_BASE_URL or "https://api.sms-gate.app/3rdparty/v1").rstrip("/")
        self.username=(username or settings.SMSGATE_USERNAME or"").strip()
        self.password=(password or settings.SMSGATE_PASSWORD or"").strip()
        self.sim_number=sim_number or 1
        self.timeout=max(timeout or 45,30)
        self._client=None
    def _auth(self):
        if not self.username or not self.password: return None
        return base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
    async def _cli(self):
        if not self._client or self._client.is_closed:
            self._client=httpx.AsyncClient(timeout=httpx.Timeout(self.timeout),follow_redirects=True)
        return self._client
    async def close(self):
        if self._client and not self._client.is_closed: await self._client.aclose(); self._client=None
    async def send_sms(self,to_number,message,**kw):
        r=await send_sms_direct(to_number,message,self.sim_number)
        return SMSResult(success=r["success"],provider_message_id=r.get("provider_message_id",""),status=r.get("status","unknown"),error=r.get("error"),raw_response=r.get("raw"))
    async def test_connection(self): return (await test_connection_direct())["success"]
    async def get_message_status(self,pid):
        if not pid: return DeliveryStatus(provider_message_id="",status="unknown")
        try:
            auth=self._auth()
            if not auth: return DeliveryStatus(provider_message_id=pid,status="unknown")
            c=await self._cli()
            r=await c.get(f"{self.base_url}/messages/{pid}",
                headers={"Authorization":f"Basic {auth}","Content-Type":"application/json"})
            if r.status_code==200:
                d=r.json();s=(d.get("state")or"").lower()
                return DeliveryStatus(provider_message_id=pid,status="delivered"if"deliver"in s else"failed"if"fail"in s else"sent"if"sent"in s else"unknown",raw=d)
            return DeliveryStatus(provider_message_id=pid,status="unknown")
        except Exception: return DeliveryStatus(provider_message_id=pid,status="unknown")
    async def health_check(self):
        ok=await self.test_connection()
        return GatewayHealth(is_healthy=ok,status="healthy"if ok else"unhealthy",last_checked=datetime.now(timezone.utc))
    def parse_inbound_message(self, raw):
        """Map an SMS-Gate webhook envelope onto InboundMessage.

        Envelope: {deviceId, event, id, payload:{...}, webhookId}
        sms:received payload uses `sender`/`message`/`recipient`/`receivedAt`
        (NOT `from`/`text` — that shape does not exist in this API).
        """
        p = raw.get("payload", raw) if isinstance(raw, dict) else {}
        event = (raw.get("event") or "sms:received") if isinstance(raw, dict) else "sms:received"
        received = self._parse_iso(p.get("receivedAt") or p.get("sentAt")) or datetime.now(timezone.utc)

        if event == "mms:downloaded":
            parts = []
            if p.get("subject"): parts.append(f"[{p['subject']}]")
            if p.get("body"): parts.append(str(p["body"]))
            for a in (p.get("attachments") or []):
                if isinstance(a, dict):
                    parts.append(f"[attachment: {a.get('name') or a.get('mimeType') or 'file'}]")
            body = " ".join(parts).strip() or "[MMS]"
        elif event == "mms:received":
            body = "[MMS received — download pending]"
        elif event == "sms:data-received":
            body = "[data message]"
        else:
            body = p.get("message") or ""

        # The device's own number is the recipient for inbound traffic.
        return InboundMessage(
            provider_message_id=str(p.get("messageId") or p.get("id") or uuid.uuid4()),
            from_number=str(p.get("sender") or "").strip(),
            to_number=str(p.get("recipient") or "").strip(),
            body=body,
            received_at=received,
            raw=raw,
        )

    # Status is carried by the event NAME, not a `status` field in the payload.
    _EVENT_STATUS = {
        "sms:sent": "sent",
        "sms:delivered": "delivered",
        "sms:failed": "failed",
        "sms:cancelled": "cancelled",
    }

    def parse_delivery_status(self, raw):
        p = raw.get("payload", raw) if isinstance(raw, dict) else {}
        event = (raw.get("event") or "") if isinstance(raw, dict) else ""
        status = self._EVENT_STATUS.get(event)
        if status is None:
            # Fall back to a state string when polling rather than webhooking.
            s = str(p.get("state") or p.get("status") or "").lower()
            status = ("delivered" if "deliver" in s else "failed" if "fail" in s
                      else "sent" if "sent" in s else "unknown")
        return DeliveryStatus(
            provider_message_id=str(p.get("messageId") or p.get("id") or ""),
            status=status,
            error=p.get("reason") if status == "failed" else None,
            raw=raw,
        )

    @staticmethod
    def _parse_iso(value):
        if not value: return None
        try:
            d = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
        return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)

    supports_webhooks=lambda self:True
    supports_polling=lambda self:True

    @staticmethod
    def validate_webhook_signature(
        raw_body: bytes,
        signature: str,
        timestamp: str,
        secret: str,
        max_age_seconds: int = 300,
    ) -> bool:
        """Verify an SMS-Gate.app webhook signature.

        SMS-Gate signs ``raw_body + timestamp`` with HMAC-SHA256 using the
        signing key from Settings -> Webhooks -> Signing Key, and sends the
        lowercase hex digest in ``X-Signature`` with the Unix seconds
        timestamp in ``X-Timestamp``.
        """
        import hashlib
        import hmac
        import time

        if not (secret and signature and timestamp):
            return False

        # Reject stale/replayed deliveries before doing any crypto work.
        try:
            sent_at = int(str(timestamp).strip())
        except (TypeError, ValueError):
            return False
        if abs(int(time.time()) - sent_at) > max_age_seconds:
            logger.warning("Webhook rejected: timestamp outside tolerance window")
            return False

        if isinstance(raw_body, str):
            raw_body = raw_body.encode("utf-8")

        message = raw_body + str(timestamp).strip().encode("utf-8")
        expected = hmac.new(
            secret.encode("utf-8"), message, hashlib.sha256
        ).hexdigest()

        return hmac.compare_digest(expected, str(signature).strip().lower())
