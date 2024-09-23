from keitaro import list_all_campaigns, read_config

def main():
    config = read_config()
    keitaro_host = config['keitaro_host']
    api_key = config['keitaro_api_key']
    list_all_campaigns(keitaro_host, api_key)

if __name__ == "__main__":
    main()