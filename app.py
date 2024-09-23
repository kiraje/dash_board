import os
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from datetime import datetime, timedelta
import threading
import logging
from logging.handlers import RotatingFileHandler
import traceback
from config import read_config, write_config, initialize_configs
from safebrowsing import initialize_safebrowsing, check_url_safe_browsing
from deploy import deploy_new_url
from keitaro import update_keitaro_flow, get_keitaro_campaigns, get_keitaro_streams, get_all_campaigns_and_streams
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
import atexit
from functools import wraps
import tailer
import json
import time
import pytz

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'your_secure_random_secret_key')

# Configure logging
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
log_file = os.path.join('/app/logs', 'application.log')
log_handler = RotatingFileHandler(log_file, maxBytes=1024 * 1024 * 100, backupCount=20)
log_handler.setFormatter(log_formatter)
log_handler.setLevel(logging.DEBUG)
app.logger.addHandler(log_handler)
app.logger.setLevel(logging.DEBUG)

# Error handling middleware
@app.errorhandler(Exception)
def handle_exception(e):
    app.logger.error(f"Unhandled exception: {str(e)}")
    app.logger.error(traceback.format_exc())
    return "An unexpected error occurred. Please check the logs for more information.", 500

# Health check endpoint
@app.route('/health')
def health_check():
    return "OK", 200

# Initialize configurations
azure_config_file_path = "azure_config.json"
keitaro_config_file_path = "keitaro_config.json"
state_file_path = "state.json"
current_urls_file_path = "current_urls.json"
service_principal_file_path = "service_principal.json"

azure_config = read_config(azure_config_file_path)
keitaro_config = read_config(keitaro_config_file_path)
initialize_configs(state_file_path, current_urls_file_path)

safe_browsing_api_key = azure_config.get("safe_browsing_api_key")

# Read base directory from config
base_repo_directory = azure_config.get("base_repo_directory", "C:/default/path")

# Read initial state
state = read_config(state_file_path)

app_state = {
    'run': False,
    'deploy_lock': threading.Lock(),
    'last_deploy_time': datetime.now(),
    'max_deploy_time_minutes': state.get('max_deploy_time_minutes', 30),
    'deploy_interval_seconds': state.get('deploy_interval_seconds', 120),
    'log_retention_days': state.get('log_retention_days', 7)
}

# Initialize Google Safe Browsing API
try:
    if not safe_browsing_api_key:
        raise ValueError("Safe Browsing API key is missing")
    safebrowsing = initialize_safebrowsing(safe_browsing_api_key)
    app.logger.info("Safe Browsing API initialized successfully")
except Exception as e:
    app.logger.error(f"Failed to initialize Safe Browsing API: {str(e)}")
    safebrowsing = None

def update_url_with_phone_number(url, phone_number):
    parsed_url = urlparse(url)
    query_params = parse_qs(parsed_url.query)
    query_params['ph0n'] = [phone_number]
    new_query = urlencode(query_params, doseq=True)
    return urlunparse(parsed_url._replace(query=new_query))

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def safe_browsing_check(url, max_retries=3, retry_delay=5):
    if not safebrowsing:
        app.logger.error("Safe Browsing API is not initialized")
        return False

    for attempt in range(max_retries):
        try:
            is_safe = check_url_safe_browsing(safebrowsing, url)
            if is_safe:
                app.logger.info(f"URL passed Safe Browsing check: {url}")
            else:
                app.logger.warning(f"URL failed Safe Browsing check: {url}")
            return is_safe
        except Exception as e:
            app.logger.error(f"Safe Browsing check failed (attempt {attempt + 1}/{max_retries}): {str(e)}")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
    
    app.logger.error(f"Safe Browsing check failed after {max_retries} attempts")
    return False

# New system-wide function for log deletion
def delete_old_logs(retention_days):
    current_time = datetime.now()
    log_files = [
        'application.log',
        'azure_storage_deployment.log',
        'safebrowsing.log',
        'keitaro.log',
        'deployment.log'
    ]
    
    deleted_files = []
    for log_file in log_files:
        log_path = os.path.join('/app/logs', log_file)
        if os.path.exists(log_path):
            file_modified_time = datetime.fromtimestamp(os.path.getmtime(log_path))
            if (current_time - file_modified_time) > timedelta(days=retention_days):
                try:
                    os.remove(log_path)
                    deleted_files.append(log_file)
                    app.logger.info(f"Deleted old log file: {log_file}")
                except Exception as e:
                    app.logger.error(f"Failed to delete log file {log_file}: {str(e)}")
    
    return deleted_files

# Function to get log file information
def get_log_file_info():
    log_files = [
        'application.log',
        'azure_storage_deployment.log',
        'safebrowsing.log',
        'keitaro.log',
        'deployment.log'
    ]
    
    file_info = []
    for log_file in log_files:
        log_path = os.path.join('/app/logs', log_file)
        try:
            if os.path.exists(log_path):
                size = os.path.getsize(log_path)
                modified_time = datetime.fromtimestamp(os.path.getmtime(log_path))
                file_info.append({
                    'name': log_file,
                    'size': f"{size / 1024:.2f} KB",
                    'last_modified': modified_time.strftime('%Y-%m-%d %H:%M:%S')
                })
            else:
                file_info.append({
                    'name': log_file,
                    'size': 'N/A',
                    'last_modified': 'File not found'
                })
        except Exception as e:
            app.logger.error(f"Error getting info for log file {log_file}: {str(e)}")
            file_info.append({
                'name': log_file,
                'size': 'Error',
                'last_modified': 'Error'
            })
    
    return file_info

# ------------------------ Routes ------------------------

@app.route('/login', methods=['GET', 'POST'])
def login():
    try:
        app.logger.debug("Entering login route")
        if request.method == 'POST':
            app.logger.debug("Login attempt - POST request received")
            password = request.form.get('password')
            app.logger.debug(f"Received password: {'*' * len(password)}")  # Log password length for debugging
            if password == os.environ.get('LOGIN_PASSWORD', 'your_password'):  # Use environment variable for password
                session['logged_in'] = True
                app.logger.info("Login successful")
                flash('Logged in successfully.', 'success')
                return redirect(url_for('home'))
            else:
                app.logger.warning("Login failed - Invalid password")
                flash('Invalid password.', 'danger')
                return redirect(url_for('login'))
        app.logger.debug("Rendering login template")
        return render_template('login.html')
    except Exception as e:
        app.logger.error(f"Error in login route: {str(e)}")
        app.logger.error(traceback.format_exc())
        return "An error occurred during login. Please check the logs for more information.", 500

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    flash('Logged out successfully.', 'success')
    return redirect(url_for('login'))

@app.route('/')
@login_required
def home():
    state = read_config(state_file_path)
    phone_number = state.get('phone_number', '')
    current_folder = state.get('current_folder', 'Not set')
    current_folder_name = os.path.basename(current_folder) if current_folder != 'Not set' else 'Not set'
    max_deploy_time_minutes = state.get('max_deploy_time_minutes', 30)
    deploy_interval_seconds = state.get('deploy_interval_seconds', 120)
    log_retention_days = state.get('log_retention_days', 7)

    current_urls = read_config(current_urls_file_path)
    current_url = current_urls.get('current_url', 'Not set')
    next_url = current_urls.get('next_url', 'Not set')
    run_status = app_state.get('run', False)

    folders = [
        name for name in os.listdir(base_repo_directory)
        if os.path.isdir(os.path.join(base_repo_directory, name))
    ]

    last_deploy_time = app_state.get('last_deploy_time', 'Never')
    if isinstance(last_deploy_time, datetime):
        last_deploy_time_str = last_deploy_time.strftime('%Y-%m-%d %H:%M:%S')
    else:
        last_deploy_time_str = 'Never'

    # Calculate time remaining until next deployment (timezone-aware calculation)
    if run_status and isinstance(last_deploy_time, datetime):
        last_deploy_time = last_deploy_time.replace(tzinfo=pytz.UTC)
        next_deploy_time = last_deploy_time + timedelta(minutes=max_deploy_time_minutes)
        current_time = datetime.now(pytz.UTC)  # Ensure both datetimes are timezone-aware
        time_remaining = next_deploy_time - current_time

        # Ensure time_remaining is not negative
        if time_remaining.total_seconds() < 0:
            time_remaining = timedelta(seconds=0)
    else:
        time_remaining = None

    # Read the last 10 lines from the log file
    try:
        with open(os.path.join('/app/logs', 'application.log'), 'r') as f:
            recent_logs = tailer.tail(f, 10)
    except Exception as e:
        recent_logs = [f"Error reading log file: {e}"]

    keitaro_campaign_id = state.get('keitaro_campaign_id', '')
    keitaro_stream_id = state.get('keitaro_stream_id', '')

    return render_template('home.html',
                           phone_number=phone_number,
                           current_folder=current_folder,
                           current_folder_name=current_folder_name,
                           current_url=current_url,
                           next_url=next_url,
                           run_status=run_status,
                           folders=folders,
                           last_deploy_time=last_deploy_time_str,
                           recent_logs=recent_logs,
                           max_deploy_time_minutes=max_deploy_time_minutes,
                           deploy_interval_seconds=deploy_interval_seconds,
                           time_remaining=time_remaining,
                           keitaro_campaign_id=keitaro_campaign_id,
                           keitaro_stream_id=keitaro_stream_id,
                           log_retention_days=log_retention_days)

@app.route('/start', methods=['POST'])
@login_required
def start():
    with app_state['deploy_lock']:
        state = read_config(state_file_path)
        phone_number = state.get('phone_number')
        current_folder = state.get('current_folder')
        keitaro_campaign_id = state.get('keitaro_campaign_id')
        keitaro_stream_id = state.get('keitaro_stream_id')

        if not phone_number or phone_number == '':
            flash('Please set the phone number before starting deployment.', 'danger')
            return redirect(url_for('home'))

        if not current_folder or current_folder == 'Not set':
            flash('Please set the current folder before starting deployment.', 'danger')
            return redirect(url_for('home'))

        if not keitaro_campaign_id or not keitaro_stream_id:
            flash('Please set the Keitaro Campaign ID and Stream ID before starting deployment.', 'danger')
            return redirect(url_for('home'))

        # Start the deployment workflow immediately
        app.logger.info('Manual deployment started by user.')

        # Deploy to Azure
        new_url = deploy_new_url()
        if new_url is None:
            app.logger.error("Failed to deploy new URL")
            flash('Deployment failed. Check logs for details.', 'danger')
            return redirect(url_for('home'))

        # Check if the new URL is safe
        if not safe_browsing_check(new_url):
            flash('Deployment failed. The new URL did not pass the Safe Browsing check.', 'danger')
            return redirect(url_for('home'))

        # Update Keitaro flow with the new URL
        update_result = update_keitaro_flow(new_url, keitaro_campaign_id, keitaro_stream_id)
        if update_result and update_result.status_code == 200:
            app.logger.info(f"Updated Keitaro flow to use new URL: {new_url}")
        else:
            app.logger.error("Failed to update Keitaro flow")
            flash('Failed to update Keitaro flow. Check logs for details.', 'danger')
            return redirect(url_for('home'))

        # Update current_urls.json
        current_urls = read_config(current_urls_file_path)
        current_urls['current_url'] = new_url
        write_config(current_urls_file_path, current_urls)

        # Reset last deploy time
        app_state['last_deploy_time'] = datetime.now()
        app_state['run'] = True  # Start automatic deployments
        app.logger.info('Manual deployment completed successfully.')

        flash('Deployment completed successfully!', 'success')
        return redirect(url_for('home'))

@app.route('/stop', methods=['POST'])
@login_required
def stop():
    app_state['run'] = False
    app.logger.info('Deployment stopped by user.')
    flash('Deployment stopped.', 'success')
    return redirect(url_for('home'))

@app.route('/set_phone_number', methods=['POST'])
@login_required
def set_phone_number():
    phone_number = request.form.get('phone_number')
    if not phone_number:
        flash('No phone number provided.', 'danger')
        return redirect(url_for('home'))

    state = read_config(state_file_path)
    state['phone_number'] = phone_number
    write_config(state_file_path, state)

    # Update current_url with new phone number if it exists
    current_urls = read_config(current_urls_file_path)
    current_url = current_urls.get('current_url')
    if current_url:
        updated_url = update_url_with_phone_number(current_url, phone_number)
        current_urls['current_url'] = updated_url
        write_config(current_urls_file_path, current_urls)
        update_keitaro_flow(updated_url, state.get('keitaro_campaign_id'), state.get('keitaro_stream_id'))
        app.logger.info(f"Updated current URL with new phone number: {updated_url}")

    app.logger.info(f"Phone number set to: {phone_number}")
    flash('Phone number updated successfully!', 'success')
    return redirect(url_for('home'))

@app.route('/set_current_folder', methods=['POST'])
@login_required
def set_current_folder():
    folder_name = request.form.get('folder_name')
    if not folder_name:
        flash('No folder name provided.', 'danger')
        return redirect(url_for('home'))

    folder_path = os.path.join(base_repo_directory, folder_name)
    if not os.path.isdir(folder_path):
        flash('Selected folder does not exist.', 'danger')
        return redirect(url_for('home'))

    state = read_config(state_file_path)
    state['current_folder'] = folder_path
    write_config(state_file_path, state)

    app.logger.info(f"Current folder set to: {folder_name}")
    flash(f"Current folder set to: {folder_name}", 'success')
    return redirect(url_for('home'))

@app.route('/set_redeploy_interval', methods=['POST'])
@login_required
def set_redeploy_interval():
    max_deploy_time_minutes = request.form.get('max_deploy_time_minutes')
    if not max_deploy_time_minutes:
        flash('No redeploy interval provided.', 'danger')
        return redirect(url_for('home'))

    try:
        max_deploy_time_minutes = int(max_deploy_time_minutes)
        if max_deploy_time_minutes <= 0:
            raise ValueError
    except ValueError:
        flash('Invalid redeploy interval. Please enter a positive integer.', 'danger')
        return redirect(url_for('home'))

    deploy_interval_seconds = request.form.get('deploy_interval_seconds')
    if not deploy_interval_seconds:
        flash('No check interval provided.', 'danger')
        return redirect(url_for('home'))

    try:
        deploy_interval_seconds = int(deploy_interval_seconds)
        if deploy_interval_seconds < 10:
            raise ValueError
    except ValueError:
        flash('Invalid check interval. Please enter an integer greater than or equal to 10.', 'danger')
        return redirect(url_for('home'))

    state = read_config(state_file_path)
    state['max_deploy_time_minutes'] = max_deploy_time_minutes
    state['deploy_interval_seconds'] = deploy_interval_seconds
    write_config(state_file_path, state)

    # Update the app_state
    app_state['max_deploy_time_minutes'] = max_deploy_time_minutes
    app_state['deploy_interval_seconds'] = deploy_interval_seconds

    # Update the scheduler
    reschedule_job()

    flash(f"Redeploy interval set to {max_deploy_time_minutes} minutes.", 'success')
    flash(f"Check interval set to {deploy_interval_seconds} seconds.", 'success')
    return redirect(url_for('home'))

@app.route('/set_keitaro_ids', methods=['POST'])
@login_required
def set_keitaro_ids():
    campaign_id = request.form.get('keitaro_campaign_id')
    stream_id = request.form.get('keitaro_stream_id')

    if not campaign_id or not stream_id:
        flash('Please provide both Campaign ID and Stream ID.', 'danger')
        return redirect(url_for('home'))

    state = read_config(state_file_path)
    state['keitaro_campaign_id'] = campaign_id
    state['keitaro_stream_id'] = stream_id
    write_config(state_file_path, state)

    app.logger.info(f"Keitaro Campaign ID set to: {campaign_id}, Stream ID set to: {stream_id}")
    flash('Keitaro Campaign ID and Stream ID updated successfully!', 'success')
    return redirect(url_for('home'))

@app.route('/list_folders')
@login_required
def list_folders():
    folders = [
        name for name in os.listdir(base_repo_directory)
        if os.path.isdir(os.path.join(base_repo_directory, name))
    ]
    return render_template('folders.html', folders=folders)

@app.route('/view_logs')
@login_required
def view_logs():
    try:
        # Read the last 50 lines from the log file
        with open('application.log', 'r') as f:
            log_lines = tailer.tail(f, 50)
    except Exception as e:
        log_lines = [f"Error reading log file: {e}"]

    return render_template('view_logs.html', log_lines=log_lines)

@app.route('/keitaro_settings', methods=['GET', 'POST'])
@login_required
def keitaro_settings():
    if request.method == 'POST':
        # Update Keitaro settings
        keitaro_api_key = request.form.get('keitaro_api_key')
        keitaro_host = request.form.get('keitaro_host')

        keitaro_config['keitaro_api_key'] = keitaro_api_key
        keitaro_config['keitaro_host'] = keitaro_host
        write_config(keitaro_config_file_path, keitaro_config)

        flash('Keitaro settings updated successfully!', 'success')
        return redirect(url_for('home'))

    # GET request: display current Keitaro settings
    keitaro_api_key = keitaro_config.get('keitaro_api_key', '')
    keitaro_host = keitaro_config.get('keitaro_host', '')

    # Fetch campaigns and streams
    campaigns = get_keitaro_campaigns(keitaro_api_key, keitaro_host)
    state = read_config(state_file_path)
    streams = get_keitaro_streams(keitaro_api_key, keitaro_host, state.get('keitaro_campaign_id', ''))

    return render_template('keitaro_settings.html',
                           keitaro_api_key=keitaro_api_key,
                           keitaro_host=keitaro_host,
                           campaigns=campaigns,
                           streams=streams)

@app.route('/get_campaigns_and_streams')
@login_required
def get_campaigns_and_streams():
    keitaro_api_key = keitaro_config.get('keitaro_api_key', '')
    keitaro_host = keitaro_config.get('keitaro_host', '')
    
    campaigns_and_streams = get_all_campaigns_and_streams(keitaro_host, keitaro_api_key)
    return jsonify(campaigns_and_streams)

# Updated route for log management
@app.route('/log_management', methods=['GET', 'POST'])
@login_required
def log_management():
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'delete_logs':
            deleted_files = delete_old_logs(app_state['log_retention_days'])
            if deleted_files:
                flash(f"Deleted old log files: {', '.join(deleted_files)}", 'success')
            else:
                flash("No old log files to delete.", 'info')
        
        elif action == 'set_retention':
            retention_days = request.form.get('log_retention_days')
            try:
                retention_days = int(retention_days)
                if retention_days <= 0:
                    raise ValueError
                
                state = read_config(state_file_path)
                state['log_retention_days'] = retention_days
                write_config(state_file_path, state)
                app_state['log_retention_days'] = retention_days
                
                flash(f"Log retention period set to {retention_days} days.", 'success')
            except ValueError:
                flash('Invalid log retention period. Please enter a positive integer.', 'danger')
    
    log_files = get_log_file_info()
    return render_template('log_management.html', log_retention_days=app_state['log_retention_days'], log_files=log_files)
# ------------------------ Deployment Logic ------------------------

def deploy_and_check():
    with app_state['deploy_lock']:
        if app_state.get('run', False):
            try:
                current_time = datetime.now(pytz.UTC)
                deploy_needed = False

                current_urls = read_config(current_urls_file_path)
                current_url = current_urls.get('current_url')

                if not current_url:
                    # No current URL, deploy one
                    deploy_needed = True
                    app.logger.info("No current URL found. Deployment needed.")
                else:
                    # Check if redeploy is needed
                    last_deploy_time = app_state['last_deploy_time'].replace(tzinfo=pytz.UTC)
                    time_since_last_deploy = current_time - last_deploy_time
                    if time_since_last_deploy >= timedelta(minutes=app_state['max_deploy_time_minutes']):
                        deploy_needed = True
                        app.logger.info(f"{app_state['max_deploy_time_minutes']} minutes passed since last deployment.")

                if deploy_needed:
                    state = read_config(state_file_path)
                    phone_number = state.get('phone_number')
                    current_folder = state.get('current_folder')
                    keitaro_campaign_id = state.get('keitaro_campaign_id')
                    keitaro_stream_id = state.get('keitaro_stream_id')

                    if not phone_number or phone_number == '':
                        app.logger.warning('Phone number not set. Skipping deployment.')
                        return

                    if not current_folder or current_folder == 'Not set':
                        app.logger.warning('Current folder not set. Skipping deployment.')
                        return

                    if not keitaro_campaign_id or not keitaro_stream_id:
                        app.logger.warning('Keitaro Campaign ID or Stream ID not set. Skipping deployment.')
                        return

                    # Deploy to Azure
                    new_url = deploy_new_url()
                    if new_url is None:
                        app.logger.error("Failed to deploy new URL")
                        return

                    # Check if the new URL is safe
                    if not safe_browsing_check(new_url):
                        return

                    # Update Keitaro flow with the new URL
                    update_result = update_keitaro_flow(new_url, keitaro_campaign_id, keitaro_stream_id)
                    if update_result and update_result.status_code == 200:
                        app.logger.info(f"Updated Keitaro flow to use new URL: {new_url}")
                    else:
                        app.logger.error("Failed to update Keitaro flow")
                        return

                    # Update current_urls.json
                    current_urls['current_url'] = new_url
                    write_config(current_urls_file_path, current_urls)

                    # Update last deploy time
                    app_state['last_deploy_time'] = current_time
                    app.logger.info('Scheduled deployment completed successfully.')

                # Automatically delete old logs
                delete_old_logs(app_state['log_retention_days'])

            except Exception as e:
                app.logger.exception("An error occurred during deploy_and_check")

def reschedule_job():
    scheduler.remove_job('deploy_and_check_job')
    scheduler.add_job(func=deploy_and_check,
                      trigger=IntervalTrigger(seconds=app_state['deploy_interval_seconds']),
                      id='deploy_and_check_job')

# Scheduler setup
scheduler = BackgroundScheduler(timezone=pytz.UTC)
scheduler.add_job(func=deploy_and_check,
                  trigger=IntervalTrigger(seconds=app_state['deploy_interval_seconds']),
                  id='deploy_and_check_job')
scheduler.start()

# Shut down the scheduler when exiting the app
atexit.register(lambda: scheduler.shutdown())

# New route for displaying configurations
@app.route('/configs')
# Updated route for displaying and editing configurations
@app.route('/configs', methods=['GET', 'POST'])
@login_required
def configs():
    if request.method == 'POST':
        config_type = request.form.get('config_type')
        if config_type == 'azure':
            config = read_config(azure_config_file_path)
            for key in config.keys():
                if key in request.form:
                    config[key] = request.form[key]
            write_config(azure_config_file_path, config)
            flash('Azure configuration updated successfully!', 'success')
        elif config_type == 'service_principal':
            config = read_config(service_principal_file_path)
            for key in config.keys():
                if key in request.form:
                    config[key] = request.form[key]
            write_config(service_principal_file_path, config)
            flash('Service Principal configuration updated successfully!', 'success')
        return redirect(url_for('configs'))

    azure_config = read_config(azure_config_file_path)
    service_principal = read_config(service_principal_file_path)
    
    # Remove sensitive information for display
    if 'client_secret' in service_principal:
        service_principal['client_secret'] = '********'
    
    return render_template('configs.html', 
                           azure_config=azure_config, 
                           service_principal=service_principal)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)  # Set debug=False for production
