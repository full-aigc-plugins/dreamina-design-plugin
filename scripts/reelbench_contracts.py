"""Semantic invariants for closed ReelBench evidence and comparison receipts."""
from __future__ import annotations

from pathlib import PurePosixPath
import math
from typing import Any, Mapping

from scripts.json_contracts import ContractValidationError, canonical_fingerprint, parse_rfc3339, validate_contract

REELBENCH_VALIDATE_GATES = (
    "timeline", "duration", "numbering", "size", "category", "camera", "transition",
    "frame-text", "dedup", "subjects", "category-evidence", "motion", "boundary", "frames", "rhythm",
)
_SYNC_VERIFY_GATES = frozenset({"duration", "dimensions", "codec", "audio_policy", "cut_alignment", "sampled_correspondence"})
_EARLY_ACTIONS = frozenset({"seed", "evidence", "render", "plan", "panels", "export"})
# Evidence schema 1.1 is tied to the reviewed upstream revision. These are its
# exact executable/report assets, independently of mutable receipt claims.
_PINNED_SHOTS_SCRIPTS = {
    "script/video-shots.mjs": ("c49963d11b34bf561355ed3896e76ee0aeda7c3c1433733ee2ff27f291bdcb22", 77449),
    "script/report.css": ("41e278aa9dca66ec12e30411e04411691151405ae779db1710c4c85a4835c52e", 33842),
    "script/report.js": ("94e888c99c0a08d62a62301b54fe9d6ae950bdfe1a9c80ea4e6ee94630ee5d23", 20988),
}


def validate_reelbench_evidence(receipt: Mapping[str, Any]) -> None:
    """Validate schema plus fingerprint, timestamp, path, and action-specific gate invariants."""
    validate_contract(receipt, "reelbench_evidence.schema.json")
    _assert_fingerprint(receipt, "evidence_fingerprint")
    parse_rfc3339(receipt["created_at"], label="created_at")
    parse_rfc3339(receipt["committed_at"], label="committed_at")
    for artifact in receipt["artifacts"]: _assert_relative_artifact_path(artifact["path"])
    gates = receipt["gates"]
    names = [gate["name"] for gate in gates]
    if len(names) != len(set(names)): raise ContractValidationError("ReelBench gate names must be unique")
    action = receipt["action"]
    if action != "seed" and receipt["parent_version"] is None:
        raise ContractValidationError("non-seed actions require exact parent version and fingerprint")
    if (receipt["parent_version"] is None) != (receipt["parent_fingerprint"] is None):
        raise ContractValidationError("parent version and fingerprint must be paired")
    number = int(receipt['version'][1:])
    parent_number = int(receipt['parent_version'][1:]) if receipt['parent_version'] is not None else 0
    if number < 1 or (receipt['parent_version'] is not None and parent_number < 1) or parent_number != number - 1:
        raise ContractValidationError('version must bind the immediately preceding parent; only initial seed has none')
    if receipt["parent_version"] is not None and int(receipt["parent_version"][1:]) >= int(receipt["version"][1:]):
        raise ContractValidationError("parent must precede child version")
    commands = receipt["commands"]
    expected_actions = ["frames", "sheet", "sheet"] if action == "evidence" else [action]
    if [command["action"] for command in commands] != expected_actions:
        raise ContractValidationError("ordered command list does not match action")
    if receipt["argv_fingerprint"] != canonical_fingerprint({"commands": commands}):
        raise ContractValidationError("command fingerprint mismatch")
    if len({item["kind"] for item in receipt["tool_identities"]}) != len(receipt["tool_identities"]):
        raise ContractValidationError("duplicate tool identities")
    for command in commands:
        if command["argv"][:3] != ["tools/node", "script/video-shots.mjs", command["action"]]:
            raise ContractValidationError("command must use exact workspace entrypoint")
        if command["tool_identities"] != receipt["tool_identities"]:
            raise ContractValidationError("command tool identity mismatch")
        if command["script_manifest"] != receipt["script_manifest"]:
            raise ContractValidationError("command script manifest mismatch")
        if command["returncode"] != 0 and action != "validate":
            raise ContractValidationError("failed command cannot publish successful artifacts")
    if action == "evidence":
        for command, pick in zip(commands[1:], ("a", "b")):
            args = command["argv"]
            if "--pick" not in args or args[args.index("--pick") + 1] != pick:
                raise ContractValidationError("ordered sheet picks must be a then b")
    paths = [item["path"] for item in receipt["artifacts"]]
    if len(paths) != len(set(paths)):
        raise ContractValidationError("duplicate artifact paths")
    consumed_paths = []
    for item in receipt["consumed_artifacts"]:
        _assert_relative_artifact_path(item["path"])
        _assert_relative_artifact_path(item["workspace_path"])
        consumed_paths.append(item["workspace_path"])
    if len(consumed_paths) != len(set(consumed_paths)):
        raise ContractValidationError("duplicate consumed input paths")
    sources = [item for item in receipt["consumed_artifacts"] if item["workspace_path"].startswith("source/")]
    if len(sources) != 1 or sources[0]["sha256"] != receipt["source_sha256"] or sources[0]["version"] != receipt["source_receipt_version"]:
        raise ContractValidationError("consumed source identity mismatch")
    if sources[0]["workspace_path"] != "source/" + receipt["source_sha256"] or not sources[0]["path"].startswith("source/"):
        raise ContractValidationError("consumed source path must bind the source digest")
    if action in {"seed", "evidence", "validate", "render"}:
        _assert_shots_commands(receipt)
    if action == "validate":
        if (commands[0]["returncode"] == 1) != any(gate["status"] == "FAIL" for gate in gates):
            raise ContractValidationError("validate command and gate verdicts contradict")
        if set(names) != set(REELBENCH_VALIDATE_GATES) or len(names) != len(REELBENCH_VALIDATE_GATES): raise ContractValidationError("validate action requires exactly all 15 upstream gates once")
        if any(gate["status"] == "SKIPPED" and gate["name"] not in {"subjects", "motion", "boundary", "frames"} for gate in gates):
            raise ContractValidationError("only upstream optional validation gates may be SKIPPED")
    elif action == "verify":
        if set(names) != _SYNC_VERIFY_GATES or len(names) != len(_SYNC_VERIFY_GATES): raise ContractValidationError("sync verify action requires exactly six gates")
        if any(gate["status"] == "SKIPPED" for gate in gates): raise ContractValidationError("sync verify gates may not be SKIPPED")
    elif action in _EARLY_ACTIONS and names:
        raise ContractValidationError("this ReelBench action must not claim validation gates")


def validate_reelbench_comparison(receipt: Mapping[str, Any]) -> None:
    """Validate schema plus comparison fingerprint and deterministic overall verdict semantics."""
    validate_contract(receipt, "reelbench_comparison.schema.json")
    _assert_fingerprint(receipt, "comparison_fingerprint")
    parse_rfc3339(receipt["compared_at"], label="compared_at")
    verdicts = {domain["verdict"] for domain in receipt["domains"].values()}
    required = "manual_review" if "manual_review" in verdicts else "matched"
    if receipt["overall"] != required: raise ContractValidationError("comparison overall must match every domain verdict")
    domain_order = {name: index for index, name in enumerate(
        ("source_identity", "duration", "timeline_continuity", "shot_count", "boundaries", "motion")
    )}
    mismatches = receipt["mismatches"]
    ordered = sorted(
        mismatches,
        key=lambda item: (domain_order[item["domain"]], item["shot_id"] or "", item["code"], item["expected"], item["observed"]),
    )
    if mismatches != ordered:
        raise ContractValidationError("comparison mismatches must use canonical stable ordering")
    if any(receipt["domains"][item["domain"]]["verdict"] != "manual_review" for item in mismatches):
        raise ContractValidationError("comparison mismatches require a manual-review domain")


def validate_reelbench_binding(receipt: Mapping[str, Any]) -> None:
    """Validate a closed corroboration sidecar and its canonical fingerprint."""
    validate_contract(receipt, "reelbench_binding.schema.json")
    _assert_fingerprint(receipt, "binding_fingerprint")


def _assert_fingerprint(receipt: Mapping[str, Any], key: str) -> None:
    core = {name: value for name, value in receipt.items() if name != key}
    if receipt[key] != canonical_fingerprint(core): raise ContractValidationError(f"{key} does not match the canonical receipt fingerprint")


def _assert_shots_commands(receipt):
    if {item["kind"] for item in receipt["tool_identities"]} != {"node", "ffmpeg", "ffprobe"}:
        raise ContractValidationError("shot actions require exact node, ffmpeg, and ffprobe identities")
    source = "source/" + receipt["source_sha256"]
    tool_paths = {"node": "tools/node", "ffmpeg": "tools/bin/ffmpeg", "ffprobe": "tools/bin/ffprobe"}
    if any(item["source_path"] != tool_paths[item["kind"]] or item["mode"] != 0o500 for item in receipt["tool_identities"]):
        raise ContractValidationError("tool identities must describe exact staged paths and mode")
    scripts = [item["path"] for item in receipt["script_manifest"]]
    if len(scripts) != 3 or set(scripts) != {"script/video-shots.mjs", "script/report.css", "script/report.js"}:
        raise ContractValidationError("complete pinned script and report asset manifest required")
    if any((item["sha256"], item["size_bytes"]) != _PINNED_SHOTS_SCRIPTS[item["path"]] for item in receipt["script_manifest"]):
        raise ContractValidationError("script manifest differs from the pinned upstream program")
    ids = receipt["shots"]["ids"]
    if ids != [f"S{index:02d}" for index in range(1, len(ids) + 1)]:
        raise ContractValidationError("shot ids must be the exact canonical numbered set")
    consumed = {entry["workspace_path"]: entry for entry in receipt["consumed_artifacts"]}
    required = {source}
    if receipt["action"] != "seed":
        required |= {"inputs/shots.json", "inputs/track.json"}
    if receipt["action"] in {"validate", "render"}:
        required |= {f"inputs/frames/{shot}{pick}.jpg" for shot in ids for pick in ("a", "b")}
    if set(consumed) != required:
        raise ContractValidationError("action must consume exactly its required source/documents/frames")
    if receipt["action"] == "seed":
        shots_artifact = next((a for a in receipt["artifacts"] if a["path"].endswith("/shots.json")), None)
    else:
        shots_artifact = consumed["inputs/shots.json"]
    if shots_artifact is None or shots_artifact["sha256"] != receipt["shots"]["sha256"]:
        raise ContractValidationError("parsed shot ids must bind the exact shots artifact digest")
    for command in receipt["commands"]:
        args = command["argv"][3:]
        action = command["action"]
        if action == "seed":
            if len(args) != 9 or args[0] != source or args[1] != "--threshold" or args[3:8] != ["--min", "0.30", "--track", "output/track.json", "--title"]:
                raise ContractValidationError("seed command is not canonical")
            try:
                threshold = float(args[2])
            except ValueError as exc:
                raise ContractValidationError("invalid seed threshold") from exc
            if not math.isfinite(threshold) or not 0.05 <= threshold <= 0.8 or not args[8] or len(args[8]) > 512 or "\0" in args[8]:
                raise ContractValidationError("invalid seed parameters")
        else:
            allowed = {
                "frames": [["inputs/shots.json", "--video", source, "--dir", "output/frames", "--width", "480"]],
                "sheet": [["inputs/shots.json", "--dir", "output/frames", "--out", "output/sheets", "--pick", pick, "--cols", "5", "--rows", "5"] for pick in ("a", "b")],
                "validate": [["inputs/shots.json", "--track", "inputs/track.json", "--frames", "inputs/frames", "--lang", "en"]],
                "render": [["inputs/shots.json", "--" + mode, "--track", "inputs/track.json", "--frames", "inputs/frames", "--video", source] for mode in ("md", "html")],
            }
            if args not in allowed.get(action, []):
                raise ContractValidationError("command paths or arguments are not canonical")
    for entry in receipt["consumed_artifacts"]:
        if entry["workspace_path"].startswith("source/"):
            continue
        prefix = f"reelbench/{entry['version']}/"
        if not entry["path"].startswith(prefix) or entry["workspace_path"] != "inputs/" + entry["path"][len(prefix):]:
            raise ContractValidationError("consumed artifact path and version mismatch")
        if int(entry["version"][1:]) >= int(receipt["version"][1:]):
            raise ContractValidationError("consumed artifact must precede child receipt")
        if entry["version"] == receipt["parent_version"] and entry["receipt_fingerprint"] != receipt["parent_fingerprint"]:
            raise ContractValidationError("consumed parent receipt fingerprint mismatch")
    paths = [entry["path"] for entry in receipt["artifacts"]]
    prefix = f"reelbench/{receipt['version']}/"
    if any(not path.startswith(prefix) for path in paths):
        raise ContractValidationError("produced artifact is outside its version")
    produced = {path[len(prefix):] for path in paths}
    action = receipt["action"]
    if action == "seed" and produced != {"shots.json", "track.json"}:
        raise ContractValidationError("seed requires complete shots and track artifacts")
    if action == "validate" and produced:
        raise ContractValidationError("validate must not claim generated artifacts")
    if action == "evidence":
        frames_a = {name[len("frames/"):-5] for name in produced if name.startswith("frames/") and name.endswith("a.jpg")}
        frames_b = {name[len("frames/"):-5] for name in produced if name.startswith("frames/") and name.endswith("b.jpg")}
        expected = {f"frames/{name}{pick}.jpg" for name in frames_a for pick in ("a", "b")}
        pages = math.ceil(len(frames_a) / 25)
        expected |= {f"sheets/sheet-{pick}{page:02d}.jpg" for pick in ("a", "b") for page in range(1, pages + 1)}
        if frames_a != set(ids) or frames_a != frames_b or produced != expected:
            raise ContractValidationError("evidence requires exact frame pairs and both sheet sets")
    if action == "render":
        mode = receipt["commands"][0]["argv"][4]
        if mode == "--md" and produced != {"report.md"}:
            raise ContractValidationError("Markdown report is not complete")
        if mode == "--html":
            consumed_frames = {entry["workspace_path"] for entry in receipt["consumed_artifacts"] if entry["workspace_path"].startswith("inputs/frames/")}
            if not consumed_frames or produced != {"report.html", *consumed_frames}:
                raise ContractValidationError("HTML report and its frames are not complete")


def _assert_relative_artifact_path(value: Any) -> None:
    if not isinstance(value, str) or not value or value.startswith("/") or "\\" in value or value.endswith("/"):
        raise ContractValidationError("artifact path must be canonical project-relative")
    path = PurePosixPath(value)
    if path.is_absolute() or str(path) != value or any(part in {"", ".", ".."} for part in path.parts):
        raise ContractValidationError("artifact path must be canonical project-relative")


__all__ = ["REELBENCH_VALIDATE_GATES", "validate_reelbench_binding", "validate_reelbench_comparison", "validate_reelbench_evidence"]
