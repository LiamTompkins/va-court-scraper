import copy
import datetime
import traceback
from flask import Blueprint, jsonify, request, make_response
from flask_httpauth import HTTPTokenAuth
from rapidfuzz import fuzz
from .models import ApiUser, KnownConflict, ConflictCheck
from .pp import get_contacts, create_contact, update_contact
from .helplinecontact import HelplineContact
from . import db

conflict = Blueprint('conflict', __name__)
tokenauth = HTTPTokenAuth(scheme='Bearer')

@tokenauth.verify_token
def verify_token(token):
    user = ApiUser.query.filter_by(token=token).first()
    if user is not None:
        return user.name
    
@conflict.route('/conflicts', methods=['POST'])
@tokenauth.login_required
def check_for_conflicts():
    try:
        conflict_check = ConflictCheck(
            time = datetime.datetime.now()
        )
        db.session.add(conflict_check)
        db.session.commit()

        username = tokenauth.current_user()
        print("Handling conflict check from {}!".format(username))

        case = request.get_json(force=True)
        print(case, flush=True)

        helpline_contacts = []
        case_id = case['id']
        intake_complete = case['intakeComplete'] if 'intakeComplete' in case else False

        print("Intake complete in ALWAYS ON mode")
        intake_complete = True

        conflict_check.time = datetime.datetime.now()
        conflict_check.case_id = case_id
        conflict_check.intake_complete = intake_complete
        conflict_check.contact_count = len(case['contacts'])
        conflict_check.request_text = str(case)

        for contact in case['contacts']:
            helpline_contact = HelplineContact(case_id, contact)
            if helpline_contact.display_name == "":
                continue
            helpline_contacts.append(helpline_contact)

        # Get known conflicts for this case
        known_conflicts = KnownConflict.query.filter_by(case_id=case_id).all()
        print(f"Found {len(known_conflicts)} known conflicts")

        # Get practice panther contacts
        pp_contacts = get_contacts()
        conflicts = []
        details = ""
        existing_contacts = {}
        exact_match_found = False

        print(f"Checking incoming conflicts against {len(pp_contacts)} PP contacts")

        # Do actual conflict check
        for helpline_contact in helpline_contacts:
            details += f"{helpline_contact.first_name} {helpline_contact.last_name} ({helpline_contact.role})\n"
            conflict_found_for_contact = False

            for pp_contact in pp_contacts:
            
                # Don't check if the contact from PP is from this case
                if helpline_contact.is_pp_contact(pp_contact):
                    existing_contacts[pp_contact['role']] = pp_contact
                    continue

                # Don't check if the contact isn't adverse
                if helpline_contact.adverse == pp_contact['is_adverse']:
                    continue

                # Don't check if contact is confirmed and already in known contacts database
                if helpline_contact.reviewed:
                    if any(kc.account_id == pp_contact['account_id'] and kc.role == helpline_contact.role for kc in known_conflicts):
                        continue

                # Fuzzy match names
                score = fuzz.partial_ratio(pp_contact['display_name'].lower(), helpline_contact.display_name.lower())

                # Handle perfect score
                if score >= 99:
                    exact_match_found = True

                # Handle close score
                if score >= 80:
                    conflict_found_for_contact = True
                    conflicts.append({
                        "role": helpline_contact.role,
                        "helplineContact": {
                            "firstname": helpline_contact.first_name,
                            "lastname": helpline_contact.last_name
                        },
                        "conflictingContact": {
                            "firstname": pp_contact['first_name'],
                            "lastname": pp_contact['last_name']
                        }
                    })
                    details += f"\t{pp_contact['first_name']} {pp_contact['last_name']} ({score}%)\n"

                    # Add to known conflicts to db
                    print("Adding contact to known conflicts table")
                    new_known_conflict = KnownConflict(
                        case_id = case_id,
                        account_id = pp_contact['account_id'],
                        contact_id = pp_contact['contact_id'],
                        role = helpline_contact.role
                    )
                    db.session.add(new_known_conflict)

            if not conflict_found_for_contact:
                details += "\tNo conflicts\n"
        
        conflict_check.conflict_count = len(conflicts)

        message = response_msgs['NO_MATCH']
        conflict_check.conflict_type = "NO_MATCH"
        if len(conflicts) > 0:
            message = response_msgs['PARTIAL_MATCH']
            conflict_check.conflict_type = "PARTIAL_MATCH"
        if exact_match_found:
            message = response_msgs['EXACT_MATCH']
            conflict_check.conflict_type = "EXACT_MATCH"

        conflict_check.pp_contacts_created = 0
        conflict_check.pp_contacts_updated = 0

        # Create contacts in PP
        if len(conflicts) == 0 and intake_complete:
            print("Adding contacts to PP")
            for helpline_contact in helpline_contacts:
                if helpline_contact.role in existing_contacts:
                    print(f"Updating {helpline_contact.display_name}")
                    existing_contact = existing_contacts[helpline_contact.role]
                    update_contact(existing_contact['account_id'], existing_contact['contact_id'], helpline_contact)
                    conflict_check.pp_contacts_updated += 1
                else:
                    print(f"Creating {helpline_contact.display_name}")
                    create_contact(helpline_contact)
                    conflict_check.pp_contacts_created += 1

        response = {
            "result": "success",
            "message": message,
            "conflicts": conflicts,
            "details": details
        }
        print(response, flush=True)

        conflict_check.error = False
        conflict_check.response_text = str(response)

        one_month_ago = datetime.datetime.now() + datetime.timedelta(days=-30)
        ConflictCheck.query.filter(ConflictCheck.time < one_month_ago).delete()

        db.session.commit()

        return jsonify(response)
    except:
        # printing stack trace 
        traceback.print_exc()
    
    try:
        conflict_check.error = True
        db.session.commit()
    except:
        print("Error saving database after exception")
        traceback.print_exc()

    return make_response(jsonify({
            "result": "error",
            "message": "Bad request"
        }), 400)

response_msgs = {
    "NO_MATCH": "No conflict: continue with intake",
    "PARTIAL_MATCH": "Volunteer review: before continuing with intake, you must review the potentially conflicting name match(es). If the near-matching names are clearly distinct and very unlikely to be the result of a poor transcription or misspelling, note that in the case record and continue with the intake. If you are not sure if the near-matching names might be the same person, stop intake and send an email to ELHsupervisor@gmail.com so a VPLC supervisor can review the potential conflict.",
    "EXACT_MATCH": "Stop intake and email ELHsupervisor@gmail.com so a VPLC supervisor can review the potential conflict. If you have the client on the phone, tell them that we need to have a supervisor review the names and we will call them back soon." 
}

sample_conflict_request = {
    "id": "Case1",
    "contacts": [
        {
            "firstname": "John",
            "lastname": "Smith",
            "role": "tenant",
            "reviewed": False
        },
        {
            "firstname": "William",
            "lastname": "Wright",
            "role": "landlord",
            "reviewed": False
        },
        {
            "lastname": "ABC Property Managment",
            "role": "propertyManager",
            "reviewed": False
        },
        {
            "firstname": "Ruth",
            "lastname": "G",
            "role": "other",
            "adverse": False,
            "reviewed": False
        }
    ]
}
