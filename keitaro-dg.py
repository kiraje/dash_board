import json
import requests
import logging
import sys

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def read_config(file_path):
    try:
        with open(file_path, 'r') as config_file:
            return json.load(config_file)
    except FileNotFoundError:
        logger.error(f"Config file not found: {file_path}")
        sys.exit(1)
    except json.JSONDecodeError:
        logger.error(f"Invalid JSON in config file: {file_path}")
        sys.exit(1)

def test_api_connection(keitaro_host, api_key):
    url = f'http://{keitaro_host}/admin_api/v1/campaigns'
    headers = {'Api-Key': api_key, 'accept': 'application/json'}
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        logger.info("API connection successful")
        return True
    except requests.RequestException as e:
        logger.error(f"API connection failed: {e}")
        return False

def get_campaign_details(keitaro_host, api_key, campaign_id):
    url = f'http://{keitaro_host}/admin_api/v1/campaigns/{campaign_id}'
    headers = {'Api-Key': api_key, 'accept': 'application/json'}
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error(f"Failed to fetch campaign details: {e}")
        return None

def get_flow_details(keitaro_host, api_key, flow_id):
    url = f'http://{keitaro_host}/admin_api/v1/streams/{flow_id}'
    headers = {'Api-Key': api_key, 'accept': 'application/json'}
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error(f"Failed to fetch flow details: {e}")
        return None

def main():
    logger.info("Starting Keitaro diagnostic script")

    # Read configuration
    config = read_config('azure_config.json')
    logger.info(f"Configuration loaded: {json.dumps(config, indent=2)}")

    # Test API connection
    if not test_api_connection(config['keitaro_host'], config['keitaro_api_key']):
        sys.exit(1)

    # Get campaign details
    campaign_details = get_campaign_details(config['keitaro_host'], config['keitaro_api_key'], config['campaign_id'])
    if campaign_details:
        logger.info(f"Campaign details: {json.dumps(campaign_details, indent=2)}")
    else:
        logger.error("Failed to fetch campaign details")
        sys.exit(1)

    # Get flow details
    flow_details = get_flow_details(config['keitaro_host'], config['keitaro_api_key'], config['flow_id'])
    if flow_details:
        logger.info(f"Flow details: {json.dumps(flow_details, indent=2)}")
    else:
        logger.error("Failed to fetch flow details")
        sys.exit(1)

    # Check if flow is in the correct campaign
    if str(flow_details['campaign_id']) != str(config['campaign_id']):
        logger.warning(f"Flow is not in the expected campaign. Expected: {config['campaign_id']}, Actual: {flow_details['campaign_id']}")
    else:
        logger.info("Flow is in the correct campaign")

    logger.info("Diagnostic script completed")

if __name__ == "__main__":
    main()