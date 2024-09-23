import logging
import os
from az import main as deploy_to_azure
from config import read_config, write_config, initialize_configs
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from keitaro import update_keitaro_flow

# Configure logging
logger = logging.getLogger('deployment')
logger.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

# File handler
log_file_path = os.path.join('/app/logs', 'deployment.log')
file_handler = logging.FileHandler(log_file_path)
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

def deploy_new_url():
    try:
        logger.info("Starting deployment of new URL")
        # Call the main function from az.py, which deploys and returns the URL
        website_url = deploy_to_azure()
        if website_url:
            logger.info(f"Deployed new URL: {website_url}")
            return website_url
        else:
            logger.error("Failed to deploy new URL")
            return None
    except Exception as e:
        logger.exception("Failed to deploy new URL")
        return None

def update_urls_with_phone_number(url, phone_number):
    parsed_url = urlparse(url)
    query_params = parse_qs(parsed_url.query)
    query_params['ph0n'] = [phone_number]
    new_query = urlencode(query_params, doseq=True)
    return urlunparse(parsed_url._replace(query=new_query))

# Example usage (if needed)
if __name__ == "__main__":
    new_url = deploy_new_url()
    if new_url:
        # Update Keitaro flow or perform other actions
        update_keitaro_flow(new_url)
