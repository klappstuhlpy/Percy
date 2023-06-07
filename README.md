# Percy Project

This project is a Discord bot written in Python using the discord.py library. The bot is designed to perform various tasks and enhance the functionality of Discord servers.

## Prerequisites

Before running the bot, make sure you have the following installed:

- Python >=3.9: [Download Python](https://www.python.org/downloads/)
- PostgreSQL: [Download PostgreSQL](https://www.postgresql.org/download/)

## Installation

1. **Clone the repository:**

```bash
git clone https://github.com/klappstuhlpy/Percy.git
```

2. **Install the required Python packages:**

```bash
pip install -r requirements.txt
```

3. **Create a PostgreSQL database for the bot:**

- Launch the PostgreSQL command-line interface.
- Run the following command to create a new database:

```sql
CREATE ROLE percy WITH LOGIN PASSWORD 'password';
CREATE DATABASE percy OWNER percy;
CREATE EXTENSION pg_trgm;
```

3.5 **Configuration of database**

To configure the PostgreSQL database for use by the bot, go to the directory where `launcher.py` is located, and run the script by doing `python3.8 launcher.py db init`

4**Configure the bot:**

- Setup a ``config.py`` File:

```py
client_id = ''  # Your Bots Client ID
token = ''  # Your Bots Token
debug = False
github_key = ''  # GitHub Gist creation
postgresql = ''  # Base Postgres
alchemy_postgresql = ''  # For ORM work
stat_webhook = ('', '')  # ID, Code

class anilist:  # AniList Configuration
    client_id: int = 0
    client_secret: str = ''
    redirect_uri: str = 'https://anilist.co/api/v2/oauth/pin'

class marvel:  # Marvel Configuration
    public_key: str = ''
    private_key: str = ''
```

## License

This project is licensed under the MPL License. See the LICENSE file for details.
This Project utilizes Code from [R. Danny](https://github.com/Rapptz/RoboDanny)
