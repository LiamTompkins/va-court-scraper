import sys
from src import auth, create_app

if len(sys.argv) == 1:
    print('create_user.py <EMAIL> <NAME> <PASSWORD>')
    exit()

app = create_app()
with app.app_context():
    result = auth._do_signup(sys.argv[1], sys.argv[2], sys.argv[3])
    if result is not None:
        print(result)
    else:
        print('USER CREATED')
