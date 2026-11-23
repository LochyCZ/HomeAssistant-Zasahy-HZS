"""Doplnění chybějících mezilehlých certifikátů (AIA), jak to dělají prohlížeče.

Některé krajské servery (např. Královéhradecký) posílají jen svůj certifikát bez
mezilehlého certifikátu certifikační autority. Chrome si ho stáhne sám z adresy
„CA Issuers“ uvedené v certifikátu, Python ne – a spojení skončí chybou
„unable to get local issuer certificate“.

Tady děláme totéž co prohlížeč:
1. přečteme certifikát serveru (bez ověření, jen kvůli adrese CA Issuers),
2. stáhneme mezilehlé certifikáty (až do kořene),
3. vytvoříme SSL kontext se standardními kořenovými certifikáty + staženými mezilehlými.

Ověřování se tím NEVYPÍNÁ: řetězec musí pořád končit u důvěryhodné kořenové
autority a sedět na jméno serveru. Proto vypínáme VERIFY_X509_PARTIAL_CHAIN
(Python 3.13 ho má ve výchozím stavu zapnutý) – jinak by stažený mezilehlý
certifikát mohl sloužit jako kotva důvěry a podvržený by prošel.
"""

from __future__ import annotations

import asyncio
import logging
import ssl

import aiohttp
import certifi
from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding, pkcs7
from cryptography.x509.oid import AuthorityInformationAccessOID, ExtensionOID

_LOGGER = logging.getLogger(__name__)

MAX_HLOUBKA = 4  # víc mezilehlých certifikátů za sebou se v praxi nevidí
MAX_VELIKOST = 64 * 1024


class RetezecError(Exception):
    """Chybějící certifikáty se nepodařilo doplnit."""


def _kontext_bez_overeni() -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


async def certifikat_serveru(host: str, port: int) -> x509.Certificate:
    """Certifikát, který server posílá (jen k přečtení adresy vydavatele)."""
    loop = asyncio.get_running_loop()
    ctx = await loop.run_in_executor(None, _kontext_bez_overeni)
    try:
        async with asyncio.timeout(15):
            _, writer = await asyncio.open_connection(host, port, ssl=ctx, server_hostname=host)
    except (OSError, TimeoutError) as err:
        raise RetezecError(f"Nelze se připojit k {host}:{port}: {err}") from err
    try:
        der = writer.get_extra_info("ssl_object").getpeercert(binary_form=True)
    finally:
        writer.close()
    if not der:
        raise RetezecError("Server neposlal certifikát")
    return x509.load_der_x509_certificate(der)


def adresy_vydavatele(cert: x509.Certificate) -> list[str]:
    """URL z rozšíření Authority Information Access → CA Issuers."""
    try:
        aia = cert.extensions.get_extension_for_oid(
            ExtensionOID.AUTHORITY_INFORMATION_ACCESS
        ).value
    except x509.ExtensionNotFound:
        return []
    return [
        popis.access_location.value
        for popis in aia
        if popis.access_method == AuthorityInformationAccessOID.CA_ISSUERS
        and isinstance(popis.access_location, x509.UniformResourceIdentifier)
        and popis.access_location.value.lower().startswith(("http://", "https://"))
    ]


def nacti_certifikaty(data: bytes) -> list[x509.Certificate]:
    """CA Issuers bývá DER, někdy PEM nebo PKCS#7 (.p7c)."""
    for nacti in (
        lambda d: [x509.load_der_x509_certificate(d)],
        lambda d: x509.load_pem_x509_certificates(d),
        pkcs7.load_der_pkcs7_certificates,
        pkcs7.load_pem_pkcs7_certificates,
    ):
        try:
            certy = nacti(data)
        except ValueError:
            continue
        if certy:
            return list(certy)
    raise RetezecError("Stažený soubor není certifikát")


async def _stahni(session: aiohttp.ClientSession, url: str) -> bytes:
    try:
        async with asyncio.timeout(15):
            resp = await session.get(url)
            resp.raise_for_status()
            data = await resp.content.read(MAX_VELIKOST + 1)
    except (aiohttp.ClientError, TimeoutError) as err:
        raise RetezecError(f"Certifikát z {url} se nepodařilo stáhnout: {err}") from err
    if len(data) > MAX_VELIKOST:
        raise RetezecError(f"Soubor z {url} je na certifikát příliš velký")
    return data


def _je_korenovy(cert: x509.Certificate) -> bool:
    return cert.issuer == cert.subject


async def stahni_mezilehle(
    session: aiohttp.ClientSession, list_cert: x509.Certificate
) -> list[x509.Certificate]:
    """Postupně stáhne vydavatele, dokud nedojde ke kořeni (ten už je v úložišti)."""
    mezilehle: list[x509.Certificate] = []
    aktualni = list_cert
    for _ in range(MAX_HLOUBKA):
        if _je_korenovy(aktualni):
            break
        adresy = adresy_vydavatele(aktualni)
        if not adresy:
            break
        vydavatel = None
        for url in adresy:
            try:
                certy = nacti_certifikaty(await _stahni(session, url))
            except RetezecError as err:
                _LOGGER.debug("%s", err)
                continue
            vydavatel = next((c for c in certy if c.subject == aktualni.issuer), certy[0])
            break
        if vydavatel is None:
            break
        if _je_korenovy(vydavatel):
            break  # kořen nepřidáváme – musí být v důvěryhodném úložišti
        mezilehle.append(vydavatel)
        aktualni = vydavatel
    if not mezilehle:
        raise RetezecError("Certifikát serveru neuvádí, odkud stáhnout vydavatele")
    return mezilehle


def vytvor_kontext(mezilehle: list[x509.Certificate], cafile: str | None = None) -> ssl.SSLContext:
    """Standardní ověřování + stažené mezilehlé certifikáty jako pomocníci řetězce.

    Blokující (načítá úložiště certifikátů) – volat v executoru.
    """
    ctx = ssl.create_default_context(cafile=cafile or certifi.where())
    # mezilehlé certifikáty nesmí být kotvou důvěry – řetěz musí dojít ke kořeni
    ctx.verify_flags &= ~getattr(ssl, "VERIFY_X509_PARTIAL_CHAIN", 0)
    ctx.load_verify_locations(
        cadata="".join(c.public_bytes(Encoding.PEM).decode() for c in mezilehle)
    )
    return ctx


def popis_certifikatu(cert: x509.Certificate) -> str:
    return f"{cert.subject.rfc4514_string()} (vydal {cert.issuer.rfc4514_string()})"


async def doplnit_retezec(
    session: aiohttp.ClientSession, host: str, port: int = 443, cafile: str | None = None
) -> tuple[ssl.SSLContext, str]:
    """SSL kontext, se kterým projde server s neúplným řetězcem certifikátů.

    Vrací i popis řetězce do logu (pro případ, že ověření selže i potom).
    """
    list_cert = await certifikat_serveru(host, port)
    adresy = adresy_vydavatele(list_cert)
    try:
        mezilehle = await stahni_mezilehle(session, list_cert)
    except RetezecError as err:
        raise RetezecError(
            f"{err}; certifikát serveru: {popis_certifikatu(list_cert)}, CA Issuers: {adresy or 'žádné'}"
        ) from err
    popis = "; ".join(
        [f"server: {popis_certifikatu(list_cert)}", f"CA Issuers: {', '.join(adresy)}"]
        + [f"doplněno: {popis_certifikatu(c)}" for c in mezilehle]
    )
    _LOGGER.warning("Server %s neposílá celý řetězec certifikátů – %s", host, popis)
    loop = asyncio.get_running_loop()
    ctx = await loop.run_in_executor(None, vytvor_kontext, mezilehle, cafile)
    return ctx, popis
