"""
Tests for the public reply-library loader.

No network: `build_library` takes any iterable of rows, so the download is only
in `fetch_rows`, which these tests do not call.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

# The script lives in scripts/ rather than the package, because it is a one-off
# tool and not part of the runtime. Load it by path.
_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "fetch_public_replies.py"
_spec = importlib.util.spec_from_file_location("fetch_public_replies", _SCRIPT)
frp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(frp)


def row(intent="get_refund", response="x" * 80):
    return {"intent": intent, "response": response, "category": "REFUND"}


class TestPlaceholders:
    def test_template_syntax_is_rewritten(self):
        # Left alone, a drafted reply hands the reviewer literal {{ }}, which
        # reads as a bug in this tool rather than in the dataset.
        assert frp.humanise_placeholders(
            "Cancel order {{Order Number}} now") == "Cancel order [order number] now"

    def test_several_placeholders_in_one_reply(self):
        out = frp.humanise_placeholders("{{Online Company Portal Info}} then {{Order Number}}")
        assert out == "[online company portal info] then [order number]"

    def test_text_without_placeholders_is_unchanged(self):
        assert frp.humanise_placeholders("No templates here.") == "No templates here."

    def test_no_template_syntax_survives_cleaning(self):
        cleaned = frp.clean_response("Please visit {{Portal}} to cancel " + "x" * 60)
        assert "{{" not in cleaned and "}}" not in cleaned


class TestCleaning:
    def test_short_acknowledgements_are_dropped(self):
        # BM25 ranks these top for short queries while saying nothing, which is
        # the failure mode of a library padded with filler.
        assert frp.clean_response("Sure, I can help!") == ""

    def test_whitespace_is_normalised(self):
        assert "  " not in frp.clean_response("a" * 60 + "   spaced    out")

    def test_blank_input_is_dropped(self):
        assert frp.clean_response("") == ""
        assert frp.clean_response(None) == ""


class TestBuildLibrary:
    def test_produces_the_reply_library_schema(self):
        lib = frp.build_library([row()])
        assert lib["version"] == 2
        assert set(lib["categories"]) == {"Refunds"}
        assert lib["source"]["licence"] == "CDLA-Sharing-1.0"

    def test_maps_intents_to_this_repositorys_categories(self):
        lib = frp.build_library([
            row(intent="get_refund", response="r" * 80),
            row(intent="track_order", response="d" * 80),
            row(intent="delete_account", response="a" * 80),
        ])
        assert set(lib["categories"]) == {
            "Refunds", "Delivery", "Account deletion / data removal"}

    def test_unknown_intents_are_dropped_not_bucketed(self):
        # Forcing an unmapped intent into the nearest category puts a reply
        # about newsletters in front of a refund request.
        lib = frp.build_library([row(intent="something_new_bitext_added")])
        assert lib["categories"] == {}

    def test_near_duplicates_are_removed(self):
        # The dataset repeats one reply across many phrasings of the same
        # question. Forty copies of one reply retrieves as badly as one copy.
        text = "The refund will be returned to your original payment method. " * 2
        lib = frp.build_library([row(response=text), row(response=text.upper()),
                                 row(response=text + " ")])
        assert len(lib["categories"]["Refunds"]) == 1

    def test_respects_the_per_category_cap(self):
        rows = [row(response=f"reply number {i} " + "y" * 70) for i in range(100)]
        lib = frp.build_library(rows, per_category=5)
        assert len(lib["categories"]["Refunds"]) == 5

    def test_generated_at_is_stable(self):
        """A moving timestamp makes two identical libraries look like different
        files in a diff."""
        assert frp.build_library([row()])["generated_at"] == \
            frp.build_library([row()])["generated_at"]

    def test_the_licence_is_recorded_in_the_output(self):
        # The file outlives the terminal output that explained where it came
        # from, and this data is not MIT.
        lib = frp.build_library([row()])
        assert "CDLA-Sharing-1.0" in lib["note"]
        assert lib["source"]["url"].startswith("https://huggingface.co/")


class TestOutputIsUsable:
    def test_the_result_loads_the_way_the_agent_reads_it(self):
        """Whatever the loader writes has to match the shape the agent already
        reads, or the file is decorative."""
        import json

        lib = frp.build_library([row(intent="get_refund", response="r" * 80),
                                 row(intent="track_order", response="d" * 80)])
        reloaded = json.loads(json.dumps(lib))

        assert isinstance(reloaded["categories"], dict)
        for name, replies in reloaded["categories"].items():
            assert isinstance(name, str)
            assert isinstance(replies, list)
            assert all(isinstance(r, str) and r for r in replies)

    def test_every_mapped_category_is_a_string_the_classifier_could_emit(self):
        for category in frp.INTENT_CATEGORIES.values():
            assert category and category == category.strip()


class TestMain:
    def test_a_dataset_that_yields_nothing_fails_loudly(self, monkeypatch, tmp_path, capsys):
        # An empty library silently disables retrieval and every draft falls
        # back to a bare acknowledgement, with nothing saying why.
        monkeypatch.setattr(frp, "fetch_rows", lambda url, **kw: iter([row(intent="unmapped")]))
        out = tmp_path / "lib.json"
        assert frp.main(["--out", str(out)]) == 1
        assert not out.exists()
        assert "no usable replies" in capsys.readouterr().err

    def test_a_download_failure_is_reported_not_swallowed(self, monkeypatch, tmp_path, capsys):
        def _boom(url, **kw):
            raise RuntimeError("HTTP 404")
        monkeypatch.setattr(frp, "fetch_rows", _boom)
        assert frp.main(["--out", str(tmp_path / "lib.json")]) == 1
        assert "404" in capsys.readouterr().err

    def test_a_successful_run_writes_the_file(self, monkeypatch, tmp_path):
        monkeypatch.setattr(frp, "fetch_rows", lambda url, **kw: iter([row()]))
        out = tmp_path / "lib.json"
        assert frp.main(["--out", str(out)]) == 0
        assert out.exists()


@pytest.mark.parametrize("intent,expected", [
    ("cancel_order", "Cancellations"),
    ("contact_human_agent", "Needs human"),
    ("recover_password", "Account access"),
])
def test_intent_mapping(intent, expected):
    assert frp.INTENT_CATEGORIES[intent] == expected
