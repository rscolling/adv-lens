"""IAPD client tests — fully offline via httpx.MockTransport."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from adv_lens.app.main import app
from adv_lens.app.settings import Settings
from adv_lens.ingestion.iapd import IAPDClient, _parse_current_brochures
from adv_lens.ingestion.models import BrochureRef

PDF_SIG = b"%PDF-1.7\n%fake-brochure-body\n%%EOF"


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        sec_rate_limit_rps=100.0,  # keep tests fast
        sec_max_retries=2,
    )


def _firm_payload(crd: str, brochures: list[dict]) -> dict:
    return {
        "hits": {
            "hits": [
                {
                    "_source": {
                        "iacontent": json.dumps({"crd": crd, "brochures": brochures}),
                    }
                }
            ]
        }
    }


async def test_fetch_brochure_writes_pdf_and_caches(tmp_path: Path) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, content=PDF_SIG, headers={"content-type": "application/pdf"})

    transport = httpx.MockTransport(handler)
    async with IAPDClient(_settings(tmp_path), transport=transport) as client:
        ref = BrochureRef(crd="108000", brochure_version_id="999001")

        first = await client.fetch_brochure(ref)
        assert first.from_cache is False
        assert first.bytes_downloaded == len(PDF_SIG)
        assert first.pdf_path.exists()
        assert first.sha256 == __import__("hashlib").sha256(PDF_SIG).hexdigest()

        second = await client.fetch_brochure(ref)
        assert second.from_cache is True
        assert len(calls) == 1  # cache hit skipped the network

        # URL shape: Part 2A brochure on files.adviserinfo.sec.gov,
        # keyed on the immutable BRCHR_VRSN_ID. The reports.* subdomain
        # serves Part 1A (regulatory data), which is a different document.
        sent = calls[0].url
        assert sent.host == "files.adviserinfo.sec.gov"
        assert sent.path == "/IAPD/Content/Common/crd_iapd_Brochure.aspx"
        assert sent.params["BRCHR_VRSN_ID"] == "999001"


async def test_fetch_brochure_rejects_non_pdf_body(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>blocked</html>")

    async with IAPDClient(_settings(tmp_path), transport=httpx.MockTransport(handler)) as client:
        ref = BrochureRef(crd="108000", brochure_version_id="999001")
        with pytest.raises(ValueError, match="not a PDF"):
            await client.fetch_brochure(ref)


async def test_fetch_brochure_retries_on_5xx(tmp_path: Path) -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 2:
            return httpx.Response(503, content=b"", headers={"Retry-After": "0"})
        return httpx.Response(200, content=PDF_SIG)

    async with IAPDClient(_settings(tmp_path), transport=httpx.MockTransport(handler)) as client:
        ref = BrochureRef(crd="108000", brochure_version_id="999001")
        result = await client.fetch_brochure(ref)
        assert result.bytes_downloaded == len(PDF_SIG)
        assert attempts["n"] == 2


async def test_list_current_brochures_parses_search_payload(tmp_path: Path) -> None:
    payload = _firm_payload(
        "108000",
        brochures=[
            {"brochureVersionId": "111", "brochureName": "Main", "isCurrent": True},
            {"brochureVersionId": "222", "brochureName": "Wrap", "isCurrent": True},
            {"brochureVersionId": "", "brochureName": "Skip", "isCurrent": True},
        ],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.adviserinfo.sec.gov"
        # SEC retired /search/entity in early 2026 in favour of /search/firm/<crd>
        assert request.url.path == "/search/firm/108000"
        return httpx.Response(200, json=payload)

    async with IAPDClient(_settings(tmp_path), transport=httpx.MockTransport(handler)) as client:
        refs = await client.list_current_brochures("108000")

    assert [r.brochure_version_id for r in refs] == ["111", "222"]
    assert refs[0].brochure_name == "Main"


async def test_list_current_brochures_parses_2026_response_shape(tmp_path: Path) -> None:
    """The post-2026 IAPD payload nests brochures inside a dict with a
    ``brochuredetails`` array and uses the capital-ID key."""
    payload = {
        "hits": {
            "hits": [
                {
                    "_source": {
                        "iacontent": (
                            '{"brochures":{"part2ExemptFlag":"N","brochuredetails":['
                            '{"brochureVersionID":1037550,'
                            '"brochureName":"BROWN ADVISORY, LLC FIRM BROCHURE",'
                            '"dateSubmitted":"3/31/2026"}]}}'
                        )
                    }
                }
            ]
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    async with IAPDClient(_settings(tmp_path), transport=httpx.MockTransport(handler)) as client:
        refs = await client.list_current_brochures("110181")

    assert [r.brochure_version_id for r in refs] == ["1037550"]
    assert refs[0].brochure_name == "BROWN ADVISORY, LLC FIRM BROCHURE"


def test_parse_current_brochures_accepts_prebaked_dict() -> None:
    payload = {
        "hits": {
            "hits": [
                {
                    "_source": {
                        "iacontent": {
                            "brochures": [
                                {"BRCHR_VRSN_ID": "42", "name": "Institutional"},
                            ]
                        }
                    }
                }
            ]
        }
    }
    refs = _parse_current_brochures("108000", payload)
    assert len(refs) == 1
    assert refs[0].brochure_version_id == "42"


def test_healthz_surfaces_ingestion_settings() -> None:
    client = TestClient(app)
    body = client.get("/healthz").json()
    assert "data_dir" in body["components"]
    assert "sec_rate_limit_rps" in body["components"]


def test_brochure_endpoint_rejects_non_numeric_crd() -> None:
    client = TestClient(app)
    r = client.get("/brochure/abc")
    assert r.status_code == 400
