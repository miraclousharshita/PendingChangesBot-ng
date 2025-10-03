# PendingChangesBot

PendingChangesBot is a Django application that inspects pending changes on Wikimedia
projects using the Flagged Revisions API. It fetches the 50 oldest pending pages for a
selected wiki, caches their pending revisions together with editor metadata, and exposes a
Vue.js interface for reviewing the results.

## Installation

1. **Clone the repository**
   ```bash
   git clone git@github.com:Wikimedia-Suomi/PendingChangesBot-ng.git
   cd PendingChangesBot-ng
   ```
2. **Create and activate a virtual environment** (recommended)
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows use: .venv\\Scripts\\activate
   ```
3. **Install Python dependencies**
   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

## Configuring Pywikibot Superset OAuth

Pywikibot needs to log in to [meta.wikimedia.org](https://meta.wikimedia.org) and approve
Superset's OAuth client before the SQL queries in `SupersetQuery` will succeed. Follow
the steps below once per user account that will run PendingChangesBot:

1. **Create a Pywikibot configuration**
   ```bash
   echo "usernames['meta']['meta'] = '$YOUR_USERNAME'" > user-config.py
   ```

3. **Log in with Pywikibot**
   ```bash
   python -m pywikibot.scripts.login -site:meta
   ```
   The command should report `Logged in on metawiki` and create a persistent login
   cookie at `~/.pywikibot/pywikibot.lwp`.

4. **Approve Superset's OAuth client**
   - While still logged in to Meta-Wiki in your browser, open
     <https://superset.wmcloud.org/login/>.
   - Authorize the OAuth request for Superset. After approval you should be redirected
     to Superset's interface.

## Running the database migrations

```bash
cd app
python manage.py makemigrations
python manage.py migrate
```

## Running the application

The Django project serves both the API and the Vue.js frontend from the same codebase.

```bash
cd app
python manage.py runserver
```

Open <http://127.0.0.1:8000/> in your browser to use the interface. JSON endpoints are
available under `/api/wikis/<wiki_id>/…`, for example `/api/wikis/1/pending/`.

## Running unit tests

Unit tests live in the Django backend project. Run them from the `app/` directory so Django can locate the correct settings module.

```bash
cd app
python manage.py test
```

## Running Flake8

Run Flake8 from the repository root to lint the code according to the configuration provided in `.flake8`.

```bash
flake8
```

If you are working inside a virtual environment, ensure it is activated before executing the command.

After these steps Pywikibot will be able to call Superset's SQL Lab API without running
into `User not logged in` errors, and PendingChangesBot can fetch pending revisions
successfully.
