import secrets
from src import db, models, create_app

app = create_app()
with app.app_context():
    user = models.ApiUser(
        name = 'admin',
        token = secrets.token_urlsafe(32)
    )
    db.session.add(user)
    db.session.commit()
