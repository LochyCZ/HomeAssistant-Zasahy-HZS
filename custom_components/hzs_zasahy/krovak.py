"""Převod souřadnic S-JTSK (Křovák, EPSG:5514) na WGS84.

API vrací gis1 = Y a gis2 = X v kladném tvaru (metry). Přesnost převodu
je pár metrů, na mapu a výpočet vzdálenosti to bohatě stačí.
"""

from __future__ import annotations

from math import atan, cos, degrees, exp, log, pi, radians, sin, sqrt, asin


def krovak_to_wgs84(y: float, x: float, h: float = 400.0) -> tuple[float, float]:
    """(Y, X) v S-JTSK -> (lat, lon) ve stupních WGS84."""
    y, x = abs(y), abs(x)

    # S-JTSK -> zeměpisné souřadnice na Besselově elipsoidu
    e = 0.081696831215303
    n = 0.97992470462083
    konst_u_ro = 12310230.12797036
    sin_uq, cos_uq = 0.863499969506341, 0.504348889819882
    sin_vq, cos_vq = 0.420215144586493, 0.907424504992097
    alfa = 1.000597498371542
    k = 1.003419163966575

    ro = sqrt(x * x + y * y)
    epsilon = 2 * atan(y / (ro + x))
    d = epsilon / n
    s = 2 * atan(exp(1 / n * log(konst_u_ro / ro))) - pi / 2
    sin_s, cos_s = sin(s), cos(s)
    sin_u = sin_uq * sin_s - cos_uq * cos_s * cos(d)
    cos_u = sqrt(1 - sin_u * sin_u)
    sin_dv = sin(d) * cos_s / cos_u
    cos_dv = sqrt(1 - sin_dv * sin_dv)
    sin_v = sin_vq * cos_dv - cos_vq * sin_dv
    cos_v = cos_vq * cos_dv + sin_vq * sin_dv
    l_jtsk = 2 * atan(sin_v / (1 + cos_v)) / alfa
    t = exp(2 / alfa * log((1 + sin_u) / cos_u / k))
    pom = (t - 1) / (t + 1)
    for _ in range(50):
        sin_b = pom
        pom = t * exp(e * log((1 + e * sin_b) / (1 - e * sin_b)))
        pom = (pom - 1) / (pom + 1)
        if abs(pom - sin_b) < 1e-15:
            break
    b_jtsk = atan(pom / sqrt(1 - pom * pom))

    # Bessel -> pravoúhlé souřadnice
    a = 6377397.15508
    f_1 = 299.152812853
    e2 = 1 - (1 - 1 / f_1) ** 2
    ro = a / sqrt(1 - e2 * sin(b_jtsk) ** 2)
    xb = (ro + h) * cos(b_jtsk) * cos(l_jtsk)
    yb = (ro + h) * cos(b_jtsk) * sin(l_jtsk)
    zb = ((1 - e2) * ro + h) * sin(b_jtsk)

    # Helmertova transformace S-JTSK -> WGS84
    dx, dy, dz = 570.69, 85.69, 462.84
    wz = radians(-5.2611 / 3600)
    wy = radians(-1.58676 / 3600)
    wx = radians(-4.99821 / 3600)
    m = 3.543e-6
    xn = dx + (1 + m) * (xb + wz * yb - wy * zb)
    yn = dy + (1 + m) * (-wz * xb + yb + wx * zb)
    zn = dz + (1 + m) * (wy * xb - wx * yb + zb)

    # pravoúhlé -> WGS84
    a = 6378137.0
    f_1 = 298.257223563
    a_b = f_1 / (f_1 - 1)
    p = sqrt(xn * xn + yn * yn)
    e2 = 1 - (1 - 1 / f_1) ** 2
    theta = atan(zn * a_b / p)
    st, ct = sin(theta), cos(theta)
    t = (zn + e2 * a_b * a * st**3) / (p - e2 * a * ct**3)
    lat = atan(t)
    lon = 2 * atan(yn / (p + xn))
    return degrees(lat), degrees(lon)


def vzdalenost_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Vzdálenost dvou bodů po povrchu Země (haversine)."""
    r = 6371.0088
    p1, p2 = radians(lat1), radians(lat2)
    dp = p2 - p1
    dl = radians(lon2 - lon1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * r * asin(sqrt(a))
