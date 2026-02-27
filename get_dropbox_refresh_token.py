#!/usr/bin/env python3
"""
One-time OAuth script to get Dropbox refresh token.

Run this locally once to authorize the app and get a refresh token.
Then add the refresh token to your .env file as DROPBOX_REFRESH_TOKEN.

Usage:
    python get_dropbox_refresh_token.py
"""

import os
import sys
import requests
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlencode

# Your Dropbox app credentials
APP_KEY = "zkuvmtfivr2fnup"
APP_SECRET = "fqr3pundaac566t"
REDIRECT_URI = "http://localhost:8080/auth"

class AuthHandler(BaseHTTPRequestHandler):
    """HTTP handler to catch the OAuth redirect."""

    auth_code = None

    def do_GET(self):
        """Handle the redirect from Dropbox."""
        query = parse_qs(self.path.split('?')[1] if '?' in self.path else '')

        if 'code' in query:
            AuthHandler.auth_code = query['code'][0]
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(b"""
                <html>
                <body style="font-family: Arial; text-align: center; padding: 50px;">
                    <h1>Authorization Successful!</h1>
                    <p>You can close this window. Your refresh token is being generated...</p>
                </body>
                </html>
            """)
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Authorization failed")

    def log_message(self, format, *args):
        """Suppress HTTP log messages."""
        pass


def main():
    print("=" * 60)
    print("Dropbox OAuth Refresh Token Generator")
    print("=" * 60)
    print()

    # Step 1: Generate authorization URL
    auth_params = {
        'client_id': APP_KEY,
        'response_type': 'code',
        'redirect_uri': REDIRECT_URI,
        'token_access_type': 'offline'
    }
    auth_url = f"https://www.dropbox.com/oauth2/authorize?{urlencode(auth_params)}"

    print("Step 1: Opening browser to authorize the app...")
    print(f"Auth URL: {auth_url}")
    print()

    # Start local server to catch redirect
    server = HTTPServer(('localhost', 8080), AuthHandler)
    print("Step 2: Waiting for authorization...")
    print()

    # Open browser
    webbrowser.open(auth_url)

    # Wait for callback
    while AuthHandler.auth_code is None:
        server.handle_request()

    server.server_close()

    auth_code = AuthHandler.auth_code
    print(f"✓ Authorization code received: {auth_code[:20]}...")
    print()

    # Step 2: Exchange code for refresh token
    print("Step 3: Exchanging authorization code for refresh token...")

    token_url = "https://api.dropboxapi.com/oauth2/token"
    token_data = {
        'code': auth_code,
        'grant_type': 'authorization_code',
        'redirect_uri': REDIRECT_URI,
        'client_id': APP_KEY,
        'client_secret': APP_SECRET
    }

    try:
        response = requests.post(token_url, data=token_data)
        response.raise_for_status()
        token_info = response.json()
    except requests.exceptions.RequestException as e:
        print(f"✗ Error exchanging code for token: {e}")
        sys.exit(1)

    if 'refresh_token' not in token_info:
        print("✗ No refresh token in response. Make sure 'token_access_type=offline' was used.")
        print(f"Response: {token_info}")
        sys.exit(1)

    refresh_token = token_info['refresh_token']
    access_token = token_info['access_token']
    expires_in = token_info.get('expires_in', 'unknown')

    print(f"✓ Refresh token obtained!")
    print()
    print("=" * 60)
    print("YOUR REFRESH TOKEN (save this to .env):")
    print("=" * 60)
    print(f"DROPBOX_REFRESH_TOKEN={refresh_token}")
    print("=" * 60)
    print()
    print("Next steps:")
    print("1. Copy the refresh token above")
    print("2. Add it to your .env file:")
    print("   DROPBOX_REFRESH_TOKEN=<paste-token-here>")
    print("3. Remove DROPBOX_ACCESS_TOKEN from .env (if present)")
    print("4. Restart your Flask app")
    print()
    print("The app will now automatically refresh the token before each upload.")
    print("=" * 60)


if __name__ == '__main__':
    main()
