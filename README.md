# Blackboard Collaborate Recording Downloader

Automated Python tool to process Blackboard Collaborate Institutional Recording CSV/XLSX reports, download public recordings, organize files by year, and generate download verification reports.

## Features
* **Auto-Dependency Installation**: Automatically verifies and installs missing Python packages (`pandas`, `openpyxl`, `yt-dlp`).
* **Multi-Report Support**: Processes all CSV and Excel files located in `./Reports`.
* **Organized Storage**: Places downloaded MP4 files in year-based folders (`./downloaded_recordings/2021/`).
* **File Integrity Verification**: Checks file size and verifies MP4 binary headers (`ftyp` magic bytes).
* **Summary Reporting**: Exports execution details and file verification statuses to `Download_Status_Report.csv`.

## Quick Start
1. Place your CSV or Excel recording reports into the `./Reports` folder.
2. Run the script:
   ```bash
   python main.py
