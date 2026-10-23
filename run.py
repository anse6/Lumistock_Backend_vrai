import os
from app import create_app 

app = create_app()
if __name__ == "__main__":
    # Le débogueur Werkzeug permet d'exécuter du code sur le serveur : il n'est
    # activé que si FLASK_DEBUG=1 est défini explicitement (en local).
    app.run(debug=os.getenv("FLASK_DEBUG") == "1", host="0.0.0.0", port=3006)