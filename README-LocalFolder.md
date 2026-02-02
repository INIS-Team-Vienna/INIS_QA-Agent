# INIS QA Agent (Local + Folder-Based QA)

Local, folder-based QA workflow for the International Nuclear Information System (INIS), with optional automation via GitHub Actions.

## Overview

This system can:
1. Run QA checks on local JSON record files or on live INIS records
2. Generate QA report JSON files into a chosen output folder
3. Apply QA corrections to local record JSON files
4. Optionally apply trusted corrections directly to INIS (with an access token)
5. Optionally send email reports (mainly for GitHub Actions runs)

## Features

- **Folder-based QA**: Run QA on local JSON records and write report JSONs to a folder
- **Smart Corrections**: Automatically fixes common issues like title formatting and affiliations
- **Local Auto-Correction**: Applies QA results to local JSON and organizes out-of-scope/duplicate records
- **Optional Production Apply**: Apply trusted corrections directly to INIS with a token
- **Optional GitHub Actions**: Cloud-scheduled runs with email reporting

## Setup (Local)

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Set Azure OpenAI variables

```bash
setx AZURE_OPENAI_API_KEY "your-key"
setx ENDPOINT_URL "https://your-resource.openai.azure.com/"
setx DEPLOYMENT_NAME "o4-mini"
```

### 3. (Optional) Set INIS token for production apply

```bash
setx ACCESS_TOKEN "your-inis-token"
```

## Usage (Local, Folder-Based QA)

### 1. Run QA on local JSON records

```bash
python o4-INISQAChecker.py --dir C:\path\to\records --out C:\path\to\QAResults
ex. python o4-INISQAChecker.py --dir C:\Harvested --out C:\Harvested\QAResults
```

Key switches:
- `--dir`: folder containing local JSON records
- `--out`: output folder for QA report JSONs (default: `c:\QAResults`)
- `--verbose`: prints a truncated model response for each record

### 2. Run QA against live INIS (optional)

```bash
python o4-INISQAChecker.py --live https://inis.iaea.org --date 2026-01-29 --out C:\QAResults
```

Key switches:
- `--live`: base URL of INIS (default `https://inis.iaea.org`)
- `--date`: fetch records for a specific date (YYYY-MM-DD)
- `--include-country-of-input`: filter to specific country codes (repeatable)
- `--exclude-country-of-input`: exclude country codes (repeatable)

### 3. Apply QA corrections to local JSON files

```bash
python local_auto_correct.py --records-dir C:\path\to\records --qa-dir C:\path\to\QAResults
ie python local_auto_correct.py --records-dir C:\Harvested --qa-dir C:\Harvested\QAResults
```

Key switches:
- `--records-dir`: folder with local JSON (and optional PDF) files
- `--qa-dir`: folder with QA report JSON files
- `--dry-run`: show actions without writing or moving files
- `--out-of-scope-dir`: subfolder name for out-of-scope records (default `Possible_Out_Of_Scope`)
- `--duplicates-dir`: subfolder name for duplicates (default `Possible_Duplicates`)
- `--report`: path to Markdown report (file or folder)

## Optional: Apply QA Corrections to INIS (Production)

Use this only when you have a valid INIS token and want to apply trusted corrections.

```bash
python auto_correction_applier.py --qa-folder C:\QAResults --apply --token YOUR_TOKEN
```

Key switches:
- `--qa-folder`: folder containing QA report JSON files
- `--apply`: actually apply changes (default is dry-run)
- `--token`: INIS API token (or set `ACCESS_TOKEN`)
- `--base-url`: override INIS API base URL (default `https://inis.iaea.org/api/records`)

## Optional: Email Configuration (GitHub Actions)

### Gmail Setup

If using Gmail:

1. Enable 2-factor authentication on your Google account
2. Generate an App Password:
   - Go to Google Account settings
   - Security -> 2-Step Verification -> App passwords
   - Generate a password for "Mail"
   - Use this password as `EMAIL_APP_PASSWORD`

### Other Email Providers

Update the `SMTP_SERVER` and `SMTP_PORT` secrets according to your provider:

- **Outlook**: `smtp.live.com`, port `587`
- **Yahoo**: `smtp.mail.yahoo.com`, port `587`
- **Custom SMTP**: Your provider's settings

## Architecture

### Core Components

- **`o4-INISQAChecker.py`**: QA checking using Azure OpenAI (local folder or live INIS)
- **`local_auto_correct.py`**: Applies QA corrections to local JSON and organizes records
- **`auto_correction_applier.py`**: Applies trusted corrections directly to INIS
- **`inis_daily_qa_automation.py`**: GitHub Actions orchestration (optional)
- **`qa_email_sender.py`**: Email reporting with attachments (optional)
- **`instructions.txt`**: QA prompt for the AI system

### Workflow (Local)

1. **Run QA**: Generate QA report JSONs in a chosen folder
2. **Apply Local Corrections**: Update local JSON, move out-of-scope or duplicates
3. **(Optional) Apply to INIS**: Apply trusted corrections directly to INIS

### Correction Application

The system can apply trusted corrections directly to INIS production system. This includes:
- **Title corrections**: Fixes formatting and language issues
- **Affiliation corrections**: Updates institutional affiliations
- **Organizational author corrections**: Corrects organizational author names

**Automation Features:**
- **Automatic activation**: Enables correction application when INIS token is available
- **Selective application**: Only applies trusted correction types (title, affiliation, organizational author)
- **QA marking**: Marks processed records with `iaea:qa_checked = True`
- **Comprehensive logging**: Detailed logs of all operations

## Monitoring (GitHub Actions)

### Logs

- Check the Actions tab for workflow run logs
- Failed runs will upload logs as artifacts
- Email delivery status is logged

### Troubleshooting

Common issues:

1. **Missing Secrets**: Ensure all required secrets are configured
2. **Email Authentication**: Verify app password is correct
3. **API Limits**: Check Azure OpenAI quota and rate limits
4. **Network Issues**: GitHub Actions may occasionally have connectivity issues

## Security

- All sensitive data is stored as GitHub Secrets
- No API keys or passwords are committed to the repository
- Temporary files are automatically cleaned up
- Email attachments are created securely and removed after sending

## Customization

### Schedule (GitHub Actions)

Modify the cron expression in `.github/workflows/daily-qa-check.yml`:

```yaml
schedule:
  - cron: '0 6 * * *'  # 6:00 AM UTC daily
```

### QA Instructions

Edit `instructions.txt` to modify the AI's quality checking behavior.

### Email Recipients (GitHub Actions)

Add multiple recipients by updating the `TO_EMAIL` secret with comma-separated addresses.

## GitHub Actions (Optional)

The workflow can still run daily in the cloud if you prefer automation.

### Required Secrets

Azure OpenAI:
- `AZURE_OPENAI_API_KEY`
- `ENDPOINT_URL`
- `DEPLOYMENT_NAME`

Email:
- `FROM_EMAIL`
- `EMAIL_APP_PASSWORD`
- `TO_EMAIL`
- `SMTP_SERVER` (optional)
- `SMTP_PORT` (optional)

INIS API:
- `INIS_ACCESS_TOKEN` (optional)
- `INIS_API_BASE_URL` (optional)

### Manual Runs

1. Go to the "Actions" tab in your repository
2. Select "Daily INIS QA Check"
3. Click "Run workflow"
4. Optionally specify a date (YYYY-MM-DD format)

## Support

For issues and questions:
- Check the GitHub Actions logs
- Review the troubleshooting section
- Create an issue in this repository
