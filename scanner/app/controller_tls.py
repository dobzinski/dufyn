"""Verified outbound Controller TLS, with an optional additional PEM CA bundle."""
import ssl
from urllib.parse import urlsplit


def ca_context(ca_file):
    context = ssl.create_default_context()
    # Add private trust without discarding the platform's public trust roots.
    context.load_verify_locations(cafile=ca_file)
    return context


def validate_ca_file(controller):
    value = controller.setdefault("ca_file", None)
    if value is not None and not isinstance(value, str):
        raise ValueError("controller.ca_file must be a PEM file path or null")
    value = value.strip() or None if isinstance(value, str) else None
    controller["ca_file"] = value
    if value:
        try:
            ca_context(value)
        except (OSError, ValueError) as error:
            raise ValueError("controller.ca_file must be a readable, valid PEM CA bundle") from error


def request_tls_options(url, ca_file=None):
    if ca_file and urlsplit(url).scheme.lower() == "https":
        return {"context": ca_context(ca_file)}
    return {}
