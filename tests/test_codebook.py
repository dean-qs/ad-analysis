"""Tests for scripts/codebook.py. Never touch a real OpenAI call --
every test monkeypatches derive_call/classify_call, same pattern as
run_actor in test_collect.py and vision_call/audio_call in
test_transcribe.py.
"""
import json

import pytest

import codebook as cb


def _row(creative_id, brand="Genentech", source="meta", headline="", body="",
        transcript=""):
    return {"creative_id": creative_id, "brand": brand, "source": source,
            "headline": headline, "body": body, "transcript": transcript}


@pytest.fixture(autouse=True)
def isolate_checkpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(cb, "CHECKPOINT_FILE", tmp_path / "codebook_apply_checkpoint.jsonl")


class _FakeCompletions:
    def __init__(self, capture):
        self.capture = capture

    def create(self, **kwargs):
        self.capture.update(kwargs)
        content = json.dumps({"strategy_themes": [], "topic_themes": []})
        msg = type("M", (), {"content": content})()
        choice = type("C", (), {"message": msg})()
        usage = type("U", (), {"prompt_tokens": 10, "completion_tokens": 5,
                                "prompt_tokens_details": None})()
        return type("R", (), {"choices": [choice], "usage": usage})()


def test_derive_call_and_classify_call_are_deterministic(monkeypatch):
    """A real live run caught this: without temperature=0, the same row
    classified two different ways across calls -- once correctly,
    once hallucinating invalid ids that got silently dropped to
    nothing. Lock in determinism so that can't regress."""
    for fn_name in ("derive_call", "classify_call"):
        captured = {}
        fake_client = type("FC", (), {"chat": type("Chat", (), {
            "completions": _FakeCompletions(captured)})()})()
        monkeypatch.setattr(cb, "_client", lambda: fake_client)
        getattr(cb, fn_name)("system", "user")
        assert captured.get("temperature") == 0, f"{fn_name} must pin temperature=0"


# ------------------------------------------------------------ row text


def test_row_text_prefers_transcript_over_body_headline():
    r = _row("c1", headline="H", body="B", transcript="T")
    assert cb._row_text(r) == "T"


def test_row_text_falls_back_to_headline_and_body():
    r = _row("c2", headline="Headline here", body="Body copy here")
    assert cb._row_text(r) == "Headline here Body copy here"


def test_row_text_empty_when_nothing_present():
    r = _row("c3")
    assert cb._row_text(r) == ""


# --------------------------------------------------------- sampling


def test_stratified_sample_skips_rows_with_no_text():
    rows = [_row("c1", body="has text"), _row("c2")]
    sample = cb.stratified_sample(rows)
    assert [r["creative_id"] for r in sample] == ["c1"]


def test_stratified_sample_caps_per_stratum():
    rows = [_row(f"c{i}", brand="Genentech", source="meta", body=f"text {i}")
            for i in range(20)]
    sample = cb.stratified_sample(rows, per_stratum=5)
    assert len(sample) == 5


def test_stratified_sample_covers_every_brand_source_stratum():
    rows = ([_row(f"g{i}", brand="Genentech", source="meta", body="x") for i in range(3)]
            + [_row(f"p{i}", brand="Pfizer", source="google", body="x") for i in range(3)])
    sample = cb.stratified_sample(rows, per_stratum=2)
    brands = {r["brand"] for r in sample}
    assert brands == {"Genentech", "Pfizer"}
    assert len(sample) == 4   # 2 per stratum x 2 strata


def test_stratified_sample_deterministic_with_same_seed():
    rows = [_row(f"c{i}", body=f"text {i}") for i in range(10)]
    a = cb.stratified_sample(rows, per_stratum=3, seed=7)
    b = cb.stratified_sample(rows, per_stratum=3, seed=7)
    assert [r["creative_id"] for r in a] == [r["creative_id"] for r in b]


# ----------------------------------------------------------- derive


def _canned_theme(label="Access and affordability", example_id="c1"):
    return {"label": label, "definition": "Ads about cost or coverage.",
            "inclusion": ["copay", "coverage"], "exclusion": ["general awareness"],
            "examples": ["real quote from the sample"], "example_ids": [example_id],
            "brandwatch_boolean_clean": "(copay OR coverage) NEAR/5 (cost OR afford*)"}


def test_derive_assigns_ids_tiers_and_labelled_boolean(monkeypatch):
    rows = [_row("c1", body="Ask your doctor about copay support programs.")]

    def fake_derive_call(system, user):
        parsed = {"strategy_themes": [_canned_theme()], "topic_themes": [
            {**_canned_theme("Oncology", "c1")}]}
        usage = type("U", (), {"prompt_tokens": 500, "completion_tokens": 200})()
        return parsed, usage

    monkeypatch.setattr(cb, "derive_call", fake_derive_call)
    result = cb.derive(rows)

    assert result["strategy_themes"][0]["id"] == "S01"
    assert result["strategy_themes"][0]["tier"] == "strategy"
    assert result["topic_themes"][0]["id"] == "T01"
    assert result["topic_themes"][0]["tier"] == "topic"

    boolean_clean = result["strategy_themes"][0]["brandwatch_boolean_clean"]
    boolean_labelled = result["strategy_themes"][0]["brandwatch_boolean_labelled"]
    assert boolean_labelled.startswith("<<< Access and affordability >>>")
    assert boolean_clean in boolean_labelled
    assert "<<<" not in boolean_clean   # clean version has no comment


def test_derive_drops_example_ids_not_in_the_given_sample(monkeypatch, capsys):
    rows = [_row("c1", body="Real sample text.")]

    def fake_derive_call(system, user):
        theme = _canned_theme(example_id="c999")   # not in the sample
        usage = type("U", (), {"prompt_tokens": 100, "completion_tokens": 50})()
        return {"strategy_themes": [theme], "topic_themes": []}, usage

    monkeypatch.setattr(cb, "derive_call", fake_derive_call)
    result = cb.derive(rows)

    assert result["strategy_themes"][0]["example_ids"] == []
    assert result["strategy_themes"][0]["examples"] == []
    assert "dropped example_id" in capsys.readouterr().out


# ------------------------------------------------------- apply prompt


def _codebook():
    return {
        "strategy_themes": [
            {"id": "S01", "label": "Access", "definition": "Cost/coverage claims.",
             "inclusion": ["copay"], "exclusion": ["awareness only"]},
        ],
        "topic_themes": [
            {"id": "T01", "label": "Oncology", "definition": "Cancer-specific claims.",
             "inclusion": ["cancer", "tumor"], "exclusion": []},
        ],
    }


def test_build_apply_system_prompt_includes_all_ids_and_criteria():
    prompt = cb.build_apply_system_prompt(_codebook())
    assert "S01" in prompt and "Access" in prompt and "copay" in prompt
    assert "T01" in prompt and "Oncology" in prompt and "cancer" in prompt


def test_estimate_apply_needs_no_download_and_counts_real_tokens():
    rows = [_row("c1", body="Ask your doctor about copay support."),
            _row("c2")]   # no text -- should be skipped
    est = cb.estimate_apply(rows, _codebook())
    assert est["n_eligible"] == 1
    assert est["n_skipped_no_text"] == 1
    assert est["system_prompt_tokens"] > 0
    assert est["cost_worst_case"] > 0
    assert est["cost_if_cached_after_first_call"] <= est["cost_worst_case"]


# ------------------------------------------------------------- apply


def test_apply_skips_rows_with_no_text_without_calling_the_model(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    def fail(*a, **kw):
        raise AssertionError("should not classify an empty row")
    monkeypatch.setattr(cb, "classify_call", fail)

    rows = [_row("empty1")]
    result = cb.apply(rows, _codebook())
    assert result[0]["strategy_themes"] == []
    assert result[0]["topic_themes"] == []


def test_apply_assigns_multi_label_themes(monkeypatch, tmp_path):
    def fake_classify(system, user):
        parsed = {"strategy_themes": ["S01"], "topic_themes": ["T01"]}
        usage = type("U", (), {"prompt_tokens": 300, "completion_tokens": 20,
                                "prompt_tokens_details": None})()
        return parsed, usage
    monkeypatch.setattr(cb, "classify_call", fake_classify)
    monkeypatch.chdir(tmp_path)

    rows = [_row("c1", body="Copay support for your cancer treatment.")]
    result = cb.apply(rows, _codebook())
    assert result[0]["strategy_themes"] == ["S01"]
    assert result[0]["topic_themes"] == ["T01"]
    assert (tmp_path / "out" / "ads_coded.json").exists()
    assert (tmp_path / "out" / "ads_coded.json.cost.json").exists()


def test_apply_drops_hallucinated_theme_ids(monkeypatch, tmp_path, capsys):
    def fake_classify(system, user):
        parsed = {"strategy_themes": ["S01", "S99"], "topic_themes": ["T404"]}
        usage = type("U", (), {"prompt_tokens": 300, "completion_tokens": 20,
                                "prompt_tokens_details": None})()
        return parsed, usage
    monkeypatch.setattr(cb, "classify_call", fake_classify)
    monkeypatch.chdir(tmp_path)

    rows = [_row("c1", body="Copay support for your cancer treatment.")]
    result = cb.apply(rows, _codebook())
    assert result[0]["strategy_themes"] == ["S01"]   # S99 dropped
    assert result[0]["topic_themes"] == []            # T404 dropped
    assert "hallucinated" in capsys.readouterr().out


def test_apply_resumes_from_checkpoint(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    cb._append_checkpoint("c1", {"strategy_themes": ["S01"], "topic_themes": []},
                          {"input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 1})

    def fail(*a, **kw):
        raise AssertionError("checkpointed row must not be re-classified")
    monkeypatch.setattr(cb, "classify_call", fail)

    rows = [_row("c1", body="Copay support.")]
    result = cb.apply(rows, _codebook())
    assert result[0]["strategy_themes"] == ["S01"]


def test_apply_keeps_partial_results_on_one_row_failure(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    def flaky_classify(system, user):
        if "bad" in user:
            raise RuntimeError("model overloaded")
        usage = type("U", (), {"prompt_tokens": 100, "completion_tokens": 10,
                                "prompt_tokens_details": None})()
        return {"strategy_themes": ["S01"], "topic_themes": []}, usage

    monkeypatch.setattr(cb, "classify_call", flaky_classify)
    monkeypatch.setattr(cb.time, "sleep", lambda s: None)   # skip real backoff delay

    good = _row("good1", body="Copay support for treatment.")
    bad = _row("bad1", body="bad content that always fails")
    result = cb.apply([good, bad], _codebook())

    by_id = {r["creative_id"]: r for r in result}
    assert by_id["good1"]["strategy_themes"] == ["S01"]
    assert by_id["bad1"]["strategy_themes"] == []
