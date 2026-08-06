#!/usr/bin/env python3
"""``chatbi-harness`` CLI: build / doctor / test-conformance (module 4, MR-C3).

Four-class operations per design §8.5; ``build`` only produces auditable
artifacts — ``deploy`` stays a separate authorized action and is NOT part of
this first CLI version (impl doc §10).

Usage:
    chatbi-harness build --target claude-code [--out dist/claude-code]
    chatbi-harness doctor --target claude-code
    chatbi-harness test-conformance --target claude-code [--scenario ID ...]

The ``agno`` target is implemented in module 5 (MR-D series): ``build``
produces the auditable runtime artifact + ``runtime-manifest.json``, ``doctor``
prints the capability manifest + fail-closed judgment, and
``test-conformance`` runs the 16 P0 scenarios on the Agno target (stubbed
runtime_stubs) and judges equivalence against the module-1 Golden Contract.
The agno subcommands require the installed agno package (deployment venv) and
fail explicitly when it is missing (FBK-003).

``build --target claude-code``:
  1. copies the audited target source ``.claude/**`` into the artifact dir;
  2. runs the fail-closed reconcile: per-file SHA-256 diff against the live
     tree (differences must be registered target extensions, MR-005) and the
     per-hook subprocess contract suite;
  3. writes ``harness-manifest.json`` with the full design-§13 field set
     including the supported matrix (rule 6) and the fail-closed judgment.

``doctor --target claude-code``:
  probe -> capability manifest -> supported / partial / unsupported sections
  + the IR-required fail-closed judgment with recovery actions (MR-005).
  Headless ``claude -p`` stays ``partial``; this CLI does NOT enable headless
  production authentication (design §9.1).

``test-conformance --target claude-code``:
  re-runs the module-1 Golden chains against the frozen expected outputs
  (``conformance/expected/*.json``) and writes a report; any P0 diff makes
  the command exit non-zero.

Applicable rules: HOOK-001, MR-005, FBK-003, PORT-001, SEC-003, invariant 5.
"""

from __future__ import annotations

import argparse
import difflib
import json
import shutil
import subprocess
import sys
from pathlib import Path

HARNESS_ROOT = Path(__file__).resolve().parents[1]
# sys.path entries: the harness root itself (so ``import runtimes.*``
# resolves AND the installed ``agno`` package is never shadowed by
# ``runtimes/agno`` — module-5 sys.path-hygiene rule) plus the packages
# container and the legacy .claude/lib (shim).
for _entry in (HARNESS_ROOT, HARNESS_ROOT / "packages", HARNESS_ROOT / ".claude/lib"):
    if _entry.is_dir() and str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))
CONFORMANCE_RUNNERS = HARNESS_ROOT / "conformance" / "runners"
if CONFORMANCE_RUNNERS.is_dir() and str(CONFORMANCE_RUNNERS) not in sys.path:
    sys.path.insert(0, str(CONFORMANCE_RUNNERS))

from chatbi_harness_ir import load_all  # noqa: E402
from chatbi_runtime_contract.capabilities import CapabilityStatus  # noqa: E402

from runtimes.claude_code.adapter import ClaudeCodeAdapter  # noqa: E402
from runtimes.claude_code.build_manifest import (  # noqa: E402
    MANIFEST_NAME,
    build_claude_manifest,
    required_union,
    write_manifest,
)
from runtimes.claude_code.reconcile import (  # noqa: E402
    check_hook_contracts,
    reconcile_build,
)

TARGET_CHOICES = ("claude-code", "agno")

DIST_ROOT = HARNESS_ROOT / "dist"
CLAUDE_DIST = DIST_ROOT / "claude-code"

#: Directories copied verbatim into the claude-code artifact tree.
_ARTIFACT_TREES = (
    "packages",
    "runtimes",
    "workflows",
    "prompts",
    "schemas",
    "fixtures",
)


def _copy_tree(source: Path, destination: Path) -> None:
    """Copy one tree, dropping derived/editor artifacts (__pycache__)."""
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", ".DS_Store"),
    )


def _probe_snapshot(harness_root: Path):
    """Best-effort local probe snapshot (None when claude is not available).

    The probe never upgrades a capability and never fails the build: a
    missing binary yields ``runtime_version=unknown`` and the §6.2 draft
    statuses stand (honest reporting, FBK-003).
    """
    try:
        from runtimes.claude_code.probe import probe_local_capabilities

        claude_executable = shutil.which("claude")
        return probe_local_capabilities(
            claude_executable=Path(claude_executable) if claude_executable else None
        )
    except Exception:
        return None


def _require_harness_target(target: str) -> None:
    """Refuse a target this CLI build cannot serve (explicit, not silent)."""
    if target not in TARGET_CHOICES:
        print(
            f"error: unknown target {target!r}; expected one of "
            f"{TARGET_CHOICES}.",
            file=sys.stderr,
        )
        raise SystemExit(2)


def _require_agno_runtime() -> None:
    """The agno subcommands need the installed agno package (deployment
    venv). Missing => explicit error (FBK-003), never a silent no-op."""
    try:
        import runtimes.agno  # noqa: F401  (unshadow guard)
        import agno  # noqa: F401  (the installed package)
    except ImportError:
        print(
            "error: the agno runtime is not importable in this interpreter; "
            "run the agno subcommands with the deployment venv python "
            "(agno-main/.venv/bin/python). Nothing was run (FBK-003).",
            file=sys.stderr,
        )
        raise SystemExit(2)


def _build_claude(out_dir: Path, args: argparse.Namespace) -> int:
    """Build the auditable claude-code artifact (design §8.5, MR-005)."""
    artifact_claude = out_dir / ".claude"
    source_claude = HARNESS_ROOT / ".claude"

    print(f"build claude-code -> {out_dir}")
    if artifact_claude.exists():
        shutil.rmtree(artifact_claude)
    _copy_tree(source_claude, artifact_claude)
    for tree in _ARTIFACT_TREES:
        destination = out_dir / tree
        if destination.exists():
            shutil.rmtree(destination)
        _copy_tree(HARNESS_ROOT / tree, destination)

    print("reconcile: per-file SHA-256 diff vs the live .claude tree ...")
    violations = reconcile_build(artifact_claude, source_claude)
    if violations:
        print("FAIL (MR-005): unregistered target-source differences:")
        for violation in violations:
            print(f"  - {violation}")
        print("Recovery: investigate the diff, or register the file as an "
              "explicit target extension in runtimes/claude_code/reconcile.py "
              "with a documented reason.")
        return 1

    print("reconcile: per-hook subprocess contracts ...")
    hook_violations = check_hook_contracts(
        HARNESS_ROOT / ".claude" / "hooks", cwd=HARNESS_ROOT
    )
    if hook_violations:
        print("FAIL (MR-005): hook contract violations:")
        for violation in hook_violations:
            print(f"  - {violation}")
        print("Recovery: the build artifact would lose a hook contract; "
              "investigate the hook and re-run.")
        return 1

    probe = _probe_snapshot(HARNESS_ROOT)
    manifest = build_claude_manifest(
        HARNESS_ROOT,
        probe_snapshot=probe,
        runtime_version=(
            getattr(probe, "claude_version", None) or "unknown"
            if probe is not None
            else "unknown"
        ),
    )
    manifest_path = write_manifest(out_dir, manifest)
    try:
        display_path = manifest_path.relative_to(HARNESS_ROOT)
    except ValueError:
        display_path = manifest_path
    print(f"manifest: {display_path}")
    print(f"manifest: harness_release={manifest['harness_release']} "
          f"kernel_version={manifest['kernel_version']} "
          f"runtime_version={manifest['runtime_version']}")
    verdict = manifest["supported_matrix"]["fail_closed"]["verdict"]
    missing = manifest["supported_matrix"]["fail_closed"]["missing_required"]
    print(f"manifest: fail-closed verdict = {verdict}")
    for item in missing:
        print(f"  - required-but-unsatisfied: {item} (recovery: see doctor "
              f"--target claude-code)")
    print("build ok: auditable artifact produced; deploy is a separate "
          "authorized action (design §8.5).")
    return 0


def _build_agno(out_dir: Path) -> int:
    """Build the auditable agno artifact: runtime + packages + workflows +
    prompts + a runtime-manifest.json (design §8.5, MR-001)."""
    from runtimes.agno.probe import ADAPTER_NAME, ADAPTER_VERSION, probe_agno

    print(f"build agno -> {out_dir}")
    if out_dir.exists():
        shutil.rmtree(out_dir)
    for tree in ("runtimes", "packages", "workflows", "prompts", "schemas"):
        destination = out_dir / tree
        if destination.exists():
            shutil.rmtree(destination)
        _copy_tree(HARNESS_ROOT / tree, destination)

    manifest = probe_agno()
    errors = manifest.validate()
    if errors:
        print("FAIL (MR-005): agno capability manifest is malformed:")
        for error in errors:
            print(f"  - {error}")
        return 1
    runtime_manifest = {
        "schema_version": "chatbi.runtime-manifest/v1",
        "adapter_name": ADAPTER_NAME,
        "adapter_version": ADAPTER_VERSION,
        "runtime": manifest.runtime,
        "runtime_version": manifest.runtime_version,
        "capabilities": {
            name: {"status": entry.status.value, "modes": list(entry.modes)}
            for name, entry in manifest.capabilities.items()
        },
        "supported_matrix": {
            "fail_closed": {
                "verdict": "partial",
                "note": (
                    "Agno target is PARTIAL until the stage-D P0 "
                    "conformance suite passes and the module-6 acceptance "
                    "list is satisfied (design §14.2, rule 5/6)."
                ),
            }
        },
    }
    manifest_path = out_dir / "runtime-manifest.json"
    manifest_path.write_text(
        json.dumps(runtime_manifest, ensure_ascii=False, sort_keys=True, indent=2)
        + "\n",
        encoding="utf-8",
    )
    try:
        display_path = manifest_path.relative_to(HARNESS_ROOT)
    except ValueError:
        display_path = manifest_path
    print(f"manifest: {display_path}")
    print(f"manifest: runtime={manifest.runtime} "
          f"runtime_version={manifest.runtime_version}")
    print("build ok: auditable artifact produced; deploy is a separate "
          "authorized action (design §8.5).")
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    _require_harness_target(args.target)
    if args.target == "agno":
        _require_agno_runtime()
        out_dir = Path(args.out).resolve() if args.out else DIST_ROOT / "agno"
        return _build_agno(out_dir)
    out_dir = Path(args.out).resolve() if args.out else CLAUDE_DIST
    return _build_claude(out_dir, args)


def _section_rows(manifest, statuses):
    rows = []
    for name, entry in sorted(manifest.capabilities.items()):
        if entry.status in statuses:
            modes = f" modes={list(entry.modes)}" if entry.modes else ""
            rows.append(f"  - {name}: {entry.status.value}{modes}")
    return rows


def _cmd_doctor_agno() -> int:
    """doctor --target agno: probe -> manifest -> fail-closed judgment."""
    from runtimes.agno.probe import probe_agno

    manifest = probe_agno()
    print(f"doctor --target agno (runtime {manifest.runtime} "
          f"{manifest.runtime_version})")
    print("supported (provided by runtime/adapter):")
    for row in _section_rows(
        manifest, {CapabilityStatus.PROVIDED_BY_RUNTIME, CapabilityStatus.PROVIDED_BY_ADAPTER}
    ):
        print(row)
    print("partial (development/synthetic acceptance only, design §13 rule 5):")
    for row in _section_rows(manifest, {CapabilityStatus.PARTIAL}):
        print(row)
    print("unsupported:")
    for row in _section_rows(manifest, {CapabilityStatus.UNSUPPORTED}):
        print(row)

    workflows = load_all(HARNESS_ROOT / "workflows")
    required = required_union(workflows)
    missing = manifest.missing_required(required)
    if missing:
        print("fail-closed judgment (MR-005): deployment REFUSED — "
              "required capabilities unsatisfied:")
        for item in missing:
            print(f"  - {item}")
        print("recovery actions:")
        print("  - install the agno runtime in this interpreter and re-run;")
        print("  - re-run the capability probe and the conformance suite "
              "after any runtime upgrade (design §13 rule 3).")
        return 1
    print("fail-closed judgment (MR-005): deployable — every IR required "
          "capability is provided by the runtime or the adapter. NOTE: "
          "supported status additionally requires the P0 conformance suite "
          "(design §14.2); the Agno target stays PARTIAL until then.")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    _require_harness_target(args.target)
    if args.target == "agno":
        _require_agno_runtime()
        return _cmd_doctor_agno()
    adapter = ClaudeCodeAdapter(HARNESS_ROOT)
    probe = _probe_snapshot(HARNESS_ROOT)
    manifest = adapter.probe(probe_snapshot=probe)

    print(f"doctor --target claude-code (runtime {manifest.runtime} "
          f"{manifest.runtime_version})")
    print(f"probe: {getattr(probe, 'evidence_source', 'none')}; "
          f"claude_available={getattr(probe, 'claude_available', None)}")
    print("supported (provided by runtime/adapter):")
    for row in _section_rows(
        manifest, {CapabilityStatus.PROVIDED_BY_RUNTIME, CapabilityStatus.PROVIDED_BY_ADAPTER}
    ):
        print(row)
    print("partial (development/synthetic acceptance only, design §13 rule 5):")
    for row in _section_rows(manifest, {CapabilityStatus.PARTIAL}):
        print(row)
    print("unsupported:")
    for row in _section_rows(manifest, {CapabilityStatus.UNSUPPORTED}):
        print(row)

    workflows = load_all(HARNESS_ROOT / "workflows")
    # OBS-A: the fail-closed union covers BOTH the requirements layer and
    # the capabilities layer (8/9 workflows declare realpath_sandbox and
    # tool_allowlist under capabilities only) — shared with build_manifest.
    required = required_union(workflows)
    missing = manifest.missing_required(required)
    if missing:
        print("fail-closed judgment (MR-005): deployment REFUSED — "
              "required capabilities unsatisfied:")
        for item in missing:
            print(f"  - {item}")
        print("recovery actions:")
        print("  - streaming partial: headless `claude -p` streaming is not "
              "production-certified (design §9.1). Certify headless streaming "
              "or keep this target in development/local-expert mode "
              "(adjudication six). This module does NOT enable headless "
              "production authentication.")
        print("  - re-run the capability probe and the conformance suite "
              "after any runtime upgrade (design §13 rule 3).")
        return 1
    print("fail-closed judgment (MR-005): deployable — every IR required "
          "capability is provided by the runtime or the adapter.")
    return 0


def _verify_scenarios(scenario_ids: list[str]):
    """Golden-equivalence diffs for the requested scenario ids ([] = OK)."""
    import golden_capture as gc  # conformance/runners on sys.path (module 1)

    results = gc.run_all()
    diffs: list[str] = []
    for scenario_id in scenario_ids:
        if scenario_id not in gc._SCENARIO_REGISTRY:
            diffs.append(
                f"{scenario_id}: unknown scenario (expected one of "
                f"{sorted(gc._SCENARIO_REGISTRY)})"
            )
            continue
        expected_path = gc.EXPECTED_DIR / f"{scenario_id}.json"
        if not expected_path.is_file():
            diffs.append(f"{scenario_id}: expected file missing: {expected_path}")
            continue
        current = (
            json.dumps(results[scenario_id], ensure_ascii=False, sort_keys=True,
                       indent=2)
            + "\n"
        )
        expected = expected_path.read_text(encoding="utf-8")
        if current != expected:
            delta = list(
                difflib.unified_diff(
                    expected.splitlines(),
                    current.splitlines(),
                    fromfile=f"expected/{scenario_id}.json",
                    tofile=f"current/{scenario_id}.json",
                    lineterm="",
                )
            )
            diffs.append(f"{scenario_id}: DIFF ({len(delta)} lines)")
            diffs.extend(delta[:40])
    return diffs


def _cmd_test_conformance_agno(args: argparse.Namespace) -> int:
    """test-conformance --target agno: run the P0 suite on the Agno target
    and judge equivalence against the Golden Contract (impl §9.4)."""
    import runner_agno as ra
    from compare import compare_all, write_report

    scenario_ids = sorted(args.scenario) if args.scenario else sorted(ra._SCENARIO_SPECS)
    print(f"test-conformance --target agno: {len(scenario_ids)} scenario(s): "
          f"{', '.join(scenario_ids)}")
    results = ra.run_all()
    expected = {}
    for sid in scenario_ids:
        expected_path = CONFORMANCE_RUNNERS.parent / "expected" / f"{sid}.json"
        if not expected_path.is_file():
            print(f"FAIL: expected golden output missing: {expected_path}")
            return 1
        import json as _json

        expected[sid] = _json.loads(expected_path.read_text(encoding="utf-8"))
    report = compare_all(expected, results, scenario_ids)
    report_path = DIST_ROOT / "agno" / "conformance-report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    write_report(report, report_path)
    print(f"report: {report_path.relative_to(HARNESS_ROOT)}")
    if report["status"] != "pass":
        print(f"FAIL: {len(report['diffs'])} difference(s) vs the Golden "
              "Contract (Agno target is NOT equivalent):")
        for diff in report["diffs"]:
            print(f"  {diff}")
        return 1
    print("PASS: all P0 scenarios on the Agno target are equivalent to the "
          "Golden Contract (final_status / gate conclusions / candidate_sha "
          "/ evidence chain / review / approval resolution).")
    return 0


def cmd_test_conformance(args: argparse.Namespace) -> int:
    _require_harness_target(args.target)
    if args.target == "agno":
        _require_agno_runtime()
        return _cmd_test_conformance_agno(args)
    if args.scenario:
        scenario_ids = list(args.scenario)
    else:
        import golden_capture as gc  # conformance/runners on sys.path

        scenario_ids = sorted(gc._SCENARIO_REGISTRY)
    print(f"test-conformance --target claude-code: {len(scenario_ids)} "
          f"scenario(s): {', '.join(scenario_ids)}")
    diffs = _verify_scenarios(scenario_ids)

    report = {
        "schema_version": "chatbi.conformance-report/v1",
        "target": "claude-code",
        "scenarios": {sid: "pass" for sid in scenario_ids},
        "diffs": diffs,
    }
    for diff in diffs:
        scenario = diff.split(":", 1)[0]
        if scenario in report["scenarios"]:
            report["scenarios"][scenario] = "fail"
        else:
            report["scenarios"][scenario] = "fail"
    report["status"] = "pass" if not diffs else "fail"

    report_path = CLAUDE_DIST / "conformance-report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    try:
        display_path = report_path.relative_to(HARNESS_ROOT)
    except ValueError:
        display_path = report_path
    print(f"report: {display_path}")
    if diffs:
        print(f"FAIL: {len(diffs)} difference(s) vs the Golden Contract:")
        for diff in diffs:
            print(f"  {diff}")
        return 1
    print("PASS: all requested scenarios are equivalent to the module-1 "
          "Golden Contract.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chatbi-harness",
        description="ChatBI Harness CLI: build / doctor / test-conformance "
        "(multi-runtime module 4; deploy is a separate authorized action).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser(
        "build", help="produce an auditable target artifact (no deploy)"
    )
    build_parser.add_argument(
        "--target", required=True, choices=TARGET_CHOICES,
        help="runtime target (agno is implemented in module 5)",
    )
    build_parser.add_argument(
        "--out", metavar="PATH",
        help="artifact output directory (default harness/dist/<target>)",
    )
    build_parser.set_defaults(handler=cmd_build)

    doctor_parser = subparsers.add_parser(
        "doctor", help="probe a target and print its capability manifest"
    )
    doctor_parser.add_argument("--target", required=True, choices=TARGET_CHOICES)
    doctor_parser.set_defaults(handler=cmd_doctor)

    conformance_parser = subparsers.add_parser(
        "test-conformance",
        help="run the P0 conformance scenarios against the Golden Contract",
    )
    conformance_parser.add_argument("--target", required=True, choices=TARGET_CHOICES)
    conformance_parser.add_argument(
        "--scenario", action="append", metavar="ID",
        help="restrict to scenario id (repeatable; default = all 16)",
    )
    conformance_parser.set_defaults(handler=cmd_test_conformance)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except SystemExit as error:
        if isinstance(error.code, int) and error.code != 0:
            return error.code
        raise
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
