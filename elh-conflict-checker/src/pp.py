import datetime
import uuid
from . import db, oauth
from .models import PpToken, PpContact

custom_fields_ids = None

def get_token():
    token = PpToken.query.first()
    if token is None:
        return None
    return token.__dict__

def save_token(token):
    global custom_fields_ids
    custom_fields_ids = None

    # delete all tokens
    PpToken.query.delete()

    # save new token
    pp_token = PpToken()
    pp_token.access_token = token['access_token']
    pp_token.refresh_token = token['refresh_token']
    pp_token.expires_in = token['expires_in']
    pp_token.expires_at = token['expires_at']
    db.session.add(pp_token)
    db.session.commit()

def fetch_token():
    print('Fetch token')
    return get_token()

def update_token(token, refresh_token=None, access_token=None):
    print('Update token')
    print(token)
    save_token(token)

def get_contacts(reset=False):
    contacts = []

    # If the reset flag is set, clear the database and download all PP contacts through the API
    if reset:
        print('Resetting pp contacts')
        PpContact.query.delete()
        db.session.commit()
    
    # After pp contacts are loaded from the local database, we'll store the time of the most recent contact
    # and use that to fetch a little data as possible from PP. This will speed up the API call.
    most_recently_updated = None
    cached_contacts = {}

    # Load pp contacts from our local database because it's faster than the PP API
    ppContacts = PpContact.query.all()
    for c in ppContacts:
        cached_contacts[c.account_id] = {
            'account_id': c.account_id,
            'contact_id': c.contact_id,
            'first_name': c.first_name,
            'last_name': c.last_name,
            'role': c.role,
            'adverse': c.adverse,
            'elh_case_number': c.elh_case_number,
            'updated_at': c.updated_at,
            'is_adverse': c.is_adverse,
            'display_name': c.display_name
        }

        # Store the most recent contact update time
        updated_at = None
        try:
            updated_at = datetime.datetime.strptime(c.updated_at, '%Y-%m-%dT%H:%M:%S.%f')
        except ValueError:
            updated_at = datetime.datetime.strptime(c.updated_at, '%Y-%m-%dT%H:%M:%S')
        if most_recently_updated is None:
            most_recently_updated = updated_at
        else:
            most_recently_updated = max([most_recently_updated, updated_at])

    print('Loaded cached contacts: ' + str(len(cached_contacts)))

    # Add the cached contacts
    contacts.extend(cached_contacts.values())

    updated_since = ''
    if most_recently_updated is not None:
        most_recently_updated = most_recently_updated + datetime.timedelta(hours=-12)
        updated_since = '?updated_since=' + most_recently_updated.isoformat()

    print('Getting PP Accounts w/ filter: ' + updated_since)

    # Get contacts from PP API
    resp = oauth.pp.get('https://app.practicepanther.com/api/v2/accounts' + updated_since)
    accounts = resp.json()

    print('Loaded PP accounts: ' + str(len(accounts)))

    for account in accounts:

        # If the contact we downloaded from PP was cached, delete it
        if account['id'] in cached_contacts:
            PpContact.query.filter(PpContact.account_id == account['id']).delete()
            del cached_contacts[account['id']]

        contact = {
            'account_id': account['id'],
            'contact_id': account['primary_contact']['id'],
            'first_name': account['primary_contact']['first_name'],
            'last_name': account['primary_contact']['last_name'],
            'role': None,
            'adverse': None,
            'elh_case_number': None,
            'updated_at': account['updated_at']
        }

        for custom_field in account['primary_contact']['custom_field_values']:
            label = custom_field['custom_field_ref']['label']
            if custom_field['value_string'] is None:
                continue
            if label == 'Role':
                contact['role'] = custom_field['value_string']
            elif label == 'Conflict Check':
                contact['adverse'] = custom_field['value_string'].lower()
            elif label == 'ELH Case Number':
                contact['elh_case_number'] = custom_field['value_string']
        
        adverse_values = ['yes', 'true', 'adverse to client']
        contact['is_adverse'] = contact['adverse'] in adverse_values
        first_name = contact['first_name'] or ''
        last_name = contact['last_name'] or ''
        contact['display_name'] = (first_name + ' ' + last_name).strip()
        
        contacts.append(contact)

        # Write the new PP contact to local db cache
        pp_contact = PpContact()
        pp_contact.account_id = contact['account_id']
        pp_contact.contact_id = contact['contact_id']
        pp_contact.first_name = contact['first_name']
        pp_contact.last_name = contact['last_name']
        pp_contact.role = contact['role']
        pp_contact.adverse = contact['adverse']
        pp_contact.elh_case_number = contact['elh_case_number']
        pp_contact.updated_at = contact['updated_at']
        pp_contact.is_adverse = contact['is_adverse']
        pp_contact.display_name = contact['display_name']
        db.session.add(pp_contact)
    
    if len(accounts) > 0:
        db.session.commit()
        print('Contacts added to cache: ' + str(len(accounts)))

    return contacts

def get_custom_fields():
    global custom_fields_ids
    if custom_fields_ids is not None:
        return custom_fields_ids

    print("Getting custom fields from PP")

    resp = oauth.pp.get('https://app.practicepanther.com/api/v2/customfields/contact')
    custom_fields = resp.json()
    custom_fields_ids = {}

    for cf in custom_fields:
        custom_fields_ids[cf['label']] = cf['id']
    
    return custom_fields_ids

def create_contact(helpline_contact):
    cf_ids = get_custom_fields()
    pp_contact = create_pp_contact(None, None, cf_ids, helpline_contact)
    
    resp = oauth.pp.post('https://app.practicepanther.com/api/v2/accounts', json=pp_contact)
    if resp.status_code != 200:
        raise Exception("PP create account failed: " + resp.text)
    return

def update_contact(account_id, contact_id, helpline_contact):
    # Confirm contact exists
    resp = oauth.pp.get('https://app.practicepanther.com/api/v2/accounts/' + account_id)
    if resp.status_code == 404:
        print("Updating account that doesn't exist. Account will be created")
        create_contact(helpline_contact)
        return

    cf_ids = get_custom_fields()
    pp_contact = create_pp_contact(account_id, contact_id, cf_ids, helpline_contact)
    
    resp = oauth.pp.put('https://app.practicepanther.com/api/v2/accounts', params={'id': account_id}, json=pp_contact)
    if resp.status_code != 200:
        raise Exception("PP update account failed: " + resp.text)
    return

def create_pp_contact(account_id, contact_id, cf_ids, helpline_contact):
    conflict_check_value = 'Adverse to client' if helpline_contact.adverse else 'Not adverse to client'
    return {
        'id': account_id if account_id is not None else str(uuid.uuid4()),
        'primary_contact': {
            'id': contact_id if contact_id is not None else str(uuid.uuid4()),
            'first_name': helpline_contact.first_name,
            'last_name': helpline_contact.last_name,
            'custom_field_values': [{
                'custom_field_ref': {'id': cf_ids['Role']},
                'value_string': helpline_contact.role
            }, {
                'custom_field_ref': {'id': cf_ids['Conflict Check']},
                'value_string': conflict_check_value
            }, {
                'custom_field_ref': {'id': cf_ids['ELH Case Number']},
                'value_string': helpline_contact.case_id
            }]
        }
    }
