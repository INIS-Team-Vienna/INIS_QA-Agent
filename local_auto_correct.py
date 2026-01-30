#!/usr/bin/env python3
"""
Local Auto-Correction Tool
==========================

Applies QA corrections to local record JSON files and optionally moves
possible out-of-scope or possible duplicate records into subfolders.
"""

import argparse
import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def load_json(path: Path) -> Optional[Dict]:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        logger.error("Invalid JSON: %s (%s)", path, exc)
        return None


def save_json(path: Path, data: Dict, dry_run: bool) -> None:
    if dry_run:
        logger.info("Dry-run: would write %s", path)
        return
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def report_key(report_path: Path, report_data: Dict) -> Optional[str]:
    record_id = report_data.get("record_id")
    if record_id:
        return str(record_id)
    stem = report_path.stem
    if stem.endswith("-report"):
        return stem[:-7]
    return stem


def apply_title(record: Dict, correction: str) -> bool:
    if not correction:
        return False
    metadata = record.setdefault("metadata", {})
    old = metadata.get("title", "")
    if old == correction:
        return False
    metadata["title"] = correction
    logger.info("Title corrected: '%s' -> '%s'", old, correction)
    return True


def apply_abstract(record: Dict, correction: str) -> bool:
    if not correction:
        return False
    metadata = record.setdefault("metadata", {})
    old = metadata.get("description", "")
    if old == correction:
        return False
    metadata["description"] = correction
    logger.info("Abstract corrected")
    return True


def apply_publication_date(record: Dict, correction: str) -> bool:
    if not correction:
        return False
    metadata = record.setdefault("metadata", {})
    old = metadata.get("publication_date", "")
    if old == correction:
        return False
    metadata["publication_date"] = correction
    logger.info("Publication date corrected: '%s' -> '%s'", old, correction)
    return True


def apply_affiliations(record: Dict, corrections: List[Dict]) -> int:
    if not corrections:
        return 0
    creators = record.get("metadata", {}).get("creators", [])
    applied = 0
    for correction in corrections:
        old_aff = correction.get("old_affiliation", "")
        new_aff = correction.get("recommended_affiliation", "")
        if not old_aff or not new_aff:
            continue
        for creator in creators:
            affiliations = creator.get("affiliations", [])
            for affiliation in affiliations:
                if affiliation.get("name", "") == old_aff:
                    affiliation["name"] = new_aff
                    applied += 1
                    logger.info("Affiliation corrected: '%s' -> '%s'", old_aff, new_aff)
    return applied


def apply_org_authors(record: Dict, corrections: List[Dict]) -> int:
    if not corrections:
        return 0
    creators = record.get("metadata", {}).get("creators", [])
    applied = 0
    for correction in corrections:
        old_org = correction.get("old_organizational_author", "")
        new_org = correction.get("recommended_organizational_author", "")
        if not old_org or not new_org:
            continue
        for creator in creators:
            person_org = creator.get("person_or_org", {})
            if person_org.get("type") == "organizational" and person_org.get("name") == old_org:
                person_org["name"] = new_org
                applied += 1
                logger.info("Org author corrected: '%s' -> '%s'", old_org, new_org)
    return applied


def apply_descriptor_deletions(record: Dict, deletions: List[str]) -> bool:
    if not deletions:
        return False
    custom_fields = record.get("custom_fields", {})
    descriptors = custom_fields.get("iaea:descriptors_cai_text")
    if not descriptors:
        return False

    if isinstance(descriptors, str):
        descriptor_list = [d.strip() for d in descriptors.replace(";", ",").split(",") if d.strip()]
        original_is_str = True
    elif isinstance(descriptors, list):
        descriptor_list = descriptors[:]
        original_is_str = False
    else:
        logger.warning("Unexpected descriptor format: %s", type(descriptors))
        return False

    deletions_lower = {d.lower() for d in deletions if d}
    filtered = [d for d in descriptor_list if d.lower() not in deletions_lower]

    if len(filtered) == len(descriptor_list):
        return False

    if original_is_str:
        custom_fields["iaea:descriptors_cai_text"] = "; ".join(filtered)
    else:
        custom_fields["iaea:descriptors_cai_text"] = filtered

    record["custom_fields"] = custom_fields
    logger.info("Descriptors deleted: %s", ", ".join(deletions))
    return True


def add_related_identifiers(record: Dict, identifiers: List[Dict]) -> int:
    if not identifiers:
        return 0
    metadata = record.setdefault("metadata", {})
    existing = metadata.setdefault("related_identifiers", [])
    existing_ids = {ri.get("identifier", "") for ri in existing}
    added = 0
    for identifier in identifiers:
        ident = identifier.get("identifier", "")
        if ident and ident not in existing_ids:
            existing.append(identifier)
            existing_ids.add(ident)
            added += 1
            logger.info("Related identifier added: %s", ident)
    return added


def safe_move(src: Path, dest_dir: Path, dry_run: bool) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if dest.exists():
        stem = src.stem
        suffix = src.suffix
        i = 1
        while True:
            candidate = dest_dir / f"{stem}_{i}{suffix}"
            if not candidate.exists():
                dest = candidate
                break
            i += 1
    if dry_run:
        logger.info("Dry-run: would move %s -> %s", src, dest)
        return
    shutil.move(str(src), str(dest))


def should_move_out_of_scope(report: Dict) -> bool:
    return report.get("scope_ok") is False


def duplicate_reason(report: Dict) -> Optional[str]:
    by_title = report.get("duplicate_by_title")
    by_doi = report.get("duplicate_by_doi")
    if by_title and by_doi:
        return "title and doi"
    if by_title:
        return "title"
    if by_doi:
        return "doi"
    return None


def should_move_duplicate(report: Dict) -> bool:
    return bool(duplicate_reason(report))


def apply_corrections(record: Dict, report: Dict) -> Tuple[bool, List[str], List[str]]:
    changed = False
    actions: List[str] = []
    unapplied: List[str] = []
    corrections = report.get("corrections", {}) or {}

    if "title" in corrections:
        if report.get("title_corrected", True):
            if apply_title(record, corrections.get("title")):
                actions.append("Title corrected")
                changed = True
        else:
            unapplied.append("Title correction present but title_corrected=false")

    if "abstract" in corrections:
        if report.get("abstract_corrected", True):
            if apply_abstract(record, corrections.get("abstract")):
                actions.append("Abstract corrected")
                changed = True
        else:
            unapplied.append("Abstract correction present but abstract_corrected=false")

    if "publication_date" in corrections:
        if report.get("date_corrected", True):
            if apply_publication_date(record, corrections.get("publication_date")):
                actions.append("Publication date corrected")
                changed = True
        else:
            unapplied.append("Publication date correction present but date_corrected=false")

    if "delete_descriptor" in corrections:
        if report.get("descriptor_corrected", True):
            deletions = corrections.get("delete_descriptor")
            if isinstance(deletions, str):
                deletions = [deletions]
            if apply_descriptor_deletions(record, deletions or []):
                actions.append("Descriptors deleted")
                changed = True
        else:
            unapplied.append("Descriptor deletions present but descriptor_corrected=false")

    aff_corrections = report.get("affiliation_corrections", []) or []
    if report.get("affiliation_correction_recommended", True):
        applied = apply_affiliations(record, aff_corrections)
        if applied > 0:
            actions.append(f"Affiliations corrected ({applied})")
            changed = True
        elif aff_corrections:
            unapplied.append("Affiliation corrections present but no matches found")
    elif aff_corrections:
        unapplied.append("Affiliation corrections present but affiliation_correction_recommended=false")

    org_corrections = report.get("organizational_author_corrections", []) or []
    if org_corrections:
        applied = apply_org_authors(record, org_corrections)
        if applied > 0:
            actions.append(f"Organizational authors corrected ({applied})")
            changed = True
        else:
            unapplied.append("Organizational author corrections present but no matches found")

    related = corrections.get("related_identifiers") if isinstance(corrections, dict) else None
    if related:
        if not isinstance(related, list):
            related = [related]
        added = add_related_identifiers(record, related)
        if added > 0:
            actions.append(f"Related identifiers added ({added})")
            changed = True

    return changed, actions, unapplied


def find_record_file(records_dir: Path, key: str) -> Optional[Path]:
    candidate = records_dir / f"{key}.json"
    if candidate.exists():
        return candidate
    return None


def render_markdown(
    report_path: Path,
    records_dir: Path,
    qa_dir: Path,
    out_of_scope_dir: Path,
    duplicates_dir: Path,
    dry_run: bool,
    entries: List[Dict],
    stats: Dict[str, int],
) -> None:
    lines: List[str] = []
    lines.append("# Local Auto-Correction Report")
    lines.append("")
    lines.append(f"- Generated: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"- Dry run: {'yes' if dry_run else 'no'}")
    lines.append(f"- Records dir: {records_dir}")
    lines.append(f"- QA dir: {qa_dir}")
    lines.append(f"- Out-of-scope dir: {out_of_scope_dir}")
    lines.append(f"- Duplicates dir: {duplicates_dir}")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Reports processed: {stats.get('processed', 0)}")
    lines.append(f"- Records corrected: {stats.get('corrected', 0)}")
    lines.append(f"- Records moved (out-of-scope): {stats.get('moved_out', 0)}")
    lines.append(f"- Records moved (duplicates): {stats.get('moved_dup', 0)}")
    lines.append(f"- Records missing: {stats.get('missing', 0)}")
    lines.append("")
    lines.append("## Details")
    lines.append("")

    if not entries:
        lines.append("_No records processed._")
    else:
        for entry in entries:
            lines.append(f"### {entry['key']}")
            lines.append("")
            if entry.get("record_path"):
                lines.append(f"- Record file: `{entry['record_path']}`")
            if entry.get("report_path"):
                lines.append(f"- QA report: `{entry['report_path']}`")
            if entry.get("actions"):
                lines.append("- Actions:")
                for action in entry["actions"]:
                    lines.append(f"  - {action}")
            else:
                lines.append("- Actions: none")

            recommendations = entry.get("recommendations", [])
            unapplied = entry.get("unapplied", [])
            if recommendations or unapplied:
                lines.append("- Recommendations not applied:")
                for rec in recommendations:
                    lines.append(f"  - {rec}")
                for note in unapplied:
                    lines.append(f"  - {note}")
            else:
                lines.append("- Recommendations not applied: none")
            lines.append("")

    if not dry_run:
        report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        logger.info("Report written to %s", report_path)
    else:
        report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        logger.info("Dry-run report written to %s", report_path)


def process(
    records_dir: Path,
    qa_dir: Path,
    out_of_scope_dir: Path,
    duplicates_dir: Path,
    dry_run: bool,
    report_path: Path,
) -> None:
    report_files = list(qa_dir.glob("*.json"))
    if not report_files:
        logger.warning("No QA report files found in %s", qa_dir)
        return

    report_map: Dict[str, Tuple[Dict, Path]] = {}
    for report_file_path in report_files:
        data = load_json(report_file_path)
        if data is None:
            continue
        key = report_key(report_file_path, data)
        if not key:
            logger.warning("Skipping report with no key: %s", report_file_path)
            continue
        report_map[key] = (data, report_file_path)

    processed = 0
    corrected = 0
    moved_out = 0
    moved_dup = 0
    missing = 0
    entries: List[Dict] = []

    for key, (report, report_file_path) in report_map.items():
        record_path = find_record_file(records_dir, key)
        if not record_path:
            logger.warning("No local record JSON found for %s", key)
            missing += 1
            entries.append(
                {
                    "key": key,
                    "record_path": None,
                    "report_path": str(report_file_path.resolve()),
                    "actions": ["Record JSON missing"],
                    "recommendations": report.get("recommendations", []) or [],
                    "unapplied": [],
                }
            )
            continue

        record = load_json(record_path)
        if record is None:
            continue

        processed += 1
        changed, actions, unapplied = apply_corrections(record, report)
        if changed:
            corrected += 1
            save_json(record_path, record, dry_run)

        move_out = should_move_out_of_scope(report)
        move_dup = should_move_duplicate(report)

        if move_out or move_dup:
            dest_dir = out_of_scope_dir if move_out else duplicates_dir
            for src in records_dir.glob(f"{key}.*"):
                if src.is_dir():
                    continue
                safe_move(src, dest_dir, dry_run)
            if move_out:
                moved_out += 1
                actions.append(f"Moved to {out_of_scope_dir.name}")
            else:
                moved_dup += 1
                reason = duplicate_reason(report) or "unknown"
                actions.append(f"Moved to {duplicates_dir.name} (duplicate by {reason})")

        if not actions:
            actions.append("No changes applied")

        entries.append(
            {
                "key": key,
                "record_path": str(record_path.resolve()),
                "report_path": str(report_file_path.resolve()),
                "actions": actions,
                "recommendations": report.get("recommendations", []) or [],
                "unapplied": unapplied,
            }
        )

    logger.info("Processed: %d", processed)
    logger.info("Corrected: %d", corrected)
    logger.info("Moved (out-of-scope): %d", moved_out)
    logger.info("Moved (duplicates): %d", moved_dup)
    logger.info("Missing records: %d", missing)

    stats = {
        "processed": processed,
        "corrected": corrected,
        "moved_out": moved_out,
        "moved_dup": moved_dup,
        "missing": missing,
    }
    render_markdown(
        report_path=report_path,
        records_dir=records_dir,
        qa_dir=qa_dir,
        out_of_scope_dir=out_of_scope_dir,
        duplicates_dir=duplicates_dir,
        dry_run=dry_run,
        entries=entries,
        stats=stats,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply local QA corrections and organize flagged records")
    parser.add_argument("--records-dir", required=True, help="Folder with local record JSON/PDF files")
    parser.add_argument("--qa-dir", required=True, help="Folder with QA report JSON files")
    parser.add_argument("--out-of-scope-dir", default="Possible_Out_Of_Scope", help="Subfolder for out-of-scope records")
    parser.add_argument("--duplicates-dir", default="Possible_Duplicates", help="Subfolder for duplicate records")
    parser.add_argument("--dry-run", action="store_true", help="Show actions without writing or moving files")
    parser.add_argument(
        "--report",
        help="Path to Markdown report file (default: QA dir)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    records_dir = Path(args.records_dir)
    qa_dir = Path(args.qa_dir)

    if not records_dir.exists():
        logger.error("Records dir does not exist: %s", records_dir)
        return 1
    if not qa_dir.exists():
        logger.error("QA dir does not exist: %s", qa_dir)
        return 1

    out_of_scope_dir = records_dir / args.out_of_scope_dir
    duplicates_dir = records_dir / args.duplicates_dir

    if args.report:
        report_arg = Path(args.report)
        if report_arg.exists() and report_arg.is_dir():
            report_path = report_arg / "corrections-report.md"
        elif report_arg.suffix.lower() == ".md":
            report_path = report_arg
        else:
            report_path = report_arg.parent / "corrections-report.md"
    else:
        report_path = qa_dir / "corrections-report.md"

    process(records_dir, qa_dir, out_of_scope_dir, duplicates_dir, args.dry_run, report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
