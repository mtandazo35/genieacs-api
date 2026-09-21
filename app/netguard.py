"""Control de destinos de las peticiones que hace la propia API (anti-SSRF).

Dos politicas:
- Firmware por URL: solo destinos PUBLICOS, salvo redes privadas listadas
  explicitamente (p.ej. un repo interno de firmware).
- NBI de GenieACS: solo redes listadas (por defecto, privadas/loopback), para
  que Ajustes no sirva para sondear Internet ni el metadata 169.254.169.254.

Se valida cada IP a la que resuelve el nombre y cada salto de redireccion.
"""
import asyncio
import ipaddress
import socket
from urllib.parse import urlparse


class BlockedURL(ValueError):
    pass


def parse_networks(spec: str) -> list[ipaddress._BaseNetwork]:
    nets = []
    for part in (spec or "").split(","):
        part = part.strip()
        if part:
            nets.append(ipaddress.ip_network(part, strict=False))
    return nets


def _norm(ip: ipaddress._BaseAddress) -> ipaddress._BaseAddress:
    # ::ffff:10.0.0.1 se evalua como 10.0.0.1
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        return ip.ipv4_mapped
    return ip


async def resolve(host: str, port: int) -> list[ipaddress._BaseAddress]:
    try:
        return [_norm(ipaddress.ip_address(host))]
    except ValueError:
        pass
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, port, 0, socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise BlockedURL(f"No se pudo resolver {host}: {e}") from e
    ips = {_norm(ipaddress.ip_address(i[4][0].split("%")[0])) for i in infos}
    if not ips:
        raise BlockedURL(f"{host} no resuelve a ninguna IP")
    return sorted(ips, key=str)


def _split(url: str) -> tuple[str, int]:
    u = urlparse(url)
    if u.scheme not in ("http", "https"):
        raise BlockedURL("Solo se permiten URLs http:// o https://")
    if not u.hostname:
        raise BlockedURL("URL sin host")
    if u.username or u.password:
        raise BlockedURL("La URL no puede llevar usuario/clave")
    try:
        port = u.port or (443 if u.scheme == "https" else 80)
    except ValueError as e:
        raise BlockedURL("Puerto invalido") from e
    return u.hostname, port


async def check_public_url(url: str, extra_allowed: list | None = None) -> None:
    """Acepta destinos publicos, o privados si caen en extra_allowed."""
    host, port = _split(url)
    for ip in await resolve(host, port):
        if any(ip in n for n in (extra_allowed or [])):
            continue
        if not ip.is_global or ip.is_multicast:
            raise BlockedURL(f"Destino no permitido ({host} -> {ip}): red privada/reservada")


async def check_allowed_url(url: str, allowed: list) -> None:
    """Acepta solo destinos cuyas IPs esten TODAS en las redes permitidas."""
    host, port = _split(url)
    for ip in await resolve(host, port):
        if not any(ip in n for n in allowed):
            raise BlockedURL(f"Destino no permitido ({host} -> {ip}): fuera de las redes autorizadas")
