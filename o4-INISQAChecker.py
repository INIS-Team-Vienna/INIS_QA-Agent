#!/usr/bin/env python3
# INIS QA Checker – Azure OpenAI o4‑mini edition
# ------------------------------------------------
# Reads the system prompt (QA instructions) from an external UTF‑8 file
#   » default: instructions.txt in the same folder
#   » override with env var QA_INSTRUCTIONS_FILE=/path/to/file.txt
#
# Example usage:
#   python inis_qa_checker_o4mini.py --dir ./records --verbose
#   python inis_qa_checker_o4mini.py --live https://inis.iaea.org --out qa_results
#
# Prerequisites:
#   pip install openai>=1.30.0
#   export AZURE_OPENAI_API_KEY=<your‑key>
#   (optional) export ENDPOINT_URL, DEPLOYMENT_NAME, QA_INSTRUCTIONS_FILE

import os
import sys
import json
import subprocess
import textwrap
from datetime import datetime, timedelta
from typing import List, Dict, Tuple
from urllib.parse import quote
import re
import argparse
import pathlib
import time
from openai import RateLimitError, APITimeoutError, APIConnectionError

from openai import AzureOpenAI

# ── CONFIG ───────────────────────────────────────────────────────────────────
AZURE_OPENAI_BASE = os.getenv("ENDPOINT_URL", "https://pdf2json.openai.azure.com/")
AZURE_DEPLOYMENT = os.getenv("DEPLOYMENT_NAME", "o4-mini")
AZURE_API_VERSION = "2025-01-01-preview"
AZURE_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
INSTRUCTIONS_PATH = os.getenv("QA_INSTRUCTIONS_FILE", "instructions.txt")

if not AZURE_API_KEY:
    sys.exit("ERROR: Set AZURE_OPENAI_API_KEY in your environment first!")

client = AzureOpenAI(
    azure_endpoint=AZURE_OPENAI_BASE,
    api_key=AZURE_API_KEY,
    api_version=AZURE_API_VERSION,
)

DEFAULT_INVENIO_URL = "https://inis.iaea.org"

# ── PROMPT LOADER ────────────────────────────────────────────────────────────

def load_system_prompt() -> str:
    """Load prompt from external file; fallback to a minimal stub."""
    if os.path.isfile(INSTRUCTIONS_PATH):
        with open(INSTRUCTIONS_PATH, encoding="utf-8") as f:
            data = f.read().strip()
            if data:
                return data
    return (
        "You are an expert QA checker for INIS metadata. Return ONLY a JSON object "
        "with corrections, recommendations, scope_ok, and the booleans title_corrected, "
        "abstract_corrected, affiliation_correction_recommended."
    )

# ── UTILITIES ────────────────────────────────────────────────────────────────

def yesterday_iso() -> str:
    return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")


def parse_assistant_json(raw: str) -> dict:
    txt = raw.strip()
    fence = re.search(r"^```[^\n]*\n(.*?)\n```$", txt, re.S)
    if fence:
        txt = fence.group(1).strip()
    txt = textwrap.dedent(txt.lstrip("json").lstrip(":")).strip()
    return json.loads(txt)


def curl_json(url: str) -> Dict:
    try:
        proc = subprocess.run(
            ["curl", "-sS", "--fail", "-H", "Accept: application/json", url],
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(proc.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return {}


def _normalize_country_list(values: List[str]) -> List[str]:
    """Normalize a list of country codes (comma or space separated)."""
    if not values:
        return []
    normalized: List[str] = []
    for raw in values:
        if not raw:
            continue
        parts = re.split(r"[,\s]+", raw.strip())
        for part in parts:
            if part:
                normalized.append(part.lower())
    return normalized


def _build_country_clause(field: str, include_ids: List[str]) -> str:
    if not include_ids:
        return ""
    if len(include_ids) == 1:
        return f'{field}: {include_ids[0]}'
    joined = " OR ".join(f"{field}: {cid}" for cid in include_ids)
    return f"({joined})"


def build_records_query(date: str, include_ids: List[str], exclude_ids: List[str]) -> str:
    clauses = [f'created:"{date}"', 'NOT custom_fields.iaea\\:qa_checked: (true)']

    include_clause = _build_country_clause("custom_fields.iaea\\:country_of_input.id", include_ids)
    if include_clause:
        clauses.append(include_clause)

    for cid in exclude_ids:
        clauses.append(f'NOT custom_fields.iaea\\:country_of_input.id: {cid}')

    return " AND ".join(clauses)


def fetch_records_by_date(
    base_url: str,
    date: str = None,
    include_country_ids: List[str] = None,
    exclude_country_ids: List[str] = None,
) -> List[Dict]:
    """Fetch records created on a given date (defaults to yesterday)."""
    if not date:
        date = yesterday_iso()
    include_ids = include_country_ids or []
    exclude_ids = exclude_country_ids or ["xa"]
    q = quote(build_records_query(date, include_ids, exclude_ids))
    url = f"{base_url}/api/records?q={q}&size=1000&sort=oldest"
    print(url)
    data = curl_json(url)
    return data.get("hits", {}).get("hits", [])


def load_json_dir(directory: str) -> List[Tuple[str, Dict]]:
    out: List[Tuple[str, Dict]] = []
    for fn in os.listdir(directory):
        if fn.lower().endswith(".json"):
            full = os.path.join(directory, fn)
            try:
                with open(full, encoding="utf-8") as f:
                    out.append((full, json.load(f)))
            except json.JSONDecodeError as e:
                print(f"ERROR: {fn}: JSON error - {e}")
    return out


def check_duplicates(record: Dict, invenio_url: str) -> Dict:
    meta = record.get("metadata", {})
    xid = record.get("id", "")
    title = meta.get("title", "")
    doi = next((i.get("identifier") for i in meta.get("identifiers", []) if i.get("scheme") == "doi"), None)

    flags = {"duplicate_by_title": False, "duplicate_by_doi": False}

    if doi:
        if xid:
            q = quote(f'identifiers.identifier:"{doi}" AND NOT id: {xid}')
        else:
            q = quote(f'identifiers.identifier:"{doi}"')
        data = curl_json(f"{invenio_url}/api/records?q={q}&size=1")
        flags["duplicate_by_doi"] = data.get("hits", {}).get("total", 0) > 0

    if title:
        if xid:
            q = quote(f'metadata.title:"{title}" AND NOT id: {xid}')
        else:
            q = quote(f'metadata.title:"{title}"')
        data = curl_json(f"{invenio_url}/api/records?q={q}&size=1")
        flags["duplicate_by_title"] = data.get("hits", {}).get("total", 0) > 0

    return flags
    
def is_valid_lead_record_id(value: str) -> bool:
    """Check if value matches INIS ID pattern: xxxxx-xxxxx (lowercase alphanumeric)."""
    return isinstance(value, str) and re.fullmatch(r'[a-z0-9]{5}-[a-z0-9]{5}', value) is not None

def is_future_date(date_str: str) -> bool:
    """Return True if date string (YYYY-MM or YYYY-MM-DD) is in the future."""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d") > datetime.now()
    except ValueError:
        try:
            return datetime.strptime(date_str, "%Y-%m") > datetime.now()
        except ValueError:
            return False  # malformed or partial

# ── GPT CALL ────────────────────────────────────────────────────────────────



def send_to_gpt(record: dict, system_prompt: str) -> str:
    """Call the Azure OpenAI chat endpoint and return assistant content.

    • Retries on transient rate-limit / timeout errors (exponential back-off).
    • Forces the model to emit valid JSON (`response_format`).
    • Caps output to 180 tokens—ample for your QA JSON, keeps costs down.
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(record, ensure_ascii=False)},
    ]

    for attempt in range(4):                  # 1 initial try + up to 3 retries
        try:
            completion = client.chat.completions.create(
                model=AZURE_DEPLOYMENT,
                messages=messages,
                max_completion_tokens=10240,
                response_format={"type": "json_object"},
                timeout=120,                  # seconds; per-request
            )

            # Optional: log token usage / finish reason for audits
            fr = completion.choices[0].finish_reason
            usage = completion.usage
            print(f"finish_reason={fr}, prompt={usage.prompt_tokens}, "
                  f"completion={usage.completion_tokens}")

            return completion.choices[0].message.content

        except (RateLimitError, APITimeoutError, APIConnectionError):
            # exponential back-off: 1 s → 2 s → 4 s → 8 s
            sleep = 2 ** attempt
            print(f"Transient error - retrying in {sleep}s...")
            time.sleep(sleep)
        except Exception:            # unexpected problems propagate
            raise

    raise RuntimeError("Max retries exceeded")


# ── QA LOOP ─────────────────────────────────────────────────────────────────


def qa_check(batch: List[Tuple[str, Dict]], invenio_url: str, out_dir: str, verbose: bool = False):
    system_prompt = load_system_prompt()

    for src_path, rec in batch:
        filename = os.path.basename(src_path)
        stem_base = os.path.splitext(filename)[0]

        # Determine QA output directory
        if out_dir:
            qa_dir = out_dir #os.path.join(out_dir, "QA")
        else:
            json_dir = os.path.dirname(src_path)
            qa_dir = os.path.join(json_dir, "QA")
            
        report_path = os.path.join(qa_dir, f"{stem_base}-report.json")

        flags = check_duplicates(rec, invenio_url)
        ai_raw = send_to_gpt(rec, system_prompt)

        try:
            ai_json = parse_assistant_json(ai_raw)
        except json.JSONDecodeError as e:
            ai_json = {"error": f"Assistant returned non-JSON: {e}", "raw_preview": ai_raw[:500]}

        ai_json.update(flags, record_id=rec.get("id"))
   # Additional determinate QA checks (non-AI)
        extra_recommendations = []

        lead_id = rec.get("custom_fields", {}).get("iaea:lead_record_id")
        if lead_id and not is_valid_lead_record_id(lead_id):
            extra_recommendations.append(f"Lead Record appears to be invalid: {lead_id}")

        pub_date = rec.get("metadata", {}).get("publication_date")
        if pub_date and is_future_date(pub_date):
            extra_recommendations.append(f"Publication date is in the future: {pub_date}")

        # Merge with assistant-generated recommendations
        if extra_recommendations:
            ai_json.setdefault("recommendations", []).extend(extra_recommendations)
            
        needs_output = (
            "error" in ai_json or
            ai_json.get("corrections") or
            ai_json.get("recommendations") or
            ai_json.get("affiliation_corrections")
        )
        needs_output = True

        if needs_output:
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(ai_json, f, indent=2, ensure_ascii=False)

        # feedback -----------------------------------------------------------
        if "error" in ai_json:
            print(f"{report_path}: ERROR {ai_json['error']}")
        elif needs_output:
            print(f"{report_path}: Fixes/Advice emitted")
        else:
            print(f"{report_path}: All OK")

        if verbose:
            print("-- Assistant raw reply (truncated 300 chars) --")
            print(ai_raw[:300].replace("\n", " ") + (" ..." if len(ai_raw) > 300 else ""))
            print("--------------------------------------------")


# ── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="INIS QA checker (Azure OpenAI chat)")
    parser.add_argument("--dir", help="Directory with JSON records")
    parser.add_argument("--live", default="https://inis.iaea.org", help="Base URL of live InvenioRDM system")
    parser.add_argument("--out", default="c:\\QAResults", help="Output directory")
    parser.add_argument("--verbose", action="store_true", help="Show assistant snippet in console")
    parser.add_argument("--date", help="Date to fetch records from (YYYY-MM-DD)")
    parser.add_argument("--include-country-of-input", action="append", help="Include only these country codes (comma or space separated)")
    parser.add_argument("--exclude-country-of-input", action="append", help="Exclude these country codes (comma or space separated)")
    args = parser.parse_args()
    base_url = args.live or DEFAULT_INVENIO_URL
    print (base_url)
    out_dir = pathlib.Path(args.out).resolve()

    if args.dir:
        records = load_json_dir(args.dir)
    elif args.live:
        include_ids = _normalize_country_list(args.include_country_of_input or [])
        exclude_ids = _normalize_country_list(args.exclude_country_of_input or [])
        fetched = fetch_records_by_date(
            base_url,
            date=args.date,
            include_country_ids=include_ids,
            exclude_country_ids=exclude_ids if exclude_ids else None,
        )
        records = [(f"{r.get('id', f'record_{i}')}.json", r) for i, r in enumerate(fetched)]
    else:
        sys.exit("ERROR: Specify --dir or --live")

    print(f"QA-checking {len(records)} record(s)...\n")
    qa_check(records, base_url, str(out_dir), verbose=args.verbose)
    print(f"\nDone. Reports -> {out_dir}")
