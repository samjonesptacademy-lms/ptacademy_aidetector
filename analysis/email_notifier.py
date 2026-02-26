"""
Email notifications via Microsoft Graph API for error alerts.
"""

import os
import json
import logging
from datetime import datetime
import requests

logger = logging.getLogger(__name__)

# Microsoft Graph API endpoint (using service principal/client credentials flow)
# For app-only auth, we must specify the user mailbox explicitly
def get_graph_api_url(email_address):
    """Get the Graph API endpoint for sending mail from a specific user/mailbox."""
    return f"https://graph.microsoft.com/v1.0/users/{email_address}/sendMail"

# Get credentials from environment
AZURE_CLIENT_ID = os.getenv('AZURE_CLIENT_ID')
AZURE_CLIENT_SECRET = os.getenv('AZURE_CLIENT_SECRET')
AZURE_TENANT_ID = os.getenv('AZURE_TENANT_ID')
EMAIL_FROM = os.getenv('EMAIL_FROM', 'no-reply@ptacademy.com')
EMAIL_TO = os.getenv('EMAIL_TO', 'sam.jones@ptacademy.com')


def get_access_token():
    """Get Microsoft Graph API access token using client credentials flow."""
    token_url = f"https://login.microsoftonline.com/{AZURE_TENANT_ID}/oauth2/v2.0/token"

    payload = {
        'grant_type': 'client_credentials',
        'client_id': AZURE_CLIENT_ID,
        'client_secret': AZURE_CLIENT_SECRET,
        'scope': 'https://graph.microsoft.com/.default'
    }

    try:
        response = requests.post(token_url, data=payload, timeout=10)
        response.raise_for_status()
        return response.json().get('access_token')
    except Exception as e:
        logger.error(f"Failed to get access token: {e}")
        return None


def send_error_email(error_title, error_message, error_traceback=''):
    """
    Send error notification email via Microsoft Graph API.

    Args:
        error_title: Brief title of the error
        error_message: Description of the error
        error_traceback: Full traceback (optional)
    """
    if not all([AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, AZURE_TENANT_ID]):
        logger.warning("Azure credentials not configured - skipping email notification")
        return False

    try:
        # Get access token
        access_token = get_access_token()
        if not access_token:
            logger.error("Could not obtain access token for email")
            return False

        # Build email body
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')
        email_body = f"""
        <html>
            <body style="font-family: Arial, sans-serif;">
                <h2 style="color: #d32f2f;">⚠️ PT Academy AI Detector - Error Alert</h2>

                <p><strong>Timestamp:</strong> {timestamp}</p>
                <p><strong>Error:</strong> {error_title}</p>

                <h3>Details:</h3>
                <p>{error_message}</p>

                {f'<h3>Traceback:</h3><pre style="background: #f5f5f5; padding: 10px; overflow-x: auto;">{error_traceback}</pre>' if error_traceback else ''}

                <hr>
                <p><small>This is an automated alert from the PT Academy AI Detection Tool</small></p>
            </body>
        </html>
        """

        # Build Graph API request
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }

        payload = {
            "message": {
                "subject": f"[Error] PT Academy AI Detector - {error_title}",
                "body": {
                    "contentType": "HTML",
                    "content": email_body
                },
                "toRecipients": [
                    {
                        "emailAddress": {
                            "address": EMAIL_TO
                        }
                    }
                ]
            }
        }

        # Use the email address to construct the endpoint (service principal requires explicit mailbox)
        graph_api_url = get_graph_api_url(EMAIL_FROM)
        response = requests.post(graph_api_url, json=payload, headers=headers, timeout=10)

        if response.status_code == 202:
            logger.info(f"Error email sent to {EMAIL_TO}")
            return True
        else:
            logger.error(f"Failed to send email: {response.status_code} - {response.text}")
            return False

    except Exception as e:
        logger.error(f"Exception while sending error email: {e}")
        return False
