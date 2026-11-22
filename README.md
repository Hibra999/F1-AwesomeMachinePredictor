# Podio probable

[Panel público](https://hibra999.github.io/F1-AwesomeMachinePredictor/) · [Modelo `pl-5d7f9a659617`](https://github.com/Hibra999/F1-AwesomeMachinePredictor/releases/tag/pl-5d7f9a659617) · [Auditorías upstream](AUDIT.md) · [Protocolo sellado](PROTOCOL.md)

Probabilidades de los tres puestos de la clasificación oficial del GP principal. Cada candidato estima una fuerza por piloto y la convierte en una distribución Plackett–Luce. La mezcla combina **distribuciones de ternas**, no rankings ni probabilidades marginales calibradas por separado. Se enumeran exactamente las `n × (n−1) × (n−2)` ternas. Cada puesto suma 100%, P(podio) es la suma de los tres puestos, y el total de P(podio) es 300%. La terna visible maximiza la probabilidad conjunta. Tolerancia de publicación por posición: `1e-12` absoluta, antes de redondear en el panel.

## Entrega

- **Catar 2026:** [22 pilotos, cuatro probabilidades y corte UTC](site/data/upcoming.json). Corte congelado `2026-11-22 04:22:54.481 UTC`, antes de FP1; el roster es el último fechado de clasificación en Las Vegas, provisional para Catar. Incluye a Rafael Câmara en Haas, no a Esteban Ocon. MAP: Verstappen → Antonelli → Norris, **0,9322%** conjunto. [Horario del GP](https://www.formula1.com/en/latest/article/formula-1-qatar-airways-qatar-grand-prix-2026.3XJyoYSnpJEcuZovstTdcG): FP1 **27 nov 13:30 UTC**; T−30 **29 nov 15:30 UTC**; carrera **29 nov 16:00 UTC**. El corte T−30 no ha ocurrido; arrancar el comando armado que se indica abajo.
- **São Paulo 2026:** repetición de 71 vueltas con dos cortes previos reconstruidos. En vuelta 36 el prefijo contiene 38 cortes y coincide exacto con el prefijo de la repetición completa. No se añade ninguna predicción o punto de evolución posterior a la pausa.
- **Las Vegas 2026:** SignalR Core anónimo comprobado durante `SessionStatus=Started`. El PC publicó al Gist [feed.json](https://gist.github.com/Hibra999/6e6438ae11bbbc1c9514f6b46810b0ce) a partir de la vuelta **13**, corte `2026-11-22 04:25:18.327 UTC`, retardo del paquete **1,54 s**. [Despliegue Pages verificado](https://github.com/Hibra999/F1-AwesomeMachinePredictor/actions/runs/44985011513): el navegador móvil (390 px) recibió y mostró la vuelta 14, con 22 pilotos. Al desconectar la red en la prueba del panel se conservaron corte y tabla y apareció «Datos anteriores» con su edad. Gist se lee sin cuenta cada 65 s; el emisor necesita un PC conectado y encendido.

## Resultados

Brier de podio promedio por piloto/corte y después por GP (menor es mejor). Modelo elegido solo con 2024–25, con 2023 para OOS interno y 2022 de arranque. Período final fijo: 20 GP de 2026, carreras 1–20. No se cambia el ganador tras abrirlo.

| Horizonte | Elegido | GP WF | Brier WF / ref. | GP final | Brier final / ref. | Log loss ganador final / ref. | ECE podio final | Ganador / podio ordenado final |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| Pre-FP1 | Promedio LGBM/NGB/Cat | 48 | 0,08341 / 0,08592 | 20 | 0,08772 / 0,09237 | 1,900 / 1,985 | 4,08% | 25,0% / 0,0% |
| T−30 | Elo + señal pre-carrera | 48 | 0,06764 / 0,07141 | 20 | 0,06729 / 0,07020 | 1,156 / 1,116 | 2,43% | 65,0% / 10,0% |
| Vueltas | Promedio LGBM/NGB/Cat | 40 | 0,04542 / 0,04878 | 20 | 0,04426 / 0,05034 | 1,082 / 1,169 | 2,16% | 67,2% / 14,5% |

El Elo final T−30 pierde en log loss de ganador. El ensamble en carrera pierde en acierto de podio ordenado frente a posiciones (14,5% vs 21,7% en el período final), aunque gana en Brier. El ensamble aprendido se calibró con pesos en el simplex y resultados internos OOS; no se eligió en ningún horizonte. Capacidad y temperatura ajustadas dentro de las ventanas; imputación NGBoost fit solo en train. Cada GP de test conserva todos sus pilotos y vueltas; train de carrera usa vueltas 1, 2 y múltiplos de 5 con ponderación por GP. Los resultados sin versión FIA suficiente se usan **solo como rótulos**, con embargo operativo de 14 días; no como variables, ni se afirma que ese embargo pruebe la historia de apelaciones.

| Tramo | Cortes WF | Brier WF | Cortes final | Brier final |
|---|---:|---:|---:|---:|
| Inicial | 807 | 0,05340 | 413 | 0,05297 |
| Medio | 798 | 0,04866 | 409 | 0,04956 |
| Final | 782 | 0,03382 | 399 | 0,02974 |

[Walk-forward completo](reports/walk-forward.json) y [final](reports/final-2026.json): métricas de los tres estimadores, dos mezclas, Elo y referencia sobre las mismas carreras, métricas por segmento, fiabilidad en diez bins de ganador/podio, tamaños y cobertura. `feature_gps` indexa las columnas de `FEATURES` en `f1forecast/engine.py`; `roster_exact_cuts` indica coincidencia retrospectiva del conjunto. Cuando un piloto oficial no estaba en el pronóstico cuenta como P=0 para el scoring. En T−30 hubo parrilla FIA fechada en solo **2/48** GP WF, clasificación temporizada en 37 y solo forma en 9; período final **0/20** parrilla fechada, 20 clasificación. No equivalen a una comparación completa contra parrilla oficial. 12/92 carreras de entrenamiento no pasaron cobertura de archivo; 40/48 de evaluación WF y 20/20 finales cubren carrera. En selección, quitar forma del piloto aumentó Brier FP1 **0,00729**, quitar clasificación aumentó T−30 **0,01409** y quitar posición aumentó carrera **0,00689**. El reporte incluye todas las ablaciones con soporte.

- **Fuga:** [prueba automática](reports/leakage.json), São Paulo 2026, con semilla 55293: retirar y corromper los eventos posteriores a pre-FP1, T−30 y vuelta 36 mantuvo todas las ternas idénticas (`Δ máx.=0`, `atol=1e-12`). Verifica el reducer y modelo sellado; no demuestra que el proveedor archivado preservó la primera llegada de cada paquete.
- **Cambios de posición:** [estudio](reports/position-gain.json) con 40 GP/24 circuitos retenidos y ajustes cronológicos, AUC 0,737; Brier 0,09753 vs 0,10384 del prior. La señal no mejoró Brier de podio (0,05517 con/sin); se excluyó. El proxy no identifica adelantamientos en pista.
- **Rendimiento:** Windows x64, Python 3.12.13, RTX 5070 12 GB, driver 621.01. Fit y selección 54,49 s CPU; preparar corpus + fit 90,83 s en esta máquina. Microbenchmark CatBoost 30 000 × 23, 140 árboles: CPU 0,753 s, CUDA 11,240 s (incluye inicialización). LightGBM CPU 0,149 s; rueda Windows sin CUDA Tree Learner. NGBoost usa scikit-learn CPU. `uv.lock` fija versiones (LGBM 4.7.0, NGB 0.5.11, CatBoost 1.2.10, FastF1 3.9.2). No hubo beneficio medido para usar CUDA ni un cuello de botella que justificara base de datos externa o reescritura.

## Fuentes y disponibilidad

| Grupo | Fuente y hora admitida |
|---|---|
| Calendario, circuito y roster | Jolpica y horario oficial. Roster: lista FIA fechada si existe; en otro caso último roster observado y pilotos con evidencia de la sesión actual. Identidad por código, no asiento. |
| Forma, Elo, ritmo anterior | Solo carreras de cronometraje anteriores cuyo último registro es anterior al corte. Fuerza relativa dentro del campo, regresión interanual; no interpreta Elo como habilidad causal sin coche. |
| Práctica y clasificación | TracingInsights/FastF1: reloj UTC `t0_date + sesT`, vuelta previa al corte; Q exige sectores ordenados. Ritmo del intervalo reconstruido, no mejor tiempo oficial con sanciones retrospectivas. |
| Parrilla | PDF FIA fechado en hora local convertido a UTC; no se usa `grid` de resultado final. Falta evidencia → referencia de Q o forma, declarada por GP. |
| Sprint | Orden provisional observado al `Finalised`, antes de usarlo para el GP principal. |
| Posición, ritmo, paradas, degradación | En replay, orden por cruces de meta ya registrados, no la tabla final ni la vuelta futura de los rezagados. En vivo, `TimingData` y reloj de recepción; usa clasificación completa observada cuando es válida. Pit en su timestamp, pendiente de ritmo tras paradas como proxy. Compuesto/edad reales no reconstruidos. |
| Meteorología, banderas y sanciones | WeatherData y TrackStatus al momento de medición; mensajes sancionadores inequívocos solo desde el anuncio. No se entrenó con meteorología observada pre-carrera como si fuera previsión. Sin histórico verificable de emisión pre-corte, el pronóstico meteorológico se excluye de la evaluación T−30. Sin historial de cuotas con hora verificable; no se usaron. |
| Clasificación objetivo | Jolpica, contrastada con clasificación FIA fechada cuando está disponible; resultados separados del reducer. |

- [FastF1](https://docs.fastf1.dev/api_reference/livetiming.html) cubre cronometraje histórico moderno, pero la prueba directa del archivo de 2025 sin cuenta devolvió 401; [documentación de acceso](https://docs.fastf1.dev/api_reference/accounts_auth.html) indica suscripción para la mayoría de los datos. `--no-auth` del CLI instalado falló pasando `None` a `signalrcore`; una suscripción anónima legítima al hub documentado, sin opción de token, recibió señal real. Captura de 74 s en Las Vegas: 804 mensajes, retraso observado 0,66–2,29 s por reloj de paquete incluido Heartbeat. No se garantiza que el subconjunto sin cuenta se mantenga, ni que funcione en todas las sesiones. El repositorio no solicita cuenta ni pago.
- [OpenF1](https://openf1.org/): histórico nominal desde 2023 sin clave para uso personal (3 req/s, 30/min), alrededor de 3 s de retraso anunciado para el servicio en vivo de pago. En la sesión activa, `position?session_key=latest` **y** una consulta a laps de Abu Dhabi 2025 devolvieron 401: el servidor restringe acceso global al histórico durante la sesión. Acceso positivo fuera de esa ventana no medido en esta ejecución; no se usa OpenF1 en directo.
- Archivo histórico **sí verificado sin cuenta**: [TracingInsights](https://github.com/TracingInsights/2026), SHA por temporada en `archive.py`. Las exportaciones corrigen relojes al procesar sesiones, no preservan latencia exacta. El backtest de carrera es causal **sobre ese reloj reconstruido**, no evidencia de lo que un operador habría recibido en el milisegundo original. Se excluyen `pos`, lap time oficial corregido, borrados, edad de neumático no demostrada y vueltas sintéticas.
- [Términos Jolpica](https://github.com/jolpica/jolpica-f1/blob/main/TERMS.md): no comercial, CC BY-NC-SA 4.0. [Derechos Formula 1](https://www.formula1.com/en/information/guidelines.4EOKE9RRqevL4niTK9kWyt): no se reproducen bloques de tiempos, resultados, telemetría ni imágenes oficiales. Solo código, pesos, métricas estadísticas y probabilidades derivadas. `.local/` y credenciales de `gh` quedan fuera del repositorio. No se afirma permiso comercial ni afiliación.

## Windows · PowerShell

```powershell
git clone https://github.com/Hibra999/F1-AwesomeMachinePredictor.git
Set-Location F1-AwesomeMachinePredictor
uv sync --locked --python 3.12
uv run --locked python -m f1forecast prepare
uv run --locked python -m f1forecast train
uv run --locked python -m f1forecast prepare --period final
uv run --locked python -m f1forecast evaluate
uv run --locked python -m f1forecast leakage --gp 2026-20
uv run --locked python -m f1forecast.gain_study
```

`--work .local/repro` **antes del subcomando** permite repetir sin sobrescribir la selección congelada. Archivo de pesos sellado: SHA-256 `abba0fabe47c2f66404b57a40a22cd7f2f58251bd91aa766fba50368ba260dd3`. `train` rehúsa abrir 2026, `prepare --period final` requiere selección sellada y `evaluate` verifica SHA. Para usar los pesos publicados sin reentrenar, ejecutar `prepare`, `gh release download pl-5d7f9a659617 --pattern model.joblib --dir .local`, `Copy-Item reports/walk-forward.json .local/selection.json`, luego `prepare --period final` para disponer del historial reciente.

```powershell
# Consultar reloj del GP o congelar pre-FP1 de otro evento antes de Libres 1.
uv run --locked python -m f1forecast schedule --gp 2026-22
uv run --locked python -m f1forecast forecast --gp 2026-23 --horizon fp1 --out .local/abu-dabi.json

# Generar replay para el panel o materializar solo su prefijo hasta la vuelta 36.
uv run --locked python -m f1forecast replay --gp 2026-20 --out site/data/sao-paulo-2026.json
uv run --locked python -m f1forecast replay --gp 2026-20 --through-lap 36 --out .local/replay-stop.json
uv run --locked python -m http.server 8070 --directory site
```

Abrir [http://localhost:8070/](http://localhost:8070/) y elegir **Archivo · São Paulo**. Los datos servidos son pronósticos generados, no resultados de vueltas. Para publicar un replay en tu Gist, `gist-init --file .local/replay-stop.json` crea uno nuevo; configura `feed_url` en `site/data/index.json`. `replay --gist <id> --interval 2` emite los cortes sucesivamente. Si no hay señal, no se mezcla una repetición con el directo: seleccionar el archivo identificado.

```powershell
# Autenticación GitHub únicamente para publicar, no para recibir datos F1.
gh auth status
$gist = '6e6438ae11bbbc1c9514f6b46810b0ce'
# Armar antes de 29 nov 15:30 UTC. Mantener Windows despierto.
uv run --locked python -m f1forecast forecast --gp 2026-22 --horizon pre_race --wait --seed-feed site/data/upcoming.json --out .local/qatar-frozen.json --gist $gist
# Ejecutar al abrirse la sesión Race, unos minutos antes de las 16:00 UTC.
uv run --locked python -m f1forecast watch --gp 2026-22 --seed-feed .local/qatar-frozen.json --out .local/qatar-live.json --gist $gist
```

Los cortes reales no se regeneran con la etiqueta «publicado» después de su ventana. `--events` permite adjuntar NDJSON de un proveedor autorizado antes del corte: `at` (epoch UTC de recepción/emisión comprobada), `source`, `kind`, y campos del contrato de `RaceState` (`forecast_weather` con `air`/`rain_p`, `qualifying` con `driver`/`seconds`, `sprint` con `driver`/`position`, por ejemplo). Al iniciar el `watch` tarde, no se inventan los cierres que ya pasaron. Si no hay un cierre válido, no reemplaza el Gist. Detener/reanudar la captura usando el mismo `--out` conserva cortes publicados y no duplica vueltas. GitHub Pages sirve `site/` por `.github/workflows/deploy.yml`; la escritura del feed no espera un nuevo despliegue por vuelta.
