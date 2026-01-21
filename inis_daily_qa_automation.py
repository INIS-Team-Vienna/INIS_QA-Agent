#!/usr/bin/env python3
"""
INIS Daily QA Automation System for GitHub Actions
=================================================

Main orchestration script that:
1. Runs QA checks on yesterday's records
2. Applies automatic corrections to fixable issues
3. Sends summary report via email to IAEA feedback team
4. Includes QA results as email attachments instead of saving locally

"""

import os
import sys
import json
import logging
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
import argparse

# Import our custom modules
from qa_email_sender import send_qa_report
from auto_correction_processor import AutoCorrectionProcessor
from auto_correction_applier import INISCorrectionApplier

class INISQAAutomation:
    def __init__(self, config_path: Optional[str] = None):
        """Initialize the QA automation system."""
        self.setup_logging()
        self.logger = logging.getLogger(__name__)
        self.load_config(config_path)
        
        # Use temporary directories for GitHub Actions
        self.temp_dir = Path(tempfile.mkdtemp(prefix="inis_qa_"))
        self.qa_results_dir = self.temp_dir / "QAResults"
        self.corrected_records_dir = self.temp_dir / "CorrectedRecords"
        
    def setup_logging(self):
        """Configure logging for the automation system."""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.StreamHandler(sys.stdout)
            ]
        )
        
    def load_config(self, config_path: Optional[str] = None):
        """Load configuration from environment variables."""
        self.config = {
            "invenio_url": "https://inis.iaea.org",
            "email": {
                "smtp_server": os.getenv("SMTP_SERVER", "smtp.gmail.com"),
                "smtp_port": int(os.getenv("SMTP_PORT", "587")),
                "from_email": os.getenv("FROM_EMAIL"),
                "to_email": os.getenv("TO_EMAIL", "inis.feedback@iaea.org"),
                "app_password": os.getenv("EMAIL_APP_PASSWORD")
            },
            "azure_openai": {
                "endpoint_url": os.getenv("ENDPOINT_URL", "https://pdf2json.openai.azure.com/"),
                "deployment_name": os.getenv("DEPLOYMENT_NAME", "o4-mini"),
                "api_version": "2025-01-01-preview",
                "api_key": os.getenv("AZURE_OPENAI_API_KEY")
            }
        }
        
        # Add INIS API configuration
        self.config["inis_api"] = {
            "access_token": os.getenv("INIS_ACCESS_TOKEN", "1hknPZe1RjjJYAYYuTcxG0rMQ47agIIRg7a40QQqfhQEfUpsysqrHV8HCFN8"),
            "base_url": os.getenv("INIS_API_BASE_URL", "https://inis.iaea.org/api/records")
        }
        
        # Validate required environment variables
        required_vars = [
            "AZURE_OPENAI_API_KEY",
            "FROM_EMAIL", 
            "EMAIL_APP_PASSWORD"
        ]
        
        missing_vars = [var for var in required_vars if not os.getenv(var)]
        if missing_vars:
            raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")
                
    def get_yesterday_date(self) -> str:
        """Get yesterday's date in ISO format."""
        return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        
    def create_dated_directory(self, base_path: Path, date: str, suffix: str = "") -> Path:
        """Create a dated directory for organizing results."""
        dir_name = f"QAResults-{date}" if not suffix else f"{suffix}-{date}"
        dated_dir = base_path / dir_name
        dated_dir.mkdir(parents=True, exist_ok=True)
        return dated_dir
        
    def run_qa_checker(
        self,
        date: Optional[str] = None,
        include_country_ids: Optional[List[str]] = None,
        exclude_country_ids: Optional[List[str]] = None,
        output_suffix: Optional[str] = None,
    ) -> bool:
        """Run the QA checker script."""
        if not date:
            date = self.get_yesterday_date()
            
        qa_date_dir = self.create_dated_directory(
            self.qa_results_dir,
            date,
            output_suffix or ""
        )
        
        try:
            self.logger.info(f"Starting QA check for date: {date}")
            
            # Set up environment variables for the QA checker
            env = os.environ.copy()
            azure_config = self.config.get("azure_openai", {})
            env["ENDPOINT_URL"] = azure_config.get("endpoint_url", "https://pdf2json.openai.azure.com/")
            env["DEPLOYMENT_NAME"] = azure_config.get("deployment_name", "o4-mini")
            env["AZURE_OPENAI_API_KEY"] = azure_config.get("api_key", "")
            env["QA_INSTRUCTIONS_FILE"] = "instructions.txt"
            
            # Prepare the command
            cmd = [
                sys.executable, "o4-INISQAChecker.py",
                "--live", self.config["invenio_url"],
                "--out", str(qa_date_dir),
                "--date", date
            ]

            if include_country_ids:
                for cid in include_country_ids:
                    cmd.extend(["--include-country-of-input", cid])
            if exclude_country_ids:
                for cid in exclude_country_ids:
                    cmd.extend(["--exclude-country-of-input", cid])
            
            self.logger.info(f"Running command: {' '.join(cmd)}")
            self.logger.info(f"Azure OpenAI Endpoint: {env['ENDPOINT_URL']}")
            self.logger.info(f"Azure OpenAI Deployment: {env['DEPLOYMENT_NAME']}")
            
            # Run the QA checker
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=Path(__file__).parent,
                env=env
            )
            
            if result.returncode == 0:
                self.logger.info("QA checker completed successfully")
                self.logger.info(f"Output: {result.stdout}")
                return True
            else:
                self.logger.error(f"QA checker failed with return code {result.returncode}")
                self.logger.error(f"Error output: {result.stderr}")
                return False
                
        except Exception as e:
            self.logger.error(f"Error running QA checker: {e}")
            return False
            
    def process_corrections(self, date: Optional[str] = None) -> bool:
        """Process and apply automatic corrections."""
        if not date:
            date = self.get_yesterday_date()
            
        qa_date_dir = self.qa_results_dir / f"QAResults-{date}"
        qa_germany_dir = self.qa_results_dir / f"QAResults-Germany-{date}"
        corrected_date_dir = self.create_dated_directory(
            self.corrected_records_dir, 
            date, 
            "QAChecked"
        )
        
        try:
            self.logger.info(f"Processing corrections for date: {date}")
            
            # Initialize the correction processor
            processor = AutoCorrectionProcessor(
                self.config["invenio_url"],
                str(corrected_date_dir),
                self.config.get("azure_openai", {})
            )
            
            success = True

            # Process all QA report files (main)
            if qa_date_dir.exists():
                report_files = list(qa_date_dir.glob("*-report.json"))
                self.logger.info(f"Found {len(report_files)} QA reports to process")

                corrections_applied = processor.process_qa_reports(report_files)
                self.logger.info(f"Applied {corrections_applied} corrections")
            else:
                self.logger.warning(f"QA results directory not found: {qa_date_dir}")
                success = False

            # Process Germany QA results if available
            if qa_germany_dir.exists():
                germany_corrected_dir = self.create_dated_directory(
                    self.corrected_records_dir,
                    date,
                    "QAChecked-Germany"
                )
                germany_processor = AutoCorrectionProcessor(
                    self.config["invenio_url"],
                    str(germany_corrected_dir),
                    self.config.get("azure_openai", {})
                )
                germany_reports = list(qa_germany_dir.glob("*-report.json"))
                self.logger.info(f"Found {len(germany_reports)} Germany QA reports to process")
                germany_corrections = germany_processor.process_qa_reports(germany_reports)
                self.logger.info(f"Applied {germany_corrections} Germany corrections")
            else:
                self.logger.info(f"Germany QA results directory not found: {qa_germany_dir}")

            return success
                
        except Exception as e:
            self.logger.error(f"Error processing corrections: {e}")
            return False
            
    def apply_corrections_to_inis(self, date: Optional[str] = None, apply_changes: bool = False) -> bool:
        """Apply corrections directly to INIS production system."""
        if not date:
            date = self.get_yesterday_date()
            
        qa_date_dir = self.qa_results_dir / f"QAResults-{date}"
        qa_germany_dir = self.qa_results_dir / f"QAResults-Germany-{date}"
        
        try:
            self.logger.info(f"Applying corrections to INIS for date: {date}")
            
            # Check if INIS access token is available
            inis_config = self.config.get("inis_api", {})
            access_token = inis_config.get("access_token")
            
            if not access_token:
                self.logger.warning("No INIS access token provided - skipping correction application")
                return True
            
            if qa_date_dir.exists():
                # Initialize the correction applier
                applier = INISCorrectionApplier(
                    access_token=access_token,
                    base_url=inis_config.get("base_url", "https://inis.iaea.org/api/records"),
                    dry_run=not apply_changes
                )
                
                # Process the QA folder
                success = applier.process_qa_folder(qa_date_dir)

                if qa_germany_dir.exists():
                    germany_success = applier.process_qa_folder(qa_germany_dir)
                    success = success and germany_success
                
                if success:
                    mode = "Applied corrections" if apply_changes else "Dry-run completed"
                    self.logger.info(f"{mode} for INIS records")
                    return True
                else:
                    self.logger.error("Failed to apply corrections to INIS")
                    return False
            else:
                self.logger.warning(f"QA results directory not found: {qa_date_dir}")
                return False
                
        except Exception as e:
            self.logger.error(f"Error applying corrections to INIS: {e}")
            return False
            
    def send_daily_report(self, date: Optional[str] = None) -> bool:
        """Send the daily QA report via email with attachments."""
        if not date:
            date = self.get_yesterday_date()
            
        qa_date_dir = self.qa_results_dir / f"QAResults-{date}"
        qa_germany_dir = self.qa_results_dir / f"QAResults-Germany-{date}"
        
        try:
            self.logger.info(f"Sending daily report for date: {date}")

            success = True

            if qa_date_dir.exists():
                main_success = send_qa_report(
                    str(qa_date_dir),
                    self.config["email"],
                    date
                )
                if not main_success:
                    self.logger.error("Failed to send daily report")
                    success = False
            else:
                self.logger.error(f"QA results directory not found: {qa_date_dir}")
                success = False

            if qa_germany_dir.exists():
                germany_email_config = dict(self.config["email"])
                germany_email_config["to_email"] = "silke.rehme@fiz-karlsruhe.de,inis.feedback@iaea.org"
                germany_subject = f"INIS Daily Health Report (Germany) - {date}"
                germany_success = send_qa_report(
                    str(qa_germany_dir),
                    germany_email_config,
                    date,
                    subject=germany_subject
                )
                if not germany_success:
                    self.logger.error("Failed to send Germany daily report")
                    success = False
            else:
                self.logger.info(f"Germany QA results directory not found: {qa_germany_dir}")

            return success
                
        except Exception as e:
            self.logger.error(f"Error sending daily report: {e}")
            return False
            
    def cleanup_temp_files(self):
        """Clean up temporary files."""
        try:
            if self.temp_dir.exists():
                shutil.rmtree(self.temp_dir)
                self.logger.info("Cleaned up temporary files")
        except Exception as e:
            self.logger.error(f"Error cleaning up temporary files: {e}")
            
    def run_daily_automation(self, date: Optional[str] = None, apply_corrections: Optional[bool] = None) -> bool:
        """Run the complete daily automation workflow."""
        if not date:
            date = self.get_yesterday_date()
            
        self.logger.info(f"Starting daily QA automation for {date}")
        
        # Auto-enable corrections if INIS token is available (unless explicitly disabled)
        if apply_corrections is None:
            inis_token = self.config.get("inis_api", {}).get("access_token")
            apply_corrections = bool(inis_token)
            if apply_corrections:
                self.logger.info("INIS access token found - correction application enabled")
            else:
                self.logger.info("No INIS access token - correction application disabled")
        
        success = True
        
        try:
            # Step 1: Run QA checker
            if not self.run_qa_checker(date, exclude_country_ids=["de"]):
                self.logger.error("QA checker failed - aborting automation")
                return False

            # Step 1b: Run Germany QA checker
            if not self.run_qa_checker(
                date,
                include_country_ids=["de"],
                output_suffix="QAResults-Germany"
            ):
                self.logger.warning("Germany QA checker failed - continuing with workflow")
                success = False
                
            # Step 2: Process corrections
            if not self.process_corrections(date):
                self.logger.warning("Correction processing failed - continuing with workflow")
                success = False
                
            # Step 3: Send daily report (includes attachments) - BEFORE applying corrections
            if not self.send_daily_report(date):
                self.logger.error("Failed to send daily report")
                success = False
                
            # Step 4: Apply corrections to INIS (after email is sent)
            if not self.apply_corrections_to_inis(date, apply_corrections):
                self.logger.warning("INIS correction application failed")
                success = False
                
            if success:
                self.logger.info("Daily QA automation completed successfully")
            else:
                self.logger.warning("Daily QA automation completed with some errors")
                
            return success
            
        finally:
            # Always cleanup temporary files
            self.cleanup_temp_files()

def main():
    """Main entry point for the automation script."""
    parser = argparse.ArgumentParser(description="INIS Daily QA Automation System for GitHub Actions")
    parser.add_argument("--date", help="Date to process (YYYY-MM-DD), defaults to yesterday")
    parser.add_argument("--qa-only", action="store_true", help="Run only QA checking")
    parser.add_argument("--corrections-only", action="store_true", help="Run only corrections processing")
    parser.add_argument("--apply-only", action="store_true", help="Run only INIS correction application")
    parser.add_argument("--email-only", action="store_true", help="Run only email sending")
    parser.add_argument("--apply-corrections", action="store_true", help="Force enable correction application to INIS")
    parser.add_argument("--no-apply-corrections", action="store_true", help="Disable correction application to INIS")
    
    args = parser.parse_args()
    
    try:
        # Initialize the automation system
        automation = INISQAAutomation()
        
        if args.qa_only:
            success = automation.run_qa_checker(args.date)
        elif args.corrections_only:
            success = automation.process_corrections(args.date)
        elif args.apply_only:
            success = automation.apply_corrections_to_inis(args.date, apply_changes=True)
        elif args.email_only:
            success = automation.send_daily_report(args.date)
        else:
            # Run full automation with automatic correction detection
            apply_corrections = None
            if args.apply_corrections:
                apply_corrections = True
            elif args.no_apply_corrections:
                apply_corrections = False
            
            success = automation.run_daily_automation(args.date, apply_corrections)
            
        sys.exit(0 if success else 1)
        
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Automation interrupted by user")
        sys.exit(1)
    except Exception as e:
        logging.getLogger(__name__).error(f"Unexpected error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
