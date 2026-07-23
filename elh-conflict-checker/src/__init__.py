import os
from dotenv import load_dotenv
from flask import Flask
from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy
from authlib.integrations.flask_client import OAuth

# Load environment vars -- only for prod "PythonAnywhere" environment
project_folder = os.path.expanduser('~/elh-conflict-checker')
load_dotenv(os.path.join(project_folder, '.env'))

# init SQLAlchemy so we can use it later in our models
db = SQLAlchemy()
oauth = OAuth()

def create_app():
    app = Flask(__name__)

    app.config['SECRET_KEY'] = os.environ['SECRET_KEY']
    # Accepts any SQLAlchemy URL, e.g.
    #   postgresql+psycopg://user:pass@host:5432/dbname
    # DATABASE_URL is preferred; MYSQL_DB is still read for backward compatibility.
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL') or os.environ['MYSQL_DB']
    app.config['PP_CLIENT_ID'] = os.environ['PP_CLIENT_ID']
    app.config['PP_CLIENT_SECRET'] = os.environ['PP_CLIENT_SECRET']
    app.config['PP_AUTHORIZE_URL'] = 'https://app.practicepanther.com/OAuth/Authorize'
    app.config['PP_ACCESS_TOKEN_URL'] = 'https://app.practicepanther.com/OAuth/Token'

    # Flask-SQLAlchemy 3.x removed SQLALCHEMY_POOL_RECYCLE/SQLALCHEMY_POOL_TIMEOUT;
    # pool options now go through SQLALCHEMY_ENGINE_OPTIONS.
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'pool_recycle': 299,
        'pool_timeout': 20,
    }

    db.init_app(app)

    from .pp import fetch_token, update_token
    oauth.init_app(app)
    oauth.register('pp',
        fetch_token=fetch_token,
        update_token=update_token,
        # PracticePanther requires client credentials in the request body for
        # both the token and refresh-token calls. Declaring this here replaces
        # the manual patch of authlib/oauth2/client.py that older Authlib needed.
        client_kwargs={'token_endpoint_auth_method': 'client_secret_post'})

    login_manager = LoginManager()
    login_manager.login_view = 'auth.login'
    login_manager.init_app(app)

    from .models import User

    @login_manager.user_loader
    def load_user(user_id):
        # since the user_id is just the primary key of our user table, use it in the query for the user
        return User.query.get(int(user_id))

    # blueprint for conflict routes in our app
    from .conflict import conflict as conflict_blueprint
    app.register_blueprint(conflict_blueprint)

    # blueprint for auth routes in our app
    from .auth import auth as auth_blueprint
    app.register_blueprint(auth_blueprint)

    # blueprint for non-auth parts of app
    from .main import main as main_blueprint
    app.register_blueprint(main_blueprint)

    return app