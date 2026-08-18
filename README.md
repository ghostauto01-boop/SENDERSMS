# SENDERSMS

Two-way SMS marketing & conversation platform (campaigns, sequences,
follow-ups, auto-replies, inbox) with pluggable SMS gateways.

## SMS gateways

The app supports two interchangeable gateways, selectable from
**Settings → SMS Gateway**:

- **SMS-Gate.app** (`smsgate`) — default; sends through your own Android
  phone via its SIM.
- **Dmobili.com** (`dmobili`) — hosted Nigerian/global bulk SMS provider
  (two-way capable with a dedicated number). See
  [docs/DMOBILI-GATEWAY.md](docs/DMOBILI-GATEWAY.md) for setup, the
  provider's API capabilities, and the required environment variables.

## Quick pointers

- `START_HERE.md` — full setup walkthrough
- `DEPLOY.md` — deployment guide
- `SCHEDULING-AND-AUTOREPLY.md` — scheduling & auto-reply internals
- `backend/` — FastAPI backend, `frontend/` — React SPA
