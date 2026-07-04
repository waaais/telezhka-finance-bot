# Staff Finance Telegram Bot

Production-ready Telegram bot for daily staff revenue accounting.

## Stack

- Python 3.11+
- aiogram 3 for Telegram long polling
- SQLAlchemy async storage layer
- PostgreSQL in production, SQLite for local development
- Alembic-ready schema module
- Structured JSON logging
- unittest test suite

## Architecture

- `app/bot` - Telegram handlers and user-facing replies
- `app/parser` - tolerant Russian free-text parser
- `app/storage` - database models and repositories
- `app/salary` - salary calculation rules
- `app/statistics` - period aggregation
- `app/config.py` - environment config
- `app/logging_config.py` - structured logs

## Quick Start

1. Create a bot with BotFather and copy the token.
2. Create `.env` from `.env.example`.
3. Install dependencies:

```bash
python -m pip install -e ".[dev]"
```

4. Run tests:

```bash
python -m unittest
```

5. Start the bot:

```bash
python -m app.main
```

SQLite data is stored in `./data/finance.db`.

For production migrations:

```bash
alembic upgrade head
```

## Docker Compose

```bash
cp .env.example .env
docker compose up --build
```

For production, set a real `BOT_TOKEN` and keep the default Postgres service in `compose.yaml`,
or point `DATABASE_URL` to a managed PostgreSQL instance.

## Google Sheets Sync

The bot can write each successful finance entry into the existing spreadsheet:

```text
ТЕЛЕЖКА - 2026
spreadsheet id: 1tZ1JwIrK0co4l3LRKSn8wODdC6XuXmXamlJ2-Tjc_FE
```

It writes to monthly tabs such as `ИЮЛЬ 2026`, `АВГУСТ 2026`.
Columns used by the bot:

- `A` - date
- `B` - seller
- `C` - salary
- `D` - cash
- `E` - cashless

To enable sync:

1. Create a Google Cloud service account with Google Sheets API access.
2. Download the service account JSON key as `google-service-account.json`.
3. Share the spreadsheet with the service account email as Editor.
4. Set in `.env`:

```bash
GOOGLE_SHEETS_ENABLED=true
GOOGLE_SHEETS_SPREADSHEET_ID=1tZ1JwIrK0co4l3LRKSn8wODdC6XuXmXamlJ2-Tjc_FE
GOOGLE_SHEETS_CREDENTIALS_FILE=./google-service-account.json
```

The bot chooses the monthly tab by entry date. If no date is provided in the message,
today is used. If the date row already has data, the bot updates that row instead of
creating a duplicate.

## Message Examples

```text
Ксюша
нал 12500
безнал 38640
```

```text
2 июля Настя нал 14000 безнал 42000
```

```text
сегодня Кристина наличка 10 000 карта 22 500
```

```text
измени наличку за 2 июля на 19000
```

```text
эвотор
```

The `эвотор` command tries to fetch today's revenue from Evotor, reads the seller
from the schedule in Google Sheets, and writes the day into the spreadsheet.

Daily reminders are enabled by default at `22:10` in `TIMEZONE`. Configure with:

```bash
DAILY_REMINDER_ENABLED=true
DAILY_REMINDER_TIME=22:10
```

## Evotor Sync

Evotor sync is disabled by default. When enabled, the bot can:

- fetch today's revenue manually with `эвотор`;
- at `22:10`, fetch revenue automatically, update the day, and send today's summary;
- read the seller from the Google Sheets schedule.

Set these variables in `.env`:

```bash
EVOTOR_ENABLED=true
EVOTOR_BASE_URL=https://api.evotor.ru
EVOTOR_REVENUE_URL_TEMPLATE=
EVOTOR_STORE_UUID=
EVOTOR_TERMINAL_UUID=
EVOTOR_AUTH_HEADER_NAME=X-Authorization
EVOTOR_TOKEN=
EVOTOR_TOKEN_FILE=./data/evotor-token.json
EVOTOR_RECEIPTS_ENABLED=true
EVOTOR_RECEIPTS_PATH=/evotor/receipts
EVOTOR_RECEIPTS_FILE=./data/evotor-receipts.jsonl
```

`EVOTOR_REVENUE_URL_TEMPLATE` supports placeholders:

```text
{base_url}
{date}
{date_from}
{date_to}
{store_uuid}
{terminal_uuid}
```

The exact URL depends on the Evotor API access/app being used. Keep the token out
of Git and store it only in `.env` on the server.

If `EVOTOR_REVENUE_URL_TEMPLATE` is empty and `EVOTOR_RECEIPTS_ENABLED=true`,
the bot reads revenue from receipts pushed by Evotor to `EVOTOR_RECEIPTS_FILE`.

### Evotor App Token Callback

The Evotor developer cabinet can send the REST API application token to this bot.
Enable the receiver only on the server:

```bash
EVOTOR_TOKEN_RECEIVER_ENABLED=true
EVOTOR_CALLBACK_SECRET=replace-with-long-random-secret
EVOTOR_CALLBACK_HOST=0.0.0.0
EVOTOR_CALLBACK_PORT=8080
EVOTOR_CALLBACK_PATH=/evotor/token
EVOTOR_TOKEN_FILE=./data/evotor-token.json
EVOTOR_RECEIPTS_ENABLED=true
EVOTOR_RECEIPTS_PATH=/evotor/receipts
EVOTOR_RECEIPTS_FILE=./data/evotor-receipts.jsonl
```

In the Evotor developer cabinet, set:

- `URL`: `http://SERVER_IP:8080/evotor/token`
- authorization type: `Ваш токен`
- `Ваш токен`: the same value as `EVOTOR_CALLBACK_SECRET`

### Evotor Receipts Callback

Use the `Чеки (ver.2)` integration block to push every receipt to the bot:

- `URL`: `http://SERVER_IP:8080/evotor/receipts`
- authorization token: the same value as `EVOTOR_CALLBACK_SECRET`
- enable fields: `type`, `storeId`, `deviceId`, `dateTime`, `totalAmount`, `paymentSource`

The bot filters receipts by `EVOTOR_TERMINAL_UUID`. For the current cash register,
this can be the KKM/device value from Evotor, for example `00307900861869`.
The `Документы с терминала` block is not required for daily revenue sync.

If Evotor requires HTTPS for the callback URL, put a domain and HTTPS reverse proxy
in front of the bot, then use `https://YOUR_DOMAIN/evotor/token`.

## Commands

- `/start` - greeting
- `/help` - input format
- `неделя` - current week statistics
- `месяц` - current month statistics
- `эвотор` - fetch today's revenue from Evotor
- `/employees` - list salary rules from database
- `/set_salary Имя 2500` - upsert employee salary rule

## Production Notes

- Incoming Telegram messages are stored in `processed_messages` before business processing.
- Duplicate Telegram updates are ignored by `(chat_id, message_id)`.
- Every bad input receives a helpful response.
- Database transactions protect record creation.
- Structured logs include exceptions for operational monitoring.
- Salaries are data-driven and can be changed via bot command without deployment.
- `finance_entries` is append-only by default, so accounting history is preserved.
- Alembic migration `0001_initial` describes the production database schema.
