# create_staff.py
import getpass
from app import create_app
from extensions import db
from models.user_model import UserModel
from utils.passwords import erreur_robustesse

def main():
    # Initialisation de l'application Flask
    app = create_app()
    
    with app.app_context():
        print("\n" + "="*45)
        print("   CRÉATION DU COMPTE STAFF (SUPERADMIN)")
        print("="*45)
        
        try:
            name = input("Nom : ").strip()
            first_name = input("Prénom : ").strip()
            email = input("Email : ").strip().lower()
            # Saisie masquée : le mot de passe n'apparaît ni à l'écran ni dans l'historique.
            password = getpass.getpass("Mot de passe : ")
            confirmation = getpass.getpass("Confirmez le mot de passe : ")
            
            if not all([name, first_name, email, password]):
                print("\n Erreur : Tous les champs sont obligatoires.")
                return
            if password != confirmation:
                print("\n Erreur : Les mots de passe ne correspondent pas.")
                return
            erreur = erreur_robustesse(password, email)
            if erreur:
                print(f"\n Erreur : {erreur}")
                return

            # Vérifier si l'utilisateur existe déjà
            existing_user = UserModel.find_by_email(email)
            if existing_user:
                print(f"\n Erreur : L'utilisateur '{email}' existe déjà.")
                return

            # Création du staff (le modèle hache le mot de passe)
            user_id = UserModel.create_staff(
                name=name,
                first_name=first_name,
                email=email,
                password=password
            )
            
            print("\n" + "-"*45)
            print("✅ SUCCÈS : Compte Staff créé !")
            print(f"🔹 ID       : {user_id}")
            print(f"🔹 Email    : {email}")
            print(f"🔹 Rôle     : staff (Superadmin)")
            print("-"*45)
            print("\nVous pouvez maintenant lancer l'application et vous connecter.")
            
        except KeyboardInterrupt:
            print("\n\n👋 Opération annulée par l'utilisateur.")
        except Exception as e:
            print(f"\n Une erreur inattendue est survenue : {e}")

if __name__ == "__main__":
    main()
