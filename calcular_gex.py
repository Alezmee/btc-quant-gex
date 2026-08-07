"""
Calcula Gamma Exposure (GEX) y el "flip point" para opciones BTC en Deribit.

GEX estimado por instrumento:
    GEX = gamma * open_interest * spot^2 * 0.01
    (convención: calls suman positivo, puts restan -> asume dealers
    netos del lado contrario al OI de cada tipo)

El flip point se calcula recalculando la gamma de cada opción (vía
Black-Scholes, usando la IV de mercado de cada instrumento) para una
grilla de precios hipotéticos de spot, y buscando dónde el GEX total
cruza cero.

Uso:
    python3 calcular_gex.py --max-instrumentos 926
"""

import argparse
import math
import time
from pathlib import Path

import pandas as pd
import requests

BASE_URL = "https://www.deribit.com/api/v2"
OUT_DIR = Path(__file__).resolve().parent / "data"
OUT_DIR.mkdir(exist_ok=True)

CONTRACT_SIZE = 1.0  # 1 opción BTC en Deribit = 1 BTC de nocional


# ---------- Black-Scholes gamma (sin dependencias externas) ----------

def norm_pdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def bs_gamma(spot, strike, tiempo_anios, iv, r=0.0):
    """Gamma de Black-Scholes (misma para call y put)."""
    if tiempo_anios <= 0 or iv <= 0 or spot <= 0 or strike <= 0:
        return 0.0
    d1 = (math.log(spot / strike) + (r + 0.5 * iv**2) * tiempo_anios) / (
        iv * math.sqrt(tiempo_anios)
    )
    return norm_pdf(d1) / (spot * iv * math.sqrt(tiempo_anios))


def bs_charm(spot, strike, tiempo_anios, iv, r=0.0):
    """
    Charm de Black-Scholes (dDelta/dTiempo), en unidades 'por año'.
    Con r=0 (asunción consistente con bs_gamma) da el mismo valor para
    call y put, porque Delta_put = Delta_call - 1 y esa diferencia es
    constante en el tiempo.
    """
    if tiempo_anios <= 0 or iv <= 0 or spot <= 0 or strike <= 0:
        return 0.0
    sqrt_t = math.sqrt(tiempo_anios)
    d1 = (math.log(spot / strike) + (r + 0.5 * iv**2) * tiempo_anios) / (iv * sqrt_t)
    d2 = d1 - iv * sqrt_t
    return norm_pdf(d1) * d2 / (2 * tiempo_anios)


def bs_vanna(spot, strike, tiempo_anios, iv, r=0.0):
    """
    Vanna de Black-Scholes (dDelta/dIV), en unidades 'por punto entero
    de IV' (ej: de 0.55 a 1.55). Misma para call y put por el mismo
    motivo que en charm (la diferencia entre delta_call y delta_put es
    constante, no depende de la IV).
    """
    if tiempo_anios <= 0 or iv <= 0 or spot <= 0 or strike <= 0:
        return 0.0
    sqrt_t = math.sqrt(tiempo_anios)
    d1 = (math.log(spot / strike) + (r + 0.5 * iv**2) * tiempo_anios) / (iv * sqrt_t)
    d2 = d1 - iv * sqrt_t
    return -norm_pdf(d1) * d2 / iv


def bs_volga(spot, strike, tiempo_anios, iv, r=0.0):
    """
    Volga (Vomma) de Black-Scholes: dVega/dIV, en unidades 'por punto
    entero de IV'. Mide qué tan no-lineal es la exposición a volatilidad
    — a diferencia de Vanna, no importa tanto la dirección sino la
    MAGNITUD: cuánto se puede acelerar la vega (y con ella el flujo de
    hedging) si la IV se mueve fuerte, como en un shock macro.
    """
    if tiempo_anios <= 0 or iv <= 0 or spot <= 0 or strike <= 0:
        return 0.0
    sqrt_t = math.sqrt(tiempo_anios)
    d1 = (math.log(spot / strike) + (r + 0.5 * iv**2) * tiempo_anios) / (iv * sqrt_t)
    d2 = d1 - iv * sqrt_t
    vega = spot * sqrt_t * norm_pdf(d1)  # vega por 1.0 de IV (BS estándar)
    return vega * d1 * d2 / iv


def norm_cdf(x):
    """CDF de la normal estándar, sin dependencias externas (vía erf)."""
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _bs_d1_d2(spot, strike, tiempo_anios, iv, r=0.0):
    sqrt_t = math.sqrt(tiempo_anios)
    d1 = (math.log(spot / strike) + (r + 0.5 * iv**2) * tiempo_anios) / (iv * sqrt_t)
    d2 = d1 - iv * sqrt_t
    return d1, d2


def bs_call_price(spot, strike, tiempo_anios, iv, r=0.0):
    """Precio de una call europea (Black-Scholes)."""
    if tiempo_anios <= 0 or iv <= 0 or spot <= 0 or strike <= 0:
        return max(spot - strike, 0.0)
    d1, d2 = _bs_d1_d2(spot, strike, tiempo_anios, iv, r)
    return spot * norm_cdf(d1) - strike * math.exp(-r * tiempo_anios) * norm_cdf(d2)


def bs_put_price(spot, strike, tiempo_anios, iv, r=0.0):
    """Precio de una put europea (Black-Scholes), vía put-call parity."""
    if tiempo_anios <= 0 or iv <= 0 or spot <= 0 or strike <= 0:
        return max(strike - spot, 0.0)
    call = bs_call_price(spot, strike, tiempo_anios, iv, r)
    return call - spot + strike * math.exp(-r * tiempo_anios)


# ---------- Descarga de datos de mercado ----------

def get_instrumentos_btc_options():
    r = requests.get(
        f"{BASE_URL}/public/get_instruments",
        params={"currency": "BTC", "kind": "option", "expired": "false"},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["result"]


def get_ticker(instrument_name):
    """Trae OI, IV marcada, greeks y spot subyacente para un instrumento."""
    r = requests.get(
        f"{BASE_URL}/public/ticker",
        params={"instrument_name": instrument_name},
        timeout=15,
    )
    r.raise_for_status()
    return r.json().get("result")


def parse_instrument_name(nombre):
    """
    Formato Deribit: BTC-3JUL26-58000-C
    Devuelve (fecha_vencimiento_str, strike, tipo)
    """
    partes = nombre.split("-")
    vencimiento_str, strike_str, tipo = partes[1], partes[2], partes[3]
    return vencimiento_str, float(strike_str), ("C" if tipo == "C" else "P")


def deribit_fecha_a_timestamp(fecha_str):
    """'3JUL26' -> datetime a las 08:00 UTC (hora estándar de vencimiento Deribit)."""
    return pd.to_datetime(fecha_str, format="%d%b%y").tz_localize("UTC") + pd.Timedelta(
        hours=8
    )


# ---------- Cálculo de GEX ----------

def construir_dataset_gex(max_instrumentos):
    instrumentos = get_instrumentos_btc_options()
    nombres = [i["instrument_name"] for i in instrumentos][:max_instrumentos]

    filas = []
    ahora = pd.Timestamp.now(tz="UTC")

    for idx, nombre in enumerate(nombres, 1):
        print(f"[{idx}/{len(nombres)}] {nombre}")
        try:
            t = get_ticker(nombre)
        except requests.RequestException as e:
            print(f"  aviso: no se pudo bajar {nombre} ({e})")
            continue

        if not t or t.get("open_interest") is None:
            continue

        vto_str, strike, tipo = parse_instrument_name(nombre)
        vto_dt = deribit_fecha_a_timestamp(vto_str)
        tiempo_anios = max((vto_dt - ahora).total_seconds(), 0) / (365 * 24 * 3600)

        greeks = t.get("greeks") or {}
        filas.append(
            {
                "instrument_name": nombre,
                "strike": strike,
                "tipo": tipo,
                "vencimiento": vto_dt,
                "tiempo_anios": tiempo_anios,
                "open_interest": t.get("open_interest", 0.0),
                "iv_marcada": (t.get("mark_iv") or 0.0) / 100.0,
                "gamma_actual": greeks.get("gamma", 0.0),
                "delta_actual": greeks.get("delta", 0.0),
                "vega_actual": greeks.get("vega", 0.0),
                "spot_subyacente": t.get("underlying_price") or t.get("index_price"),
            }
        )
        time.sleep(0.05)

    df = pd.DataFrame(filas)
    df.to_parquet(OUT_DIR / "gex_dataset_crudo.parquet", index=False)
    return df


def calcular_gex_actual(df):
    """GEX total con la gamma reportada por Deribit al spot actual."""
    spot = df["spot_subyacente"].dropna().iloc[0]
    df = df.copy()
    df["gex_contrib"] = (
        df["gamma_actual"] * df["open_interest"] * CONTRACT_SIZE * spot**2 * 0.01
    )
    df.loc[df["tipo"] == "P", "gex_contrib"] *= -1

    por_strike = (
        df.groupby("strike")["gex_contrib"].sum().sort_index().reset_index()
    )
    gex_total = df["gex_contrib"].sum()
    return spot, gex_total, por_strike


def calcular_muros(df, top_n=5):
    """
    Identifica call wall y put wall: los strikes con mayor concentración
    de GEX (gamma * OI) del lado de calls y de puts respectivamente.

    Convención habitual: el call wall suele actuar como resistencia
    (los dealers venden para cubrirse a medida que el precio se acerca
    desde abajo) y el put wall como soporte (compran a medida que el
    precio cae hacia ahí) — bajo el mismo supuesto de siempre sobre de
    qué lado están los dealers. Se agrega across todos los vencimientos
    de la muestra (no separa por fecha), que es la convención más común
    para graficar "muros" de gamma.
    """
    spot = df["spot_subyacente"].dropna().iloc[0]
    df2 = df.copy()
    df2["gex_abs"] = df2["gamma_actual"] * df2["open_interest"] * CONTRACT_SIZE * spot**2 * 0.01

    calls = df2[df2["tipo"] == "C"].groupby("strike")["gex_abs"].sum().sort_values(ascending=False)
    puts = df2[df2["tipo"] == "P"].groupby("strike")["gex_abs"].sum().sort_values(ascending=False)

    call_wall = calls.index[0] if len(calls) else None
    call_wall_valor = calls.iloc[0] if len(calls) else 0.0
    put_wall = puts.index[0] if len(puts) else None
    put_wall_valor = puts.iloc[0] if len(puts) else 0.0

    calls_top = calls.head(top_n)
    puts_top = puts.head(top_n)

    candidatos_resistencia = calls_top[calls_top.index > spot]
    candidatos_soporte = puts_top[puts_top.index < spot]

    resistencia_cercana = candidatos_resistencia.index.min() if len(candidatos_resistencia) else None
    soporte_cercano = candidatos_soporte.index.max() if len(candidatos_soporte) else None

    return {
        "spot": spot,
        "call_wall": call_wall,
        "call_wall_valor": call_wall_valor,
        "put_wall": put_wall,
        "put_wall_valor": put_wall_valor,
        "resistencia_cercana": resistencia_cercana,
        "soporte_cercano": soporte_cercano,
        "calls_top": calls_top,
        "puts_top": puts_top,
    }


def calcular_dex(df):
    """
    Delta Exposure (DEX) neto del mercado.

    Convención: sumamos delta*OI tal cual (calls con delta positivo,
    puts con delta negativo, Deribit ya los devuelve con su signo).
    Esto representa el delta neto que tienen los TENEDORES de las
    opciones (customers). El delta de los dealers se asume opuesto:
    si el DEX de los customers da positivo, se interpreta que los
    dealers están netos cortos delta (y viceversa).

    Devuelve el DEX en BTC (unidades de subyacente) y en USD nocional.
    """
    df = df.copy()
    spot = df["spot_subyacente"].dropna().iloc[0]

    df["dex_btc"] = df["delta_actual"] * df["open_interest"] * CONTRACT_SIZE
    dex_btc_total = df["dex_btc"].sum()
    dex_usd_total = dex_btc_total * spot

    por_strike = (
        df.groupby("strike")["dex_btc"].sum().sort_index().reset_index()
    )
    return dex_btc_total, dex_usd_total, por_strike


def calcular_vega_neta(df):
    """
    Vega neta agregada del mercado (magnitud de exposición a cambios de IV).

    A diferencia del DEX, acá no importa tanto el signo por tipo (calls
    y puts tienen vega positiva): lo que interesa es el TAMAÑO total de
    la exposición, que indica cuánto puede moverse el hedging de dealers
    ante un cambio de IV (esto es lo que Vanna/Volga terminan traduciendo
    en flujo real más adelante).
    """
    df = df.copy()
    df["vega_contrib"] = df["vega_actual"] * df["open_interest"] * CONTRACT_SIZE
    vega_total = df["vega_contrib"].sum()

    por_vencimiento = (
        df.groupby(df["vencimiento"].dt.date)["vega_contrib"]
        .sum()
        .reset_index()
        .rename(columns={"vencimiento": "fecha_vencimiento"})
    )
    return vega_total, por_vencimiento


def calcular_charm(df):
    """
    Charm Exposure (CharmEX): cuánto va a decaer el delta agregado del
    mercado solo por el paso de un día, sin que se mueva el precio.

    Usamos la misma convención de signo que en GEX (calls suman, puts
    restan) para poder leerlo de forma consistente junto al resto —
    es una convención, no un estándar único de la industria.

    Se calcula vía Black-Scholes (Deribit no expone charm directamente
    en el ticker), usando la IV marcada y el tiempo a vencimiento de
    cada instrumento.
    """
    df = df.copy()
    spot = df["spot_subyacente"].dropna().iloc[0]

    df["charm_anual"] = df.apply(
        lambda f: bs_charm(spot, f["strike"], f["tiempo_anios"], f["iv_marcada"]),
        axis=1,
    )
    df["charm_dia"] = df["charm_anual"] / 365.0  # decaimiento de delta por día

    df["charmex_btc_dia"] = df["charm_dia"] * df["open_interest"] * CONTRACT_SIZE
    df.loc[df["tipo"] == "P", "charmex_btc_dia"] *= -1

    charmex_total = df["charmex_btc_dia"].sum()

    por_vencimiento = (
        df.groupby(df["vencimiento"].dt.date)["charmex_btc_dia"]
        .sum()
        .reset_index()
        .rename(columns={"vencimiento": "fecha_vencimiento"})
        .sort_values("fecha_vencimiento")
    )
    return charmex_total, por_vencimiento


def calcular_vanna(df):
    """
    Vanna Exposure (VannaEX): cuánto cambiaría el delta agregado del
    mercado si la IV subiera/bajara 1 punto entero (ej: de 55% a 56%),
    sin que se mueva el precio.

    Misma convención de signo que GEX/CharmEX (calls suman, puts restan).

    Interpretación de la dirección del flujo de hedging (bajo el mismo
    supuesto de siempre: dealers del lado contrario al OI):
    - VannaEX > 0 y la IV sube -> el delta de los tenedores aumenta ->
      los dealers necesitan AMPLIAR su cobertura (comprar más)
    - VannaEX < 0 y la IV sube -> el delta de los tenedores baja ->
      los dealers necesitan REDUCIR su cobertura (vender)
    (e igual pero invertido si la IV baja en vez de subir)
    """
    df = df.copy()
    spot = df["spot_subyacente"].dropna().iloc[0]

    df["vanna_por_punto_iv"] = df.apply(
        lambda f: bs_vanna(spot, f["strike"], f["tiempo_anios"], f["iv_marcada"]),
        axis=1,
    ) / 100.0  # convertir de 'por 1.0 de IV' a 'por 1 punto porcentual'

    df["vannaex_btc_por_1pct"] = df["vanna_por_punto_iv"] * df["open_interest"] * CONTRACT_SIZE
    df.loc[df["tipo"] == "P", "vannaex_btc_por_1pct"] *= -1

    vannaex_total = df["vannaex_btc_por_1pct"].sum()

    por_vencimiento = (
        df.groupby(df["vencimiento"].dt.date)["vannaex_btc_por_1pct"]
        .sum()
        .reset_index()
        .rename(columns={"vencimiento": "fecha_vencimiento"})
        .sort_values("fecha_vencimiento")
    )
    return vannaex_total, por_vencimiento


def calcular_volga(df):
    """
    Volga Exposure (VolgaEX): qué tan no-lineal es la exposición a
    volatilidad del mercado. A diferencia de GEX/DEX/CharmEX/VannaEX,
    acá lo relevante no es tanto el signo sino la MAGNITUD relativa a
    la vega neta: mucha 'convexidad' de vega significa que un shock de
    IV grande (evento macro, crash) puede generar una aceleración
    desproporcionada del flujo de hedging, no solo un ajuste lineal.
    """
    df = df.copy()
    spot = df["spot_subyacente"].dropna().iloc[0]

    df["volga_por_punto_iv"] = df.apply(
        lambda f: bs_volga(spot, f["strike"], f["tiempo_anios"], f["iv_marcada"]),
        axis=1,
    ) / 100.0

    df["volgaex_btc_por_1pct"] = df["volga_por_punto_iv"] * df["open_interest"] * CONTRACT_SIZE
    df.loc[df["tipo"] == "P", "volgaex_btc_por_1pct"] *= -1

    volgaex_total = df["volgaex_btc_por_1pct"].sum()
    volgaex_magnitud_total = df["volgaex_btc_por_1pct"].abs().sum()  # convexidad bruta, sin cancelaciones

    por_vencimiento = (
        df.groupby(df["vencimiento"].dt.date)["volgaex_btc_por_1pct"]
        .sum()
        .reset_index()
        .rename(columns={"vencimiento": "fecha_vencimiento"})
        .sort_values("fecha_vencimiento")
    )
    return volgaex_total, volgaex_magnitud_total, por_vencimiento


def _percentil(valor, serie):
    """Percentil (0-100) de 'valor' dentro de 'serie' (incluye a valor mismo)."""
    serie = serie.dropna()
    if len(serie) == 0:
        return 50.0  # sin histórico, neutral
    return float((serie <= valor).mean() * 100)


def calcular_score_confluencia(
    gex_total, dex_btc_total, vega_total, charmex_total,
    vannaex_total, volgaex_magnitud, charm_por_vencimiento,
):
    """
    Traduce las 5 griegas a 3 ejes de lectura, en vez de un único número
    (mezclar cosas que miden fenómenos distintos en un solo score sería
    menos honesto que separarlos, igual que hace el roadmap con sus
    checklists por escenario).

    Usa el histórico acumulado en data/historico_snapshots.csv para dar
    contexto de percentil — con pocas corridas todavía es aproximado,
    mejora a medida que se acumulan más datos.

    Devuelve un dict con los 3 ejes y su explicación.
    """
    hist_path = OUT_DIR / "historico_snapshots.csv"
    hist = _leer_historico_seguro(hist_path)

    charm_cercano = charm_por_vencimiento.head(2)["charmex_btc_dia"].abs().sum() if not charm_por_vencimiento.empty else 0.0
    ratio_volga_vega = volgaex_magnitud / abs(vega_total) if vega_total else 0.0

    # ---- Eje 1: confianza de régimen (rango/reversión vs tendencia/expansión) ----
    pct_gex = _percentil(abs(gex_total), hist["gex_total"].abs()) if "gex_total" in hist else 50.0
    pct_charm = _percentil(charm_cercano, hist["charmex_total"].abs()) if "charmex_total" in hist else 50.0

    confianza_regimen = 50 + 0.25 * (pct_gex - 50) + 0.25 * (pct_charm - 50)
    confianza_regimen = max(0, min(100, confianza_regimen))
    tipo_regimen = "RANGO / REVERSIÓN (long gamma)" if gex_total > 0 else "TENDENCIA / EXPANSIÓN (short gamma)"

    # ---- Eje 2: sesgo direccional (DEX + Vanna) ----
    dex_signo = 1 if dex_btc_total > 0 else -1
    vanna_signo = 1 if vannaex_total > 0 else -1
    alineados = dex_signo == vanna_signo

    pct_dex = _percentil(abs(dex_btc_total), hist["dex_btc_total"].abs()) if "dex_btc_total" in hist else 50.0
    fuerza = pct_dex / 100.0  # 0 a 1

    if alineados:
        sesgo_score = dex_signo * (50 + 50 * fuerza * 0.5)  # hasta +/-75 si confirmado y fuerte
    else:
        sesgo_score = dex_signo * (15 * fuerza)  # débil, sin confirmación de Vanna
    sesgo_score = max(-100, min(100, sesgo_score))

    # ---- Eje 3: riesgo de aceleración (Volga/Vega) ----
    pct_ratio = _percentil(ratio_volga_vega, (hist["volgaex_magnitud"].abs() / hist["vega_total"].abs())) if "volgaex_magnitud" in hist and "vega_total" in hist else 50.0
    riesgo_aceleracion = pct_ratio

    return {
        "confianza_regimen": confianza_regimen,
        "tipo_regimen": tipo_regimen,
        "sesgo_score": sesgo_score,
        "sesgo_alineado": alineados,
        "riesgo_aceleracion": riesgo_aceleracion,
        "ratio_volga_vega": ratio_volga_vega,
    }




def _leer_historico_seguro(path):
    """
    Lee el CSV histórico de forma tolerante a cambios de esquema entre
    versiones del script (por ejemplo, si se agregaron columnas nuevas
    en el medio y el archivo quedó con filas de distinto largo). Si no
    se puede leer, hace un backup del archivo problemático y arranca
    un histórico limpio en vez de romper la corrida actual.
    """
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:
        backup = path.with_name(
            f"{path.stem}_corrupto_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.csv"
        )
        try:
            path.rename(backup)
            print(f"aviso: {path.name} tenía un formato incompatible (cambió de columnas entre")
            print(f"  versiones del script). Se guardó una copia en {backup.name} y se arranca")
            print(f"  un histórico nuevo limpio a partir de esta corrida.")
        except Exception:
            pass
        return pd.DataFrame()


def guardar_snapshot_historico(spot, gex_total, dex_btc_total, dex_usd_total, vega_total, charmex_total, vannaex_total, volgaex_magnitud, flip, score, muros):
    """
    Va agregando una fila por corrida a un CSV histórico, para poder
    comparar el DEX/Vega/Charm/Vanna/Volga/GEX de hoy contra corridas
    anteriores (necesario para saber si están 'muy sesgados' o 'altos'
    en términos relativos, como pide el roadmap).

    Además guarda el SCORE derivado (régimen, sesgo, riesgo) y los
    niveles de MUROS de esa corrida — necesario para poder hacer forward-
    testing después: comparar qué decía el score en el momento T contra
    qué pasó con el precio unas horas/días más tarde.

    Reescribe el archivo completo cada vez (en vez de solo agregar una
    fila) para que el esquema de columnas nunca quede inconsistente,
    incluso si en el futuro se suman columnas nuevas.
    """
    fila = pd.DataFrame([{
        "timestamp": pd.Timestamp.now(tz="UTC"),
        "spot": spot,
        "gex_total": gex_total,
        "flip_point": flip,
        "dex_btc_total": dex_btc_total,
        "dex_usd_total": dex_usd_total,
        "vega_total": vega_total,
        "charmex_total": charmex_total,
        "vannaex_total": vannaex_total,
        "volgaex_magnitud": volgaex_magnitud,
        "call_wall": muros.get("call_wall"),
        "put_wall": muros.get("put_wall"),
        "resistencia_cercana": muros.get("resistencia_cercana"),
        "soporte_cercano": muros.get("soporte_cercano"),
        "tipo_regimen": score.get("tipo_regimen"),
        "confianza_regimen": score.get("confianza_regimen"),
        "sesgo_score": score.get("sesgo_score"),
        "sesgo_alineado": score.get("sesgo_alineado"),
        "riesgo_aceleracion": score.get("riesgo_aceleracion"),
    }])

    path = OUT_DIR / "historico_snapshots.csv"
    hist_previo = _leer_historico_seguro(path)

    if not hist_previo.empty:
        combinado = pd.concat([hist_previo, fila], ignore_index=True, sort=False)
    else:
        combinado = fila

    combinado.to_csv(path, mode="w", header=True, index=False)


def calcular_perfil_gex(df, rango_pct=0.15, pasos=61):
    """
    Recalcula el GEX total para una grilla de precios hipotéticos de spot,
    usando Black-Scholes con la IV marcada de cada instrumento (fija) y
    el OI actual (fijo). Sirve para encontrar el flip point.
    """
    spot_actual = df["spot_subyacente"].dropna().iloc[0]
    spots = [
        spot_actual * (1 - rango_pct) + i * (spot_actual * 2 * rango_pct) / (pasos - 1)
        for i in range(pasos)
    ]

    perfil = []
    for s in spots:
        gex_total = 0.0
        for _, fila in df.iterrows():
            if fila["tiempo_anios"] <= 0 or fila["iv_marcada"] <= 0:
                continue
            g = bs_gamma(s, fila["strike"], fila["tiempo_anios"], fila["iv_marcada"])
            contrib = g * fila["open_interest"] * CONTRACT_SIZE * s**2 * 0.01
            if fila["tipo"] == "P":
                contrib *= -1
            gex_total += contrib
        perfil.append({"spot_hipotetico": s, "gex_total": gex_total})

    return pd.DataFrame(perfil)


def encontrar_flip_point(perfil_df):
    """Busca el cruce por cero (cambio de signo) en el perfil de GEX."""
    perfil_df = perfil_df.sort_values("spot_hipotetico").reset_index(drop=True)
    signo = perfil_df["gex_total"] > 0

    cruces = signo.ne(signo.shift()).fillna(False)
    idx_cruces = perfil_df.index[cruces][1:]  # ignorar el primer punto

    if len(idx_cruces) == 0:
        return None  # no hay cruce en el rango simulado

    idx = idx_cruces[0]
    x0, x1 = perfil_df.loc[idx - 1, "spot_hipotetico"], perfil_df.loc[idx, "spot_hipotetico"]
    y0, y1 = perfil_df.loc[idx - 1, "gex_total"], perfil_df.loc[idx, "gex_total"]
    # interpolación lineal para afinar el punto de cruce
    flip = x0 + (0 - y0) * (x1 - x0) / (y1 - y0)
    return flip


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-instrumentos", type=int, default=100)
    parser.add_argument("--rango-pct", type=float, default=0.15, help="Rango +/- para el perfil de GEX")
    args = parser.parse_args()

    print("Descargando OI, IV y greeks por instrumento...")
    df = construir_dataset_gex(args.max_instrumentos)

    if df.empty:
        print("No se obtuvieron datos. Revisá la conexión o el límite de instrumentos.")
        return

    spot, gex_total, por_strike = calcular_gex_actual(df)
    print(f"\nSpot actual: {spot:,.0f}")
    print(f"GEX total (con gamma reportada por Deribit): {gex_total:,.2f}")
    print(f"Régimen actual: {'LONG GAMMA (amortiguador)' if gex_total > 0 else 'SHORT GAMMA (amplificador)'}")

    por_strike.to_csv(OUT_DIR / "gex_por_strike.csv", index=False)
    print(f"GEX por strike guardado en data/gex_por_strike.csv")

    # ---- Muros de gamma (Checklist 1 del roadmap) ----
    muros = calcular_muros(df)
    muros["calls_top"].reset_index().rename(columns={"gex_abs": "gex_calls"}).to_csv(
        OUT_DIR / "muros_calls.csv", index=False
    )
    muros["puts_top"].reset_index().rename(columns={"gex_abs": "gex_puts"}).to_csv(
        OUT_DIR / "muros_puts.csv", index=False
    )

    print(f"\n--- Muros de gamma ---")
    print(f"Call wall (mayor concentración): {muros['call_wall']:,.0f}  (GEX ~{muros['call_wall_valor']:,.0f})")
    print(f"Put wall (mayor concentración): {muros['put_wall']:,.0f}  (GEX ~{muros['put_wall_valor']:,.0f})")
    if muros["resistencia_cercana"] is not None:
        print(f"Resistencia más cercana por encima del spot: {muros['resistencia_cercana']:,.0f}")
    else:
        print("Sin call wall relevante por encima del spot en esta muestra")
    if muros["soporte_cercano"] is not None:
        print(f"Soporte más cercano por debajo del spot: {muros['soporte_cercano']:,.0f}")
    else:
        print("Sin put wall relevante por debajo del spot en esta muestra")
    print("(convención: call wall = resistencia esperada, put wall = soporte esperado,")
    print(" bajo el mismo supuesto de siempre sobre de qué lado están los dealers)")
    print("Top strikes guardados en data/muros_calls.csv y data/muros_puts.csv")

    # ---- Capa base: Delta y Vega neto (Paso 0.5 del roadmap) ----
    dex_btc_total, dex_usd_total, dex_por_strike = calcular_dex(df)
    vega_total, vega_por_vencimiento = calcular_vega_neta(df)

    dex_por_strike.to_csv(OUT_DIR / "dex_por_strike.csv", index=False)
    vega_por_vencimiento.to_csv(OUT_DIR / "vega_por_vencimiento.csv", index=False)

    print(f"\n--- Capa base: Delta / Vega neto ---")
    print(f"DEX neto: {dex_btc_total:,.2f} BTC  (~ {dex_usd_total:,.0f} USD nocional)")
    sesgo_dex = "LARGO" if dex_btc_total > 0 else "CORTO"
    sesgo_dealer = "CORTOS" if dex_btc_total > 0 else "LARGOS"
    print(f"  -> tenedores de opciones netos {sesgo_dex} delta")
    print(f"  -> bajo el supuesto estándar, dealers netos {sesgo_dealer} delta")
    print(f"  -> eso implica que los dealers sostienen una cobertura {'LARGA' if dex_btc_total > 0 else 'CORTA'} en spot/futuros de ~{abs(dex_btc_total):,.0f} BTC para quedar delta-neutral")

    print(f"\nVega neta total: {vega_total:,.2f}")
    print("  -> tamaño de exposición a cambios de IV (sin histórico previo todavía no podemos decir si es 'alta' o 'baja' en términos relativos)")
    print("  -> se va guardando en data/historico_snapshots.csv para poder comparar corrida a corrida")

    # ---- Charm (Checklist 1/2/3 del roadmap) ----
    charmex_total, charm_por_vencimiento = calcular_charm(df)
    charm_por_vencimiento.to_csv(OUT_DIR / "charm_por_vencimiento.csv", index=False)

    print(f"\n--- Charm (decaimiento de delta por paso del tiempo) ---")
    print(f"CharmEX total: {charmex_total:,.2f} BTC/día")
    print("Por vencimiento (los más próximos son los que más pesan):")
    for _, fila in charm_por_vencimiento.head(5).iterrows():
        print(f"  {fila['fecha_vencimiento']}: {fila['charmex_btc_dia']:,.2f} BTC/día")
    print("charm_por_vencimiento.csv guardado en data/")

    # ---- Vanna (Checklist 2/4 del roadmap) ----
    vannaex_total, vanna_por_vencimiento = calcular_vanna(df)
    vanna_por_vencimiento.to_csv(OUT_DIR / "vanna_por_vencimiento.csv", index=False)

    print(f"\n--- Vanna (sensibilidad del delta a cambios de IV) ---")
    print(f"VannaEX total: {vannaex_total:,.2f} BTC por cada punto de IV")
    if vannaex_total > 0:
        print("  -> si la IV SUBE (típico en caídas fuertes), los dealers necesitarían AMPLIAR cobertura (comprar)")
        print("  -> si la IV BAJA (vol crush, típico post-evento sin sorpresa), necesitarían REDUCIRLA (vender)")
    else:
        print("  -> si la IV SUBE (típico en caídas fuertes), los dealers necesitarían REDUCIR cobertura (vender)")
        print("  -> si la IV BAJA (vol crush, típico post-evento sin sorpresa), necesitarían AMPLIARLA (comprar)")
    print("vanna_por_vencimiento.csv guardado en data/")

    # ---- Volga (Checklist 4 del roadmap, shocks de IV grandes) ----
    volgaex_total, volgaex_magnitud, volga_por_vencimiento = calcular_volga(df)
    volga_por_vencimiento.to_csv(OUT_DIR / "volga_por_vencimiento.csv", index=False)

    print(f"\n--- Volga (no-linealidad de la vega ante shocks de IV) ---")
    print(f"VolgaEX neto: {volgaex_total:,.2f} | magnitud bruta (sin cancelaciones): {volgaex_magnitud:,.2f}")
    if vega_total != 0:
        ratio_convexidad = volgaex_magnitud / abs(vega_total)
        print(f"Ratio Volga/Vega: {ratio_convexidad:.2f}")
        if ratio_convexidad > 1:
            print("  -> convexidad alta: ante un shock de IV grande, la vega puede cambiar más que")
            print("     proporcionalmente. Más riesgo de que el hedging se acelere en vez de ajustarse suave.")
        else:
            print("  -> convexidad moderada/baja: la vega se comporta de forma más lineal ante cambios de IV")
    print("volga_por_vencimiento.csv guardado en data/")

    print("\nCalculando perfil de GEX en distintos niveles de spot (esto tarda un poco)...")
    perfil = calcular_perfil_gex(df, rango_pct=args.rango_pct)
    perfil.to_csv(OUT_DIR / "perfil_gex.csv", index=False)

    flip = encontrar_flip_point(perfil)
    if flip:
        print(f"\nFlip point estimado: {flip:,.0f}")
        if spot > flip:
            print("  -> spot está ARRIBA del flip point: régimen long gamma mientras se mantenga así")
        else:
            print("  -> spot está ABAJO del flip point: régimen short gamma mientras se mantenga así")
    else:
        print("\nNo se encontró flip point dentro del rango simulado (probá con --rango-pct más grande)")

    print("\nPerfil completo guardado en data/perfil_gex.csv")

    # ---- Score de confluencia (3 ejes) — se calcula ANTES de guardar el snapshot ----
    score = calcular_score_confluencia(
        gex_total, dex_btc_total, vega_total, charmex_total,
        vannaex_total, volgaex_magnitud, charm_por_vencimiento,
    )

    guardar_snapshot_historico(
        spot, gex_total, dex_btc_total, dex_usd_total, vega_total,
        charmex_total, vannaex_total, volgaex_magnitud, flip,
        score, muros,
    )
    print("Snapshot agregado a data/historico_snapshots.csv (para comparar contra corridas futuras)")

    # ---- Combinación GEX + DEX, como pide el roadmap ----
    print("\n--- Lectura combinada GEX + DEX (Paso 0.5 del roadmap) ---")
    umbral_dex_relevante = 0.05 * df["open_interest"].sum()  # heurística simple, se refina con histórico
    dex_sesgado = abs(dex_btc_total) > umbral_dex_relevante
    lado_sesgo = "ALCISTA" if dex_btc_total > 0 else "BAJISTA"

    if gex_total > 0 and not dex_sesgado:
        print("GEX positivo + DEX ~neutral -> rango contenido 'limpio', sin sesgo de fondo")
    elif gex_total > 0 and dex_sesgado:
        print(f"GEX positivo + DEX sesgado {lado_sesgo} -> el rango se sostiene por ahora, pero hay una")
        print(f"   cobertura direccional grande de los dealers acumulada (ver 'DEX neto' arriba).")
        print(f"   ADVERTENCIA sobre la lectura: no asumas un único sentido para esto. Si el régimen de gamma")
        print(f"   se rompe, el efecto depende de si esa cobertura se REFUERZA o se DESARMA con el movimiento:")
        print(f"   - si el precio rompe a favor del sesgo, los dealers podrían necesitar ampliar más esa")
        print(f"     cobertura (flujo que empuja en la misma dirección)")
        print(f"   - si el precio rompe en contra del sesgo, esa misma cobertura podría empezar a deshacerse")
        print(f"     (flujo que empuja en la dirección OPUESTA al sesgo)")
        print(f"   Tratalo como 'hay una posición grande de dealers para vigilar', no como una predicción de dirección.")
    elif gex_total <= 0 and dex_sesgado:
        print(f"GEX negativo + DEX sesgado {lado_sesgo} -> régimen ya amplificador de por sí, y además hay una")
        print(f"   cobertura direccional grande de dealers en juego. Mismo matiz que arriba: el sentido en que")
        print(f"   esa cobertura suma o resta al movimiento depende de si se refuerza o se desarma, no es automático.")
    else:
        print("GEX negativo + DEX ~neutral -> movimiento amplificado pero sin sesgo direccional claro de fondo")

    # ---- Combinación Gamma + Charm (Checklist 1/2 del roadmap) ----
    print("\n--- Lectura combinada Gamma + Charm ---")
    vencimientos_proximos = charm_por_vencimiento.head(2)
    hay_venc_inminente = not vencimientos_proximos.empty

    if hay_venc_inminente:
        charm_cercano = vencimientos_proximos["charmex_btc_dia"].abs().sum()
        print(f"Hay vencimientos en las próximas 24-48h con CharmEX combinado de ~{charm_cercano:,.0f} BTC/día")
        if gex_total > 0:
            print("  -> régimen long gamma + charm relevante cerca de vencimiento: mayor probabilidad de")
            print("     'pinning' del precio hacia los strikes de mayor OI a medida que se acerca el cierre")
        else:
            print("  -> régimen short gamma + charm relevante cerca de vencimiento: el efecto de pinning es")
            print("     menos confiable, el mercado ya viene amplificando movimientos por su cuenta")
    else:
        print("Sin datos de vencimientos próximos en esta muestra (subí --max-instrumentos para cubrir más)")

    # ---- Score de confluencia (impresión; ya se calculó arriba) ----
    print("\n--- Score de confluencia (3 ejes, no un único número) ---")
    print(f"Eje 1 — Régimen: {score['tipo_regimen']}")
    print(f"  Confianza: {score['confianza_regimen']:.0f}/100 (basada en percentil histórico de |GEX| y CharmEX cercano)")

    print(f"\nEje 2 — Sesgo direccional: {score['sesgo_score']:+.0f} (rango -100 bajista a +100 alcista)")
    print(f"  DEX y Vanna {'CONFIRMAN' if score['sesgo_alineado'] else 'NO confirman'} el mismo sentido")

    print(f"\nEje 3 — Riesgo de aceleración (Volga/Vega): percentil {score['riesgo_aceleracion']:.0f}/100")
    print(f"  (ratio actual: {score['ratio_volga_vega']:.2f})")

    print("\nNota: con pocas corridas acumuladas los percentiles todavía son aproximados —")
    print("se vuelven más confiables a medida que 'data/historico_snapshots.csv' crece.")
    print("Esto sigue siendo información descriptiva para armar reglas de trading, no una señal en sí misma.")


if __name__ == "__main__":
    main()
