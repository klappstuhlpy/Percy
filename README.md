# Percy Project

# !! This Project is outdated and currently getting rewritten. Once I've finished, the new Code will be published to a new Repository !!

This project is a Discord bot written in Python using the discord.py library. The bot is designed to perform various tasks and enhance the functionality of Discord servers.

I would prefer if you not run an instance of my bot, just invite Percy to your Discord but clicking [this](https://discord.com/api/oauth2/authorize?client_id=1070054930125176923&permissions=1480988813527&scope=bot%20applications.commands) link. :)

## Prerequisites

Before running the bot, make sure you have the following installed:

- Python >=3.12: [Download Python](https://www.python.org/downloads/)
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

- Set up a ``config.py`` File:

```py
from types import SimpleNamespace

client_id = ''  # Your Bots Client ID
token = ''  # Your Bots Token

debug = False

mystbin_key = ''  # Mystbin API Key
wolfram_api_key = ''  # Wolfram Alpha API Key
github_key = ''  # GitHub Gist creation
dbots_key = ''  # Discord Bots API Key
stability_key = ''  # Stability API Key

postgresql = ''  # Base Postgres
alchemy_postgresql = ''  # For ORM work

stat_webhook = ('', '')  # ID, Code

anilist = SimpleNamespace(client_id=0, client_secret='', redirect_uri='https://anilist.co/api/v2/oauth/pin')  # Anilist API Keys
marvel = SimpleNamespace(public_key='', private_key='')  # Marvel API Keys
genius = SimpleNamespace(access_token='')  # Genius API Key
wavelink = SimpleNamespace(uri='', password='')  # Lavalink Server
```

## License

This project is licensed under the MPL License. See the LICENSE file for details.
