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
    if (walk["version"] != final["version"] or walk["model_sha256"] != final["model_sha256"]
            or tuning["released_model_unchanged"] != walk["version"]
            or tuning["training_corpus_sha256"] != walk["training_sha256"]
            or tuning["final_2026_opened_by_experiment"] is not False
            or ranking["corpus_sha256"] != walk["training_sha256"]
            or ranking["scope"] != "retrospective_validation_only_T-30"
            or ranking["promoted_to_production"] is not False
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
        "eda": study["eda"],
        "leakage": _read(root, "leakage"),
    }


if __name__ == "__main__":
    for target, data in ((ANALYSIS_JSON, analysis_data()), (RECENT_JSON, _read(ROOT, "recent-podiums"))):
        (ROOT / "site" / target).write_text(
            json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n",
            encoding="utf-8", newline="\n",
        )
        print(target)
