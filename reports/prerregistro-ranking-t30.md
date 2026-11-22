# Contraste prospectivo de diseño: ranking T−30

Este plan se registra **antes de calcular los resultados del nuevo experimento**, no antes de las carreras. Las carreras de desarrollo ya se estudiaron con otros modelos. Es otra comparación retrospectiva, no otro período final ciego. No se abre el corpus final de 2026, no se consultan los resultados de Las Vegas ni se modifica el [protocolo del modelo publicado](../PROTOCOL.md).

## Pregunta y candidatos fijos

¿Ayuda aprender directamente la probabilidad de los tres puestos de la carrera, en vez de sumar a mano Elo y la referencia T−30? Se comparan cuatro políticas en los mismos GP:

- **Controles:** referencia causal del proyecto (parrilla fechada, si no clasificación conocida, si no forma) y Elo publicado para ese horizonte. Sus temperaturas usan solo predicciones cronológicas anteriores con etiqueta disponible.
- **Contraste principal:** Plackett–Luce truncado al podio, en la línea de [Henderson y Kirrane](https://doi.org/10.1214/17-BA1048). Siete predictores: referencia al corte, Elo de piloto y constructor, forma de ambos, distancia al mejor tiempo en clasificación y en práctica. Mediana y escala ajustadas únicamente con entrenamiento. Una fuerza por piloto y enumeración de ternas sin repetición. Temperatura 1. Regularización ridge fija: media de pérdida por etapa observada, luego por GP, más `0.05/2 × ||β||²`. Si falta un clasificado, la verosimilitud usa solo el prefijo conocido; **no** se excluye ese GP del scoring.
- **Contraste secundario:** mismo ranking, más una interacción entre la referencia y un aviso de lluvia en el pronóstico aeronáutico TAF vigente a la salida. [Iowa Environmental Mesonet](https://mesonet.agron.iastate.edu/request/taf.php): Austin–KAUS, Miami–KFLL y Las Vegas–KLAS, fijados sin resultados. Solo boletines emitidos y difundidos como máximo en T−30; sin observaciones posteriores. Sin cobertura y tiempo seco no añaden interacción; se cuentan por separado. El pronóstico es del aeropuerto, no una medición en pista. Este contraste estima el aporte del dato, no da una segunda oportunidad de seleccionar un ganador.

Se imputa la mediana del entrenamiento (cero si falta la columna entera) y se escala con su desviación estándar poblacional tras imputar (uno si es constante). La interacción multiplica la referencia estandarizada por el indicador de lluvia y no se reescala.

## Comparación y decisión

Solo `validation-data.joblib`: 2022 para arranque, 2023 para predicciones cronológicas internas; cuatro semestres de 2024–25 como pruebas externas. Todos los pilotos de un GP permanecen juntos. Etiqueta y carrera de entrenamiento deben preceder tanto al límite del semestre como al primer corte evaluado; los rótulos sin versión respetan los 14 días del protocolo. Ningún ajuste de reglas, fuentes o hiperparámetros a partir de los resultados. Los GP sin TAF siguen en la comparación.

Métrica principal: Brier de podio con igual peso por GP. Se publican además log loss de ganador, acierto de ganador y podio exacto, cobertura y resultados por ventana. Intervalo percentil del 95 % para la diferencia pareada de Brier: 10 000 remuestreos de GP, semilla 55293. No representa un ensayo prospectivo ni corrige dependencia entre carreras próximas.

**Regla:** solo el candidato principal puede pasar a evaluación en sombra. Debe tener límite superior del intervalo de ΔBrier frente a Elo estrictamente inferior a cero y log loss de ganador no mayor. En otro caso se conserva Elo. Incluso si cumple, no se reescriben cortes emitidos ni se sustituye públicamente el modelo sin una comparación en carreras futuras aún no observadas.

La regla exige los 48 GP previstos. Δ es candidato menos comparador. Se contrastan también ambos rankings con la referencia y el ranking con TAF frente al mismo ranking sin TAF.
