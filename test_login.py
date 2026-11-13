#!/usr/bin/env python3
"""
Test script to debug TECMIS login issues.
Shows exactly what response we get from TECMIS.
"""

import os
import sys
from pathlib import Path

import requests

BASE_URL = "https://paravi.ruh.ac.lk/tecmis/"
LOGIN_URL = BASE_URL + "login.php"
NOTICES_URL = BASE_URL + "forms/form_53_a.php"

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

def load_dotenv(path: Path = Path(__file__).with_name(".env")) -> None:
    """Tiny .env loader (KEY=VALUE lines) - environment variables win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value

def test_login():
    load_dotenv()
    
    user = os.environ.get("TECMIS_USER", "")
    password = os.environ.get("TECMIS_PASS", "")
    
    if not user or not password:
        print("ERROR: TECMIS_USER / TECMIS_PASS not set in .env file")
        sys.exit(1)
    
    print(f"Testing login for user: {user}")
    print("=" * 60)
    
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Referer": BASE_URL + "index.php",
    })
    
    # Step 1: Get login page
    print("\n1. Getting login page...")
    try:
        login_page = session.get(BASE_URL + "index.php", timeout=60)
        login_page.raise_for_status()
        print(f"   ✓ Status: {login_page.status_code}")
    except Exception as e:
        print(f"   ✗ Error: {e}")
        sys.exit(1)
    
    # Step 2: POST login
    print("\n2. Posting login credentials...")
    try:
        response = session.post(
            LOGIN_URL,
            data={"uname": user, "upwd": password},
            headers={"Referer": BASE_URL + "index.php"},
            timeout=60,
        )
        response.raise_for_status()
        print(f"   ✓ Status: {response.status_code}")
        print(f"   ✓ Final URL: {response.url}")
    except Exception as e:
        print(f"   ✗ Error: {e}")
        sys.exit(1)
    
    # Step 3: Check response
    print("\n3. Analyzing response...")
    response_text = response.text
    
    # Check for password change
    if "mpcng.php" in response_text:
        print("   ✗ TECMIS is requesting password change!")
        print("   → Response contains 'mpcng.php'")
        
        # Save the response for inspection
        Path("debug_response.html").write_text(response_text, encoding="utf-8")
        print("   → Saved response to debug_response.html")
        sys.exit(1)
    
    # Check for login failure
    if "You Have Not Permission" in response_text or "name='uname'" in response_text:
        print("   ✗ Login FAILED!")
        print("   → Credentials were rejected")
        
        # Check if we're back at login page
        if "name='uname'" in response_text:
            print("   → We're back at the login page (wrong password?)")
        
        # Save the response for inspection
        Path("debug_response.html").write_text(response_text, encoding="utf-8")
        print("   → Saved response to debug_response.html")
        sys.exit(1)
    
    # Check for successful login indicators
    if "Welcome!" in response_text or "Log Out" in response_text:
        print("   ✓ Login appears SUCCESSFUL!")
        print("   → Response contains welcome message")
    else:
        print("   ? Login status unclear")
    
    # Step 4: Try to fetch notices page
    print("\n4. Fetching notices page...")
    try:
        notices_page = session.get(NOTICES_URL, timeout=60)
        notices_page.raise_for_status()
        print(f"   ✓ Status: {notices_page.status_code}")
        
        # Check if we got the actual notices
        if "Most Recent Notices" in notices_page.text:
            print("   ✓ Successfully retrieved notices page!")
            print("\n" + "=" * 60)
            print("SUCCESS! Login works correctly.")
            print("=" * 60)
            
            # Count notices
            recent_count = notices_page.text.count('<tr class="trbgc">')
            print(f"\nFound approximately {recent_count} notice rows")
            
        elif "name='uname'" in notices_page.text:
            print("   ✗ Got redirected back to login!")
            print("   → Session might have expired")
        else:
            print("   ? Got a response but no notices found")
            Path("debug_notices.html").write_text(notices_page.text, encoding="utf-8")
            print("   → Saved response to debug_notices.html")
            
    except Exception as e:
        print(f"   ✗ Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    test_login()
