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
    penalties = _read(root, "grid-penalties")
    if (walk["version"] != final["version"] or walk["model_sha256"] != final["model_sha256"]
            or tuning["released_model_unchanged"] != walk["version"]
            or tuning["training_corpus_sha256"] != walk["training_sha256"]
            or tuning["final_2026_opened_by_experiment"] is not False
            or ranking["corpus_sha256"] != walk["training_sha256"]
            or ranking["scope"] != "retrospective_validation_only_T-30"
            or ranking["promoted_to_production"] is not False
            or drivers["corpus_sha256"] != walk["training_sha256"] or drivers["clock_audit"] != ranking["clock_audit"]
            or drivers["scope"] != ranking["scope"] or drivers["promoted_to_production"] is not False
            or penalties["corpus_sha256"] != walk["training_sha256"] or penalties["scope"] != ranking["scope"]
            or penalties["clock_audit"] != ranking["clock_audit"]
            or penalties["metrics"]["elo"]["gps"] != ranking["metrics"]["elo"]["gps"]
            or abs(penalties["metrics"]["elo"]["brier"] - ranking["metrics"]["elo"]["brier"]) > 1e-12
            or penalties["promoted_to_production"] is not False
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
        "grid_penalties": {
            "coverage": {k: penalties["coverage"][k] for k in (
                "gps", "witness_gps", "decision_gps", "affected_gps", "affected_drivers", "places_lost",
                "pit_excluded", "predicted_drivers", "rows_changed", "changed_gps", "fia_reviewed_gps", "fia_partial_gps")},
            "metrics": {arm: {k: penalties["metrics"][arm][k] for k in (
                "gps", "brier", "winner_log_loss", "winner_hit", "ordered_hit", "map_members_hit")}
                for arm in ("elo", "sanctions")},
            "contrasts": {metric: {k: penalties["contrasts"][metric][k] for k in ("delta", "ci95")}
                          for metric in ("brier", "winner_log_loss")},
            "finish_mae": {k: penalties["sanctioned_finish_mae"][k] for k in ("gps", "matched_drivers")} |
                          {"mae": {a: penalties["sanctioned_finish_mae"]["mae"][a] for a in ("elo", "sanctions")}},
            "decision": penalties["decision"],
        },
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
    penalties = report["grid_penalties"]
    c = penalties["coverage"]
    print(f"Sanciones T−30, validación pareada 2024–25: {c['gps']} GP, incluidos los que carecen de evidencia. "
          f"Posición numerada en {c['witness_gps']} GP; sanción FIA en {c['decision_gps']}. "
          f"Revisión disciplinaria: {c['fia_reviewed_gps']} GP completos y {c['fia_partial_gps']} parciales.")
    print(f"  {c['places_lost']} puestos netos perdidos (no nominales) por {c['affected_drivers']} pilotos-GP "
          f"en {c['affected_gps']} GP; {c['pit_excluded']} órdenes de salir desde boxes excluidas. "
          f"Ajuste en {c['changed_gps']} GP, {c['rows_changed']}/{c['predicted_drivers']} pilotos-GP. Sin prueba no se ajusta.")
    for key, label in (("brier", "Brier podio"), ("winner_log_loss", "Log loss ganador")):
        contrast = penalties["contrasts"][key]
        lo, hi = contrast["ci95"]
        print(f"  {label}: Elo {penalties['metrics']['elo'][key]:.5f}; con sanciones "
              f"{penalties['metrics']['sanctions'][key]:.5f}; Δ {contrast['delta']:+.5f} (IC 95 % {lo:+.5f}, {hi:+.5f}).")
    finish = penalties["finish_mae"]
    if finish["gps"]:
        print(f"  Error de puesto medio en los sancionados con ajuste y etiqueta: {finish['mae']['elo']:.2f} Elo "
              f"y {finish['mae']['sanctions']:.2f} con sanciones ({finish['matched_drivers']} pilotos-GP; {finish['gps']} GP).")
    print(f"  {penalties['decision']} Sin selección con 2026 ni cambios en producción.")
    print("Próxima prueba: ritmo de tanda por compuesto y edad del neumático. Exigir fuente fechada y mejora por GP.")


if __name__ == "__main__":
    for target, data in ((ANALYSIS_JSON, analysis_data()), (RECENT_JSON, _read(ROOT, "recent-podiums"))):
        (ROOT / "site" / target).write_text(
            json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n",
            encoding="utf-8", newline="\n",
        )
        print(target)
