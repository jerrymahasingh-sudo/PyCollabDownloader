"""
Blackboard Collaborate Recording Source Verification & Audit Script
Developed with assistance from Gemini.
"""
import importlib
import subprocess
import sys

REQUIRED_PACKAGES = {
    "pandas": "pandas",
    "openpyxl": "openpyxl",
    "yt_dlp": "yt-dlp",
}


def ensure_dependencies():
    """Checks and automatically installs any missing dependencies."""
    for module_name, pip_name in REQUIRED_PACKAGES.items():
        try:
            importlib.import_module(module_name)
        except ImportError:
            print(f"[*] Package '{pip_name}' missing. Installing...")
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", pip_name]
            )


ensure_dependencies()

import json
import logging
import re
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

REPORTS_DIR = Path("./Reports")
OUTPUT_DIR = Path("./downloaded_recordings")
AUDIT_REPORT_CSV = Path("./Verification_Audit_Report.csv")

# Number of concurrent metadata checks against Blackboard servers
MAX_WORKERS = 8

# Tolerances for comparing local vs. remote files
SIZE_TOLERANCE_PERCENT = 2.0      # Allow up to 2% difference in file size
DURATION_TOLERANCE_SECONDS = 3.0  # Allow up to 3 seconds difference in length

HAS_FFPROBE = shutil.which("ffprobe") is not None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("verification_audit_log.txt", encoding="utf-8"),
    ],
)

counter_lock = threading.Lock()
checked_counter = 0


def clean_name(value):
    """Sanitize strings to match exact downloader naming logic."""
    if pd.isna(value):
        return "Unnamed"
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value).strip())
    return value.rstrip(". ")[:180] or "Unnamed"


def extract_year_and_date(raw_created):
    """Extract date and 4-digit year to locate local folder."""
    if pd.isna(raw_created) or not str(raw_created).strip():
        return "Unknown", "Unknown_Year"

    raw_created = str(raw_created).strip()
    recording_created = raw_created.split()[0] if raw_created else "Unknown"

    year_match = re.search(r"\b(20\d{2}|19\d{2})\b", recording_created)
    year_folder = year_match.group(1) if year_match else "Unknown_Year"

    return recording_created, year_folder


def size_bytes_to_mb(size_in_bytes):
    if not size_in_bytes:
        return 0.0
    return round(size_in_bytes / (1024 * 1024), 2)


def check_mp4_header(filepath):
    """Verify file is >1MB and contains a valid MP4 'ftyp' signature."""
    path = Path(filepath)
    if not path.exists():
        return False, "File_Missing"

    size_bytes = path.stat().st_size
    if size_bytes < 1_000_000:
        return False, f"Too_Small ({size_bytes_to_mb(size_bytes)} MB)"

    try:
        with open(path, "rb") as f:
            header = f.read(32)
            if b"ftyp" in header:
                return True, "Valid_Header"
            return False, "Corrupted_Header"
    except Exception as e:
        return False, f"Read_Error: {e}"


def get_local_duration(filepath):
    """Use ffprobe (if installed) to extract exact duration of local MP4 in seconds."""
    if not HAS_FFPROBE:
        return None

    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(filepath),
    ]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
        if res.returncode == 0 and res.stdout.strip():
            return float(res.stdout.strip())
    except Exception:
        pass
    return None


def get_remote_metadata(url):
    """Fetch expected filesize and duration from Blackboard without downloading the video."""
    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        url,
        "--dump-json",
        "--no-warnings",
        "--no-check-certificate",
        "--socket-timeout",
        "15",
    ]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60)
        if res.returncode == 0 and res.stdout.strip():
            data = json.loads(res.stdout.splitlines()[0])
            remote_size = data.get("filesize") or data.get("filesize_approx") or 0
            remote_duration = data.get("duration") or 0
            return True, int(remote_size), float(remote_duration), "OK"
        else:
            err = res.stderr.strip() or "Metadata fetch failed"
            return False, 0, 0.0, err[:100]
    except subprocess.TimeoutExpired:
        return False, 0, 0.0, "Source_Timeout"
    except Exception as e:
        return False, 0, 0.0, str(e)[:100]


def verify_single_item(item, total_items):
    """Worker function to verify one local file against its remote source."""
    global checked_counter

    with counter_lock:
        checked_counter += 1
        idx = checked_counter

    destination = item["destination"]
    old_root_path = item["old_root_path"]
    raw_url = item["raw_url"]

    record = {
        "Report_File": item["report_name"],
        "Year": item["year_folder"],
        "ContextIdentifier": item["context_id"],
        "SessionName": item["session_name"],
        "RecordingName": item["recording_name"],
        "RecordingLink": raw_url,
        "Local_Path": str(destination),
        "Audit_Result": "PENDING",
        "Details": "",
        "Local_Size_MB": 0.0,
        "Source_Size_MB": 0.0,
        "Size_Diff_Percent": 0.0,
        "Local_Duration_Sec": None,
        "Source_Duration_Sec": None,
    }

    # 1. Validate URL
    if not raw_url or raw_url.lower() == "nan" or not raw_url.startswith("http"):
        record["Audit_Result"] = "SKIPPED_INVALID_URL"
        record["Details"] = "No valid HTTP link in report"
        return record

    # 2. Locate Local File
    target_path = None
    if destination.exists():
        target_path = destination
    elif old_root_path.exists():
        target_path = old_root_path

    if not target_path:
        logging.warning("[%d/%d] MISSING LOCAL FILE: %s", idx, total_items, item["filename"])
        record["Audit_Result"] = "MISSING_LOCAL"
        record["Details"] = "File not found in downloaded_recordings"
        return record

    record["Local_Path"] = str(target_path)
    local_bytes = target_path.stat().st_size
    record["Local_Size_MB"] = size_bytes_to_mb(local_bytes)

    # 3. Local MP4 Header Check
    header_ok, header_msg = check_mp4_header(target_path)
    if not header_ok:
        logging.error("[%d/%d] CORRUPTED LOCAL FILE (%s): %s", idx, total_items, header_msg, item["filename"])
        record["Audit_Result"] = "CORRUPTED_LOCAL"
        record["Details"] = header_msg
        return record

    # 4. Query Remote Source Metadata
    src_ok, remote_bytes, remote_duration, src_msg = get_remote_metadata(raw_url)
    if not src_ok:
        logging.warning("[%d/%d] SOURCE UNREACHABLE (%s): %s", idx, total_items, src_msg, item["filename"])
        record["Audit_Result"] = "SOURCE_ERROR"
        record["Details"] = f"Local MP4 header valid, but source check failed: {src_msg}"
        return record

    record["Source_Size_MB"] = size_bytes_to_mb(remote_bytes)
    record["Source_Duration_Sec"] = round(remote_duration, 1) if remote_duration else None

    # 5. Compare File Sizes (if source reports size)
    size_mismatch = False
    if remote_bytes > 0:
        diff_pct = abs(local_bytes - remote_bytes) / remote_bytes * 100
        record["Size_Diff_Percent"] = round(diff_pct, 2)
        if diff_pct > SIZE_TOLERANCE_PERCENT:
            size_mismatch = True

    # 6. Compare Video Duration (if ffprobe is available and source reports duration)
    duration_mismatch = False
    local_duration = get_local_duration(target_path)
    if local_duration is not None:
        record["Local_Duration_Sec"] = round(local_duration, 1)
        if remote_duration > 0:
            if abs(local_duration - remote_duration) > DURATION_TOLERANCE_SECONDS:
                duration_mismatch = True

    # 7. Final Verdict
    if duration_mismatch:
        record["Audit_Result"] = "DURATION_MISMATCH"
        record["Details"] = f"Local {record['Local_Duration_Sec']}s vs Source {record['Source_Duration_Sec']}s"
        logging.error("[%d/%d] DURATION MISMATCH: %s (%s)", idx, total_items, item["filename"], record["Details"])
    elif size_mismatch:
        record["Audit_Result"] = "SIZE_MISMATCH"
        record["Details"] = f"Local {record['Local_Size_MB']}MB vs Source {record['Source_Size_MB']}MB ({record['Size_Diff_Percent']}% diff)"
        logging.warning("[%d/%d] SIZE MISMATCH: %s (%s)", idx, total_items, item["filename"], record["Details"])
    else:
        record["Audit_Result"] = "PASS"
        record["Details"] = "Verified against source metadata"
        logging.info("[%d/%d] PASS: %s", idx, total_items, item["filename"])

    return record


def main():
    global checked_counter
    checked_counter = 0

    if not REPORTS_DIR.exists():
        logging.error("Reports directory not found: %s", REPORTS_DIR.resolve())
        return

    report_files = [
        f
        for f in REPORTS_DIR.iterdir()
        if f.suffix.lower() in (".csv", ".xlsx") and not f.name.startswith("~$")
    ]

    if not report_files:
        logging.warning("No report files (.csv or .xlsx) found in %s", REPORTS_DIR)
        return

    if not HAS_FFPROBE:
        logging.info("Note: 'ffprobe' not found on system PATH. Verification will rely on file size & MP4 headers (install FFmpeg to enable exact duration matching).")

    tasks = []
    for report_path in report_files:
        if report_path.suffix.lower() == ".csv":
            df = pd.read_csv(report_path, encoding="utf-8-sig")
        else:
            df = pd.read_excel(report_path)

        for index, row in df.iterrows():
            raw_url = str(row.get("RecordingLink", "")).strip()
            recording_created, year_folder = extract_year_and_date(row.get("RecordingCreated"))
            context_id = clean_name(row.get("ContextIdentifier", row.get("ContextName", "NoCourseID")))
            session_name = clean_name(row.get("SessionName", "Session"))
            recording_name = clean_name(row.get("RecordingName", f"rec_{index+1}"))

            raw_filename = f"{recording_created}_{context_id}_{session_name}_{recording_name}"
            clean_title = re.sub(r'[\\/*?:"<>|]', "", raw_filename)
            filename = f"{clean_title}.mp4"

            tasks.append({
                "report_name": report_path.name,
                "year_folder": year_folder,
                "context_id": context_id,
                "session_name": session_name,
                "recording_name": recording_name,
                "raw_url": raw_url,
                "filename": filename,
                "destination": OUTPUT_DIR / year_folder / filename,
                "old_root_path": OUTPUT_DIR / filename,
            })

    total_items = len(tasks)
    logging.info("Starting source verification for %d recordings using %d threads...", total_items, MAX_WORKERS)

    audit_results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(verify_single_item, item, total_items) for item in tasks]
        for future in as_completed(futures):
            try:
                audit_results.append(future.result())
            except Exception as e:
                logging.error("Unhandled verification error: %s", e)

    summary_df = pd.DataFrame(audit_results)
    summary_df.to_csv(AUDIT_REPORT_CSV, index=False, encoding="utf-8-sig")

    # Print quick summary breakdown to console
    print("\n--- VERIFICATION SUMMARY ---")
    print(summary_df["Audit_Result"].value_counts().to_string())
    print(f"\nFull audit report saved to: {AUDIT_REPORT_CSV.resolve()}")


if __name__ == "__main__":
    main()
