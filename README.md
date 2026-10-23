```markdown
# Blackboard Collaborate Recording Downloader

An automated Python tool to process Blackboard Collaborate Institutional Recording CSV/XLSX reports, download public recordings, organize files by year, and generate download verification reports.

---

## ⚠️ Important Note on Recording Access

This script **only works for recordings set to Public Access**.

If recordings are set to *Course Members Only* in Class Collaborate, this script cannot download them directly due to Blackboard/Collaborate authentication restrictions.

### What to do if downloads fail due to restricted access:
1. Raise a support ticket with **[support.class.com](https://support.class.com)**.
2. Request the support team to **set the bulk recordings to Public Access**.
3. Once updated, run this script to download and organize your files.

---

## Prerequisites & Installation

### 1. Install Python

Ensure you have Python installed on your system (Python 3.8 to 3.11 is recommended).

* **Windows**:
  1. Download the installer from [python.org](https://www.python.org/downloads/).
  2. During installation, **make sure to check the box: "Add Python to PATH"**.
  3. Verify installation in Terminal/Command Prompt:
     ```bash
     python --version
     ```


Verify installation:

```bash
python3 --version

```

---

## Getting Started

### 2. Clone the Repository

Open your terminal or command prompt, navigate to your desired directory (e.g., Desktop), and clone this repository:

```bash
cd ~/Desktop
$ git clone https://github.com/jerrymahasingh-sudo/PyCollabDownloader.git

cd PyCollabDownloader

```

---

### 3. Usage Instructions

1. Place all your `.csv` or `.xlsx` Collaborate recording report files inside the `Reports/` folder.
2. Run the main script:
```bash
python main.py

```


---

## Features

* **Auto-Dependency Installation**: Automatically verifies and installs missing Python packages (`pandas`, `openpyxl`, `yt-dlp`).
* **Multi-Report Support**: Processes all CSV and Excel files located in `./Reports`.
* **Organized Storage**: Places downloaded MP4 files into year-based subfolders (`./downloaded_recordings/2021/`).
* **File Integrity Verification**: Checks file size and verifies MP4 binary headers (`ftyp` magic bytes).
* **Summary Reporting**: Exports execution details and file verification statuses to `Download_Status_Report.csv`.
