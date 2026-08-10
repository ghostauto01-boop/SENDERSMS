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
    except: return {"success":False,"online":False,"message":"Cannot reach"}

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
                except: pass
        return results
    except: return[]

async def register_webhook_direct(url):
    u,p=_creds()
    if not u or not p: return{"success":False}
    auth=base64.b64encode(f"{u}:{p}".encode()).decode()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15)) as c:
            r=await c.post(GW_WH,headers={"Content-Type":"application/json","Authorization":f"Basic {auth}"},
                json={"url":url,"events":["sms:received","sms:delivered","sms:sent","sms:failed"]})
            return{"success":r.status_code<400,"http":r.status_code}
    except: return{"success":False}

async def list_webhooks_direct():
    u,p=_creds()
    if not u or not p: return{"webhooks":[]}
    auth=base64.b64encode(f"{u}:{p}".encode()).decode()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15)) as c:
            r=await c.get(GW_WH,headers={"Content-Type":"application/json","Authorization":f"Basic {auth}"})
            return{"webhooks":r.json() if r.status_code==200 else[]}
    except: return{"webhooks":[]}

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
            c=await self._cli();r=await c.get(f"{GW_API}/{pid}")
            if r.status_code==200:
                d=r.json();s=(d.get("state")or"").lower()
                return DeliveryStatus(provider_message_id=pid,status="delivered"if"deliver"in s else"failed"if"fail"in s else"sent"if"sent"in s else"unknown",raw=d)
            return DeliveryStatus(provider_message_id=pid,status="unknown")
        except: return DeliveryStatus(provider_message_id=pid,status="unknown")
    async def health_check(self):
        ok=await self.test_connection()
        return GatewayHealth(is_healthy=ok,status="healthy"if ok else"unhealthy",last_checked=datetime.now(timezone.utc))
    def parse_inbound_message(self,raw):
        p=raw.get("payload",raw);ts=p.get("sentAt")or p.get("receivedAt");dt=datetime.now(timezone.utc)
        if ts:
            try:dt=datetime.fromisoformat(ts.replace("Z","+00:00"))
            except:pass
        return InboundMessage(provider_message_id=p.get("id",str(uuid.uuid4())),from_number=p.get("from",p.get("sender","")),to_number=p.get("to",""),body=p.get("text",p.get("message","")),received_at=dt,raw=raw)
    def parse_delivery_status(self,raw):
        p=raw.get("payload",raw);s=str(p.get("status","")).lower()
        return DeliveryStatus(provider_message_id=p.get("id",""),status="delivered"if"deliver"in s else"failed"if"fail"in s else"sent"if"sent"in s else"unknown",raw=raw)
    supports_webhooks=lambda self:True
    supports_polling=lambda self:True
    validate_webhook_signature=lambda self,*a,**kw:True
