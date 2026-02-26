"""
Dropbox uploader for AI detection reports.

Handles uploading PDF reports to Dropbox with automatic folder structure creation.
"""

import os
import logging
from io import BytesIO
import dropbox
from dropbox.exceptions import ApiError

logger = logging.getLogger(__name__)

DROPBOX_ACCESS_TOKEN = os.getenv('DROPBOX_ACCESS_TOKEN')


def upload_report_to_dropbox(pdf_bytes, learner_name, course_name):
    """
    Upload a PDF report to Dropbox.

    Creates folder structure: /AI Detection Reports/{Course Name}/
    Filename format: {Learner Name}_{Course Name}_AI Report.pdf

    Args:
        pdf_bytes: PDF document as bytes
        learner_name: Name of the learner
        course_name: Name of the course

    Returns:
        dict: {'success': True, 'path': '...'} or {'success': False, 'error': '...'}
    """
    if not DROPBOX_ACCESS_TOKEN:
        logger.warning("DROPBOX_ACCESS_TOKEN not set. Skipping Dropbox upload.")
        return {'success': False, 'error': 'DROPBOX_ACCESS_TOKEN not configured'}

    try:
        dbx = dropbox.Dropbox(DROPBOX_ACCESS_TOKEN)

        # Verify credentials
        try:
            dbx.users_get_current_account()
        except ApiError as e:
            logger.error(f"Dropbox authentication failed: {e}")
            return {'success': False, 'error': 'Dropbox authentication failed'}

        # Create folder path: /AI Detection Reports/{Course Name}/
        folder_path = f"/AI Detection Reports/{course_name}"

        # Create filename: {Learner Name}_{Course Name}_AI Report.pdf
        # Sanitize filename to remove invalid characters
        import re
        safe_learner = re.sub(r'[^\w\s\-]', '', learner_name).strip()
        safe_learner = re.sub(r'\s+', ' ', safe_learner)
        safe_course = re.sub(r'[^\w\s\-]', '', course_name).strip()
        safe_course = re.sub(r'\s+', ' ', safe_course)
        filename = f"{safe_learner}_{safe_course}_AI Report.pdf"

        file_path = f"{folder_path}/{filename}"

        # Upload file (create folder if needed)
        dbx.files_upload(
            pdf_bytes,
            file_path,
            mode=dropbox.files.WriteMode('add', None),  # Add mode to avoid overwriting
            autorename=True,  # Auto-rename if file exists
        )

        logger.info(f"Successfully uploaded report to Dropbox: {file_path}")
        return {'success': True, 'path': file_path}

    except ApiError as e:
        logger.error(f"Dropbox API error during upload: {e}")
        return {'success': False, 'error': str(e)}
    except Exception as e:
        logger.error(f"Unexpected error uploading to Dropbox: {e}")
        return {'success': False, 'error': str(e)}
