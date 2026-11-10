To refactor the script for multithreaded downloads using Python's `concurrent.futures.ThreadPoolExecutor`, we will separate the job creation (parsing the reports) from execution (downloading and verifying in parallel).

### Key Modifications

1. **`MAX_WORKERS` setting:** Controls concurrent downloads (default set to 5–10 to saturate bandwidth without triggering strict server rate-limits).
2. **`ThreadPoolExecutor`:** Dispatches `process_single_item` tasks concurrently across worker threads.
3. **Thread Safety:** Uses thread-safe progress counters and logging.
4. **`yt_dlp` Concurrent Handling:** `yt_dlp` creates unique `.part` temporary files per target path to prevent file collisons between threads.

```python
"""
Blackboard Collaborate Recording Downloader (Multithreaded)
Developed with assistance from Gemini.
"""
import importlib
import subprocess
import sys

# Define required packages and their pip package names
REQUIRED_PACKAGES = {
    "pandas": "pandas",
    "openpyxl": "openpyxl",  # Needed by pandas for reading .xlsx files
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


# Run auto-installer before executing main script logic
ensure_dependencies()

import gc
import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import yt_dlp

REPORTS_DIR = Path("./Reports")
OUTPUT_DIR = Path("./downloaded_recordings")
STATUS_REPORT_CSV = Path("./Download_Status_Report.csv")

# Set the maximum number of concurrent downloads
MAX_WORKERS = 8

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("download_log.txt", encoding="utf-8"),
    ],
)

# Counter thread lock
counter_lock = threading.Lock()
completed_counter = 0


def clean_name(value):
    """Sanitize strings for safe file and folder names."""
    if pd.isna(value):
        return "Unnamed"
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value).strip())
    return value.rstrip(". ")[:180] or "Unnamed"


def extract_year_and_date(raw_created):
    """Extract date and 4-digit year for organization."""
    if pd.isna(raw_created) or not str(raw_created).strip():
        return "Unknown", "Unknown_Year"

    raw_created = str(raw_created).strip()
    recording_created = raw_created.split()[0] if raw_created else "Unknown"

    year_match = re.search(r"\b(20\d{2}|19\d{2})\b", recording_created)
    year_folder = year_match.group(1) if year_match else "Unknown_Year"

    return recording_created, year_folder


def verify_file_integrity(filepath):
    """
    Verifies that the downloaded file exists, is non-empty,
    and has a valid MP4 header signature.
    """
    path = Path(filepath)
    if not path.exists():
        return False, "File_Missing"

    size_bytes = path.stat().st_size
    if size_bytes < 1_000_000:  # Files smaller than 1 MB are considered incomplete
        return False, f"File_Too_Small_{size_bytes_to_mb(size_bytes):.2f}MB"

    # Check for MP4 magic byte header ('ftyp' box near offset 4)
    try:
        with open(path, "rb") as f:
            header = f.read(32)
            if b"ftyp" in header:
                return True, "Verified_Valid_MP4"
            else:
                return False, "Corrupted_Header"
    except Exception as e:
        return False, f"Read_Error_{e}"


def size_bytes_to_mb(size_in_bytes):
    return size_in_bytes / (1024 * 1024)


def download_public_recording(url, destination):
    """Download public recording stream directly using yt-dlp."""
    # Use thread-safe temporary file pathing
    thread_id = threading.get_ident()
    temp_file_path = str(destination.with_suffix(f".part_{thread_id}"))

    ydl_opts = {
        "outtmpl": temp_file_path,
        "quiet": True,
        "nocheckcertificate": True,
        "retries": 10,
        "fragment_retries": 10,
        "continuedl": False,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        temp_path = Path(temp_file_path)
        if temp_path.exists():
            temp_path.replace(destination)
            return True, "Downloaded_Successfully"
    except Exception as e:
        Path(temp_file_path).unlink(missing_ok=True)
        return False, f"Download_Failed: {str(e)[:100]}"

    return False, "Download_Failed_Unknown"


def process_single_item(item_data, total_items):
    """Worker function executed per thread."""
    global completed_counter

    report_name = item_data["report_name"]
    year_folder = item_data["year_folder"]
    context_id = item_data["context_id"]
    session_name = item_data["session_name"]
    recording_name = item_data["recording_name"]
    raw_url = item_data["raw_url"]
    filename = item_data["filename"]
    destination = item_data["destination"]
    old_root_path = item_data["old_root_path"]

    record = {
        "Report_File": report_name,
        "Year": year_folder,
        "ContextIdentifier": context_id,
        "SessionName": session_name,
        "RecordingName": recording_name,
        "RecordingLink": raw_url,
        "Local_Path": str(destination),
        "File_Size_MB": 0.0,
        "Status": "Pending",
        "Verification_Status": "Unverified",
    }

    with counter_lock:
        completed_counter += 1
        current_idx = completed_counter

    # Invalid or missing link check
    if (
        not raw_url
        or raw_url.lower() == "nan"
        or not raw_url.startswith("http")
    ):
        record["Status"] = "Skipped_Invalid_URL"
        record["Verification_Status"] = "N/A"
        return record

    # Determine existing file location
    target_path = None
    if destination.exists():
        target_path = destination
    elif old_root_path.exists():
        target_path = old_root_path

    # Skip & Verification step for already existing files
    if target_path:
        is_valid, v_status = verify_file_integrity(target_path)
        file_size_mb = size_bytes_to_mb(target_path.stat().st_size)

        if is_valid:
            logging.info(
                "[%d/%d] Skipping existing & verified file (%s): %s",
                current_idx,
                total_items,
                year_folder,
                filename,
            )
            record["Status"] = "Already_Exists"
            record["Verification_Status"] = v_status
            record["File_Size_MB"] = round(file_size_mb, 2)
            record["Local_Path"] = str(target_path)
            return record

    # Perform Download
    logging.info(
        "[%d/%d] Downloading (%s): %s",
        current_idx,
        total_items,
        year_folder,
        filename,
    )
    success, status_msg = download_public_recording(raw_url, destination)

    # Verification step post-download
    if success:
        is_valid, v_status = verify_file_integrity(destination)
        file_size_mb = size_bytes_to_mb(destination.stat().st_size)

        record["Status"] = status_msg
        record["Verification_Status"] = v_status
        record["File_Size_MB"] = round(file_size_mb, 2)
    else:
        record["Status"] = status_msg
        record["Verification_Status"] = "Failed_Verification"

    return record


def main():
    global completed_counter
    completed_counter = 0

    report_files = [
        f
        for f in REPORTS_DIR.iterdir()
        if f.suffix.lower() in (".csv", ".xlsx") and not f.name.startswith("~$")
    ]

    if not report_files:
        logging.warning("No report files (.csv or .xlsx) found in %s", REPORTS_DIR)
        return

    logging.info("Found %d report file(s) in %s", len(report_files), REPORTS_DIR)

    tasks = []

    # Build queue of items to download
    for report_path in report_files:
        logging.info("--- Extracting items from report: %s ---", report_path.name)

        if report_path.suffix.lower() == ".csv":
            df = pd.read_csv(report_path, encoding="utf-8-sig")
        else:
            df = pd.read_excel(report_path)

        for index, row in df.iterrows():
            raw_url = str(row.get("RecordingLink", "")).strip()

            recording_created, year_folder = extract_year_and_date(
                row.get("RecordingCreated")
            )
            context_id = clean_name(
                row.get("ContextIdentifier", row.get("ContextName", "NoCourseID"))
            )
            session_name = clean_name(row.get("SessionName", "Session"))
            recording_name = clean_name(
                row.get("RecordingName", f"rec_{index+1}")
            )

            raw_filename = f"{recording_created}_{context_id}_{session_name}_{recording_name}"
            clean_title = re.sub(r'[\\/*?:"<>|]', "", raw_filename)
            filename = f"{clean_title}.mp4"

            year_dir = OUTPUT_DIR / year_folder
            year_dir.mkdir(parents=True, exist_ok=True)

            destination = year_dir / filename
            old_root_path = OUTPUT_DIR / filename

            tasks.append(
                {
                    "report_name": report_path.name,
                    "year_folder": year_folder,
                    "context_id": context_id,
                    "session_name": session_name,
                    "recording_name": recording_name,
                    "raw_url": raw_url,
                    "filename": filename,
                    "destination": destination,
                    "old_root_path": old_root_path,
                }
            )

    total_items = len(tasks)
    logging.info("Starting multithreaded processing with %d worker threads for %d tasks.", MAX_WORKERS, total_items)

    tracking_results = []

    # Execute concurrent downloads
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [
            executor.submit(process_single_item, item, total_items)
            for item in tasks
        ]

        for future in as_completed(futures):
            try:
                result = future.result()
                tracking_results.append(result)
            except Exception as e:
                logging.error("Unhandled error during processing: %s", e)

    # Save summary status CSV report
    summary_df = pd.DataFrame(tracking_results)
    summary_df.to_csv(STATUS_REPORT_CSV, index=False, encoding="utf-8-sig")
    logging.info(
        "Finished processing all reports. Download status report generated at: %s",
        STATUS_REPORT_CSV.resolve(),
    )


if __name__ == "__main__":
    main()