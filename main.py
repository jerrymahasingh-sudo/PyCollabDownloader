"""
Blackboard Collaborate Recording Downloader
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
from pathlib import Path

import pandas as pd
import yt_dlp

REPORTS_DIR = Path("./Reports")
OUTPUT_DIR = Path("./downloaded_recordings")
STATUS_REPORT_CSV = Path("./Download_Status_Report.csv")

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
    temp_file_path = str(destination.with_suffix(".mp4.part"))

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


def main():
    report_files = [
        f
        for f in REPORTS_DIR.iterdir()
        if f.suffix.lower() in (".csv", ".xlsx") and not f.name.startswith("~$")
    ]

    if not report_files:
        logging.warning("No report files (.csv or .xlsx) found in %s", REPORTS_DIR)
        return

    logging.info("Found %d report file(s) in %s", len(report_files), REPORTS_DIR)

    tracking_results = []
    total_processed = 0

    for report_path in report_files:
        logging.info("--- Processing report file: %s ---", report_path.name)

        if report_path.suffix.lower() == ".csv":
            df = pd.read_csv(report_path, encoding="utf-8-sig")
        else:
            df = pd.read_excel(report_path)

        for index, row in df.iterrows():
            total_processed += 1
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

            # Tracking record template
            record = {
                "Report_File": report_path.name,
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

            # Invalid or missing link check
            if (
                not raw_url
                or raw_url.lower() == "nan"
                or not raw_url.startswith("http")
            ):
                record["Status"] = "Skipped_Invalid_URL"
                record["Verification_Status"] = "N/A"
                tracking_results.append(record)
                continue

            # Determine existing file location (year subfolder or old root folder)
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
                        "[%d] Skipping existing & verified file (%s): %s",
                        total_processed,
                        year_folder,
                        filename,
                    )
                    record["Status"] = "Already_Exists"
                    record["Verification_Status"] = v_status
                    record["File_Size_MB"] = round(file_size_mb, 2)
                    record["Local_Path"] = str(target_path)
                    tracking_results.append(record)
                    continue

            # Perform Download
            logging.info(
                "[%d] Downloading (%s): %s",
                total_processed,
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

            tracking_results.append(record)

            if total_processed % 10 == 0:
                gc.collect()

    # Save summary status CSV report
    summary_df = pd.DataFrame(tracking_results)
    summary_df.to_csv(STATUS_REPORT_CSV, index=False, encoding="utf-8-sig")
    logging.info(
        "Finished processing all reports. Download status report generated at: %s",
        STATUS_REPORT_CSV.resolve(),
    )


if __name__ == "__main__":
    main()
