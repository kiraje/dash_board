import logging
import json
import os
from googleapiclient.discovery import build

# Configure logging for Safe Browsing checks
safebrowsing_logger = logging.getLogger('safebrowsing')
log_file_path = os.path.join('/app/logs', 'safebrowsing.log')
safebrowsing_handler = logging.FileHandler(log_file_path)
safebrowsing_handler.setLevel(logging.INFO)
safebrowsing_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
safebrowsing_logger.addHandler(safebrowsing_handler)

def initialize_safebrowsing(api_key):
    return build('safebrowsing', 'v4', developerKey=api_key)

def check_url_safe_browsing(safebrowsing, url: str, retries: int = 2) -> bool:
    def perform_check() -> bool:
        try:
            request_body = {
                'client': {
                    'clientId': 'yourclientid',
                    'clientVersion': '1.5.2'
                },
                'threatInfo': {
                    'threatTypes': ['MALWARE', 'SOCIAL_ENGINEERING'],
                    'platformTypes': ['WINDOWS'],
                    'threatEntryTypes': ['URL'],
                    'threatEntries': [{'url': url}]
                }
            }
            response = safebrowsing.threatMatches().find(body=request_body).execute()
            if 'matches' not in response:
                safebrowsing_logger.info(f"URL is safe: {url}")
                return True
            else:
                safebrowsing_logger.warning(f"URL is not safe: {url}")
                safebrowsing_logger.warning(f"Safe Browsing API response: {json.dumps(response, indent=2)}")
                return False
        except Exception as e:
            safebrowsing_logger.error(f"An error occurred while checking URL: {e}")
            return False

    # Perform the initial check
    is_safe = perform_check()
    if is_safe:
        return True

    # If the URL is flagged, perform additional checks
    for _ in range(retries):
        is_safe = perform_check()
        if is_safe:
            return True
    
    return False
