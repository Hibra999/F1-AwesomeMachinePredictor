"""Build the public, static analysis document from tracked aggregate reports."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_JSON = "data/analysis.json"
RECENT_JSON = "data/recent-podiums.json"
HORIZONS = ("fp1", "pre_race", "race")


def _read(root, name):
    return json.loads((root / "reports" / f"{name}.json").read_text(encoding="utf-8"))


def analysis_data(root=ROOT):
    walk = _read(root, "walk-forward")
    final = _read(root, "final-2026")
    tuning = _read(root, "rustuna-t30")
    study = _read(root, "analysis-inspection")
    ranking = _read(root, "rank-study")
    drivers = _read(root, "driver-performance")
    if (walk["version"] != final["version"] or walk["model_sha256"] != final["model_sha256"]
            or tuning["released_model_unchanged"] != walk["version"]
            or tuning["training_corpus_sha256"] != walk["training_sha256"]
            or tuning["final_2026_opened_by_experiment"] is not False
            or ranking["corpus_sha256"] != walk["training_sha256"]
            or ranking["scope"] != "retrospective_validation_only_T-30"
            or ranking["promoted_to_production"] is not False
            or drivers["corpus_sha256"] != walk["training_sha256"] or drivers["clock_audit"] != ranking["clock_audit"]
            or drivers["scope"] != ranking["scope"] or drivers["promoted_to_production"] is not False
            or study["eda"]["period"]["end_year"] != 2025):
        raise ValueError("analysis mixes incompatible seals or includes final data in tuning/EDA")

    evaluation = {"walk_forward": {}, "final": {}}
    for h in HORIZONS:
        selection = walk["horizons"][h]["selected"]
        if selection != final["horizons"][h]["selected"]:
            raise ValueError(f"the final report changed the selection for {h}")
        for period, source, scores in (("walk_forward", walk, "validation"), ("final", final, "metrics")):
            block = source["horizons"][h]
            evaluation[period][h] = {"selected": selection, "metrics": block[scores], "coverage": block["coverage"]}
        block = walk["horizons"][h]
        coverage = study["eda"]["horizons"][h]["importance_coverage"]
        if any(coverage[k] != block["coverage"][k] for k in ("gps", "cuts")):
            raise ValueError(f"importance is not scored on the walk-forward cuts for {h}")
        evaluation["walk_forward"][h].update(
            folds=[{k: fold[k] for k in ("start", "inner_oos_gps", "test_gps")} for fold in block["folds"]],
            group_ablation=block["ablation_delta_brier_when_removed"],
        )
        evaluation["final"][h]["recipe"] = block["recipes_for_final"][selection]

    cycles = study["rustuna_cycles"]
    trials = sum(len(s["trials"]) for cycle in cycles for s in cycle["studies"].values())
    if (trials != tuning["search"]["trials_total"] or len(cycles) != tuning["search"]["outer_windows_including_calibration_and_final_refit"]):
        raise ValueError("Rustuna trial log does not match the aggregate report")
    return {
        "schema": 1,
        "model": {k: walk[k] for k in ("version", "fit_utc", "train_max_time_exclusive", "wall_seconds", "model_seconds")},
        "evaluation": evaluation,
        "rustuna": {"summary": tuning, "cycles": cycles, "importance": study["rustuna_importance"]},
        "ranking": {
            "metrics": {name: {k: values[k] for k in ("gps", "brier", "winner_log_loss")} for name, values in ranking["metrics"].items()},
            "brier_contrasts": {key: ranking["contrasts"][key]["brier"] for key in ("rank_pl_minus_elo", "rank_pl_taf_minus_rank_pl")},
            "taf": {**{key: ranking["taf"][key] for key in ("usable", "wet")},
                    "any_wet_train": any(fold["train_taf"]["wet"] for fold in ranking["folds"])},
            "corrected_clocks": sum(v["status"] == "corrected" for v in ranking["clock_audit"].values()),
            "decision": ranking["decision_primary_only"],
        },
        "driver_performance": {k: drivers[k] for k in ("metrics", "coverage", "contrasts", "decision")},
        "eda": study["eda"],
        "leakage": _read(root, "leakage"),
    }


def print_diagnosis():
    report = analysis_data()
    labels = {"fp1": "Pre-FP1", "pre_race": "T-30", "race": "En carrera"}
    names = {"reference": "Referencia", "elo": "Elo", "lightgbm": "LightGBM", "ngboost": "NGBoost",
             "catboost": "CatBoost", "average": "Promedio", "learned": "Mezcla"}
    print(f"Modelo {report['model']['version']}. Brier de podio: menor es mejor. Cada GP pesa igual.")
    print(f"{'Período':<11} {'Corte':<10} {'GP':>2}", *(f"{n:>10}" for n in names.values()), " Elegido en validación")
    for period, blocks in report["evaluation"].items():
        period_name = "WF 2024-25" if period == "walk_forward" else "Final 2026"
        for h, block in blocks.items():
            print(f"{period_name:<11} {labels[h]:<10} {block['coverage']['gps']:2}",
                  *(f"{block['metrics'][k]['brier']:10.5f}" for k in names), " " + names[block["selected"]])
    print("T-30 sellado: dos horarios nominales incorrectos en 2024. No usar el período final para elegir.")
    corrected = report["ranking"]["metrics"]
    print(f"T-30 corregido, {corrected['elo']['gps']} GP: Elo {corrected['elo']['brier']:.5f} / "
          f"referencia {corrected['reference']['brier']:.5f}. No se recalcularon los tres clasificadores en ese reloj.")
    print("En el final, para el modelo elegido: log loss ganador (menor es mejor); podio exacto:")
    for h, block in report["evaluation"]["final"].items():
        chosen, ref = block["metrics"][block["selected"]], block["metrics"]["reference"]
        print(f"  {labels[h]:<10} {chosen['winner_log_loss']:.3f} vs {ref['winner_log_loss']:.3f}; "
              f"{chosen['ordered_hit']:.1%} vs {ref['ordered_hit']:.1%} (referencia)")
    print("Variable con mayor pérdida de Brier al ocultarla en LightGBM (validación, no efecto causal):")
    for h, block in report["eda"]["horizons"].items():
        best = max((v for v in block["features"] if v["oos_delta_brier"] is not None),
                   key=lambda v: v["oos_delta_brier"])
        print(f"  {labels[h]:<10} {best['name']}: {best['oos_delta_brier']:+.5f}; evidencia en {best['oos_evidence_gps']} GP")
    study = report["driver_performance"]
    coverage, metrics = study["coverage"], study["metrics"]
    print(f"Puesto final T-30: MAE {metrics['reference']['mae']:.3f} referencia; {metrics['base']['mae']:.3f} "
          f"CatBoost base; {metrics['extended']['mae']:.3f} con compañero y tandas FP1. "
          f"{coverage['matched']}/{coverage['predicted']} previstos con etiqueta. Decisión: {study['decision']}.")
    print("Próximas pruebas: coste por sanción entre clasificación y parrilla FIA; ritmo de tanda por compuesto y edad; "
          "riesgo de abandono por equipo basado en salidas previas. Exigir fuente fechada y mejora por GP.")


if __name__ == "__main__":
    for target, data in ((ANALYSIS_JSON, analysis_data()), (RECENT_JSON, _read(ROOT, "recent-podiums"))):
        (ROOT / "site" / target).write_text(
            json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n",
            encoding="utf-8", newline="\n",
        )
        print(target)
