# Backtesting de opciones BTC — Deribit API pública

Datos directos de Deribit (sin necesidad de Derivatives Monkey ni ningún
intermediario). API pública, sin autenticación, sin costo.

## Instalación

```bash
pip install -r requirements.txt
```

## 1. Backfill histórico (para empezar a backtestear ya)

```bash
python3 backfill_historico.py --dias 7 --max-instrumentos 20
```

- `--dias`: cuántos días hacia atrás traer.
- `--max-instrumentos`: cuántos instrumentos de opciones bajar (subilo
  cuando quieras el dataset completo; con todos los strikes/vencimientos
  puede ser pesado, así que arrancá chico para probar).

**El script acumula, no sobreescribe**: cada corrida fusiona los datos
nuevos con lo que ya tenías guardado en `data/`, eliminando duplicados
automáticamente (por `trade_id` en trades, por `timestamp` en DVOL). Así
que podés correrlo todos los días sin perder lo que ya bajaste antes.

**¿Cuántos `--dias` pedir si lo corro todos los días?**
- Para la primera corrida (arrancar con una base): `--dias 30` o más,
  para tener historia real desde el principio.
- Para corridas diarias de mantenimiento después: `--dias 2` alcanza —
  le da 1 día completo de margen de superposición por si un día te
  olvidás de correrlo o falla, sin necesidad de volver a bajar todo el
  historial completo cada vez (los duplicados se descartan solos, pero
  no tiene sentido pedir de más si no hace falta).
- Si te salteás varios días seguidos sin correrlo, para esa corrida sí
  usá un `--dias` más grande (por ejemplo, si pasaron 5 días sin correrlo,
  `--dias 6` para no dejar un hueco).

Genera (y va acumulando en):
- `data/dvol_btc.parquet` — índice de volatilidad implícita histórico de BTC.
- `data/trades_opciones_btc.parquet` — trades históricos de opciones (precio,
  tamaño, IV implícita del trade, timestamp, instrumento).

## 2. Colector en vivo (para ir sumando datos hacia adelante)

```bash
python3 colector_en_vivo.py
```

Se conecta por WebSocket y va guardando trades y ticker cada 5 minutos en
`data/live/`. Dejalo corriendo en un servidor o screen/tmux si querés
acumular histórico propio en el tiempo. Ctrl+C corta limpio y guarda lo
que tenga en el buffer.

## Nota sobre el entorno donde generé esto

Probé el código en un sandbox con acceso a internet restringido a una
lista blanca de dominios (para paquetes de pip, GitHub, etc.), y
`deribit.com` no estaba en esa lista — por eso no pude ejecutar una
prueba en vivo desde acá. El código compila sin errores de sintaxis,
pero te recomiendo correr una primera prueba corta (`--dias 1
--max-instrumentos 3`) en tu propia máquina para confirmar que todo
responde como se espera antes de lanzar una descarga grande.

## 3. Calcular GEX y flip point

```bash
python3 calcular_gex.py --max-instrumentos 100
```

- `--max-instrumentos`: cuántos instrumentos de opciones bajar (subilo para
  cubrir más strikes/vencimientos; con `--max-instrumentos 926` cubrís todo
  el book actual de opciones BTC).
- `--rango-pct`: rango de precios hipotéticos a simular alrededor del spot
  actual para buscar el flip point (default 15%).

Qué hace:
1. Baja open interest, IV marcada y greeks actuales de cada instrumento
   (vía `public/ticker`).
2. Calcula el **GEX actual** (con la gamma reportada por Deribit al spot
   de hoy) y te dice si el régimen es long gamma o short gamma.
3. Recalcula la gamma de cada opción con Black-Scholes para una grilla de
   precios de spot hipotéticos (manteniendo fijo el OI y la IV marcada), y
   encuentra el **flip point**: el nivel de precio donde el GEX total
   cruza de negativo a positivo.

Genera:
- `data/gex_dataset_crudo.parquet` — OI, IV, greeks (gamma/delta/vega) por
  instrumento (para reusar sin tener que volver a pegarle a la API).
- `data/gex_por_strike.csv` — GEX actual desglosado por strike.
- `data/muros_calls.csv` / `data/muros_puts.csv` — top strikes con mayor
  concentración de GEX del lado call y put respectivamente (call wall /
  put wall, resistencia y soporte esperados).
- `data/perfil_gex.csv` — GEX total simulado en cada nivel de spot
  hipotético (para graficar el perfil completo).
- `data/dex_por_strike.csv` — Delta Exposure (DEX) neto desglosado por strike.
- `data/vega_por_vencimiento.csv` — vega neta agregada por fecha de vencimiento.
- `data/charm_por_vencimiento.csv` — Charm Exposure (decaimiento de delta
  por día) agregado por fecha de vencimiento. Los vencimientos más
  próximos son los que más pesan — relevante para el efecto de "pinning"
  cerca del cierre que describe el roadmap.
- `data/vanna_por_vencimiento.csv` — Vanna Exposure (cuánto cambia el
  delta agregado por cada punto de IV) por vencimiento. Relevante para
  entender qué pasaría con el hedging de dealers si la IV se dispara o
  se desinfla (vol crush post-evento).
- `data/volga_por_vencimiento.csv` — Volga Exposure (no-linealidad de la
  vega ante shocks de IV) por vencimiento. Con esto ya está completo el
  marco Gamma → Charm → Vanna → Volga del roadmap.
- `data/historico_snapshots.csv` — una fila por corrida con spot, GEX,
  flip point, DEX y vega totales. Se va acumulando corrida a corrida, así
  con el tiempo podés ver si el DEX/vega de hoy es alto o bajo *comparado
  con corridas anteriores* (el roadmap de gamma/charm/vanna que compartiste
  pide justamente esa referencia relativa, no un umbral fijo).

### Sobre DEX y Vega neta (capa base del framework)

Además del GEX, el script ahora calcula:
- **DEX (Delta Exposure) neto**: si los tenedores de opciones están netos
  largos o cortos delta. Se cruza automáticamente con el régimen de GEX
  para dar una lectura combinada (ej: "GEX positivo + DEX sesgado ->
  el rango se sostiene por ahora, pero hay combustible direccional
  acumulado").
- **Vega neta**: tamaño total de la exposición a cambios de IV. Por sí
  sola no dice dirección (eso lo da Vanna, que es el siguiente paso del
  roadmap) pero indica cuánto puede moverse el hedging de dealers ante
  un evento de volatilidad.

**Nota:** para el histórico (backtesting real de régimen día a día)
necesitás correr este script repetidamente en el tiempo, o adaptarlo para
usar snapshots históricos de OI si Deribit los expone — el ticker solo da
el estado *actual*, no el histórico. El archivo `historico_snapshots.csv`
es el punto de partida para ir armando esa serie de tiempo vos mismo.

### Score de confluencia (3 ejes)

En vez de forzar las 5 griegas en un solo número, el script las traduce a
3 ejes independientes que miden cosas distintas:

- **Eje 1 — Régimen** (rango/reversión vs tendencia/expansión): basado en
  GEX y qué tan relevante es el Charm cercano a vencimiento, con un score
  de confianza 0-100 calculado contra el percentil histórico.
- **Eje 2 — Sesgo direccional** (-100 bajista a +100 alcista): basado en
  si DEX y Vanna apuntan al mismo lado o no.
- **Eje 3 — Riesgo de aceleración**: percentil histórico del ratio
  Volga/Vega, para saber si la exposición a volatilidad está en una zona
  más o menos convexa que de costumbre.

**Importante:** los percentiles dependen de `data/historico_snapshots.csv`
— con pocas corridas acumuladas son aproximados, se vuelven más
confiables cuantas más corridas (o más automatizadas) se acumulen. Esto
sigue siendo información para armar reglas de trading, no una señal de
entrada/salida en sí misma.

## 4. Automatizar corridas periódicas

```bash
python3 automatizar_corridas.py --intervalo-min 30 --max-instrumentos 100
```

Corre `calcular_gex.py` en loop cada N minutos, para que el histórico
(`data/historico_snapshots.csv`) se construya solo en vez de tener que
ejecutarlo a mano cada vez. Cada corrida se guarda además en
`logs/corrida_[timestamp].log`, con el output completo — útil para
revisar qué pasó en una corrida puntual (incluso si falló).

- `--intervalo-min`: minutos entre corridas (default 30).
- `--max-instrumentos` / `--rango-pct`: se pasan directo a `calcular_gex.py`.

Se corta con `Ctrl+C` — espera a que termine la corrida en curso antes
de salir, no la interrumpe a la mitad. Pensado para dejarlo corriendo en
una ventana de consola por horas o días, igual que `colector_en_vivo.py`.

Si una corrida individual falla (por ejemplo, un problema de red
puntual), el loop no se corta — queda registrado en el log y sigue con
la próxima corrida programada.

## 5. Forward-testing del score

```bash
python3 evaluar_forward_test.py --horizonte-horas 24 --umbral-pct 2.0
```

Como no hay histórico de OI hacia atrás (ver nota más abajo), no se
puede backtestear el GEX/DEX/Charm/Vanna/Volga contra el pasado. Este
script resuelve eso hacia **adelante**: cada vez que `calcular_gex.py`
corre, ahora también guarda el régimen y sesgo que predijo el score en
`data/historico_snapshots.csv`. Este script toma esas predicciones
pasadas y las compara contra lo que realmente pasó con el precio
`--horizonte-horas` después (usando corridas posteriores del propio
histórico, sin necesitar datos externos).

- `--horizonte-horas`: cuánto tiempo después de la señal mirar el
  resultado (default 24h).
- `--umbral-pct`: a partir de qué variación de precio se considera
  "ruptura" en vez de "rango sostenido" (default 2%).

Genera `data/forward_test_resultados.csv` con cada señal evaluada
(acierto/error) y un resumen de accuracy global y por tipo de régimen.

**Importante:** esto recién empieza a ser útil con varios días de
`automatizar_corridas.py` corriendo — con pocas señales evaluadas el
% de acierto es ruido, no una conclusión. Pensado para correr una vez
por día (o cada tanto) mientras el histórico se sigue acumulando.

## Sobre backtesting histórico real (limitación importante)

Todo lo que calcula `calcular_gex.py` (GEX, DEX, Charm, Vanna, Volga,
muros) sale del **open interest actual** vía `public/ticker`. Deribit no
expone snapshots históricos de OI por strike, así que no es posible
reconstruir "qué GEX había hace 3 meses" para testear una estrategia
basada en gamma contra el pasado — solo se puede ir acumulando hacia
adelante. El forward-testing de arriba es la forma de generar evidencia
real sin esa limitación (comparando predicción vs. resultado real, día
a día, desde ahora en adelante).

Lo que sí se puede backtestear con datos históricos reales (no hace
falta esperar): reglas técnicas puras sobre precio, y régimen de
volatilidad con el histórico de DVOL que ya se descarga en el backfill.

## Correr todo en la nube (24/7, sin depender de tu PC)

Ver `deploy/DEPLOY.md` — guía paso a paso para desplegar el colector en
vivo y la automatización de corridas en un VPS barato (~$4-6/mes), con
los archivos de `systemd` ya armados (`deploy/*.service`) para que los
servicios se reinicien solos si el servidor reinicia o si algo se cae.

## Backend API + Dashboard + Reporte Word

Además de los scripts de consola, el proyecto tiene una versión con
API (FastAPI), un dashboard visual (HTML/JS, sin build de Node) y un
generador de reportes ejecutivos en Word con gráficos.

### Instalación adicional

```bash
pip install -r api/requirements.txt --break-system-packages
```

El generador de reportes usa `docx-js` (Node) — si tenés Node instalado,
no hace falta instalar nada más (el paquete `docx` viene resuelto por
`npm` la primera vez que corras `node reporte/generar_docx.js`; si te
tira error de módulo no encontrado, corré `npm install docx` dentro de
la carpeta `reporte/`).

### 1. Levantar el backend

```bash
cd api
uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

Docs interactivas (Swagger) en `http://127.0.0.1:8000/docs`. Endpoints:

- `GET /health` — chequeo de que el servidor está vivo
- `GET /api/v1/price` — precio actual del índice BTC/USD de Deribit
- `GET /api/v1/gex?max_instrumentos=100` — análisis completo en JSON
  (mismo contenido que `calcular_gex.py`, pero como respuesta de API en
  vez de imprimirse por consola). Guarda snapshot en el histórico igual
  que la versión de consola.
- `GET /api/v1/download-report?max_instrumentos=100` — genera y
  descarga el reporte en Word al vuelo

### 2. Abrir el dashboard

Con el backend corriendo, abrí `dashboard/index.html` directamente en
el navegador (doble click, o `file://` en la barra de direcciones — no
hace falta servirlo, es un archivo HTML autocontenido). Se conecta solo
a `http://127.0.0.1:8000`.

Muestra: spot, régimen (badge de color), una tira visual con los
niveles de gamma (put wall, soporte, flip point, spot, resistencia,
call wall), tarjetas con DEX/Vega/Charm/Vanna/Volga, las 3 barras del
score de confluencia, y los gráficos de GEX por strike y perfil de GEX.
Tiene un botón para pedir más/menos instrumentos y otro para descargar
el reporte Word directamente.

### 3. Generar el reporte Word sin pasar por el dashboard

```bash
cd reporte
python3 generar_reporte.py --max-instrumentos 100
```

Genera `reporte/reporte_btc_gex.docx` con resumen ejecutivo (narrativa
automática), tabla de métricas clave, los mismos gráficos que el
dashboard, y las limitaciones metodológicas.

### Nota sobre rutas compartidas

Los tres puntos de entrada (`calcular_gex.py` por consola,
`automatizar_corridas.py`, y el backend vía `api/`) comparten la misma
carpeta `data/` en la raíz del proyecto, sin importar desde qué carpeta
se ejecuten — así que podés usar la consola y la API indistintamente y
todo se acumula en el mismo histórico.

## Siguientes pasos posibles

- Sumar Bybit y OKX en paralelo (mismo patrón, distintos endpoints/canales).
- Armar el motor de backtesting propiamente dicho (por ejemplo con
  `backtesting.py` o un loop propio) una vez tengas el dataset armado.
- Si vas a correr esto 24/7, conviene pasar el colector en vivo a un
  proceso con reconexión automática (por ahora corta si se cae el socket).
