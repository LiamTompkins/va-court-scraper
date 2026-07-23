# elh-conflict-checker
Checks for VPLC conflicts of interest between their two case management systems

## Setup

Requires Python 3.14 (or any 3.9+).

```
python -m venv venv
.\venv\Scripts\activate.ps1
pip install -r requirements.txt
```

Set the following environment variables (in prod these are read from a `.env`
file in `~/elh-conflict-checker`; for local development, set them directly):

* `SECRET_KEY` — Flask session secret
* `DATABASE_URL` — SQLAlchemy connection string, e.g. `postgresql+psycopg://user:pass@host:5432/dbname`
* `PP_CLIENT_ID` — PracticePanther OAuth client ID
* `PP_CLIENT_SECRET` — PracticePanther OAuth client secret

The app uses PostgreSQL (via the `psycopg` driver). Create the database first, then
let `create_db.py` create the tables:

```
createdb vacourtdata     # or: psql -c "CREATE DATABASE vacourtdata;"
```

Initialize the database and create your first users:

```
python create_db.py                              # create tables
python create_user.py <email> <name> <password>  # dashboard login
python create_api_users.py                        # creates an API user + bearer token
```

Run the app (self-signed HTTPS):

```
python -m flask run --cert=adhoc --host=127.0.0.1
```

Then log into the dashboard and visit `/pp/login` to connect to PracticePanther.

> Note: Older versions of this project required a manual patch to
> `authlib/oauth2/client.py` to send client credentials on token refresh. That
> is no longer needed — the PracticePanther client is now registered with
> `token_endpoint_auth_method='client_secret_post'` in `src/__init__.py`.

## Authorization

Bearer Token

## POST /conflicts

### Request Body

* id: Case ID
* intakeComplete: Set if the initial intake process is complete
* contacts: A list of all contacts in the case. Each contact consists of a name and role. If the role is "other", the "adverse" field should be set. If the contact was previously flagged as a potential conflict and has since been reviewed by VPLC, the "reviewed" field should be set.

```
{
    "id": "Case1",
    "intakeComplete": false,
    "contacts": [
        {
            "firstname": "John",
            "lastname": "Smith",
            "role": "tenant",
            "reviewed": false
        },
        {
            "firstname": "William",
            "lastname": "Wright",
            "role": "landlord",
            "reviewed": false
        },
        {
            "lastname": "ABC Property Managment",
            "role": "propertyManager",
            "reviewed": false
        },
        {
            "firstname": "Ruth",
            "lastname": "G",
            "role": "other",
            "adverse": false,
            "reviewed": false
        }
    ]
}
```

### Response

* result: success or failure
* message: User friendly conflict check result message (see examples below)
* conflicts: List of conflicts found. Each object represents a potential conflict and consists of "helplineContact" (the contact object from the request) and "conflictingContact" (the matching contact from PracticePanther), along with the "role" of the helpline contact.
* details: Human-readable summary of every contact checked and the matches found for each.

```
{
    "result": "success",
    "message": response_msgs["PARTIAL_MATCH"],
    "conflicts": [
        {
            "role": "tenant",
            "helplineContact": {
                "firstname": "John",
                "lastname": "Smith"
            },
            "conflictingContact": {
                "firstname": "Jonathan",
                "lastname": "Smith"
            }
        }
    ],
    "details": "John Smith (tenant)\n\tJonathan Smith (84%)\n"
}
```

### Possible conflict result messages

* No conflict: continue with intake
* Volunteer review: before continuing with intake, you must review the potentially conflicting name match(es). If the near-matching names are clearly distinct and very unlikely to be the result of a poor transcription or misspelling, note that in the case record and continue with the intake. If you are not sure if the near-matching names might be the same person, stop intake and send an email to ELHsupervisor@gmail.com so a VPLC supervisor can review the potential conflict.
* Stop intake and email ELHsupervisor@gmail.com so a VPLC supervisor can review the potential conflict. If you have the client on the phone, tell them that we need to have a supervisor review the names and we will call them back soon.

### Notes

* For contacts to be created in VPLC's internal CMS, the conflict check message must be sent with the `intakeComplete` field set to `true`
* If any conflicts are found, none of the contacts in the request will be added to VPLC's internal CMS
* The first time a conflict is found, the conflict check system will remember it. That specific contact will be bypassed on subsequent conflict checks if the `reviewed` flag is set to `true` for that contact.
* It is possible that a new conflict could be found for a contact after a previous conflict has been found and reviewed. In this case, the new conflict will be returned in the response. As in the first instance, a subsequent conflict check with the `reviewed` flag set will bypass the conflict.
