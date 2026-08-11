"""Tests for structured/derived JSON provenance (spec §6, §20, §24 "Metadata").

Covers the three producer shapes (fetch/compute/model), the bronze/silver
split between `write_structured` (structured/, fetch|compute only) and
`write_derived` (derived/[<namespace>/], fetch|compute|model), the credential
scan over `meta.request`, and `json.dump(..., allow_nan=False)`.
"""
from __future__ import annotations

import json

import pytest

from lib import provenance as prov
from lib.provenance import StructuredMeta


# --- fixtures --------------------------------------------------------------

def _fetch_meta(**over) -> StructuredMeta:
    base = dict(
        id="balance_sheet_yahoo",
        ticker="PANW",
        producer="fetch",
        title="PANW Balance Sheet (Yahoo)",
        source="Yahoo Finance",
        as_of="2026-04-30",
        url="https://finance.yahoo.com/quote/PANW/balance-sheet",
        fetched_at="2026-07-30T14:20:11Z",
        provider_tool="yfinance.Ticker.balance_sheet",
        fetch_cmd="uv run python sra.py prefetch PANW --kinds financials",
    )
    base.update(over)
    return StructuredMeta(**base)


def _fetch_api_meta(**over) -> StructuredMeta:
    base = dict(
        request={"method": "GET", "params": {"symbol": "PANW", "period": "quarter"}},
    )
    base.update(over)
    return _fetch_meta(**base)


def _compute_meta(**over) -> StructuredMeta:
    base = dict(
        id="key_ratios_computed",
        ticker="PANW",
        producer="compute",
        title="PANW Key Ratios (computed)",
        source=prov.SOURCE_COMPUTED,
        as_of="2026-04-30",
        derived_from=["balance_sheet_yahoo", "income_statement_yahoo"],
        computed_at="2026-07-30T14:20:11Z",
        provider_tool="lib/analytics/ratios.py",
        fetch_cmd="uv run python sra.py prefetch PANW --kinds financials",
    )
    base.update(over)
    return StructuredMeta(**base)


def _model_meta(**over) -> StructuredMeta:
    base = dict(
        id="peers_ranked",
        ticker="PANW",
        producer="model",
        title="PANW Peer Ranking",
        source="peers-rater",
        as_of="2026-08-11",
        derived_from=["peers_candidates"],
        generated_at="2026-08-11T12:00:00Z",
    )
    base.update(over)
    return StructuredMeta(**base)


# --- check_fetch_shape / check_compute_shape / check_model_shape -----------
# Unit-tested directly (not just through the writers) so a future validator
# reading a meta off disk gets the identical verdict (decision #1).

def test_check_fetch_shape_valid_meta_has_no_problems():
    assert prov.check_fetch_shape(_fetch_meta()) == []


@pytest.mark.parametrize(
    "field_name",
    ["id", "ticker", "title", "source", "url", "fetched_at", "as_of",
     "provider_tool", "fetch_cmd"],
)
def test_check_fetch_shape_reports_each_missing_required_field(field_name):
    meta = _fetch_meta(**{field_name: None if field_name != "id" else ""})
    # id can't be None (dataclass allows it but downstream str ops assume str);
    # empty string exercises the same "falsy" branch.
    problems = prov.check_fetch_shape(meta)
    assert problems
    assert any(field_name in p for p in problems)


def test_check_compute_shape_valid_meta_has_no_problems():
    assert prov.check_compute_shape(_compute_meta()) == []


def test_check_compute_shape_requires_derived_from():
    meta = _compute_meta(derived_from=[])
    problems = prov.check_compute_shape(meta)
    assert any("derived_from" in p for p in problems)


def test_check_compute_shape_forbids_url():
    meta = _compute_meta(url="https://example.com/should-not-be-here")
    problems = prov.check_compute_shape(meta)
    assert any("url" in p for p in problems)


@pytest.mark.parametrize(
    "field_name",
    ["id", "ticker", "title", "source", "computed_at", "as_of",
     "provider_tool", "fetch_cmd"],
)
def test_check_compute_shape_reports_each_missing_required_field(field_name):
    meta = _compute_meta(**{field_name: None})
    problems = prov.check_compute_shape(meta)
    assert any(field_name in p for p in problems)


def test_check_model_shape_valid_meta_has_no_problems():
    assert prov.check_model_shape(_model_meta()) == []


def test_check_model_shape_requires_derived_from():
    meta = _model_meta(derived_from=[])
    problems = prov.check_model_shape(meta)
    assert any("derived_from" in p for p in problems)


def test_check_model_shape_forbids_url():
    meta = _model_meta(url="https://example.com/nope")
    problems = prov.check_model_shape(meta)
    assert any("url" in p for p in problems)


def test_check_model_shape_forbids_fetch_cmd():
    meta = _model_meta(fetch_cmd="uv run python sra.py something")
    problems = prov.check_model_shape(meta)
    assert any("fetch_cmd" in p for p in problems)


@pytest.mark.parametrize(
    "field_name", ["id", "ticker", "title", "source", "generated_at", "as_of"],
)
def test_check_model_shape_reports_each_missing_required_field(field_name):
    meta = _model_meta(**{field_name: None})
    problems = prov.check_model_shape(meta)
    assert any(field_name in p for p in problems)


# --- credential scan ---------------------------------------------------

def test_request_never_carries_credentials(tmp_ticker_dir):
    meta = _fetch_meta(request={"params": {"symbol": "PANW", "apikey": "X"}})
    with pytest.raises(ValueError, match="apikey"):
        prov.write_structured(tmp_ticker_dir, meta, {})
    assert not (tmp_ticker_dir / "structured" / "balance_sheet_yahoo.json").exists()


@pytest.mark.parametrize(
    "cred_key", ["apikey", "api_key", "token", "access_token", "APIKEY", "Api_Key"],
)
def test_check_request_credentials_rejects_all_forbidden_names_case_insensitive(cred_key):
    problems = prov.check_request_credentials({"params": {cred_key: "x"}})
    assert problems


def test_check_request_credentials_catches_credential_nested_in_list():
    # a credential nested inside a list of dicts (e.g. a batched request body)
    # must not slip past a shallow dict-only walk
    request = {"body": {"items": [{"symbol": "PANW"}, {"apikey": "X"}]}}
    problems = prov.check_request_credentials(request)
    assert problems


def test_check_request_credentials_rejects_empty_placeholder_value():
    # §5: a credential must be OMITTED, not blanked — even "" must be rejected
    problems = prov.check_request_credentials({"params": {"token": ""}})
    assert problems


# httpx/requests both accept `params`/`body` as a sequence of (name, value)
# pairs (the "repeated query parameter" form), and json.dump serializes a
# tuple as a JSON array indistinguishable from a list — so a credential
# recorded that way must be caught exactly like a dict key.

def test_check_request_credentials_catches_list_of_pairs():
    request = {"params": [["apikey", "SECRET"], ["symbol", "PANW"]]}
    problems = prov.check_request_credentials(request)
    assert problems


def test_check_request_credentials_catches_tuple_of_pairs():
    request = {"params": (("apikey", "SECRET"),)}
    problems = prov.check_request_credentials(request)
    assert problems


def test_check_request_credentials_catches_tuple_containing_dict():
    # a tuple (not a list) wrapping a dict — the walk must recurse into
    # tuples at all, not just detect the pair-of-strings shape
    request = {"params": ({"apikey": "SECRET"},)}
    problems = prov.check_request_credentials(request)
    assert problems


def test_write_structured_refuses_credential_carried_as_list_of_pairs(tmp_ticker_dir):
    meta = _fetch_meta(request={"params": [["apikey", "SECRET"], ["symbol", "PANW"]]})
    with pytest.raises(ValueError, match="apikey"):
        prov.write_structured(tmp_ticker_dir, meta, {})
    on_disk = tmp_ticker_dir / "structured" / "balance_sheet_yahoo.json"
    assert not on_disk.exists()


def test_check_request_credentials_pair_shape_does_not_false_positive_on_ordinary_data():
    # a 2-element list/tuple whose first element is NOT a credential name
    # must not be flagged just because it happens to look pair-shaped
    request = {"params": [["symbol", "PANW"], ["period", "quarter"]]}
    assert prov.check_request_credentials(request) == []


def test_check_request_credentials_none_or_empty_request_is_clean():
    assert prov.check_request_credentials(None) == []
    assert prov.check_request_credentials({}) == []


def test_check_request_credentials_ordinary_params_are_clean():
    assert prov.check_request_credentials(
        {"method": "GET", "params": {"symbol": "PANW", "period": "quarter"}}
    ) == []


# --- write_structured: shape enforcement --------------------------------

def test_fetch_shape_requires_url_and_cmd(tmp_ticker_dir):
    meta = _fetch_meta(url=None)
    with pytest.raises(ValueError, match="url"):
        prov.write_structured(tmp_ticker_dir, meta, {})

    meta2 = _fetch_meta(fetch_cmd=None)
    with pytest.raises(ValueError, match="fetch_cmd"):
        prov.write_structured(tmp_ticker_dir, meta2, {})


def test_compute_shape_forbids_url(tmp_ticker_dir):
    meta = _compute_meta(url="https://example.com/should-not-be-here")
    with pytest.raises(ValueError, match="url"):
        prov.write_structured(tmp_ticker_dir, meta, {})


def test_model_producer_rejected_by_write_structured(tmp_ticker_dir):
    meta = _model_meta()
    with pytest.raises(ValueError, match="model"):
        prov.write_structured(tmp_ticker_dir, meta, {})
    assert not (tmp_ticker_dir / "structured" / "peers_ranked.json").exists()
    assert not (tmp_ticker_dir / "derived" / "peers_ranked.json").exists()


def test_write_structured_rejects_unknown_producer(tmp_ticker_dir):
    meta = _fetch_meta(producer="scrape")
    with pytest.raises(ValueError):
        prov.write_structured(tmp_ticker_dir, meta, {})


def test_write_structured_writes_only_into_structured(tmp_ticker_dir):
    meta = _fetch_meta()
    path = prov.write_structured(tmp_ticker_dir, meta, {"TotalAssets": 1})
    assert path == tmp_ticker_dir / "structured" / "balance_sheet_yahoo.json"
    assert path.exists()
    assert not (tmp_ticker_dir / "derived" / "balance_sheet_yahoo.json").exists()


def test_write_structured_overwrite_allowed(tmp_ticker_dir):
    prov.write_structured(tmp_ticker_dir, _fetch_meta(), {"v": 1})
    prov.write_structured(tmp_ticker_dir, _fetch_meta(), {"v": 2})
    _, data = prov.read_structured(tmp_ticker_dir / "structured" / "balance_sheet_yahoo.json")
    assert data["v"] == 2


def test_write_structured_compute_ok(tmp_ticker_dir):
    meta = _compute_meta()
    path = prov.write_structured(tmp_ticker_dir, meta, {"pe_ratio": 12.3})
    assert path == tmp_ticker_dir / "structured" / "key_ratios_computed.json"
    got_meta, data = prov.read_structured(path)
    assert got_meta.producer == "compute"
    assert got_meta.derived_from == ["balance_sheet_yahoo", "income_statement_yahoo"]
    assert data["pe_ratio"] == 12.3


def test_write_structured_path_traversal_rejected(tmp_ticker_dir):
    meta = _fetch_meta(id="../evil")
    with pytest.raises(ValueError, match="evil|traversal|filename"):
        prov.write_structured(tmp_ticker_dir, meta, {})


def test_write_structured_request_optional_not_enforced(tmp_ticker_dir):
    # decision #5: request presence is NOT enforced at write time — a page
    # fetch (no request) must write cleanly.
    meta = _fetch_meta(request=None)
    path = prov.write_structured(tmp_ticker_dir, meta, {})
    assert path.exists()


def test_write_structured_api_fetch_with_request_round_trips(tmp_ticker_dir):
    meta = _fetch_api_meta()
    path = prov.write_structured(tmp_ticker_dir, meta, {})
    got_meta, _ = prov.read_structured(path)
    assert got_meta.request == {"method": "GET", "params": {"symbol": "PANW", "period": "quarter"}}


# --- json.dump(..., allow_nan=False) ------------------------------------

@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_floats_are_refused(tmp_ticker_dir, bad):
    with pytest.raises(ValueError):
        prov.write_structured(tmp_ticker_dir, _fetch_meta(), {"rows": [{"pe_ratio": bad}]})
    assert not (tmp_ticker_dir / "structured" / "balance_sheet_yahoo.json").exists()


def test_null_values_stay_null_not_zero_filled(tmp_ticker_dir):
    path = prov.write_structured(tmp_ticker_dir, _fetch_meta(), {"missing_metric": None})
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["data"]["missing_metric"] is None


# --- write_derived -------------------------------------------------------

def test_model_via_write_derived_lands_in_derived(tmp_ticker_dir):
    meta = _model_meta()
    path = prov.write_derived(tmp_ticker_dir, meta, {"ranked": ["ZS", "CRWD"]}, namespace="peers")
    assert path == tmp_ticker_dir / "derived" / "peers" / "peers_ranked.json"
    got_meta, data = prov.read_structured(path)
    assert got_meta.producer == "model"
    assert got_meta.derived_from == ["peers_candidates"]
    assert data["ranked"] == ["ZS", "CRWD"]


def test_write_derived_no_namespace_writes_flat_under_derived(tmp_ticker_dir):
    meta = _model_meta(id="research_answer_1")
    path = prov.write_derived(tmp_ticker_dir, meta, {"answer": "..."})
    assert path == tmp_ticker_dir / "derived" / "research_answer_1.json"
    # a writer that computed the right path but skipped writing would still
    # pass the assertion above -- confirm the file actually landed, with
    # the right content, not just that the path arithmetic is correct
    assert path.exists()
    got_meta, data = prov.read_structured(path)
    assert got_meta.id == "research_answer_1"
    assert data == {"answer": "..."}


def test_write_derived_accepts_fetch_and_compute_too(tmp_ticker_dir):
    fetch_path = prov.write_derived(tmp_ticker_dir, _fetch_meta(id="scratch_fetch"), {"a": 1})
    assert fetch_path == tmp_ticker_dir / "derived" / "scratch_fetch.json"
    assert fetch_path.exists()
    fetch_got_meta, fetch_data = prov.read_structured(fetch_path)
    assert fetch_got_meta.producer == "fetch"
    assert fetch_data == {"a": 1}

    compute_path = prov.write_derived(
        tmp_ticker_dir, _compute_meta(id="scratch_compute"), {"b": 2}, namespace="peers"
    )
    assert compute_path == tmp_ticker_dir / "derived" / "peers" / "scratch_compute.json"
    assert compute_path.exists()
    compute_got_meta, compute_data = prov.read_structured(compute_path)
    assert compute_got_meta.producer == "compute"
    assert compute_data == {"b": 2}


def test_write_derived_compute_shape_under_derived_is_still_shape_checked(tmp_ticker_dir):
    # §4.2: location sets the layer, not producer shape — but the *shape* rules
    # (url forbidden for compute) still apply regardless of where it lands.
    meta = _compute_meta(url="https://example.com/leaked")
    with pytest.raises(ValueError, match="url"):
        prov.write_derived(tmp_ticker_dir, meta, {}, namespace="peers")


def test_write_derived_rejects_unknown_producer(tmp_ticker_dir):
    meta = _fetch_meta(producer="scrape")
    with pytest.raises(ValueError):
        prov.write_derived(tmp_ticker_dir, meta, {})


def test_write_derived_model_shape_enforced(tmp_ticker_dir):
    meta = _model_meta(derived_from=[])
    with pytest.raises(ValueError, match="derived_from"):
        prov.write_derived(tmp_ticker_dir, meta, {}, namespace="peers")


def test_write_derived_namespace_path_traversal_rejected(tmp_ticker_dir):
    meta = _model_meta()
    with pytest.raises(ValueError, match="traversal|namespace|filename"):
        prov.write_derived(tmp_ticker_dir, meta, {}, namespace="../escape")


def test_write_derived_overwrite_allowed(tmp_ticker_dir):
    prov.write_derived(tmp_ticker_dir, _model_meta(), {"v": 1}, namespace="peers")
    prov.write_derived(tmp_ticker_dir, _model_meta(), {"v": 2}, namespace="peers")
    _, data = prov.read_structured(tmp_ticker_dir / "derived" / "peers" / "peers_ranked.json")
    assert data["v"] == 2


def test_write_derived_credential_in_request_rejected(tmp_ticker_dir):
    meta = _fetch_meta(id="scratch", request={"params": {"symbol": "PANW", "token": "secret"}})
    with pytest.raises(ValueError, match="token"):
        prov.write_derived(tmp_ticker_dir, meta, {})


# --- read_structured -----------------------------------------------------

def test_read_structured_round_trips_fetch(tmp_ticker_dir):
    meta = _fetch_api_meta()
    path = prov.write_structured(tmp_ticker_dir, meta, {"TotalAssets": 21234000000})
    got_meta, data = prov.read_structured(path)
    assert got_meta == meta
    assert data["TotalAssets"] == 21234000000


def test_read_structured_fills_defaults_for_omitted_optional_fields(tmp_ticker_dir):
    meta = _fetch_meta()
    path = prov.write_structured(tmp_ticker_dir, meta, {})
    got_meta, _ = prov.read_structured(path)
    assert got_meta.request is None
    assert got_meta.period is None
    assert got_meta.currency is None
    assert got_meta.adjusted is None


def test_write_structured_adjusted_false_is_preserved_not_dropped(tmp_ticker_dir):
    # §6.4: `_meta.adjusted` records whether prices are split/dividend adjusted;
    # False is a meaningful, distinct value from "unset" and must round-trip,
    # not collapse to the same on-disk shape as omitting the field entirely.
    path = prov.write_structured(tmp_ticker_dir, _fetch_meta(adjusted=False), {})
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["_meta"]["adjusted"] is False
    got_meta, _ = prov.read_structured(path)
    assert got_meta.adjusted is False


def test_write_structured_derived_from_written_as_empty_list_not_omitted(tmp_ticker_dir):
    # §6's own example shows "derived_from": [] present even on a fetch shape,
    # not omitted — round-tripping must not turn it into a missing key.
    path = prov.write_structured(tmp_ticker_dir, _fetch_meta(), {})
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["_meta"]["derived_from"] == []


# --- read_structured: malformed artifact -> ValueError, not bare KeyError --

def test_read_structured_missing_meta_key_raises_value_error_not_key_error(tmp_ticker_dir):
    # write_structured/write_derived can never produce this, but §8.4's
    # `validate` must be deterministic and fatal WITH A MESSAGE, so a
    # hand-edited or truncated artifact must not surface as a bare KeyError.
    bad = tmp_ticker_dir / "structured" / "hand_edited.json"
    bad.write_text(json.dumps({
        "_meta": {"id": "hand_edited", "ticker": "PANW", "producer": "fetch"},
        "data": {},
    }))
    with pytest.raises(ValueError, match="title"):
        prov.read_structured(bad)


def test_read_structured_missing_meta_block_raises_value_error_not_key_error(tmp_ticker_dir):
    bad = tmp_ticker_dir / "structured" / "truncated.json"
    bad.write_text(json.dumps({"data": {}}))
    with pytest.raises(ValueError, match="_meta"):
        prov.read_structured(bad)


def test_read_structured_missing_data_key_raises_value_error_not_key_error(tmp_ticker_dir):
    bad = tmp_ticker_dir / "structured" / "no_data.json"
    bad.write_text(json.dumps({"_meta": {
        "id": "no_data", "ticker": "PANW", "producer": "fetch", "title": "t",
        "source": "s", "as_of": "2026-08-11",
    }}))
    with pytest.raises(ValueError, match="data"):
        prov.read_structured(bad)


# --- atomicity: refused writes leave no .tmp litter and no corruption -----

def test_write_structured_refused_write_leaves_no_tmp_litter(tmp_ticker_dir):
    # a shape-check failure never reaches the filesystem at all
    with pytest.raises(ValueError):
        prov.write_structured(tmp_ticker_dir, _fetch_meta(url=None), {})
    leftovers = list((tmp_ticker_dir / "structured").glob("*.tmp"))
    assert leftovers == []
    assert list((tmp_ticker_dir / "structured").glob("*")) == []


def test_write_structured_nan_failure_leaves_no_tmp_litter(tmp_ticker_dir):
    # unlike the shape-check case above, a NaN payload fails INSIDE
    # _write_structured_json, after mkstemp has already created a temp file —
    # this exercises the `except BaseException: unlink` cleanup path directly
    with pytest.raises(ValueError):
        prov.write_structured(tmp_ticker_dir, _fetch_meta(), {"v": float("nan")})
    assert list((tmp_ticker_dir / "structured").glob("*")) == []


def test_write_structured_refused_overwrite_leaves_existing_artifact_byte_unchanged(tmp_ticker_dir):
    good_path = prov.write_structured(tmp_ticker_dir, _fetch_meta(), {"v": 1})
    original_bytes = good_path.read_bytes()

    # a second write to the SAME id, this time with an invalid (NaN) payload,
    # must be refused without disturbing the artifact already on disk
    with pytest.raises(ValueError):
        prov.write_structured(tmp_ticker_dir, _fetch_meta(), {"v": float("nan")})

    assert good_path.read_bytes() == original_bytes
    leftovers = list((tmp_ticker_dir / "structured").glob("*.tmp"))
    assert leftovers == []
