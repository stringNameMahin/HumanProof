"""Small HTTP client for the analyst API."""

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class ApiError(Exception):
    """An API request could not be completed."""


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


class AnalystClient:
    def __init__(self, base_url, api_key, timeout=10):
        self.base_url = base_url.rstrip("/")
        try:
            parsed = urlsplit(self.base_url)
            port = parsed.port
        except ValueError as exc:
            raise ApiError("Enter a valid HTTP or HTTPS API address.") from exc
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or port == 0:
            raise ApiError("Enter a valid HTTP or HTTPS API address.")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ApiError("The API address cannot contain credentials, a query, or a fragment.")
        self.api_key = api_key
        self.timeout = timeout
        self.opener = build_opener(NoRedirects())

    def request(self, path, params=None, payload=None):
        url = f"{self.base_url}{path}"
        if params:
            query = {key: value for key, value in params.items() if value is not None}
            url += "?" + urlencode(query)
        headers = {"Accept": "application/json", "X-Analyst-Key": self.api_key}
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode("utf-8")
        request = Request(url, data=data, headers=headers)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read(20_000_001)
                if len(raw) > 20_000_000:
                    raise ApiError("The response is too large. Use a smaller page.")
                result = json.loads(raw)
        except HTTPError as exc:
            with exc:
                if 300 <= exc.code < 400:
                    raise ApiError("The API redirected the request. Enter its final address in the sidebar.") from exc
                if exc.code in {401, 403}:
                    raise ApiError("Access denied. Check the analyst key in the sidebar.") from exc
                if exc.code == 404:
                    raise ApiError("The API route or session was not found.") from exc
                message = f"The API returned HTTP {exc.code}."
                try:
                    detail = json.loads(exc.read(4096)).get("detail")
                    if isinstance(detail, str):
                        message += " " + detail[:300]
                except (ValueError, AttributeError):
                    pass
                raise ApiError(message) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ApiError("Cannot reach the API. Check the address and Django server.") from exc
        except (ValueError, UnicodeError) as exc:
            raise ApiError("The API did not return valid JSON.") from exc
        if not isinstance(result, dict):
            raise ApiError("The API returned an unexpected response.")
        return result
