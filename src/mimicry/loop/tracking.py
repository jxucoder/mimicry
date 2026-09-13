"""Explicit, optional sponsor adapters; no telemetry is enabled by default."""

import json


class WeaveSink:
    """Export decision lineage without exporting draft text or provider request bodies."""

    def __init__(self, project: str, *, client=None):
        if client is None:
            try:
                import weave
            except ImportError:
                raise RuntimeError("Install weave to enable optional trace export.") from None
            client = weave.init(project)
        self.client = client

    def __call__(self, event: dict):
        inputs = {k: v for k, v in event.items()
                  if k in ("owner_hash", "trace_id", "event_id", "run_id", "proposal_id",
                           "evaluator_id", "candidate_policy_id", "version", "target")}
        call = self.client.create_call(op="mimicry." + event["action"].lower(), inputs=inputs)
        output = {k: v for k, v in event.items()
                  if k in ("action", "status", "promoted", "accepted", "score", "eligible",
                           "reason", "selected", "partition", "to", "passed", "hard_passed",
                           "failed", "uncertain", "skipped_rules", "rule_ids")}
        self.client.finish_call(call, output=output)


def export_wandb(report: dict, policy: dict, *, project: str, directory,
                 entity=None, mode="offline", wandb_module=None) -> str:
    """Export preference hashes and outcomes; online export requires explicit mode."""
    if mode not in ("offline", "online", "disabled"):
        raise ValueError("Choose offline, online, or disabled tracking.")
    if wandb_module is None:
        try:
            import wandb as wandb_module
        except ImportError:
            raise RuntimeError("Install wandb to enable experiment export.") from None
    directory.mkdir(parents=True, exist_ok=True)
    model = {k: policy[k] for k in ("id", "parent_id", "model", "output_format",
                                   "version")}
    # Hashes provide version linkage; prompts and human feedback remain local.
    model["question_hashes"] = {k: q["semantic_hash"] for k, q in policy["questions"].items()}
    model["rule_ids"] = [r["id"] for r in policy["rules"]]
    path = directory / "preferences.json"
    path.write_text(json.dumps(model, indent=2, allow_nan=False))
    with wandb_module.init(project=project, entity=entity, mode=mode, dir=str(directory),
                           job_type="personal-preference-validation",
                           config={"policy_id": policy["id"], "report_id": report["id"]}) as run:
        values = {"promoted": int(report.get("promoted", False)),
                  "active_rules": len(policy["rules"]),
                  "supporting_edits": sum(bool(r.get("supports")) for r in report.get("rows", [])),
                  "contradicting_edits": sum(bool(r.get("contradicts"))
                                            for r in report.get("rows", []))}
        run.log(values)
        artifact = wandb_module.Artifact("mimicry-preferences", type="policy")
        artifact.add_file(str(path))
        run.log_artifact(artifact)
        return run.id
