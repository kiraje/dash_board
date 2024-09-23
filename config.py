import json
import os

def read_config(file_path):
    if not os.path.exists(file_path):
        return {}
    with open(file_path, 'r') as config_file:
        return json.load(config_file)

def write_config(file_path, config):
    with open(file_path, 'w') as config_file:
        json.dump(config, config_file, indent=4)

def initialize_configs(*file_paths):
    for file_path in file_paths:
        if not os.path.exists(file_path):
            write_config(file_path, {})
