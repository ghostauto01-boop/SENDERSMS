"""Tests for the keyless AI reply classifier."""

from app.services.ai_classifier import analyze_sentiment, classify, detect_intent


class TestSentiment:
    def test_positive_confirmation(self):
        assert classify("yes").get("sentiment") == "positive"
        assert classify("Yes we are!").get("sentiment") == "positive"
        assert classify("yes o").get("confirm_yes") is True

    def test_negative_confirmation(self):
        assert classify("no").get("sentiment") == "negative"
        assert classify("No, that's not us").get("confirm_no") is True

    def test_wrong_number_is_negative(self):
        result = classify("wrong number, please remove me")
        assert result["sentiment"] == "negative"
        assert result["intent"] == "wrong_number"

    def test_not_interested(self):
        result = classify("I am not interested, stop texting me")
        assert result["sentiment"] == "negative"
        assert result["intent"] in ("not_interested", "opt_out")

    def test_interested(self):
        result = classify("I'm interested, send me the menu")
        assert result["sentiment"] == "positive"
        assert result["intent"] == "interested"

    def test_neutral_greeting(self):
        result = classify("hello")
        assert result["sentiment"] in ("neutral", "positive")

    def test_business_confirmation_question(self):
        result = classify("is this Chicken Republic?")
        assert result["intent"] == "confirm_business_question"

    def test_pidgin_negative(self):
        result = classify("abeg stop, you have the wrong person")
        assert result["sentiment"] == "negative"

    def test_confidence_is_bounded(self):
        result = classify("yes o, this is us, we are interested")
        assert 0.0 <= result["confidence"] <= 1.0

    def test_opt_out_detected(self):
        assert detect_intent("STOP")[0] == "opt_out"
        assert detect_intent("unsubscribe me")[0] == "opt_out"

    def test_analyze_sentiment_weights_phrases(self):
        result = analyze_sentiment("that's not us, wrong number")
        assert result["score"] < 0
