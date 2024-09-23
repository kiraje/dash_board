import json
import subprocess

# Load the existing azure_config.json
with open('azure_config.json', 'r') as file:
    config = json.load(file)

# Function to execute a shell command and return the output
def run_command(command, capture_output=True):
    print(f"Running command: {command}")
    if capture_output:
        result = subprocess.run(command, capture_output=True, text=True, shell=True)
    else:
        result = subprocess.run(command, shell=True)
    if result.returncode != 0:
        raise Exception(f"Command '{command}' failed with error: {result.stderr}")
    return result.stdout.strip() if capture_output else None

try:
    # Login to Azure using device code for Azure CLI
    print("Logging in to Azure CLI...")
    subprocess.run("az login --use-device-code", shell=True, check=True)
    print("Logged in to Azure CLI successfully.")

    # Get the subscription and tenant details using Azure CLI
    print("Getting subscription and tenant details from Azure CLI...")
    account_details = run_command("az account show")
    print(f"Account details: {account_details}")

    # Parse the account details
    account_details_json = json.loads(account_details)
    subscription_id = account_details_json.get('id')
    tenant_id = account_details_json.get('tenantId')

    print(f"Parsed Subscription ID: {subscription_id}")
    print(f"Parsed Tenant ID: {tenant_id}")

    # Check if subscription ID and tenant ID were retrieved correctly
    if not subscription_id or not tenant_id:
        raise Exception("Failed to retrieve subscription ID or tenant ID from Azure CLI")

    # Update the azure_config.json with the retrieved subscription ID
    config['subscription_id'] = subscription_id

    # Login to Azure using device code for Azure PowerShell with Tenant ID
    print("Logging in to Azure PowerShell...")
    run_command(f"powershell -Command \"Connect-AzAccount -UseDeviceAuthentication -TenantId {tenant_id}\"", capture_output=False)
    print("Logged in to Azure PowerShell successfully.")

    # Get the subscription ID using Azure PowerShell
    print("Getting subscription ID from Azure PowerShell...")
    subscription_id_ps = run_command("powershell -Command \"Get-AzSubscription | Select-Object -ExpandProperty Id\"")
    print(f"Subscription ID (PowerShell): {subscription_id_ps}")

    # Verify that both subscription IDs match
    if subscription_id != subscription_id_ps:
        raise Exception("Subscription IDs from Azure CLI and Azure PowerShell do not match.")

    # Save the updated azure_config.json
    with open('azure_config.json', 'w') as file:
        json.dump(config, file, indent=4)

    print("azure_config.json updated successfully.")
except Exception as e:
    print(f"An error occurred: {e}")
