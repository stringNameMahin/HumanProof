"""Network-layer features from server-observed request metadata and the optional TLS edge."""

import re
from collections import Counter
from statistics import fmean


CLIENT_LIBRARIES = re.compile(
    r"python-urllib|python-requests|aiohttp|httpx|curl/|wget/|go-http-client|node-fetch|undici|^node$|"
    r"axios/|okhttp|libwww-perl|java/|apache-httpclient|postmanruntime|scrapy|httpie", re.I)
CHROMIUM = re.compile(r"(Chrome|Chromium|CriOS|Edg)/(\d+)")


def user_agent_family(agent):
    if not agent:
        return "missing"
    if CLIENT_LIBRARIES.search(agent):
        return "library"
    if "Firefox/" in agent:
        return "firefox"
    if CHROMIUM.search(agent):
        return "chromium"
    if "Safari/" in agent:
        return "safari"
    return "other"


def _primary_language(value):
    if not isinstance(value, str) or not value.strip():
        return None
    return value.split(",")[0].split(";")[0].strip().split("-")[0].lower() or None


def _share(observations, name):
    return round(sum(bool(item.get(name)) for item in observations) / len(observations), 4)


def _mean(values):
    values = [value for value in values if isinstance(value, (int, float))]
    return round(fmean(values), 4) if values else None


def network_features(observations, browser=None):
    """Summarise per-request observations; every flag is None when its evidence is missing."""
    observations = [item for item in (observations or []) if isinstance(item, dict)]
    browser = browser or {}
    if not observations:
        return {"available": False, "request_count": 0, "transport": None}
    agents = Counter(item.get("user_agent") or "" for item in observations)
    agent = agents.most_common(1)[0][0]
    family = user_agent_family(agent)
    chromium = CHROMIUM.search(agent or "")
    chromium_version = int(chromium.group(2)) if chromium else None
    browser_like = family in {"chromium", "firefox", "safari"}
    sec_fetch = _share(observations, "sec_fetch_mode")
    client_hints = _share(observations, "sec_ch_ua")
    accept_language = _share(observations, "accept_language")
    hints = " ".join(item.get("sec_ch_ua") or "" for item in observations)
    tls = [item["tls"] for item in observations if isinstance(item.get("tls"), dict)]
    page_agent = browser.get("user_agent")
    page_language = _primary_language(browser.get("language"))
    header_language = _primary_language(next((item.get("accept_language") for item in observations
                                              if item.get("accept_language")), None))
    features = {
        "available": True,
        "request_count": len(observations),
        "transport": "edge-tls" if tls else "http",
        "user_agent": agent[:512] or None,
        "user_agent_family": family,
        "distinct_user_agents": len(agents),
        "header_count_mean": _mean([len(item.get("header_names") or []) for item in observations]),
        "header_order_hash": Counter(item.get("header_order_hash") for item in observations).most_common(1)[0][0],
        "sec_fetch_share": sec_fetch,
        "client_hint_share": client_hints,
        "accept_language_share": accept_language,
        "client_library_user_agent": family in {"library", "missing"},
        "missing_browser_headers": browser_like and (
            accept_language < 0.5 or (family == "chromium" and sec_fetch < 0.5)),
        "missing_client_hints": (family == "chromium" and chromium_version is not None
                                 and chromium_version >= 90 and client_hints < 0.5),
        "headless_client_hints": "HeadlessChrome" in hints if hints.strip() else None,
        "user_agent_mismatch": (page_agent != agent) if page_agent and agent else None,
        "language_mismatch": (page_language != header_language) if page_language and header_language else None,
        "tls_available": bool(tls),
    }
    if features["missing_client_hints"]:
        features["missing_browser_headers"] = True
    if tls:
        first = tls[0]
        grease = any(item.get("grease") for item in tls)
        features.update({
            "ja4": first.get("ja4"),
            "ja3_hash": first.get("ja3_hash"),
            "tls_version": first.get("tls_version"),
            "alpn": first.get("alpn"),
            "tls_grease": grease,
            "tls_cipher_count": first.get("cipher_count"),
            "tls_extension_count": first.get("extension_count"),
            "tls_handshake_ms": _mean([item.get("handshake_ms") for item in tls]),
            "tls_hello_delay_ms": _mean([item.get("hello_delay_ms") for item in tls]),
            "distinct_ja4": len({item.get("ja4") for item in tls}),
            "tls_user_agent_mismatch": family == "chromium" and not grease,
        })
    else:
        features.update({"ja4": None, "ja3_hash": None, "tls_version": None, "alpn": None, "tls_grease": None,
                         "tls_cipher_count": None, "tls_extension_count": None, "tls_handshake_ms": None,
                         "tls_hello_delay_ms": None, "distinct_ja4": 0, "tls_user_agent_mismatch": None})
    return features
