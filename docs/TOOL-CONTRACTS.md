# Assistant tools: the contract with the endpoints

The tools in `backend/app/mcp/registry.py` are hand-written descriptions of calls to the app's own
endpoints. They drift: an endpoint changes, or a parameter is declared where the endpoint does not read it,
and the tool quietly does nothing (or something else). Three bugs of that family were found in the QA sweep
(`reply_to_conversation` always failing, `list_followups` always empty, `update_contact` advertising a default
country). The fix is a rule plus a test that enforces it.

## The rules

1. **A tool's parameters are what the endpoint reads, where it reads them.** A query name the endpoint does not
   declare, a body field its model does not have, or a path placeholder that is not in the route is a CI failure
   (`backend/tests/test_sprint2_tool_contracts.py`, which walks every tool against the app's own OpenAPI document).
2. **An advertised default is what happens.** The dispatcher now *applies* a declared `default` when the caller
   leaves the argument out (it used to be documentation only: the argument was dropped and the endpoint's own default
   applied, so a tool could say "channel: all" while the endpoint used "sms"). So do not declare a default on a field of
   an update call: it would overwrite whatever it was not asked to change.
3. **A default that differs from the endpoint's own must be on purpose.** The assistant's defaults are allowed to be
   different (a page size of 25 so a reply fits; "all channels" where the dashboard page is SMS-only) but each one is listed in
   `INTENTIONAL_DEFAULTS` in that test with the reason. An unlisted difference fails.
4. **Enums are enforced before the call.** A value outside a parameter's `enum` is refused with the allowed values,
   instead of being forwarded for the endpoint to answer with whatever it happens to answer (a bad `mark` was a 405). A
   different case is accepted in its canonical spelling.
5. **What the endpoint requires, the tool must be able to send**, and a tool's `maximum` can never exceed the endpoint's.

## Adding or changing a tool

Declare the `Param`s exactly as the endpoint reads them, run `pytest backend/tests/test_sprint2_tool_contracts.py`,
and either fix what it reports or list the intentional default with its reason. Tools that are one declared HTTP call are
all covered (the four hand-written handlers -- the guide, the endpoint catalogue, the generic request tool and
`preview_template` -- are the only exceptions, and `preview_template` calls the same endpoint as `render_text_preview`).

## Not done

The brief asked for tool schemas to be *generated* from the endpoint models. They are still written by hand; the check above is what
keeps them honest. Generation would remove the need for the check but is a larger change.
