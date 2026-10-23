# extensions.py

from database import SQLDatabase
from flask_jwt_extended import JWTManager
from flask_mail import Mail

db = SQLDatabase()
jwt = JWTManager()
mail = Mail()
