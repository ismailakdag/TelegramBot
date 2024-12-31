# Visa Appointment Tracker

A GUI application to track visa appointments for various countries. The application monitors appointment availability and sends notifications via Telegram.

## Features

- Real-time appointment monitoring
- Telegram notifications for new appointments
- GUI interface for easy configuration
- Multi-threaded design for responsive UI
- Tracks changes in appointment availability
- Shows waiting list changes
- Supports multiple visa centers

## Installation

1. Clone the repository:
```bash
git clone https://github.com/yourusername/visa-appointment-tracker.git
cd visa-appointment-tracker
```

2. Create a virtual environment (recommended):
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install the required packages:
```bash
pip install -r requirements.txt
```

4. Configure your Telegram bot:
   - Create a new bot with [@BotFather](https://t.me/botfather) on Telegram
   - Get your bot token
   - Update the `settings.json` file with your bot token and chat ID

## Configuration

1. Create or edit `settings.json`:
```json
{
    "bot_token": "YOUR_BOT_TOKEN",
    "chat_id": "YOUR_CHAT_ID",
    "source_country": "Turkiye",
    "mission_country": "Netherlands",
    "check_interval": 60,
    "send_all_updates": true,
    "last_check": null
}
```

2. Make sure `scan_history.json` exists (will be created automatically if not present)

## Usage

1. Run the application:
```bash
python visa_tracker.py
```

2. In the GUI:
   - Select source and destination countries
   - Set check interval (in seconds)
   - Choose notification preferences
   - Click "Start Tracking"

## Error Handling

The application includes comprehensive error handling:
- Logs errors to `visa_tracker.log`
- Shows error messages in GUI
- Sends error notifications via Telegram
- Automatically recovers from network issues

## Notes

- The application uses thread-safe operations for file access
- GUI remains responsive during appointment checks
- All times are converted to local timezone
- Appointment history is preserved between sessions

## Requirements

- Python 3.7 or higher
- See `requirements.txt` for package dependencies

## License

This project is licensed under the MIT License - see the LICENSE file for details.
