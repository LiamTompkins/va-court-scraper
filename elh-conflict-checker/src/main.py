import time
import secrets
from flask import Blueprint, render_template, redirect, url_for, current_app, session, request, jsonify
from flask_login import login_required, current_user
from . import db, oauth
from .models import ApiUser, PpToken, ConflictCheck, KnownConflict
from .pp import get_token, save_token, get_contacts

main = Blueprint('main', __name__)

@main.route('/')
@login_required
def index():
    token = get_token()
    if token is not None:
        token['expired'] = token['issued_at'] + token['expires_in'] < time.time()
    conflict_checks = ConflictCheck.query.all()
    known_conflicts = KnownConflict.query.all()
    return render_template('index.html', token=token, conflict_checks=conflict_checks, known_conflicts=known_conflicts)

@main.route('/profile')
@login_required
def profile():
    return render_template('profile.html', name=current_user.name)

@main.route('/apikeys')
@login_required
def apikeys():
    api_users = ApiUser.query.all()
    return render_template('apikeys.html', api_users=api_users)

@main.route('/apiuser', methods=['POST'])
@login_required
def create_api_user():
    user = ApiUser(
        name = request.form['name'],
        token = secrets.token_urlsafe(32)
    )
    db.session.add(user)
    db.session.commit()
    return redirect(url_for('main.apikeys'))

@main.route('/pp/login')
def pp_login():
    redirect_uri = url_for('main.pp_authorize', _external=True)
    return oauth.pp.authorize_redirect(redirect_uri)

@main.route('/pp/authorize')
def pp_authorize():
    token = oauth.pp.authorize_access_token()
    print("Saving token")
    save_token(token)
    return redirect(url_for('main.index'))

@main.route('/pp/refresh')
def pp_refresh():
    contacts = get_contacts(True)
    return jsonify({'contacts': len(contacts)})

