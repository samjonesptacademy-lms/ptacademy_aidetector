"""
Dropbox uploader for AI detection reports.

Handles uploading PDF reports to Dropbox with automatic folder structure creation.
Uses OAuth refresh tokens for automatic token refresh.
"""

import os
import logging
import time
import requests
from io import BytesIO
import dropbox
from dropbox.exceptions import ApiError

logger = logging.getLogger(__name__)

DROPBOX_REFRESH_TOKEN = os.getenv('DROPBOX_REFRESH_TOKEN')
DROPBOX_APP_KEY = "zkuvmtfivr2fnup"
DROPBOX_APP_SECRET = "fqr3pundaac566t"

# Token cache: {access_token, expires_at}
_token_cache = {'access_token': None, 'expires_at': 0}


def _refresh_access_token():
    """
    Refresh the Dropbox access token using the refresh token.

    Returns:
        str: Valid access token

    Raises:
        Exception: If token refresh fails
    """
    global _token_cache

    # Check if cached token is still valid (with 5 minute buffer)
    if _token_cache['access_token'] and time.time() < _token_cache['expires_at'] - 300:
        return _token_cache['access_token']

    logger.info("Refreshing Dropbox access token...")

    token_url = "https://api.dropboxapi.com/oauth2/token"
    token_data = {
        'grant_type': 'refresh_token',
        'refresh_token': DROPBOX_REFRESH_TOKEN,
        'client_id': DROPBOX_APP_KEY,
        'client_secret': DROPBOX_APP_SECRET
    }

    try:
        response = requests.post(token_url, data=token_data)
        response.raise_for_status()
        token_info = response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to refresh Dropbox token: {e}")
        raise Exception(f"Dropbox token refresh failed: {e}")

    access_token = token_info.get('access_token')
    expires_in = token_info.get('expires_in', 3600)

    if not access_token:
        raise Exception("No access token in refresh response")

    # Cache the token with expiration time
    _token_cache['access_token'] = access_token
    _token_cache['expires_at'] = time.time() + expires_in

    logger.info(f"Access token refreshed. Valid for {expires_in} seconds.")
    return access_token


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
    if not DROPBOX_REFRESH_TOKEN:
        logger.warning("DROPBOX_REFRESH_TOKEN not set. Skipping Dropbox upload.")
        return {'success': False, 'error': 'DROPBOX_REFRESH_TOKEN not configured'}

    try:
        # Get valid access token (refreshes if needed)
        access_token = _refresh_access_token()
        dbx = dropbox.Dropbox(access_token)

        # Verify credentials
        try:
            dbx.users_get_current_account()
        except ApiError as e:
            logger.error(f"Dropbox authentication failed: {e}")
            return {'success': False, 'error': 'Dropbox authentication failed'}

        # Create folder path: /{Course Name}/
        folder_path = f"/{course_name}"

        # Create filename: {Learner Name}_{Course Name}_AI Report_{YYYY-MM-DD}.pdf
        # Sanitize filename to remove invalid characters
        import re
        from datetime import datetime
        safe_learner = re.sub(r'[^\w\s\-]', '', learner_name).strip()
        safe_learner = re.sub(r'\s+', ' ', safe_learner)
        safe_course = re.sub(r'[^\w\s\-]', '', course_name).strip()
        safe_course = re.sub(r'\s+', ' ', safe_course)
        date_str = datetime.now().strftime('%Y-%m-%d')
        filename = f"{safe_learner}_{safe_course}_AI Report_{date_str}.pdf"

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
