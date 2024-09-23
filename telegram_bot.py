import logging
import asyncio
import os
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import Application, CommandHandler, CallbackContext
from config import read_config, write_config, initialize_configs
from safebrowsing import initialize_safebrowsing, check_url_safe_browsing
from deploy import deploy_new_url
from keitaro import update_keitaro_flow  # Changed from adspect to keitaro
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from logging.handlers import RotatingFileHandler

# Configure logging with rotation
handler = RotatingFileHandler('telegram_bot.log', maxBytes=1000000, backupCount=5)
logging.basicConfig(
    handlers=[handler],
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

deploy_lock = asyncio.Lock()

# Load configuration
config_file_path = "azure_config.json"
state_file_path = "state.json"
current_urls_file_path = "current_urls.json"

config = read_config(config_file_path)
initialize_configs(state_file_path, current_urls_file_path)

telegram_api_key = config["telegram_api_key"]
keitaro_api_key = config["keitaro_api_key"]  # Changed from adspect_api_key
safe_browsing_api_key = config["safe_browsing_api_key"]
flow_id = config["flow_id"]  # Changed from stream_id

# Read base directory and intervals from config
base_repo_directory = config.get("base_repo_directory", "C:/default/path")
deploy_interval_seconds = config.get("deploy_interval_seconds", 20)
max_deploy_time_minutes = config.get("max_deploy_time_minutes", 30)

# Initialize Google Safe Browsing API
safebrowsing = initialize_safebrowsing(safe_browsing_api_key)


def update_url_with_phone_number(url, phone_number):
    """
    Safely updates the given URL with the phone number using urllib.parse.

    Args:
        url (str): The original URL.
        phone_number (str): The phone number to add to the URL.

    Returns:
        str: The updated URL with the phone number.
    """
    parsed_url = urlparse(url)
    query_params = parse_qs(parsed_url.query)
    query_params['ph0n'] = [phone_number]
    new_query = urlencode(query_params, doseq=True)
    return urlunparse(parsed_url._replace(query=new_query))


async def start(update: Update, context: CallbackContext) -> None:
    """
    Starts the bot and initializes the deployment cycle.

    Args:
        update (Update): The update from Telegram.
        context (CallbackContext): The context from Telegram.
    """
    context.chat_data['run'] = True
    context.chat_data['chat_id'] = update.effective_chat.id

    # Read current URLs from file
    current_urls = read_config(current_urls_file_path)
    current_url = current_urls.get("current_url", "")
    next_url = current_urls.get("next_url", "")
    context.chat_data['last_deploy_time'] = datetime.now()

    if next_url:
        # Update Keitaro flow with the next URL
        update_keitaro_flow(next_url)
        current_url = next_url
        await update.message.reply_text(
            f"Updated Keitaro flow with next URL and set it as current URL:\nCurrent URL: {current_url}"
        )

        # Deploy a new URL and set it as next_url
        new_url = deploy_new_url()
        next_url = new_url
        await update.message.reply_text(f"Deployed new URL:\nNext URL: {next_url}")

        # Save the updated URLs to file
        current_urls["current_url"] = current_url
        current_urls["next_url"] = next_url
        write_config(current_urls_file_path, current_urls)
    else:
        # Deploy initial URLs if not found
        current_url = deploy_new_url()
        update_keitaro_flow(current_url)
        context.chat_data['last_deploy_time'] = datetime.now()

        next_url = deploy_new_url()

        await update.message.reply_text(
            f"Deployed initial URLs.\nCurrent URL: {current_url}\nNext URL: {next_url}"
        )

        # Save the initial URLs to file
        current_urls = {"current_url": current_url, "next_url": next_url}
        write_config(current_urls_file_path, current_urls)

    # Set current and next URL in chat data
    context.chat_data['current_url'] = current_url
    context.chat_data['next_url'] = next_url

    # Immediately start the Safe Browsing check
    await deploy_and_check(context)

    # Start the deployment and monitoring cycle
    context.job_queue.run_repeating(
        deploy_and_check,
        interval=deploy_interval_seconds,
        first=0,
        context=update.effective_chat.id
    )


async def stop(update: Update, context: CallbackContext) -> None:
    """
    Stops the bot's deployment cycle.

    Args:
        update (Update): The update from Telegram.
        context (CallbackContext): The context from Telegram.
    """
    await update.message.reply_text('Bot stopped!')
    context.chat_data['run'] = False


async def list_folders(update: Update, context: CallbackContext) -> None:
    """
    Lists the folders in the base repository directory.

    Args:
        update (Update): The update from Telegram.
        context (CallbackContext): The context from Telegram.
    """
    folders = [
        name for name in os.listdir(base_repo_directory)
        if os.path.isdir(os.path.join(base_repo_directory, name))
    ]
    if folders:
        folder_list = "\n".join(folders)
        await update.message.reply_text(f"Available folders:\n{folder_list}")
    else:
        await update.message.reply_text("No folders found.")


async def set_current_folder(update: Update, context: CallbackContext) -> None:
    """
    Sets the current folder for deployment.

    Args:
        update (Update): The update from Telegram.
        context (CallbackContext): The context from Telegram.
    """
    try:
        folder_name = " ".join(context.args)
        if not folder_name:
            raise ValueError("No folder name provided")

        folder_path = os.path.join(base_repo_directory, folder_name)
        if not os.path.isdir(folder_path):
            raise ValueError("Folder does not exist")

        state = read_config(state_file_path)
        state['current_folder'] = folder_path
        write_config(state_file_path, state)

        await update.message.reply_text(f"Current folder set to: {folder_path}")
    except (IndexError, ValueError) as e:
        await update.message.reply_text(
            f"Usage: /set_current_folder <folder_name>\nError: {str(e)}"
        )


async def deploy_and_check(context: CallbackContext) -> None:
    """
    Deploys new URLs and checks existing ones periodically.

    Args:
        context (CallbackContext): The context from Telegram.
    """
    async with deploy_lock:
        chat_id = context.job.context
        chat_data = context.application.chat_data.get(chat_id, {})
        if chat_data.get('run', True):
            try:
                current_time = datetime.now()
                deploy_needed = False

                if not chat_data.get('current_url'):
                    # Deploy initial URLs
                    url_a = deploy_new_url()
                    update_keitaro_flow(url_a)
                    chat_data['current_url'] = url_a
                    chat_data['last_deploy_time'] = current_time

                    url_b = deploy_new_url()
                    chat_data['next_url'] = url_b

                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=f"Deployed initial URLs.\nUrl A: {url_a}\nUrl B: {url_b}"
                    )

                    current_urls = {"current_url": url_a, "next_url": url_b}
                    write_config(current_urls_file_path, current_urls)
                    logging.info(f"Updated current_urls.json: {current_urls}")
                else:
                    current_url = chat_data['current_url']
                    if not check_url_safe_browsing(safebrowsing, current_url):
                        deploy_needed = True
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=f"URL flagged: {current_url}"
                        )

                    if current_time - chat_data['last_deploy_time'] >= timedelta(minutes=max_deploy_time_minutes):
                        deploy_needed = True
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=f"{max_deploy_time_minutes} minutes passed since last deployment."
                        )

                    if deploy_needed:
                        next_url = chat_data['next_url']
                        update_keitaro_flow(next_url)
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=f"Updated Keitaro flow to use Url B: {next_url}"
                        )

                        new_url = deploy_new_url()
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=f"Deployed new URL: {new_url}"
                        )

                        chat_data['current_url'] = next_url
                        chat_data['next_url'] = new_url
                        chat_data['last_deploy_time'] = current_time

                        current_urls = {
                            "current_url": chat_data['current_url'],
                            "next_url": chat_data['next_url']
                        }
                        write_config(current_urls_file_path, current_urls)
                        logging.info(f"Updated current_urls.json: {current_urls}")

                # Update chat_data back to application chat_data
                context.application.chat_data[chat_id] = chat_data

            except Exception as e:
                logging.exception("An error occurred during deploy_and_check")


async def set_phone_number(update: Update, context: CallbackContext) -> None:
    """
    Sets the phone number and updates URLs accordingly.

    Args:
        update (Update): The update from Telegram.
        context (CallbackContext): The context from Telegram.
    """
    try:
        phone_number = " ".join(context.args)
        if not phone_number:
            raise ValueError("No phone number provided")

        state = read_config(state_file_path)
        state['phone_number'] = phone_number
        write_config(state_file_path, state)

        chat_data = context.chat_data
        updated_current_url = ""
        updated_next_url = ""

        if chat_data.get('current_url'):
            updated_current_url = update_url_with_phone_number(
                chat_data['current_url'], phone_number
            )
            chat_data['current_url'] = updated_current_url
            update_keitaro_flow(updated_current_url)

        if chat_data.get('next_url'):
            updated_next_url = update_url_with_phone_number(
                chat_data['next_url'], phone_number
            )
            chat_data['next_url'] = updated_next_url

        current_urls = {
            "current_url": chat_data.get('current_url', ""),
            "next_url": chat_data.get('next_url', "")
        }
        write_config(current_urls_file_path, current_urls)
        logging.info(f"Updated current_urls.json with phone number: {current_urls}")

        await update.message.reply_text(
            f"Phone number set to: {phone_number}\n"
            f"Updated Current URL: {updated_current_url}\n"
            f"Updated Next URL: {updated_next_url}"
        )
    except (IndexError, ValueError) as e:
        await update.message.reply_text(
            f"Usage: /set_phone_number <phone_number>\nError: {str(e)}"
        )
    except Exception as e:
        logging.exception("An error occurred in set_phone_number")
        await update.message.reply_text(f"An unexpected error occurred: {str(e)}")


async def status(update: Update, context: CallbackContext) -> None:
    """
    Retrieves the current status of the bot.

    Args:
        update (Update): The update from Telegram.
        context (CallbackContext): The context from Telegram.
    """
    state = read_config(state_file_path)
    phone_number = state.get('phone_number', 'Not set')
    current_folder = state.get('current_folder', 'Not set')
    await update.message.reply_text(
        f"Current phone number: {phone_number}\nCurrent folder: {current_folder}"
    )


def main() -> None:
    """
    The main function that starts the Telegram bot.
    """
    application = Application.builder().token(telegram_api_key).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stop", stop))
    application.add_handler(CommandHandler("set_phone_number", set_phone_number))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("list_folders", list_folders))
    application.add_handler(CommandHandler("set_current_folder", set_current_folder))

    application.run_polling()


if __name__ == "__main__":
    main()
