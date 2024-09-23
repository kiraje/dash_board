import json
import requests
import logging
import os
from requests.exceptions import RequestException

# Configure logging
keitaro_logger = logging.getLogger('keitaro')
keitaro_logger.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# File handler
log_file_path = os.path.join('/app/logs', 'keitaro.log')
file_handler = logging.FileHandler(log_file_path)
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(formatter)
keitaro_logger.addHandler(file_handler)

# Console handler
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(formatter)
keitaro_logger.addHandler(console_handler)

AVAILABLE_ACTIONS = [
    "http", "meta", "js", "blank_referrer", "curl", "double_meta",
    "formsubmit", "iframe", "remote", "frame"
]

def read_config():
    azure_config_path = 'azure_config.json'
    keitaro_config_path = 'keitaro_config.json'
    
    with open(azure_config_path, 'r') as azure_file:
        azure_config = json.load(azure_file)
    
    with open(keitaro_config_path, 'r') as keitaro_file:
        keitaro_config = json.load(keitaro_file)
    
    return {**azure_config, **keitaro_config}

def get_keitaro_campaigns(api_key, host):
    url = f'http://{host}/admin_api/v1/campaigns'
    headers = {
        'Api-Key': api_key,
        'accept': 'application/json'
    }
    keitaro_logger.debug(f"Preparing to fetch campaigns from URL: {url}")
    keitaro_logger.debug(f"Request headers: {headers}")
    
    try:
        keitaro_logger.info("Sending request to fetch campaigns...")
        response = requests.get(url, headers=headers, timeout=10)
        keitaro_logger.info(f"Received response with status code: {response.status_code}")
        
        if response.status_code == 200:
            keitaro_logger.info("Successfully fetched campaigns")
            return response.json()
        else:
            keitaro_logger.error(f"Failed to fetch campaigns. Status code: {response.status_code}")
            keitaro_logger.error(f"Response content: {response.text}")
            return []
    except RequestException as e:
        keitaro_logger.exception(f"An error occurred while fetching campaigns: {e}")
        return []

def get_keitaro_streams(api_key, host, campaign_id):
    url = f'http://{host}/admin_api/v1/campaigns/{campaign_id}/streams'
    headers = {
        'Api-Key': api_key,
        'accept': 'application/json'
    }
    keitaro_logger.debug(f"Preparing to fetch streams from URL: {url}")
    keitaro_logger.debug(f"Request headers: {headers}")
    try:
        keitaro_logger.info(f"Sending request to fetch streams for campaign ID: {campaign_id}...")
        response = requests.get(url, headers=headers, timeout=10)
        keitaro_logger.info(f"Received response with status code: {response.status_code}")
        if response.status_code == 200:
            keitaro_logger.info(f"Successfully fetched streams for campaign ID: {campaign_id}")
            return response.json()
        else:
            keitaro_logger.error(f"Failed to fetch streams. Status code: {response.status_code}")
            keitaro_logger.error(f"Response content: {response.text}")
            return []
    except RequestException as e:
        keitaro_logger.exception(f"An error occurred while fetching streams: {e}")
        return []

def get_campaign_details(keitaro_host, api_key, campaign_id):
    url = f'http://{keitaro_host}/admin_api/v1/campaigns/{campaign_id}'
    headers = {
        'Api-Key': api_key,
        'accept': 'application/json'
    }
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            return response.json()
        else:
            keitaro_logger.error(f"Failed to fetch campaign details. Status code: {response.status_code}")
            keitaro_logger.error(f"Response content: {response.text}")
            return None
    except RequestException as e:
        keitaro_logger.exception(f"An error occurred while fetching campaign details: {e}")
        return None

def get_flow_config(keitaro_host, api_key, flow_id):
    url = f'http://{keitaro_host}/admin_api/v1/streams/{flow_id}'
    headers = {
        'Api-Key': api_key,
        'accept': 'application/json'
    }
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            return response.json()
        else:
            keitaro_logger.error(f"Failed to fetch flow configuration. Status code: {response.status_code}")
            keitaro_logger.error(f"Response content: {response.text}")
            return None
    except RequestException as e:
        keitaro_logger.exception(f"An error occurred while fetching flow configuration: {e}")
        return None

def update_keitaro_flow(new_url, campaign_id, stream_id, action_key='http', force_update=False):
    instance_id = os.getenv('INSTANCE_ID', 'unknown')
    keitaro_logger.info(f"[Instance {instance_id}] Starting update_keitaro_flow")
    
    try:
        config = read_config()
        api_key = config['keitaro_api_key']
        keitaro_host = config['keitaro_host']

        keitaro_logger.info(f"[Instance {instance_id}] Configuration loaded: campaign_id={campaign_id}, stream_id={stream_id}")

        current_config = get_flow_config(keitaro_host, api_key, stream_id)
        if not current_config:
            keitaro_logger.error(f"[Instance {instance_id}] Unable to fetch current flow configuration. Aborting update.")
            return None

        current_campaign_id = current_config.get('campaign_id')
        
        keitaro_logger.info(f"[Instance {instance_id}] Current flow configuration: campaign_id={current_campaign_id}")

        if str(current_campaign_id) != str(campaign_id):
            keitaro_logger.warning(f"[Instance {instance_id}] Campaign ID mismatch. Expected: {campaign_id}, Actual: {current_campaign_id}")
            if not force_update:
                keitaro_logger.error(f"[Instance {instance_id}] Aborting update to prevent unintended changes. Use force_update=True to override.")
                return None
            else:
                keitaro_logger.warning(f"[Instance {instance_id}] Proceeding with update despite campaign ID mismatch.")

        # Check if the current action_payload is different from the new_url
        if current_config.get('action_payload') == new_url:
            keitaro_logger.info(f"[Instance {instance_id}] No update needed. Current URL is already set to {new_url}")
            return None

        update_payload = {
            'id': int(stream_id),
            'campaign_id': int(campaign_id),
            'action_type': action_key,
            'action_payload': new_url,
            'comments': f"Updated via API - Instance {instance_id}"
        }

        keitaro_logger.info(f"[Instance {instance_id}] Preparing to update with payload: {json.dumps(update_payload, indent=2)}")

        url = f'http://{keitaro_host}/admin_api/v1/streams/{stream_id}'
        headers = {
            'Api-Key': api_key,
            'Content-Type': 'application/json',
            'accept': 'application/json'
        }

        response = requests.put(url, headers=headers, json=update_payload, timeout=10)
        
        keitaro_logger.info(f"[Instance {instance_id}] Update response status code: {response.status_code}")
        keitaro_logger.debug(f"[Instance {instance_id}] Update response content: {response.text}")

        if response.status_code == 200:
            keitaro_logger.info(f"[Instance {instance_id}] Keitaro flow updated successfully")
        else:
            keitaro_logger.error(f"[Instance {instance_id}] Failed to update Keitaro flow. Status code: {response.status_code}")
            keitaro_logger.error(f"[Instance {instance_id}] Response content: {response.text}")

    except Exception as e:
        keitaro_logger.exception(f"[Instance {instance_id}] An error occurred: {e}")

    return response

def list_all_campaigns(keitaro_host, api_key):
    campaigns = get_keitaro_campaigns(api_key, keitaro_host)
    if campaigns:
        keitaro_logger.info("All Campaigns:")
        for campaign in campaigns:
            keitaro_logger.info(f"ID: {campaign['id']}, Name: {campaign['name']}")
            streams = get_keitaro_streams(api_key, keitaro_host, campaign['id'])
            if streams:
                keitaro_logger.info(f"  Streams for campaign {campaign['id']}:")
                for stream in streams:
                    keitaro_logger.info(f"    Stream ID: {stream['id']}, Name: {stream['name']}")
            else:
                keitaro_logger.info(f"  No streams found for campaign {campaign['id']}")
    else:
        keitaro_logger.info("No campaigns found or failed to fetch campaigns.")

def show_campaign_and_flow_details(keitaro_host, api_key, campaign_id, flow_id):
    campaign_details = get_campaign_details(keitaro_host, api_key, campaign_id)
    flow_config = get_flow_config(keitaro_host, api_key, flow_id)

    if campaign_details:
        keitaro_logger.info(f"Campaign Details (ID: {campaign_id}):")
        keitaro_logger.info(f"Name: {campaign_details.get('name')}")
        keitaro_logger.info(f"Status: {campaign_details.get('state')}")
    else:
        keitaro_logger.info(f"Failed to fetch details for campaign ID: {campaign_id}")

    if flow_config:
        keitaro_logger.info(f"\nFlow Details (ID: {flow_id}):")
        keitaro_logger.info(f"Name: {flow_config.get('name')}")
        keitaro_logger.info(f"Campaign ID: {flow_config.get('campaign_id')}")
        keitaro_logger.info(f"Action Type: {flow_config.get('action_type')}")
        keitaro_logger.info(f"Action Payload: {flow_config.get('action_payload')}")
    else:
        keitaro_logger.info(f"Failed to fetch details for flow ID: {flow_id}")

def get_all_campaigns_and_streams(keitaro_host, api_key):
    campaigns = get_keitaro_campaigns(api_key, keitaro_host)
    result = []
    if campaigns:
        for campaign in campaigns:
            campaign_data = {
                'id': campaign['id'],
                'name': campaign['name'],
                'streams': []
            }
            streams = get_keitaro_streams(api_key, keitaro_host, campaign['id'])
            if streams:
                for stream in streams:
                    campaign_data['streams'].append({
                        'id': stream['id'],
                        'name': stream['name']
                    })
            result.append(campaign_data)
    return result