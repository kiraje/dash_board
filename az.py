import os
import time
import json
import logging
from azure.identity import ClientSecretCredential
from azure.storage.blob import BlobServiceClient, PublicAccess, ContentSettings
from azure.mgmt.storage import StorageManagementClient
from azure.mgmt.storage.models import StorageAccountCreateParameters, Sku
from azure.core.exceptions import AzureError
from azure.mgmt.resource import ResourceManagementClient
from azure.mgmt.resource.resources.models import ResourceGroup
from mimetypes import guess_type
import random
import string

# Configure logging
log_file_path = os.path.join('/app/logs', 'azure_storage_deployment.log')
logging.basicConfig(filename=log_file_path, level=logging.INFO, 
                    format='%(asctime)s - %(levelname)s - %(message)s')

# Function to generate a random storage account name
def generate_storage_account_name():
    timestamp = str(int(time.time()))[-8:]  # Get the last 8 digits of the timestamp
    random_str = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return f"msstoera{random_str}{timestamp}"

# Function to read configuration from a JSON file
def read_config(file_path):
    with open(file_path, 'r') as config_file:
        return json.load(config_file)

# Function to write configuration to a JSON file
def write_config(file_path, config):
    with open(file_path, 'w') as config_file:
        json.dump(config, config_file, indent=4)

# Function to determine the content type based on the file extension
def get_content_type(file_path):
    content_type, _ = guess_type(file_path)
    return content_type or 'application/octet-stream'

# Function to create a resource group if it doesn't exist
def create_resource_group(resource_client, resource_group_name, location):
    try:
        logging.info(f"Checking if resource group '{resource_group_name}' exists...")
        resource_group = resource_client.resource_groups.get(resource_group_name)
        logging.info(f"Resource group '{resource_group_name}' already exists.")
        return resource_group
    except Exception as e:
        logging.info(f"Resource group '{resource_group_name}' not found. Creating new resource group...")
        resource_group_params = {'location': location}
        resource_group = resource_client.resource_groups.create_or_update(resource_group_name, resource_group_params)
        logging.info(f"Resource group '{resource_group_name}' created successfully.")
        return resource_group

# Function to create storage account with retry and timeout logic
def create_storage_account(storage_client, resource_group, storage_account_name, location):
    timeout = 600  # 10 minutes timeout
    start_time = time.time()
    while True:
        try:
            logging.info(f"Creating storage account {storage_account_name} in resource group {resource_group}...")
            poller = storage_client.storage_accounts.begin_create(
                resource_group,
                storage_account_name,
                StorageAccountCreateParameters(
                    sku=Sku(name="Standard_LRS"),
                    kind="StorageV2",
                    location=location,
                    allow_blob_public_access=True
                )
            )
            while not poller.done():
                if time.time() - start_time > timeout:
                    logging.error("Timed out waiting for storage account creation.")
                    raise Exception("Timed out waiting for storage account creation.")
                logging.info("Waiting for storage account creation to complete...")
                time.sleep(10)
            account_result = poller.result()
            logging.info(f"Storage account {storage_account_name} created successfully.")
            return account_result
        except AzureError as e:
            logging.error(f"Error creating storage account: {e}")
            if time.time() - start_time > timeout:
                logging.error("Timed out waiting for storage account creation.")
                raise Exception("Timed out waiting for storage account creation.")
            logging.info("Retrying...")
            time.sleep(10)

# Function to enable static website hosting via Azure SDK (without PowerShell)
def enable_static_website(blob_service_client):
    logging.info("Enabling static website hosting via Azure SDK...")
    blob_service_client.set_service_properties(
        static_website={
            'enabled': True,  # Ensure the enabled field is present and set to True
            'index_document': 'index.html',
            'error_document_404_path': '404.html'
        }
    )
    logging.info("Static website hosting enabled via Azure SDK.")

# Function to delete all storage accounts except the 2 youngest
def delete_except_two_youngest(storage_client, resource_group):
    logging.info(f"Checking for storage accounts in resource group '{resource_group}'...")

    storage_accounts = storage_client.storage_accounts.list_by_resource_group(resource_group)
    accounts = []
    
    # Retrieve all accounts and their creation time
    for account in storage_accounts:
        logging.info(f"Found storage account: {account.name}")
        creation_time = storage_client.storage_accounts.get_properties(resource_group, account.name).creation_time
        accounts.append({'name': account.name, 'creation_time': creation_time})

    # Sort by creation time (newest first)
    accounts.sort(key=lambda x: x['creation_time'], reverse=True)

    # Keep the 2 youngest accounts and delete the rest
    if len(accounts) > 2:
        for account in accounts[2:]:  # Skip the 2 newest accounts
            logging.info(f"Deleting storage account: {account['name']} (created at {account['creation_time']})")
            storage_client.storage_accounts.delete(resource_group, account['name'])  # Delete account
            logging.info(f"Storage account '{account['name']}' deleted successfully.")
    else:
        logging.info("No need to delete any storage accounts.")

# Main function to create and configure the storage account
def main():
    config_file_path = "azure_config.json"
    state_file_path = "state.json"
    az_file_path = "service_principal.json"  # Updated to use relative path
    config = read_config(config_file_path)
    state = read_config(state_file_path)
    az = read_config(az_file_path)

    storage_account_name = generate_storage_account_name()
    resource_group = config["resource_group"]
    location = config["location"]
    local_folder_path = state.get("current_folder", config["local_folder_path"])
    subscription_id = config["subscription_id"]

    # Update the configuration with the generated storage account name
    config["storage_account_name"] = storage_account_name
    write_config(config_file_path, config)

    logging.info(f"Using storage account name: {storage_account_name}")

    # Authenticate using Service Principal
    credential = ClientSecretCredential(
        tenant_id=az['tenant'],
        client_id=az['appId'],
        client_secret=az['password']
    )
    
    storage_client = StorageManagementClient(credential, subscription_id)
    resource_client = ResourceManagementClient(credential, subscription_id)

    try:
        # Create resource group if it doesn't exist
        create_resource_group(resource_client, resource_group, location)

        # Create storage account with retry and timeout logic
        create_storage_account(storage_client, resource_group, storage_account_name, location)

        # Get storage account keys
        keys = storage_client.storage_accounts.list_keys(resource_group, storage_account_name)
        storage_account_key = keys.keys[0].value
        logging.info("Storage account keys retrieved successfully.")

        # Create BlobServiceClient using the connection string
        blob_service_client = BlobServiceClient(
            f"https://{storage_account_name}.blob.core.windows.net",
            credential=storage_account_key
        )

        # Enable static website hosting using Azure SDK
        enable_static_website(blob_service_client)

        # Check if the $web container exists and set access level
        web_container_client = blob_service_client.get_container_client('$web')
        try:
            web_container_client.get_container_properties()
            logging.info("$web container already exists. Setting access level to container...")
        except Exception as e:
            logging.info("$web container does not exist. Creating and setting access level to Container...")
            web_container_client.create_container()

        # Set access level to Container (anonymous read access)
        web_container_client.set_container_access_policy(signed_identifiers={}, public_access=PublicAccess.Container)
        logging.info("$web container access level set to Container.")

        # Upload local folder to $web container
        logging.info(f"Uploading files from {local_folder_path} to $web container...")
        for root, dirs, files in os.walk(local_folder_path):
            for file in files:
                file_path = os.path.join(root, file)
                # Preserve folder structure in the container
                blob_path = os.path.relpath(file_path, local_folder_path)
                blob_client = web_container_client.get_blob_client(blob_path)
                content_type = get_content_type(file_path)
                with open(file_path, 'rb') as data:
                    blob_client.upload_blob(
                        data,
                        overwrite=True,
                        content_settings=ContentSettings(content_type=content_type)
                    )
        logging.info("Files uploaded successfully.")

        # Get the phone number from state.json
        phone_number = state.get("phone_number", "1-833-289-0081")  # Default phone number
        
        # Find the website URL for index.html
        website_url = f"https://{storage_account_name}.blob.core.windows.net/$web/index.html?ph0n={phone_number}"
        logging.info(f"Website URL: {website_url}")

        # Delete all storage accounts except the 2 youngest
        delete_except_two_youngest(storage_client, resource_group)

        return website_url

    except Exception as e:
        logging.error(f"An error occurred: {e}")
        return None

if __name__ == "__main__":
    main()
