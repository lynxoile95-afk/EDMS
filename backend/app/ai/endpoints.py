"""Classify local-provider URLs conservatively for data-egress consent."""

from ipaddress import ip_address, ip_network
from urllib.parse import urlparse

from app.ai.registry import get_provider

_PRIVATE_NETWORKS = tuple(ip_network(net) for net in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "::1/128", "fc00::/7",
))


def host_is_local(base_url: str) -> bool:
    host = (urlparse(base_url).hostname or "").lower().rstrip(".")
    if host in {"localhost", "ollama", "vlm"} or host.endswith((".localhost", ".local", ".internal")):
        return True
    try:
        addr = ip_address(host)
        return any(addr in network for network in _PRIVATE_NETWORKS)
    except ValueError:
        return False


def provider_is_local(provider_id: str, base_url: str) -> bool:
    return bool((get_provider(provider_id) or {}).get("isLocal", False)) and host_is_local(base_url)


def has_remote_consent(provider_id: str, base_url: str, consent: bool, extra: dict) -> bool:
    if provider_is_local(provider_id, base_url):
        return True
    # Older Ollama credentials were treated as local and could have consent=true
    # without the administrator ever seeing a cloud consent checkbox.
    if provider_id == "ollama":
        return bool(consent and extra.get("ollama_cloud_consent") is True)
    return bool(consent)
