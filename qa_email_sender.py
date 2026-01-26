#!/usr/bin/env python3
"""
QA Email Sender Module
=====================

Sends QA check results to the IAEA feedback team with improved formatting
and error handling.

"""

import os
import json
import smtplib
import logging
from email.message import EmailMessage
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
import tempfile
import zipfile

logger = logging.getLogger(__name__)

class QAEmailSender:
    def __init__(self, email_config: Dict):
        """Initialize the email sender with configuration."""
        self.smtp_server = email_config.get("smtp_server", "smtp.gmail.com")
        self.smtp_port = email_config.get("smtp_port", 587)
        self.from_email = email_config.get("from_email")
        to_email = email_config.get("to_email", "inis.feedback@iaea.org")
        if isinstance(to_email, (list, tuple, set)):
            to_email = ", ".join([str(addr) for addr in to_email if addr])
        self.to_email = to_email
        self.app_password = email_config.get("app_password")
        
        if not self.from_email or not self.app_password:
            raise ValueError("Email configuration incomplete: missing from_email or app_password")

    def create_summary_report(self, qa_folder: str) -> Dict:
        """Create a comprehensive summary report from QA results."""
        summary = {
            "records_checked": 0,
            "title_corrections": 0,
            "affiliation_corrections": 0,
            "organizational_author_corrections": 0,
            "abstract_corrections": 0,
            "descriptor_corrections": 0,
            "date_corrections": 0,
            "errors": 0
        }

        duplicates = set()
        out_of_scope = set()
        suspicious_content = set()
        historical_context = set()
        descriptor_deletions = defaultdict(list)
        abstract_recommendations = {}
        general_recommendations = defaultdict(list)
        corrections_summary = defaultdict(list)
        errors = []

        qa_path = Path(qa_folder)
        if not qa_path.exists():
            logger.error(f"QA folder does not exist: {qa_folder}")
            return None

        report_files = list(qa_path.glob("*-report.json"))
        logger.info(f"Processing {len(report_files)} QA report files")

        for filepath in report_files:
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except json.JSONDecodeError as e:
                logger.error(f"Error reading {filepath}: {e}")
                errors.append(f"Could not parse {filepath.name}: {e}")
                summary["errors"] += 1
                continue
            except Exception as e:
                logger.error(f"Unexpected error reading {filepath}: {e}")
                errors.append(f"Error reading {filepath.name}: {e}")
                summary["errors"] += 1
                continue

            summary["records_checked"] += 1
            record_id = data.get("record_id", filepath.stem.replace("-report", ""))

            # Count corrections
            if data.get("title_corrected"):
                summary["title_corrections"] += 1
                
            if data.get("abstract_corrected"):
                summary["abstract_corrections"] += 1
                
            if data.get("descriptor_corrected"):
                summary["descriptor_corrections"] += 1
                
            if data.get("date_corrected"):
                summary["date_corrections"] += 1

            if data.get("affiliation_correction_recommended") and data.get("affiliation_corrections"):
                count = len(data["affiliation_corrections"])
                summary["affiliation_corrections"] += count
                
            if data.get("organizational_author_corrections"):
                count = len(data["organizational_author_corrections"])
                summary["organizational_author_corrections"] += count

            # Collect issues
            if not data.get("scope_ok", True):
                out_of_scope.add(record_id)
                
            if data.get("duplicate_by_title") or data.get("duplicate_by_doi"):
                duplicates.add(record_id)
                
            if data.get("suspicious_content"):
                suspicious_content.add(record_id)
                
            if data.get("historical_context_required"):
                historical_context.add(record_id)

            # Descriptor deletions
            corrections = data.get("corrections", {})
            if "delete_descriptor" in corrections:
                delete_desc = corrections["delete_descriptor"]
                if isinstance(delete_desc, str):
                    descriptor_deletions[record_id].append(delete_desc)
                elif isinstance(delete_desc, list):
                    descriptor_deletions[record_id].extend(delete_desc)

            # Abstract corrections
            if data.get("abstract_corrected") and "abstract" in corrections:
                abstract_recommendations[record_id] = corrections["abstract"]

            # General recommendations
            if data.get("recommendations"):
                for rec in data["recommendations"]:
                    general_recommendations[record_id].append(rec)

            # Corrections summary
            if corrections:
                for key, value in corrections.items():
                    if key != "delete_descriptor" and key != "abstract":
                        corrections_summary[record_id].append(f"{key}: {value}")

        return {
            "summary": summary,
            "duplicates": duplicates,
            "out_of_scope": out_of_scope,
            "suspicious_content": suspicious_content,
            "historical_context": historical_context,
            "descriptor_deletions": descriptor_deletions,
            "abstract_recommendations": abstract_recommendations,
            "general_recommendations": general_recommendations,
            "corrections_summary": corrections_summary,
            "errors": errors
        }

    def format_email_body(self, report_data: Dict, date: str) -> str:
        """Format the email body with the QA report data."""
        if not report_data:
            return f"QA Check Results for {date}\n\nError: Could not generate report data."

        summary = report_data["summary"]
        
        lines = [
            f"INIS QA Check Results for {date}",
            "=" * 50,
            "",
            "SUMMARY:",
            f"• {summary['records_checked']} records were checked",
            "",
            "CORRECTIONS APPLIED:",
            f"• {summary['title_corrections']} title corrections",
            f"• {summary['affiliation_corrections']} affiliation corrections", 
            f"• {summary['organizational_author_corrections']} organizational author corrections",
            f"• {summary['abstract_corrections']} abstract corrections",
            f"• {summary['descriptor_corrections']} descriptor corrections",
            f"• {summary['date_corrections']} date corrections",
            ""
        ]

        if summary["errors"] > 0:
            lines.extend([
                f"ERRORS: {summary['errors']} files could not be processed",
                ""
            ])
            for error in report_data["errors"]:
                lines.append(f"• {error}")
            lines.append("")

        # Duplicates
        if report_data["duplicates"]:
            lines.extend([
                "POSSIBLE DUPLICATE RECORDS:",
                "These records may be duplicates and should be reviewed:"
            ])
            for record_id in sorted(report_data["duplicates"]):
                lines.append(f"• https://inis.iaea.org/records/{record_id}")
            lines.append("")

        # Out of scope
        if report_data["out_of_scope"]:
            lines.extend([
                "OUT-OF-SCOPE RECORDS:",
                "These records may not be suitable for INIS:"
            ])
            for record_id in sorted(report_data["out_of_scope"]):
                lines.append(f"• https://inis.iaea.org/records/{record_id}")
            lines.append("")

        # Suspicious content
        if report_data["suspicious_content"]:
            lines.extend([
                "SUSPICIOUS CONTENT:",
                "These records may contain pseudoscience or require review:"
            ])
            for record_id in sorted(report_data["suspicious_content"]):
                lines.append(f"• https://inis.iaea.org/records/{record_id}")
            lines.append("")

        # Historical context
        if report_data["historical_context"]:
            lines.extend([
                "HISTORICAL CONTEXT REQUIRED:",
                "These records use outdated terminology or methods:"
            ])
            for record_id in sorted(report_data["historical_context"]):
                lines.append(f"• https://inis.iaea.org/records/{record_id}")
            lines.append("")

        # General recommendations
        if report_data["general_recommendations"]:
            nadia_recommendations = {}
            general_recommendations = {}
            nadia_markers = ("nadia insufficient confidence", "insufficient index confidence")

            for record_id, recommendations in report_data["general_recommendations"].items():
                nadia_recs = []
                other_recs = []
                for rec in recommendations:
                    rec_lower = rec.lower()
                    if any(marker in rec_lower for marker in nadia_markers):
                        nadia_recs.append(rec)
                    else:
                        other_recs.append(rec)

                if nadia_recs:
                    nadia_recommendations[record_id] = nadia_recs
                if other_recs:
                    general_recommendations[record_id] = other_recs

            if general_recommendations:
                lines.extend([
                    "GENERAL RECOMMENDATIONS:",
                    "Records requiring manual review:"
                ])
                for record_id, recommendations in general_recommendations.items():
                    lines.append(f"• https://inis.iaea.org/records/{record_id}")
                    for rec in recommendations:
                        lines.append(f"  - {rec}")
                    lines.append("")

            if nadia_recommendations:
                lines.extend([
                    "NADIA INSUFFICIENT CONFIDENCE:",
                    "Records requiring manual indexing:"
                ])
                for record_id, recommendations in nadia_recommendations.items():
                    lines.append(f"• https://inis.iaea.org/records/{record_id}")
                    for rec in recommendations:
                        lines.append(f"  - {rec}")
                    lines.append("")

        # Descriptor deletions
        if report_data["descriptor_deletions"]:
            lines.extend([
                "DESCRIPTOR DELETION RECOMMENDATIONS:",
                "The following descriptors should be removed:"
            ])
            for record_id, descriptors in report_data["descriptor_deletions"].items():
                lines.append(f"• https://inis.iaea.org/records/{record_id}")
                for desc in descriptors:
                    lines.append(f"  - \"{desc}\"")
                lines.append("")

        # Abstract recommendations
        if report_data["abstract_recommendations"]:
            lines.extend([
                "ABSTRACT RECOMMENDATIONS:",
                "Suggested abstracts for records missing English abstracts:"
            ])
            for record_id, abstract in report_data["abstract_recommendations"].items():
                lines.append(f"• https://inis.iaea.org/records/{record_id}")
                lines.append(f"  Suggested: {abstract[:200]}{'...' if len(abstract) > 200 else ''}")
                lines.append("")

        lines.extend([
            "",
            "---",
            "This report was generated automatically by the INIS QA system.",
            f"Generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}",
            "",
            "For questions or issues, please contact the INIS team."
        ])

        return "\n".join(lines)

    def create_qa_results_archive(self, qa_folder: str) -> Optional[str]:
        """Create a ZIP archive of QA results for email attachment."""
        try:
            qa_path = Path(qa_folder)
            if not qa_path.exists():
                logger.error(f"QA folder does not exist: {qa_folder}")
                return None

            # Create temporary ZIP file
            temp_zip = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
            temp_zip.close()
            
            with zipfile.ZipFile(temp_zip.name, 'w', zipfile.ZIP_DEFLATED) as zipf:
                # Add all JSON report files
                report_files = list(qa_path.glob("*-report.json"))
                for report_file in report_files:
                    zipf.write(report_file, report_file.name)
                
                logger.info(f"Created QA results archive with {len(report_files)} files")
            
            return temp_zip.name
            
        except Exception as e:
            logger.error(f"Error creating QA results archive: {e}")
            return None

    def send_email_with_attachment(self, subject: str, body: str, attachment_path: Optional[str] = None) -> bool:
        """Send an email with optional attachment."""
        try:
            msg = MIMEMultipart()
            msg["Subject"] = subject
            msg["From"] = self.from_email
            msg["To"] = self.to_email
            
            # Add text body
            msg.attach(MIMEText(body, 'plain'))
            
            # Add attachment if provided
            if attachment_path and os.path.exists(attachment_path):
                with open(attachment_path, 'rb') as f:
                    file_data = f.read()
                    
                from email.mime.application import MIMEApplication
                attachment = MIMEApplication(file_data, _subtype='zip')
                attachment.add_header('Content-Disposition', 'attachment', 
                                    filename=f"qa_results_{datetime.now().strftime('%Y%m%d')}.zip")
                msg.attach(attachment)
                logger.info(f"Attached QA results archive: {os.path.basename(attachment_path)}")

            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.from_email, self.app_password)
                server.send_message(msg)
                
            logger.info(f"Email sent successfully to {self.to_email}")
            
            # Clean up temporary attachment file
            if attachment_path and os.path.exists(attachment_path):
                try:
                    os.unlink(attachment_path)
                    logger.info("Cleaned up temporary attachment file")
                except Exception as e:
                    logger.warning(f"Could not clean up temporary file {attachment_path}: {e}")
            
            return True
            
        except smtplib.SMTPException as e:
            logger.error(f"SMTP error sending email: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error sending email: {e}")
            return False

    def send_email(self, subject: str, body: str) -> bool:
        """Send an email with the specified subject and body."""
        return self.send_email_with_attachment(subject, body)

def send_qa_report(
    qa_folder: str,
    email_config: Dict,
    date: str,
    subject: Optional[str] = None,
    to_email: Optional[str] = None,
) -> bool:
    """
    Main function to send QA report email with attached results.
    
    Args:
        qa_folder: Path to folder containing QA report JSON files
        email_config: Email configuration dictionary
        date: Date string for the report
        subject: Optional subject override
        to_email: Optional recipient override (string or comma-separated)
        
    Returns:
        bool: True if email was sent successfully, False otherwise
    """
    try:
        effective_config = dict(email_config)
        if to_email:
            effective_config["to_email"] = to_email
        sender = QAEmailSender(effective_config)
        report_data = sender.create_summary_report(qa_folder)
        
        if not report_data:
            logger.error("Could not generate report data")
            return False
            
        email_body = sender.format_email_body(report_data, date)
        subject = subject or f"INIS QA Check Results - {date}"
        
        # Create archive attachment with QA results
        attachment_path = sender.create_qa_results_archive(qa_folder)
        
        success = sender.send_email_with_attachment(subject, email_body, attachment_path)
        
        if success:
            logger.info(f"QA report for {date} sent successfully with attachment")
        else:
            logger.error(f"Failed to send QA report for {date}")
            
        return success
        
    except Exception as e:
        logger.error(f"Error in send_qa_report: {e}")
        return False

# For backwards compatibility and standalone usage
if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Send INIS QA report via email")
    parser.add_argument("qa_folder", help="Path to QA results folder")
    parser.add_argument("--date", help="Date for the report (YYYY-MM-DD)")
    
    args = parser.parse_args()
    
    # Email config from environment variables
    email_config = {
        "smtp_server": os.getenv("SMTP_SERVER", "smtp.gmail.com"),
        "smtp_port": int(os.getenv("SMTP_PORT", "587")),
        "from_email": os.getenv("FROM_EMAIL"),
        "to_email": os.getenv("TO_EMAIL", "inis.feedback@iaea.org"),
        "app_password": os.getenv("EMAIL_APP_PASSWORD")
    }
    
    # Validate required environment variables
    if not email_config["from_email"] or not email_config["app_password"]:
        print("❌ Missing required environment variables: FROM_EMAIL and EMAIL_APP_PASSWORD")
        exit(1)
    
    date = args.date or datetime.now().strftime("%Y-%m-%d")
    
    logging.basicConfig(level=logging.INFO)
    success = send_qa_report(args.qa_folder, email_config, date)
    
    if success:
        print("✅ Email sent successfully.")
    else:
        print("❌ Failed to send email.")
        exit(1)
