"""P0-4: what an assistant can actually read through the MCP tools, 5,000 contacts.

Counts the COMPLETE contact rows obtainable through `export_contacts_csv` (following
whatever continuation the tool offers), and checks that `search_contacts per_page=100`
is parseable JSON and says whether it was cut.
"""
import csv
import io
import json

import _common as c


def split(text):
    """(json_object|None, csv_body, next_cursor) for either response style."""
    try:
        obj = json.loads(text)
    except ValueError:
        # old style: 'METHOD path -> HTTP n', a blank line, then {"raw": "<the CSV, cut at 4,000 chars>"}
        rest = text.partition("\n\n")[2] or text
        try:
            body = json.loads(rest).get("raw", "")
        except ValueError:
            body = rest
        return None, body, None
    data = obj.get("data") or {}
    return obj, data.get("csv", ""), obj.get("next_cursor")


async def main():
    from app.mcp import server
    from app.mcp.registry import TOOLS_BY_NAME
    from app.models.contact import Contact

    engine, factory = await c.new_db()
    async with factory() as db:
        db.add_all([Contact(
            first_name=f"Person{i}", last_name="Test", business_name=f"Business number {i} Ltd",
            email=f"p{i}@company{i}.io", phone_number=f"+23480312{i:05d}", city="Lagos",
            notes="note " * 12) for i in range(5000)])
        await db.flush()
        c.sign_in_as_admin(db)
        server.CURRENT_ADMIN_ID.set(1)

        class Stub:
            def add(self, row): pass
            async def flush(self): pass

        async def call(tool, **args):
            out = await server.run_tool(Stub(), server.TokenView(
                id=1, name="t", scope="write", prefix="mcp_t"), TOOLS_BY_NAME[tool], args)
            return out.result["content"][0]["text"]

        print("export_contacts_csv  (5,000 contacts in the database)")
        text = await call("export_contacts_csv")
        obj, body, cursor = split(text)
        rows = list(csv.reader(io.StringIO(body)))
        header, width = rows[0], len(rows[0])
        first_rows = [r for r in rows[1:] if len(r) == width]  # a cut-off last row is not "complete"
        says_partial = bool(obj and (obj.get("truncated") or obj.get("next_cursor")))
        print(f"  first call: {len(text):,} characters, {len(first_rows)} complete data rows, "
              f"tells the caller it is partial: {says_partial}")
        total, pages = len(first_rows), 1
        while cursor and pages < 500:
            obj, body, cursor = split(await call("export_contacts_csv", cursor=cursor))
            total += len([r for r in csv.reader(io.StringIO(body)) if len(r) == width])
            pages += 1
        print(f"  following the continuation to its end: {total} rows over {pages} page(s)")

        print("search_contacts per_page=100")
        text = await call("search_contacts", per_page=100)
        try:
            obj, valid = json.loads(text), True
        except ValueError:
            obj, valid = None, False
        print(f"  {len(text):,} characters; valid JSON: {valid}")
        if obj:
            print(f"  truncated={obj.get('truncated')} returned={obj.get('returned')} "
                  f"total={obj.get('total')} next_cursor={obj.get('next_cursor')}")
    await engine.dispose()


c.run(main())
