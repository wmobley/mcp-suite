"""
Tests for tapis_client.py — HTTP interactions with Tapis Abaco API.

Uses the ``responses`` library to mock HTTP calls without network access.
Verifies:
- submit_message returns execution_id on 200 OK.
- get_execution returns status dict.
- get_logs returns scrubbed log string.
- TapisError raised on HTTP errors; token values scrubbed from error messages.
"""

from __future__ import annotations

import json

import pytest
import responses as resp_lib

from dso_geo_mcp.tapis_client import (
    TapisError,
    get_execution,
    get_logs,
    submit_message,
)

TAPIS_BASE = "https://portals.tapis.io"
ACTOR_ID = "testactor123"
EXEC_ID = "exec-abc-123"
TOKEN = "eyJfaketoken.fake.sig"
MSG_DICT = {"operation": "gdalinfo", "input_url": "https://localhost/test.tif"}


# ---------------------------------------------------------------------------
# submit_message
# ---------------------------------------------------------------------------

class TestSubmitMessage:
    @resp_lib.activate
    def test_returns_execution_id_on_success(self):
        resp_lib.add(
            resp_lib.POST,
            f"{TAPIS_BASE}/v3/actors/{ACTOR_ID}/messages",
            json={"result": {"execution_id": EXEC_ID}, "status": "success"},
            status=200,
        )
        eid = submit_message(ACTOR_ID, MSG_DICT, TOKEN, TAPIS_BASE)
        assert eid == EXEC_ID

    @resp_lib.activate
    def test_posts_correct_message_body(self):
        """Verify the body is {"message": "<json string>"}."""
        received_body = {}

        def request_callback(request):
            received_body.update(json.loads(request.body))
            return (200, {}, json.dumps({"result": {"execution_id": EXEC_ID}}))

        resp_lib.add_callback(
            resp_lib.POST,
            f"{TAPIS_BASE}/v3/actors/{ACTOR_ID}/messages",
            callback=request_callback,
        )
        submit_message(ACTOR_ID, MSG_DICT, TOKEN, TAPIS_BASE)
        assert "message" in received_body
        # The message value is a JSON string
        parsed_msg = json.loads(received_body["message"])
        assert parsed_msg["operation"] == MSG_DICT["operation"]

    @resp_lib.activate
    def test_raises_on_http_error(self):
        resp_lib.add(
            resp_lib.POST,
            f"{TAPIS_BASE}/v3/actors/{ACTOR_ID}/messages",
            json={"error": "unauthorized"},
            status=401,
        )
        with pytest.raises(TapisError):
            submit_message(ACTOR_ID, MSG_DICT, TOKEN, TAPIS_BASE)

    @resp_lib.activate
    def test_scrubs_token_from_error_body(self):
        """Token appearing in error response body must be scrubbed before raising."""
        token_body = f"Authorization failed for token {TOKEN}"
        resp_lib.add(
            resp_lib.POST,
            f"{TAPIS_BASE}/v3/actors/{ACTOR_ID}/messages",
            body=token_body,
            status=401,
        )
        with pytest.raises(TapisError) as exc_info:
            submit_message(ACTOR_ID, MSG_DICT, TOKEN, TAPIS_BASE)
        # The raw token must NOT appear in the exception message
        assert TOKEN not in str(exc_info.value)

    @resp_lib.activate
    def test_raises_on_missing_execution_id_in_response(self):
        resp_lib.add(
            resp_lib.POST,
            f"{TAPIS_BASE}/v3/actors/{ACTOR_ID}/messages",
            json={"result": {}},  # execution_id missing
            status=200,
        )
        with pytest.raises(TapisError):
            submit_message(ACTOR_ID, MSG_DICT, TOKEN, TAPIS_BASE)


# ---------------------------------------------------------------------------
# get_execution
# ---------------------------------------------------------------------------

class TestGetExecution:
    @resp_lib.activate
    def test_returns_result_dict(self):
        resp_lib.add(
            resp_lib.GET,
            f"{TAPIS_BASE}/v3/actors/{ACTOR_ID}/executions/{EXEC_ID}",
            json={"result": {"status": "COMPLETE", "id": EXEC_ID}},
            status=200,
        )
        result = get_execution(ACTOR_ID, EXEC_ID, TOKEN, TAPIS_BASE)
        assert result["status"] == "COMPLETE"
        assert result["id"] == EXEC_ID

    @resp_lib.activate
    def test_raises_on_http_error(self):
        resp_lib.add(
            resp_lib.GET,
            f"{TAPIS_BASE}/v3/actors/{ACTOR_ID}/executions/{EXEC_ID}",
            json={"error": "not found"},
            status=404,
        )
        with pytest.raises(TapisError):
            get_execution(ACTOR_ID, EXEC_ID, TOKEN, TAPIS_BASE)

    @resp_lib.activate
    def test_scrubs_token_from_error_body(self):
        token_in_body = f"Bearer {TOKEN} is invalid"
        resp_lib.add(
            resp_lib.GET,
            f"{TAPIS_BASE}/v3/actors/{ACTOR_ID}/executions/{EXEC_ID}",
            body=token_in_body,
            status=401,
        )
        with pytest.raises(TapisError) as exc_info:
            get_execution(ACTOR_ID, EXEC_ID, TOKEN, TAPIS_BASE)
        assert TOKEN not in str(exc_info.value)

    @resp_lib.activate
    def test_raises_on_missing_result_key(self):
        resp_lib.add(
            resp_lib.GET,
            f"{TAPIS_BASE}/v3/actors/{ACTOR_ID}/executions/{EXEC_ID}",
            json={"status": "COMPLETE"},  # no "result" key
            status=200,
        )
        with pytest.raises(TapisError):
            get_execution(ACTOR_ID, EXEC_ID, TOKEN, TAPIS_BASE)


# ---------------------------------------------------------------------------
# get_logs
# ---------------------------------------------------------------------------

ACTOR_JSON_SUCCESS = json.dumps({
    "status": "ok",
    "operation": "gdalinfo",
    "gdal_version": "GDAL 3.8.0, released 2023/11/13",
    "metrics": {"duration_ms": 1234},
    "metadata": {"driverShortName": "GTiff"},
})


class TestGetLogs:
    @resp_lib.activate
    def test_returns_log_string(self):
        resp_lib.add(
            resp_lib.GET,
            f"{TAPIS_BASE}/v3/actors/{ACTOR_ID}/executions/{EXEC_ID}/logs",
            json={"result": {"logs": ACTOR_JSON_SUCCESS}},
            status=200,
        )
        logs = get_logs(ACTOR_ID, EXEC_ID, TOKEN, TAPIS_BASE)
        assert "gdalinfo" in logs

    @resp_lib.activate
    def test_scrubs_jwt_from_logs(self):
        """Tokens that appear in actor logs must be scrubbed."""
        logs_with_token = f"Some error: token={TOKEN}\n{ACTOR_JSON_SUCCESS}"
        resp_lib.add(
            resp_lib.GET,
            f"{TAPIS_BASE}/v3/actors/{ACTOR_ID}/executions/{EXEC_ID}/logs",
            json={"result": {"logs": logs_with_token}},
            status=200,
        )
        logs = get_logs(ACTOR_ID, EXEC_ID, TOKEN, TAPIS_BASE)
        assert TOKEN not in logs

    @resp_lib.activate
    def test_scrubs_bearer_from_logs(self):
        logs_with_bearer = f"Authorization: Bearer {TOKEN}\n{ACTOR_JSON_SUCCESS}"
        resp_lib.add(
            resp_lib.GET,
            f"{TAPIS_BASE}/v3/actors/{ACTOR_ID}/executions/{EXEC_ID}/logs",
            json={"result": {"logs": logs_with_bearer}},
            status=200,
        )
        logs = get_logs(ACTOR_ID, EXEC_ID, TOKEN, TAPIS_BASE)
        assert TOKEN not in logs

    @resp_lib.activate
    def test_raises_on_http_error(self):
        resp_lib.add(
            resp_lib.GET,
            f"{TAPIS_BASE}/v3/actors/{ACTOR_ID}/executions/{EXEC_ID}/logs",
            json={"error": "forbidden"},
            status=403,
        )
        with pytest.raises(TapisError):
            get_logs(ACTOR_ID, EXEC_ID, TOKEN, TAPIS_BASE)

    @resp_lib.activate
    def test_raises_on_missing_logs_key(self):
        resp_lib.add(
            resp_lib.GET,
            f"{TAPIS_BASE}/v3/actors/{ACTOR_ID}/executions/{EXEC_ID}/logs",
            json={"result": {}},  # no "logs" key
            status=200,
        )
        with pytest.raises(TapisError):
            get_logs(ACTOR_ID, EXEC_ID, TOKEN, TAPIS_BASE)
