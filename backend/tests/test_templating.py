"""Cover the single placeholder renderer used by every send path.

Before `app.utils.templating` existed there were four independent, subtly
different renderers (campaign task, /send, sms_service, template preview).
The live consequences, all reproduced here:

1. A contact with no first name produced "Hi , ..." -- a visibly broken SMS.
2. /send and sms_service substituted only 4 of the 8 supported fields, so
   real customers received texts containing the literal string "{{website}}".
3. Unknown tokens and whitespace variants ("{{ first_name }}") were never
   touched and shipped verbatim.
4. Preview rendered differently from what was actually sent.
"""

import pytest

from app.models.contact import Contact
from app.utils.templating import GENERIC_NAME, build_context, render_template


def contact(**kw):
    base = dict(phone_number="+2348030000000", country="Nigeria")
    base.update(kw)
    return Contact(**base)


class TestFieldCoverage:
    def test_all_documented_fields_substitute(self):
        c = contact(
            first_name="Ada", last_name="Obi", business_name="Gwarinpa Autos",
            email="ada@example.com", city="Abuja", state="FCT",
            website="gwarinpa.ng", industry="Automotive",
        )
        body = ("{{first_name}}|{{last_name}}|{{business_name}}|{{phone_number}}|"
                "{{email}}|{{city}}|{{state}}|{{country}}|{{website}}|{{industry}}")
        assert render_template(body, c) == (
            "Ada|Obi|Gwarinpa Autos|+2348030000000|ada@example.com|"
            "Abuja|FCT|Nigeria|gwarinpa.ng|Automotive"
        )

    def test_website_is_not_shipped_literally(self):
        """The exact bug seen at the gateway: {{website}} reached the handset."""
        out = render_template("Visit {{website}} today", contact(website="acme.ng"))
        assert out == "Visit acme.ng today"
        assert "{{" not in out

    def test_full_name_and_name_aliases(self):
        c = contact(first_name="Ada", last_name="Obi")
        assert render_template("{{full_name}}", c) == "Ada Obi"
        assert render_template("{{name}}", c) == "Ada Obi"


class TestNameDegradation:
    def test_missing_first_name_falls_back_to_business(self):
        c = contact(business_name="Zenith Motors")
        assert render_template("Hi {{first_name}}", c) == "Hi Zenith Motors"

    def test_no_name_at_all_uses_generic(self):
        assert render_template("Hi {{first_name}}", contact()) == f"Hi {GENERIC_NAME}"

    def test_never_emits_dangling_comma(self):
        """Regression: an empty first name produced the tell-tale 'Hi , ...'."""
        out = render_template("Hi {{first_name}}, welcome", contact())
        assert ", " not in out.split("welcome")[0].replace(f"Hi {GENERIC_NAME}, ", "")
        assert not out.startswith("Hi ,")


class TestTokenSyntax:
    def test_whitespace_inside_braces_is_tolerated(self):
        c = contact(first_name="Ada")
        assert render_template("Hi {{ first_name }}", c) == "Hi Ada"
        assert render_template("Hi {{first_name }}", c) == "Hi Ada"

    def test_case_insensitive(self):
        assert render_template("Hi {{First_Name}}", contact(first_name="Ada")) == "Hi Ada"

    def test_unknown_token_is_removed_not_leaked(self):
        out = render_template("Hi {{nonexistent_field}} there", contact(first_name="Ada"))
        assert "{{" not in out and "}}" not in out
        assert "nonexistent_field" not in out

    def test_explicit_fallback_syntax(self):
        assert render_template("Hi {{first_name|friend}}", contact()) == "Hi friend"
        assert render_template(
            "Hi {{first_name|friend}}", contact(first_name="Ada")
        ) == "Hi Ada"

    def test_custom_fields_are_available(self):
        c = contact(first_name="Ada", custom_fields={"plan": "Gold"})
        assert render_template("Plan: {{plan}}", c) == "Plan: Gold"

    def test_real_columns_beat_custom_fields(self):
        c = contact(first_name="Ada", custom_fields={"first_name": "WRONG"})
        assert render_template("{{first_name}}", c) == "Ada"

    def test_brand_name_alias_uses_business_name(self):
        c = contact(business_name="Acme Foods")
        assert render_template("{{brand_name}} / {{brand}}", c) == "Acme Foods / Acme Foods"


class TestOverridesAndContext:
    def test_overrides_win(self):
        c = contact(first_name="Ada")
        assert render_template("{{first_name}}", c, first_name="Ngozi") == "Ngozi"

    def test_renders_without_a_contact(self):
        out = render_template("Hi {{first_name}}, visit {{website}}")
        assert "{{" not in out

    def test_build_context_exposes_expected_keys(self):
        ctx = build_context(contact(first_name="Ada", business_name="Acme"))
        for key in ("first_name", "last_name", "business_name", "phone_number",
                    "email", "city", "state", "country", "website", "industry",
                    "full_name", "name"):
            assert key in ctx


class TestNoBracesEverEscape:
    @pytest.mark.parametrize("body", [
        "Hi {{first_name}}, this is about {{business_name}}. Visit {{website}}.",
        "{{ unknown }} and {{first_name}}",
        "{{industry}} specialists in {{city}}",
        "No placeholders here at all",
    ])
    def test_output_is_always_brace_free(self, body):
        for c in (contact(), contact(first_name="Ada", business_name="Acme",
                                     website="acme.ng", industry="Retail", city="Abuja")):
            out = render_template(body, c)
            assert "{{" not in out and "}}" not in out
