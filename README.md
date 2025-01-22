# Percy v2 Project

This project is a Discord bot written in Python using the discord.py library. The bot is designed to perform various tasks and enhance the moderation, fun and utility of a Discord server.

I would prefer if you not run an instance of my bot, just invite Percy to your Discord but clicking [this](https://discord.com/api/oauth2/authorize?client_id=1070054930125176923&permissions=1480988813527&scope=bot%20applications.commands) link. :)

## Prerequisites

Before running the bot, make sure you have the following installed:

- Python =3.12: [Download Python](https://www.python.org/downloads/)
- PostgreSQL: [Download PostgreSQL](https://www.postgresql.org/download/)
- Poetry: [Download Poetry](https://python-poetry.org/docs/)

## Installation

1. **Clone the repository:**

```bash
git clone https://github.com/klappstuhlpy/Percy.git
```

2. **Install the required Python dependencies with poetry:**

```bash
poetry install
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

To configure the PostgreSQL database for use by the bot, go to the directory where `main.py` is located, and run the script by doing `python3.12 main.py db init`

4**Configure the bot:**

- Set up a ``docker-compose.yml`` file in the root directory of the project.
- This will store your docker image settings and **important** environment variables that *need* to be defined!

You can find an example here: [docker-compose.yml](https://gist.github.com/klappstuhlpy/92e2e5d857458af46feb91ab4cf88fb4)

## License

This project is licensed under the MPL License. See the LICENSE file for details.
