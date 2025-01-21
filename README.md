# Percy v2 Project

This project is a Discord bot written in Python using the discord.py library. The bot is designed to perform various tasks and enhance the moderation, fun and utility of a Discord server.

I would prefer if you not run an instance of my bot, just invite Percy to your Discord but clicking [this](https://discord.com/api/oauth2/authorize?client_id=1070054930125176923&permissions=1480988813527&scope=bot%20applications.commands) link. :)

## Prerequisites

Before running the bot, make sure you have the following installed:

- Python =3.12: [Download Python](https://www.python.org/downloads/)
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

To configure the PostgreSQL database for use by the bot, go to the directory where `main.py` is located, and run the script by doing `python3.8 main.py db init`

4**Configure the bot:**

- Set up a ``.env`` file in the root directory of the project with the following content:

```env
DISCORD_TOKEN=your_discord_bot_token
DISCORD_BETA_TOKEN=your_discord_beta_bot_token
DISCORD_CLIENT_SECRET=your_discord_client_secret
```

## License

This project is licensed under the MPL License. See the LICENSE file for details.
