"""
Debug script: Fetch raw SMS-Gate.app messages and show exact format.
Run: cd backend && python debug_poll.py
"""
import asyncio, base64, json, httpx

U = "_O48UB"
P = "nw_e7wyhwjwubp"
BASE = "https://api.sms-gate.app/3rdparty/v1"

async def main():
    auth = base64.b64encode(f"{U}:{P}".encode()).decode()
    headers = {"Content-Type": "application/json", "Authorization": f"Basic {auth}"}

    async with httpx.AsyncClient(timeout=httpx.Timeout(30)) as c:
        # Try different endpoints and limits
        for limit in [50, 25, 10, 5]:
            print(f"\n{'='*60}")
            print(f"LIMIT={limit}")
            print(f"{'='*60}")
            try:
                r = await c.get(f"{BASE}/messages?limit={limit}", headers=headers)
                print(f"HTTP {r.status_code}")
                print(f"Response length: {len(r.text)} chars")

                if r.status_code == 200:
                    data = r.json()
                    if isinstance(data, list):
                        msgs = data
                    elif isinstance(data, dict):
                        print(f"Top-level keys: {list(data.keys())}")
                        msgs = data.get("messages", data.get("data", []))
                    else:
                        print(f"Unexpected type: {type(data)}")
                        msgs = []

                    print(f"Messages count: {len(msgs)}")

                    for i, msg in enumerate(msgs[:3]):  # Show first 3 in detail
                        print(f"\n--- Message {i+1} ---")
                        print(json.dumps(msg, indent=2, default=str))

                    if len(msgs) > 3:
                        print(f"\n... and {len(msgs)-3} more messages")

                    # Summary of fields present
                    all_keys = set()
                    for msg in msgs:
                        all_keys.update(msg.keys())
                    print(f"\nAll fields found: {sorted(all_keys)}")

                    # Check recipients format
                    for msg in msgs:
                        recips = msg.get("recipients", [])
                        if recips:
                            print(f"\nSample recipients: {json.dumps(recips[:2], indent=2)}")
                            break

                    break  # Stop after first successful attempt
                else:
                    print(f"Body: {r.text[:500]}")
            except Exception as e:
                print(f"Error: {e}")

        # Also try inbox endpoint
        print(f"\n{'='*60}")
        print("INBOX ENDPOINT")
        print(f"{'='*60}")
        try:
            r = await c.get(f"{BASE}/inbox?limit=10", headers=headers)
            print(f"HTTP {r.status_code}: {r.text[:300]}")
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
