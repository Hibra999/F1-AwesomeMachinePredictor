# Podio probable

[Panel público](https://hibra999.github.io/F1-AwesomeMachinePredictor/) · [Laboratorio de pronóstico](https://hibra999.github.io/F1-AwesomeMachinePredictor/analysis.html) · [Modelo `pl-5d7f9a659617`](https://github.com/Hibra999/F1-AwesomeMachinePredictor/releases/tag/pl-5d7f9a659617) · [Auditorías upstream](AUDIT.md) · [Protocolo sellado](PROTOCOL.md)

Probabilidades de los tres puestos de la clasificación oficial del GP principal. Cada candidato estima una fuerza por piloto y la convierte en una distribución Plackett–Luce. La mezcla combina **distribuciones de ternas**, no rankings ni probabilidades marginales calibradas por separado. Se enumeran exactamente las `n × (n−1) × (n−2)` ternas. Cada puesto suma 100%, P(podio) es la suma de los tres puestos, y el total de P(podio) es 300%. La terna visible maximiza la probabilidad conjunta. Tolerancia de publicación por posición: `1e-12` absoluta, antes de redondear en el panel.

## Entrega

- **Catar 2026:** [22 pilotos, cuatro probabilidades y corte UTC](site/data/upcoming.json). Corte congelado `2026-11-22 04:22:54.481 UTC`, antes de FP1; el roster es el último fechado de clasificación en Las Vegas, provisional para Catar. Incluye a Rafael Câmara en Haas, no a Esteban Ocon. MAP: Verstappen → Antonelli → Norris, **0,9322%** conjunto. [Horario del GP](https://www.formula1.com/en/latest/article/formula-1-qatar-airways-qatar-grand-prix-2026.3XJyoYSnpJEcuZovstTdcG): FP1 **27 nov 13:30 UTC**; T−30 **29 nov 15:30 UTC**; carrera **29 nov 16:00 UTC**. El corte T−30 no ha ocurrido; arrancar el comando armado que se indica abajo.
- **GitHub Pages:** muestra la última previa publicada, el análisis agregado y los [dos últimos podios](https://hibra999.github.io/F1-AwesomeMachinePredictor/#resultados). Compara la terna T−30 puesto por puesto; distingue una emisión real de un backtest, una clasificación provisional de una final y un acierto exacto de un piloto correcto en otro puesto. No sirve cronometraje, posiciones ni archivos de carrera.
- **São Paulo 2026:** repetición local con 71 vueltas y dos previas reconstruidas. Muestra los pilotos ubicables, el progreso y las salidas de SC y VSC hasta cada corte. El prefijo hasta la vuelta 36 contiene 38 cortes; no usa los eventos posteriores.
- **Las Vegas 2026:** es la carrera que abre el panel local. Conserva 25 cortes publicados durante el GP, hasta la vuelta 50/50 (`2026-11-22 05:31:27 UTC`). Indica **Directo finalizado**: no simula actividad ni rellena las vueltas sin señal. La captura incluida solo contiene probabilidades propias; no contiene paquetes crudos, GPS ni la clasificación oficial.
  La [clasificación final FIA, documento 65](https://www.fia.com/system/files/decision-document/2026_las_vegas_grand_prix_-_final_race_classification.pdf) se publicó a las **08:20 UTC**: Verstappen, Norris, Piastri. La previa T−30 **reconstruida** acertó el ganador y dos integrantes, solo uno en su puesto. De los 25 cortes **emitidos** se evaluaron 24 (vueltas 13–49, con huecos): ganador **24/24**, podio exacto **0/24**, Brier **0,09463**, log loss **0,85453**. El corte 50 ya contenía el desenlace. Los 24 cortes no son 24 GP independientes.

## Resultados

Brier de podio promedio por piloto/corte y después por GP (menor es mejor). Modelo elegido solo con 2024–25, con 2023 para OOS interno y 2022 de arranque. Período final fijo: 20 GP de 2026, carreras 1–20. No se cambia el ganador tras abrirlo.

El [laboratorio](https://hibra999.github.io/F1-AwesomeMachinePredictor/analysis.html) tiene filtros por período, horizonte y métrica. Incluye calibración, cobertura de la referencia y tercios de carrera. El panel local sirve la misma vista y el mismo JSON en [127.0.0.1:8070/analysis.html](http://127.0.0.1:8070/analysis.html).

| Horizonte | Elegido | GP WF | Brier WF / ref. | GP final | Brier final / ref. | Log loss ganador final / ref. | ECE podio final | Ganador / podio ordenado final |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| Pre-FP1 | Promedio LGBM/NGB/Cat | 48 | 0,08341 / 0,08592 | 20 | 0,08772 / 0,09237 | 1,900 / 1,985 | 4,08% | 25,0% / 0,0% |
| T−30 | Elo + señal pre-carrera | 48 | 0,06764 / 0,07141 | 20 | 0,06729 / 0,07020 | 1,156 / 1,116 | 2,43% | 65,0% / 10,0% |
| Vueltas | Promedio LGBM/NGB/Cat | 40 | 0,04542 / 0,04878 | 20 | 0,04426 / 0,05034 | 1,082 / 1,169 | 2,16% | 67,2% / 14,5% |

Estos son los números del informe sellado, con sus horarios originales. Dos GP de 2024 no tenían un T−30 válido en ese reloj. La [comparación posterior con horarios corregidos](reports/rank-study.json) no reescribe el informe ni la selección publicada.

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

**En consola:** `uv run --locked python -m f1forecast diagnostico` compara LightGBM, NGBoost, CatBoost, Elo y las mezclas en el walk-forward y en las 20 carreras finales. Imprime el Brier de cada candidato, el log loss y el podio exacto frente a la referencia, la cobertura y las variables más útiles al enmascararlas fuera de muestra. Lee los informes versionados: no necesita descargar pesos ni reentrenar.

### Ajuste T−30 con Rustuna

[Rustuna 0.3.1](https://github.com/optuna/rustuna) ejecutó TPE con semilla 55293: ocho ensayos por familia y ventana, 168 en total. Cada estudio usa dos bloques cronológicos internos por GP completo, con embargo de etiquetas. Las temperaturas y mezclas de evaluación se ajustan con OOS previo. El experimento no leyó 2026 ni cambió el modelo desplegado.

| Familia | Política original | Mejor preset fijo observado | Rustuna |
|---|---:|---:|---:|
| LightGBM | 0,07217 | 0,07127 | 0,07681 |
| NGBoost | 0,06970 | 0,06820 | 0,06991 |
| CatBoost | 0,06915 | 0,06783 | 0,07127 |

Brier de podio en los mismos 48 GP de 2024–25; menor es mejor. Se conserva **Elo (0,06764)**. La búsqueda tomó 39,09 s y el entrenamiento completo 112,51 s en Windows CPU con rueda nativa Rustuna. [Informe y huellas del experimento](reports/rustuna-t30.json). El laboratorio muestra los 168 ensayos, sus parámetros, tiempos y mejores valores por ventana.

### Contraste T−30 con relojes verificados

**Se conserva Elo.** En la [comparación corregida](reports/rank-study.json), los mismos 48 GP de 2024–25 dieron:

| Método | Brier de podio ↓ | Log loss ganador ↓ | Ganador | Podio en orden |
|---|---:|---:|---:|---:|
| Referencia disponible al corte | 0,06983 | 1,359 | 24/48 | 5/48 |
| Elo y referencia | **0,06606** | **1,272** | **26/48** | 6/48 |
| Ranking Plackett–Luce aprendido | 0,06808 | 1,372 | 24/48 | 7/48 |
| Ranking + previsión TAF | 0,06808 | 1,372 | 24/48 | 7/48 |

El ranking acertó un podio exacto más que Elo, pero perdió en ambas métricas de probabilidad. ΔBrier frente a Elo: **+0,00202** (IC 95 % pareado por GP: **−0,00148 a +0,00560**). No pasó el [criterio registrado antes del ensayo](reports/prerregistro-ranking-t30.md). Se contrastaron **92 horarios** con revisiones anteriores al corte: São Paulo 2024 adelantó la salida 90 minutos; Las Vegas 2024 correspondía al día siguiente en UTC. Los cuatro métodos usan los mismos relojes corregidos y excluyen la sesión Race del GP objetivo. [Enmienda publicada antes de puntuar](reports/enmienda-reloj-t30.md). Un evento de salida ya observado impide emitir una previa T−30.

El piloto meteorológico recuperó 11 boletines emitidos para tres aeropuertos de EE. UU.; seis cubrieron carreras de validación. Solo uno mencionaba lluvia a la salida y ninguno apareció en el entrenamiento de esas ventanas. No se pudo estimar el efecto de la lluvia; la puntuación prácticamente idéntica no demuestra que el clima sea irrelevante. No se reentrenó el modelo publicado ni se usaron resultados de 2026 para este contraste.

[Gráfico e intervalos](https://hibra999.github.io/F1-AwesomeMachinePredictor/analysis.html#ranking) · [Cuatro artículos, cuatro repositorios y auditoría de datos FIA/NOAA](reports/metodos-y-datos.md). La siguiente comparación con fundamento es separar ritmo de clasificación y coste de salir retrasado usando parrillas con revisión y fecha de publicación.

Para reproducir desde la raíz después de `prepare` (2022–25):

```powershell
git clone https://github.com/theOehrly/f1schedule.git .local/f1schedule
uv run --locked python -m f1forecast.rank_study --work .local
uv run --locked python -m f1forecast.rank_study --work .local --offline
```

El primer comando se ejecuta solo si falta el clon. El estudio fija la historia del calendario hasta `e50468712d66daaaf8d637551798c8ce70891e82`. La primera ejecución descarga los boletines; la segunda usa la caché. Un archivo meteorológico ausente o dañado no cuenta como tiempo seco: la puntuación se detiene. El informe incluye fechas de emisión y disponibilidad, SHA de calendarios y huellas de boletines; los datos crudos quedan en `.local/`.

### Rendimiento por piloto: puesto medio, no solo podio

`uv run --locked python -m f1forecast rendimiento` muestra el **puesto medio** de cada piloto entre los inscritos de la última previa publicada. Por componente Plackett–Luce, suma la probabilidad de que cada rival termine delante y añade uno; después pondera los puestos medios con los pesos originales del ensamble. No es el orden de las tres mayores probabilidades de podio ni una clasificación oficial. Antes de mostrarlo, reproduce todas las marginales y el MAP publicados con tolerancia absoluta `1e-12`; si falta el modelo, la alineación o un evento original, no sustituye la previa. Usa el modelo sellado y el historial local de aquel corte. `--feed` permite elegir otra previa; `--events` admite sus eventos originales. El cálculo posterior no se presenta como un nuevo corte emitido entonces. Los puestos por debajo del tercero son una extensión del modelo, no una calibración independiente del orden completo.

**¿Mejora un modelo entrenado directamente para el puesto?** [Prerregistro](reports/prerregistro-rendimiento-pilotos.md) · [Cuatro ventanas y cobertura](reports/driver-performance.json) · [Gráfico](https://hibra999.github.io/F1-AwesomeMachinePredictor/analysis.html#pilotos). Se entrenó CatBoost sobre el percentil oficial de llegada, sin identificadores, con el mismo peso por GP. Las dos señales nuevas fueron la diferencia frente al compañero en su última fase común de clasificación y el ritmo relativo en tandas continuas de FP1. En 48 GP de 2024–25 hubo etiqueta para 951 de los 960 pilotos previstos. Hubo evidencia de compañero para 40 y de tanda para 706; no se imputó información posterior.

| T−30 corregido | MAE del puesto medio ↓ | Brier de podio ↓ | Log loss del ganador ↓ |
|---|---:|---:|---:|
| Orden según la referencia disponible | 3,163 | — | — |
| CatBoost de puestos, base | 3,179 | 0,07119 | 1,568 |
| CatBoost de puestos + dos señales | 3,183 | 0,07400 | 1,611 |
| Elo, control probabilístico | — | 0,06606 | 1,272 |

ΔMAE con las dos señales frente a CatBoost base: **+0,005 puestos** (IC 95 % pareado por GP: −0,016 a +0,025). ΔBrier frente a Elo: **+0,00794** (IC: +0,00095 a +0,01474). No cumple la regla de mejora; queda como diagnóstico, no sustituye al modelo de podio. Un error medio de tres puestos no significa tres errores de podio. Próximos contrastes: coste de la penalización de parrilla con boletín FIA fechado; ritmos largos separados por compuesto y edad observados; riesgo de abandono del constructor según carreras anteriores. Ninguna de esas mejoras se da por demostrada.

```powershell
# Tras preparar el corpus 2022–25 y el clon f1schedule de arriba:
uv run --locked python -m f1forecast.driver_performance --work .local
uv run --locked python -m f1forecast.driver_performance --work .local --gp 2025-01
```

El segundo comando separa la previsión histórica por piloto de `evaluation_only`. Para probar CatBoost experimental en un GP futuro, ejecuta `uv run --locked python -m f1forecast.driver_performance --work .local --gp 2026-22` **a T−30**; no emite fuera de la ventana ni con una lista de inscritos de otro GP. Se puede añadir `--events entradas.jsonl` con `gp_id`, `at` UTC de recepción, `source`, `kind` y campos del evento. Su historial de ajuste y forma termina en 2025; los eventos conocidos del fin de semana pueden entrar en la instantánea. No escribe en Pages.

### Datos e importancia fuera de muestra

El [estudio agregado](reports/analysis-inspection.json) resume tamaños de alineación y faltantes del corpus 2022–25. Mide el cambio de Brier al enmascarar una variable fuera de muestra en 2024–25. Usa el LightGBM pequeño fijo en los tres horizontes y repite la medición para cada candidato Rustuna en T−30, con los mismos GP y temperaturas que su resultado original. La ablación por grupo es distinta: reentrena sin el grupo. Ninguna mide causalidad ni el peso de una variable en Elo.

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
- [Términos Jolpica](https://github.com/jolpica/jolpica-f1/blob/main/TERMS.md): no comercial, CC BY-NC-SA 4.0. [Derechos Formula 1](https://www.formula1.com/en/information/guidelines.4EOKE9RRqevL4niTK9kWyt): no se redistribuyen tablas completas, tiempos, coordenadas de autos ni imágenes oficiales. La retrospectiva cita los tres puestos comparados y enlaza la clasificación. `.local/` y credenciales de `gh` quedan fuera del repositorio. No se afirma permiso comercial ni afiliación.

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

### Repetir el ajuste sin tocar el modelo publicado

```powershell
uv run --locked python -m f1forecast --work .local/rustuna prepare
uv run --locked python -m f1forecast --work .local/rustuna train --trials 8
```

Usa un directorio limpio sin datos finales. `train` sin `--trials` reproduce la selección original.

Para regenerar la vista estática desde los informes versionados, basta `python -m f1forecast.analysis_data`. Para repetir además la inspección estadística medida en este PC, `uv run --locked python -m f1forecast.inspection` exige el corpus original y el sello Rustuna correspondiente en `.local/rustuna`. El flujo de Pages verifica que el JSON público coincida con los informes.

### Mesa de carrera local

```powershell
# Mesa local: última captura real y backtest opcional; no requiere pesos.
uv run --locked python -m f1forecast panel --port 8070

# Opcional: regenerar replay con el modelo sellado o materializar el prefijo causal.
uv run --locked python -m f1forecast replay --gp 2026-20 --out .local/replay.json
uv run --locked python -m f1forecast replay --gp 2026-20 --through-lap 36 --out .local/replay-stop.json
uv run --locked python -m f1forecast panel --port 8070 --replay .local/replay-stop.json
```

Abre [la carrera actual](http://127.0.0.1:8070/). La mesa lee `.local/live.json`; en una instalación limpia muestra la captura real incluida de Las Vegas. Si se crea el archivo de directo más tarde, el panel lo detecta sin reiniciarse. Con `--live` explícito la mesa espera ese archivo; no lo sustituye por otra carrera. **Reproducir** recorre solamente los cortes disponibles.

Para ver una reconstrucción, usa **Sesión que quieres ver → Backtest** o abre [el backtest de São Paulo](http://127.0.0.1:8070/#backtest). Una repetición completa abre en pausa a mitad de carrera y **Inicio** retrocede a la primera previa. Una repetición parcial abre en su último corte. En una captura real cerrada, **Último · ver cierre y evaluación** muestra el desenlace sin salir de la mesa. Retroceder, reproducir o cambiar a backtest oculta el resultado. La fecha del cierre de la captura y la publicación de la clasificación FIA son dos relojes distintos. El servidor escucha solo en este PC.

Elige un piloto en el mapa, la tabla o la gráfica. En las cuatro pistas, los puntos avanzan en el sentido de carrera sobre el eje de [OpenStreetMap, ODbL](AUDIT.md#trazados-usados-openstreetmap-odbl-10). Representan el progreso **estimado** por último cruce y ritmo observado, no GPS. Sin evidencia reciente no se extrapola otra vuelta. Mapa, pastel y tabla usan el mismo color por equipo. Sin WebGL funciona la vista 2D; la extrusión 3D no mide elevación.

**SC** cuenta despliegues físicos; **VSC**, períodos virtuales. Empezar a recibir señal tarde da un mínimo (`≥`); «Sin dato» no significa cero. Los cortes antiguos que no guardaron posiciones se conservan sin inventarlas. El pastel reparte un solo puesto entre pilotos (100 %). El cambio de P(podio) entre cortes se expresa en puntos porcentuales, no en puestos ganados.

Para Catar, inicia `watch` en una consola cuando se abra la sesión Race, unos minutos antes de la salida. Abre el panel en otra consola:

```powershell
uv run --locked python -m f1forecast watch --gp 2026-22 --seed-feed site/data/upcoming.json --out .local/qatar-live.json
uv run --locked python -m f1forecast panel --port 8070 --live .local/qatar-live.json
```

No necesitas credenciales de GitHub. El HTML consulta el archivo local cada 3 s y conserva la última revisión válida. No rellena vueltas perdidas al arrancar tarde. Reanudar con el mismo `--out` conserva los cortes previos. Plotly.js Basic 4.3.0 se sirve desde el proyecto bajo [licencia MIT](site/vendor/plotly.LICENSE.txt), sin CDN.

### Publicar la proyección previa

```powershell
uv run --locked python -m f1forecast schedule --gp 2026-22
# Armar antes del 29 nov 2026 a las 15:30 UTC y mantener Windows despierto.
uv run --locked python -m f1forecast forecast --gp 2026-22 --horizon pre_race --wait --out site/data/upcoming.json
# Opcional: refrescar sin esperar al sondeo, sin pesos ni corpus privados.
uv run --locked python -m f1forecast.recent_results --issued-only --refresh
python -m f1forecast.analysis_data
python -m f1forecast.pages
```

Haz commit y push del pronóstico después de congelarlo. Pages lo publica con `.github/workflows/deploy.yml`; el flujo rechaza archivos ajenos y cortes en directo o reconstruidos. La hora del corte y la emisión del modelo no son la hora de despliegue.

**Después de la bandera:** `watch` cierra al recibir el estado terminal, también si la carrera se acorta. No inventa vueltas en una reconexión ni reescribe los cortes. En el último corte, el navegador busca cambios de clasificación cada minuto. Si hace falta, el servidor consulta las fuentes oficiales en segundo plano, como máximo una vez cada cinco minutos por GP; conserva el último informe válido en `WORK/recent-podiums.json`. Solo la clasificación **final** habilita el Brier, el log loss y los aciertos de los cortes reales anteriores a la bandera. La vuelta del desenlace nunca puntúa. Un GP sin captura verificable no recibe ceros ficticios.

El flujo de Pages consulta los documentos **cada hora de sábado a lunes** y **a las 09:17 UTC de martes a viernes**, además del disparo manual. Solo guarda y despliega una comparación que haya cambiado. No necesita que Windows esté encendido para actualizar el resultado oficial. Si una captura nueva solo existe en el PC, el servidor de GitHub no puede evaluarla por sí solo. Para publicar **solo su agregado**, ejecuta `uv run --locked python -m f1forecast.recent_results --issued-only --refresh --live .local/qatar-live.json`, exporta con `python -m f1forecast.analysis_data`, verifica con `python -m f1forecast.pages` y sube ambos JSON de `recent-podiums`. No subas el cronometraje ni los paquetes recibidos.

Para otro GP congela primero en un archivo nuevo (`forecast --gp 2026-23 --horizon fp1 --out .local/abu-dabi.json`, antes de Libres 1); después sustituye `site/data/upcoming.json` por ese documento para publicarlo. No se vuelven a emitir cortes reales fuera de su ventana. `--events` acepta NDJSON de un proveedor autorizado: `at` en epoch UTC de recepción o emisión comprobada, `source`, `kind` y los campos correspondientes de `RaceState`.
