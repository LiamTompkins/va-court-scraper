import time
from flask_login import UserMixin
from . import db

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(100), unique=True)
    password = db.Column(db.String(255))
    name = db.Column(db.String(1000))

class ApiUser(db.Model):
    __tablename__ = 'api_users'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64))
    # PostgreSQL varchar comparison is already case-sensitive / byte-exact,
    # so no explicit collation is needed for exact token matching.
    token = db.Column(db.String(64))

class PpToken(db.Model):
    __tablename__ = 'pp_tokens'
    id = db.Column(db.Integer, primary_key=True)
    access_token = db.Column(db.String(1000), nullable=False)
    refresh_token = db.Column(db.String(255))
    issued_at = db.Column(
        db.Integer, nullable=False, default=lambda: int(time.time())
    )
    expires_in = db.Column(db.Integer, nullable=False, default=0)
    expires_at = db.Column(db.Integer, nullable=False, default=0)

class ConflictCheck(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    time = db.Column(db.DateTime)
    case_id = db.Column(db.String(20))
    intake_complete = db.Column(db.Boolean)
    contact_count = db.Column(db.Integer)
    conflict_count = db.Column(db.Integer)
    conflict_type = db.Column(db.String(20))
    pp_contacts_created = db.Column(db.Integer)
    pp_contacts_updated = db.Column(db.Integer)
    error = db.Column(db.Boolean)
    request_text = db.Column(db.Text)
    response_text = db.Column(db.Text)

class KnownConflict(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    case_id = db.Column(db.String(20), nullable=False)
    account_id = db.Column(db.String(36), nullable=False)
    contact_id = db.Column(db.String(36), nullable=False)
    role = db.Column(db.String(20))

class PpContact(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.String(64))
    contact_id = db.Column(db.String(64))
    first_name = db.Column(db.String(64))
    last_name = db.Column(db.String(64))
    role = db.Column(db.String(64))
    adverse = db.Column(db.String(64))
    elh_case_number = db.Column(db.String(64))
    updated_at = db.Column(db.String(64))
    is_adverse = db.Column(db.Boolean)
    display_name = db.Column(db.String(128))
