"""
Run this once locally to get your Dropbox refresh token.
You'll need a Dropbox app with 'offline' access type.

Steps:
1. Go to https://www.dropbox.com/developers/apps
2. Create App → Scoped access → Full Dropbox (or App folder)
3. In Permissions tab, enable: files.content.read, files.content.write, files.metadata.read
4. Copy your App key and App secret from the Settings tab
5. Run: python setup_dropbox.py
6. Paste the refresh token into your .env or Railway env vars
"""

import os
from dropbox import DropboxOAuth2FlowNoRedirect

APP_KEY = input("Dropbox App key: ").strip()
APP_SECRET = input("Dropbox App secret: ").strip()

auth_flow = DropboxOAuth2FlowNoRedirect(
    APP_KEY,
    APP_SECRET,
    token_access_type="offline",
)

authorize_url = auth_flow.start()
print(f"\nGo to this URL and authorize the app:\n{authorize_url}\n")
auth_code = input("Paste the authorization code here: ").strip()

result = auth_flow.finish(auth_code)
print(f"\nRefresh token (save this):\n{result.refresh_token}")
print(f"\nAdd to .env:\nDROPBOX_REFRESH_TOKEN={result.refresh_token}")
print(f"DROPBOX_APP_KEY={APP_KEY}")
print(f"DROPBOX_APP_SECRET={APP_SECRET}")
