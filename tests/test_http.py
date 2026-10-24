import json

import pytest

from kepler.http import Http, HttpError


@pytest.mark.parametrize("failure", ["provider", "transport"])
def test_errors_do_not_reflect_credentials(failure):
    token = "synthetic-secret"

    def transport(*args):
        if failure == "transport":
            raise RuntimeError("Authorization: Bearer " + token)
        return 401, json.dumps({"error": {"message": "Credential rejected: " + token}}).encode()

    client = Http({"Authorization": "Bearer " + token}, transport)
    with pytest.raises(HttpError) as caught:
        client.get("https://example.invalid/data")
    assert token not in str(caught.value)
    assert "Authorization" not in str(caught.value)


@pytest.mark.parametrize("body", [b"", b"{", b"upstream failure", b'{"value": NaN}', b'{"error": "failure"}'])
def test_invalid_json_or_provider_error_is_a_failed_read(body):
    with pytest.raises(HttpError):
        Http(transport=lambda *args: (200, body)).get("https://example.invalid/data")
