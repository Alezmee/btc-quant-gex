"""
Envoltorio del motor de calcular_gex.py para que el backend (y el
generador de reportes) puedan pedir 'dame el análisis completo como
diccionario' sin duplicar ninguna lógica de cálculo — reutiliza
exactamente las mismas funciones que usa la versión de consola.
"""

import sys
from pathlib import Path

# calcular_gex.py vive un nivel arriba de esta carpeta (api/)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import calcular_gex as gex  # noqa: E402


def _num(x):
    """Convierte numpy/pandas scalars a tipos nativos de Python (para JSON)."""
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return x


def _df_a_registros(df, columnas_fecha=None):
    """DataFrame -> lista de dicts, con columnas de fecha pasadas a string."""
    if df is None or df.empty:
        return []
    df = df.copy()
    for col in columnas_fecha or []:
        if col in df.columns:
            df[col] = df[col].astype(str)
    return df.to_dict(orient="records")


def generar_analisis(max_instrumentos: int = 100, rango_pct: float = 0.15, guardar_snapshot: bool = True):
    """
    Corre el análisis completo (GEX, muros, DEX, Vega, Charm, Vanna,
    Volga, flip point, score) y devuelve todo como un diccionario
    serializable a JSON. Es exactamente la misma secuencia que hace
    calcular_gex.py por consola, solo que acá se devuelve en vez de
    imprimirse.
    """
    df = gex.construir_dataset_gex(max_instrumentos)
    if df.empty:
        raise ValueError("No se obtuvieron datos de Deribit (revisar conexión o límite de instrumentos)")

    spot, gex_total, por_strike = gex.calcular_gex_actual(df)
    muros = gex.calcular_muros(df)
    dex_btc_total, dex_usd_total, dex_por_strike = gex.calcular_dex(df)
    vega_total, vega_por_vencimiento = gex.calcular_vega_neta(df)
    charmex_total, charm_por_vencimiento = gex.calcular_charm(df)
    vannaex_total, vanna_por_vencimiento = gex.calcular_vanna(df)
    volgaex_total, volgaex_magnitud, volga_por_vencimiento = gex.calcular_volga(df)

    perfil = gex.calcular_perfil_gex(df, rango_pct=rango_pct)
    flip = gex.encontrar_flip_point(perfil)

    score = gex.calcular_score_confluencia(
        gex_total, dex_btc_total, vega_total, charmex_total,
        vannaex_total, volgaex_magnitud, charm_por_vencimiento,
    )

    if guardar_snapshot:
        gex.guardar_snapshot_historico(
            spot, gex_total, dex_btc_total, dex_usd_total, vega_total,
            charmex_total, vannaex_total, volgaex_magnitud, flip,
            score, muros,
        )

    return {
        "spot": _num(spot),
        "gex_total": _num(gex_total),
        "regimen": "long_gamma" if gex_total > 0 else "short_gamma",
        "flip_point": _num(flip),
        "muros": {
            "call_wall": _num(muros["call_wall"]),
            "call_wall_valor": _num(muros["call_wall_valor"]),
            "put_wall": _num(muros["put_wall"]),
            "put_wall_valor": _num(muros["put_wall_valor"]),
            "resistencia_cercana": _num(muros["resistencia_cercana"]),
            "soporte_cercano": _num(muros["soporte_cercano"]),
        },
        "dex": {
            "btc_total": _num(dex_btc_total),
            "usd_total": _num(dex_usd_total),
        },
        "vega_total": _num(vega_total),
        "charm": {
            "total": _num(charmex_total),
            "por_vencimiento": _df_a_registros(charm_por_vencimiento, ["fecha_vencimiento"]),
        },
        "vanna": {
            "total": _num(vannaex_total),
            "por_vencimiento": _df_a_registros(vanna_por_vencimiento, ["fecha_vencimiento"]),
        },
        "volga": {
            "total": _num(volgaex_total),
            "magnitud": _num(volgaex_magnitud),
            "por_vencimiento": _df_a_registros(volga_por_vencimiento, ["fecha_vencimiento"]),
        },
        "score": {
            "confianza_regimen": _num(score["confianza_regimen"]),
            "tipo_regimen": score["tipo_regimen"],
            "sesgo_score": _num(score["sesgo_score"]),
            "sesgo_alineado": bool(score["sesgo_alineado"]),
            "riesgo_aceleracion": _num(score["riesgo_aceleracion"]),
            "ratio_volga_vega": _num(score["ratio_volga_vega"]),
        },
        "por_strike": _df_a_registros(por_strike),
        "perfil_gex": _df_a_registros(perfil),
    }
